"""
Network host discovery
"""

import socket
import subprocess
import re
from typing import List, Dict
from utils.i18n import _

from utils.logger import Logger

class NetworkDiscovery:
    """Sunshine host discovery on network"""
    
    def __init__(self):
        self.hosts = []
        self.logger = Logger()
        
    def discover_hosts(self, callback=None):
        import threading
        def run():
            hosts = []
            try:
                res = subprocess.run(['avahi-browse', '-t', '-r', '-p', '_nvstream._tcp'], capture_output=True, text=True, timeout=5)
                if res.returncode == 0 and res.stdout: hosts = self.parse_avahi_output(res.stdout)
                if not hosts: hosts = self.manual_scan()
            except: hosts = self.manual_scan()
            if callback:
                from gi.repository import GLib
                GLib.idle_add(callback, hosts)
        threading.Thread(target=run, daemon=True).start()
        
    def parse_avahi_output(self, output: str) -> List[Dict]:
        """
        Parses avahi output prioritizing Global IPv6 > IPv4 > Link-Local IPv6
        """
        host_map = {}
        
        for line in output.split('\n'):
            p = line.split(';')
            if len(p) > 7 and p[0] == '=':
                service_name = p[3]
                hostname = p[6]
                ip = p[7]
                interface = p[1]
                port = int(p[8])
                
                # Create entry if not exists
                if service_name not in host_map:
                    host_map[service_name] = {
                        'name': service_name,
                        'hostname': hostname,
                        'port': port,
                        'status': 'online',
                        'ips': []
                    }
                
                # Classify IP
                ip_type = 'ipv4'
                if ':' in ip:
                    if ip.startswith('fe80'):
                        ip_type = 'ipv6_link_local'
                        # Fix scope ID
                        if "%" not in ip: ip = f"{ip}%{interface}"
                    else:
                        ip_type = 'ipv6_global'
                
                # Add formatted IP to list
                # User reported Moonlight CLI on Linux prefers raw IP without brackets
                formatted_ip = ip
                host_map[service_name]['ips'].append({'ip': formatted_ip, 'type': ip_type, 'raw': ip})
        
        # Enrichment: Ensure IPv4 exists
        for name, data in host_map.items():
            has_v4 = any(ip['type'] == 'ipv4' for ip in data['ips'])
            if not has_v4 and data['hostname']:
                try:
                    # Try to resolve IPv4 explicitly
                    hostname = data['hostname']
                    # Sometimes avahi returns hostname without .local, try both if needed
                    # But usually it is hostname.local
                    ipv4 = socket.gethostbyname(hostname)
                    if ipv4 and not ipv4.startswith('127.'):
                        data['ips'].append({'ip': ipv4, 'type': 'ipv4', 'raw': ipv4})
                except:
                    pass
        
        final_hosts = []
        for name, data in host_map.items():
            # Add all discovered IPs to the list so user can choose
            for ip_info in data['ips']:
                display_name = data['name']
                # Append protocol info to distinguish in UI if needed, 
                # although the subtitle in UI showing the IP is usually enough.
                # However, to be explicit:
                if ip_info['type'] == 'ipv6_link_local':
                    display_name += _(" (IPv6 Local)")
                elif ip_info['type'] == 'ipv6_global':
                    display_name += _(" (IPv6 Global)")
                
                final_hosts.append({
                    'name': display_name,
                    'ip': ip_info['ip'],
                    'port': data['port'],
                    'status': 'online',
                    'hostname': data['hostname']
                })
                
        return final_hosts
        
    def manual_scan(self) -> List[Dict]:
        import concurrent.futures
        from concurrent.futures import ThreadPoolExecutor
        hosts = []; local_ip = self.get_local_ip()
        targets = ['127.0.0.1', '::1']
        
        # IPv4 scan
        if local_ip and '.' in local_ip:
            subnet = '.'.join(local_ip.split('.')[:-1])
            for i in range(1, 255): targets.append(f"{subnet}.{i}")
            
        # IPv6 Radical Scan: Check neighbor cache and active interfaces
        try:
            # 1. Check neighbor cache
            res = subprocess.run(['ip', '-6', 'neigh', 'show'], capture_output=True, text=True, timeout=2)
            if res.returncode == 0:
                for line in res.stdout.splitlines():
                    parts = line.split()
                    if len(parts) >= 3 and ':' in parts[0]:
                        ip = parts[0]
                        if ip.startswith('fe80'):
                            try:
                                dev_idx = parts.index('dev')
                                if dev_idx + 1 < len(parts): targets.append(f"{ip}%{parts[dev_idx+1]}")
                            except: pass
                        else: targets.append(ip)
            
            # 2. Flush neighbor cache to avoid stale entries
            try: subprocess.run(['ip', '-6', 'neigh', 'flush', 'all'], capture_output=True, timeout=1)
            except: pass
            
            # 3. Ping all-nodes multicast briefly to populate neighbor cache
            subprocess.run(['ping', '-6', '-c', '1', '-W', '1', 'ff02::1%lo'], capture_output=True, timeout=1) 
        except: pass

        def check(ip):
            if self.check_sunshine_port(ip):
                try: 
                    # Try to resolve name, but don't fail if it takes too long
                    name = socket.gethostbyaddr(ip)[0]
                except: 
                    name = ip
                
                # User reported Moonlight CLI on Linux prefers raw IP without brackets
                return {'name': _("Host ({})").format(ip), 'ip': ip, 'port': 47989, 'status': 'online'}
            return None

        with ThreadPoolExecutor(max_workers=100) as ex:
            for r in ex.map(check, targets):
                if r: hosts.append(r)
        return hosts
        
    def check_sunshine_port(self, ip: str, port: int = 47989, timeout: float = 0.5) -> bool:
        try:
            with socket.create_connection((ip, port), timeout=timeout) as s: return True
        except: return False
            
    def get_local_ip(self) -> str:
        # Try IPv4
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80)); return s.getsockname()[0]
        except: pass
        # Try IPv6
        try:
            with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as s:
                s.connect(("2001:4860:4860::8888", 80)); return s.getsockname()[0]
        except: pass
        return ""

    def resolve_pin(self, pin: str, timeout: int = 3) -> str:
        if not pin or len(pin) != 6: return ""
        import threading
        results = {'v4': None, 'v6': None}
        
        def try_v4():
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1); s.settimeout(timeout)
                    s.sendto(f"WHO_HAS_PIN {pin}".encode(), ('<broadcast>', 48011))
                    data, addr = s.recvfrom(1024)
                    if data.decode().startswith("I_HAVE_PIN"): results['v4'] = addr[0]
            except: pass

        def try_v6():
            try:
                # ff02::1 is all-nodes link-local multicast
                with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as s:
                    s.settimeout(timeout)
                    s.sendto(f"WHO_HAS_PIN {pin}".encode(), ('ff02::1', 48011))
                    data, addr = s.recvfrom(1024)
                    if data.decode().startswith("I_HAVE_PIN"): 
                        results['v6'] = addr[0]
            except: pass
            
        t1 = threading.Thread(target=try_v4); t2 = threading.Thread(target=try_v6)
        t1.start(); t2.start()
        t1.join(timeout); t2.join(timeout)
        
        return results['v4'] or results['v6'] or ""

    def start_pin_listener(self, pin: str, name: str):
        import threading
        running = [True]
        def run():
            # Listen on both IPv4 and IPv6
            for family in [socket.AF_INET, socket.AF_INET6]:
                try:
                    s = socket.socket(family, socket.SOCK_DGRAM)
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    if family == socket.AF_INET6:
                        # Ensure IPv6 socket doesn't block IPv4
                        try: s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                        except: pass
                        s.bind(('::', 48011))
                    else:
                        s.bind(('0.0.0.0', 48011))
                    s.settimeout(1)
                    
                    def listener(sock):
                        while running[0]:
                            try:
                                data, addr = sock.recvfrom(1024)
                                if data.decode().strip() == f"WHO_HAS_PIN {pin}": 
                                    sock.sendto(f"I_HAVE_PIN {name}".encode(), addr)
                            except: pass
                        sock.close()
                    
                    threading.Thread(target=listener, args=(s,), daemon=True).start()
                except: pass
        
        run()
        return lambda: running.__setitem__(0, False)

    def get_global_ipv4(self) -> str:
        for url in ['ipinfo.io/ip', 'checkip.amazonaws.com']:
            try:
                res = subprocess.run(['curl', '-s', '-4', '--connect-timeout', '3', url], capture_output=True, text=True)
                if res.returncode == 0 and res.stdout.strip(): return res.stdout.strip()
            except: pass
        return "None"
        
    def get_global_ipv6(self) -> str:
        for url in ['ifconfig.me', 'icanhazip.com']:
            try:
                res = subprocess.run(['curl', '-s', '-6', '--connect-timeout', '3', url], capture_output=True, text=True)
                if res.returncode == 0 and res.stdout.strip(): return res.stdout.strip()
            except: pass
        return "None"

def resolve_pin_to_ip(pin: str) -> dict | None:
    """Helper for GuestView to resolve PIN to IP info"""
    discovery = NetworkDiscovery()
    ip = discovery.resolve_pin(pin)
    if ip:
        return {'ip': ip, 'hostname': _("Host"), 'port': 47989}
    return None
