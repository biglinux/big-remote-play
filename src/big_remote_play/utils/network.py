"""
Network host discovery
"""

import socket
import subprocess
from typing import List, Dict
from big_remote_play.utils.i18n import _

from big_remote_play.utils.logger import Logger
from big_remote_play.integration_contracts import BRP_DISCOVERY_CODE_LENGTH

# Virtual/container interface prefixes that pollute discovery: a host running
# Docker (or VPN/bridges) exposes its Sunshine service over every one of these
# link-local interfaces, producing a duplicate entry per veth. Real LAN peers
# are reachable over physical interfaces, so these are dropped from discovery.
_VIRTUAL_IFACE_PREFIXES = (
    "veth",
    "docker",
    "br-",
    "virbr",
    "vnet",
    "vmnet",
    "lo",
)


def _is_virtual_iface(iface: str) -> bool:
    """True for container/bridge/loopback interfaces that flood discovery."""
    name = iface.strip().lower()
    return name.startswith(_VIRTUAL_IFACE_PREFIXES)


class NetworkDiscovery:
    """Sunshine host discovery on network"""

    def __init__(self):
        self.hosts = []
        self.logger = Logger()

    def discover_hosts(self, callback=None, *, allow_scan=True):
        import threading

        def run():
            hosts = []
            try:
                res = subprocess.run(["avahi-browse", "-t", "-r", "-p", "_nvstream._tcp"], capture_output=True, text=True, timeout=5)
                if res.returncode == 0 and res.stdout:
                    hosts = self.parse_avahi_output(res.stdout)
                if not hosts and allow_scan:
                    hosts = self.manual_scan()
            except Exception:
                hosts = self.manual_scan() if allow_scan else []
            hosts = self.drop_this_machine(hosts)
            if callback:
                from gi.repository import GLib  # type: ignore

                GLib.idle_add(callback, hosts)

        threading.Thread(target=run, daemon=True).start()

    def local_addresses(self) -> set:
        """Every address this machine answers on, plus loopback."""
        addresses = {"127.0.0.1", "::1"}
        try:
            res = subprocess.run(["ip", "-o", "addr", "show"], capture_output=True, text=True, timeout=3)
            for line in res.stdout.splitlines():
                parts = line.split()
                if len(parts) > 3 and parts[2] in ("inet", "inet6"):
                    addresses.add(parts[3].split("/")[0].split("%")[0].lower())
        except (OSError, subprocess.SubprocessError):
            pass
        return addresses

    def local_names(self) -> set:
        """Hostnames that resolve to this machine, as Avahi spells them."""
        names = set()
        try:
            hostname = socket.gethostname()
        except OSError:
            return names
        for name in (hostname, f"{hostname}.local", socket.getfqdn()):
            if name:
                names.add(name.lower().rstrip("."))
        return names

    def drop_this_machine(self, hosts: List[Dict]) -> List[Dict]:
        """Remove the local PC: streaming to itself is never the intent.

        Sunshine announces itself over mDNS and answers on loopback, so the PC
        running the server showed up as a connection target on its own screen.
        """
        addresses = self.local_addresses()
        names = self.local_names()
        remaining = []
        for host in hosts:
            ip = str(host.get("ip", "")).split("%")[0].strip("[]").lower()
            hostname = str(host.get("hostname", "")).lower().rstrip(".")
            if ip in addresses or (hostname and hostname in names):
                continue
            remaining.append(host)
        return remaining

    def parse_avahi_output(self, output: str) -> List[Dict]:
        """
        One computer per service endpoint, with alternative addresses retained.
        """
        host_map: Dict[tuple[str, str, int], dict] = {}

        for line in output.split("\n"):
            p = line.split(";")
            if len(p) >= 9 and p[0] == "=":
                service_name = p[3]
                hostname = p[6]
                ip = p[7]
                interface = p[1]
                try:
                    port = int(p[8])
                    if not 1 <= port <= 65535:
                        continue
                except ValueError:
                    continue

                # Skip container/bridge interfaces: the same host is announced
                # over every veth/docker iface, otherwise flooding the list.
                if _is_virtual_iface(interface):
                    continue

                # Names are not globally unique; keep distinct host/port pairs.
                key = (service_name, hostname, port)
                if key not in host_map:
                    host_map[key] = {"name": service_name, "hostname": hostname, "port": port, "status": "online", "ips": []}

                # Classify IP
                ip_type = "ipv4"
                if ":" in ip:
                    if ip.startswith("fe80"):
                        ip_type = "ipv6_link_local"
                        # Fix scope ID
                        if "%" not in ip:
                            ip = f"{ip}%{interface}"
                    else:
                        ip_type = "ipv6_global"

                # Add formatted IP to list
                # User reported Moonlight CLI on Linux prefers raw IP without brackets
                formatted_ip = ip
                address = {"ip": formatted_ip, "type": ip_type, "raw": ip}
                if address not in host_map[key]["ips"]:
                    host_map[key]["ips"].append(address)

        final_hosts = []
        for data in host_map.values():
            type_rank = {"ipv4": 0, "ipv6_global": 1, "ipv6_link_local": 2}
            ordered = sorted(data["ips"], key=lambda i: type_rank.get(i["type"], 3))
            if not ordered:
                continue
            final_hosts.append(
                {
                    "name": data["name"],
                    "ip": ordered[0]["ip"],
                    "port": data["port"],
                    "status": "online",
                    "hostname": data["hostname"],
                    "addresses": [entry["ip"] for entry in ordered],
                }
            )
        return final_hosts

    def manual_scan(self) -> List[Dict]:
        from concurrent.futures import ThreadPoolExecutor

        hosts = []
        local_ip = self.get_local_ip()
        targets = []

        # IPv4 scan
        if local_ip and "." in local_ip:
            subnet = ".".join(local_ip.split(".")[:-1])
            for i in range(1, 255):
                targets.append(f"{subnet}.{i}")

        # IPv6 Radical Scan: Check neighbor cache and active interfaces
        try:
            # 1. Check neighbor cache
            res = subprocess.run(["ip", "-6", "neigh", "show"], capture_output=True, text=True, timeout=2)
            if res.returncode == 0:
                for line in res.stdout.splitlines():
                    parts = line.split()
                    if len(parts) >= 3 and ":" in parts[0]:
                        ip = parts[0]
                        if ip.startswith("fe80"):
                            try:
                                dev_idx = parts.index("dev")
                                if dev_idx + 1 < len(parts):
                                    dev = parts[dev_idx + 1]
                                    if not _is_virtual_iface(dev):
                                        targets.append(f"{ip}%{dev}")
                            except Exception:
                                pass
                        else:
                            targets.append(ip)

            # The neighbour table is used as-is: an `ip -6 neigh flush` needs root
            # (silently fails otherwise) and a loopback multicast ping populates
            # nothing useful, so both were removed.
        except Exception:
            pass

        def check(ip):
            if self.check_sunshine_port(ip):
                # User reported Moonlight CLI on Linux prefers raw IP without brackets
                return {"name": _("Host ({})").format(ip), "ip": ip, "port": 47989, "status": "online"}
            return None

        with ThreadPoolExecutor(max_workers=32) as ex:
            for r in ex.map(check, targets):
                if r:
                    hosts.append(r)
        return hosts

    def check_sunshine_port(self, ip: str, port: int = 47989, timeout: float = 0.5) -> bool:
        try:
            with socket.create_connection((ip, port), timeout=timeout):
                return True
        except Exception:
            return False

    def get_local_ip(self) -> str:
        # Try IPv4
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                return s.getsockname()[0]
        except Exception:
            pass
        # Try IPv6
        try:
            with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as s:
                s.connect(("2001:4860:4860::8888", 80))
                return s.getsockname()[0]
        except Exception:
            pass
        return ""

    def resolve_pin(self, pin: str, timeout: int = 3) -> str:
        if not pin or len(pin) != BRP_DISCOVERY_CODE_LENGTH:
            return ""
        import threading

        results = {"v4": None, "v6": None}

        def try_v4():
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                    s.settimeout(timeout)
                    s.sendto(f"WHO_HAS_PIN {pin}".encode(), ("<broadcast>", 48011))
                    data, addr = s.recvfrom(1024)
                    if data.decode().startswith("I_HAVE_PIN"):
                        results["v4"] = addr[0]
            except Exception:
                pass

        def try_v6():
            try:
                # ff02::1 is all-nodes link-local multicast
                with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as s:
                    s.settimeout(timeout)
                    s.sendto(f"WHO_HAS_PIN {pin}".encode(), ("ff02::1", 48011))
                    data, addr = s.recvfrom(1024)
                    if data.decode().startswith("I_HAVE_PIN"):
                        results["v6"] = addr[0]
            except Exception:
                pass

        t1 = threading.Thread(target=try_v4)
        t2 = threading.Thread(target=try_v6)
        t1.start()
        t2.start()
        t1.join(timeout)
        t2.join(timeout)

        return results["v4"] or results["v6"] or ""

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
                        try:
                            s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                        except Exception:
                            pass
                        s.bind(("::", 48011))
                    else:
                        s.bind(("0.0.0.0", 48011))  # nosec B104 (LAN PIN-discovery listener must accept datagrams from any local peer; mirrors the "::" bind above)
                    s.settimeout(1)

                    def listener(sock):
                        while running[0]:
                            try:
                                data, addr = sock.recvfrom(1024)
                                if data.decode().strip() == f"WHO_HAS_PIN {pin}":
                                    sock.sendto(f"I_HAVE_PIN {name}".encode(), addr)
                            except Exception:
                                pass
                        sock.close()

                    threading.Thread(target=listener, args=(s,), daemon=True).start()
                except Exception:
                    pass

        run()
        return lambda: running.__setitem__(0, False)

    def get_global_ipv4(self) -> str:
        """Public IPv4, or "" if it cannot be determined.

        Uses `curl -4` to force the address family (no secret in the request).
        """
        for url in ["ipinfo.io/ip", "checkip.amazonaws.com"]:
            try:
                res = subprocess.run(["curl", "-fsS", "-4", "--connect-timeout", "3", "--max-time", "5", "https://" + url], capture_output=True, text=True, timeout=6)
                if res.returncode == 0 and res.stdout.strip():
                    return res.stdout.strip()
            except Exception:
                pass
        return ""

    def get_global_ipv6(self) -> str:
        """Public IPv6, or "" if it cannot be determined."""
        for url in ["ifconfig.me", "icanhazip.com"]:
            try:
                res = subprocess.run(["curl", "-fsS", "-6", "--connect-timeout", "3", "--max-time", "5", "https://" + url], capture_output=True, text=True, timeout=6)
                if res.returncode == 0 and res.stdout.strip():
                    return res.stdout.strip()
            except Exception:
                pass
        return ""


def resolve_pin_to_ip(pin: str) -> dict | None:
    """Helper for GuestView to resolve PIN to IP info"""
    discovery = NetworkDiscovery()
    ip = discovery.resolve_pin(pin)
    if ip:
        return {"ip": ip, "hostname": _("Host"), "port": 47989}
    return None
