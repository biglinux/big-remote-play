#!/usr/bin/env python3
"""
Performance chart widget using Cairo drawing.
Replaces the old text-based monitor with a modern visual chart.
"""

from __future__ import annotations
from collections import deque
from dataclasses import dataclass
import time
import random
import threading
import subprocess
import re
from pathlib import Path
import queue
import os
import signal
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, GLib, Adw, Gdk
from utils.i18n import _

try:
    import cairo
except ImportError:
    cairo = None

CHART_MAX_HISTORY = 60

from utils.icons import create_icon_widget, set_icon

@dataclass
class PerformanceDataPoint:
    """Single data point for performance chart."""
    latency: float
    fps: float
    bandwidth: float
    device_latencies: dict
    latency_text: str
    fps_text: str
    bandwidth_text: str
    users_count: int = 0

class PerformanceChartWidget(Gtk.DrawingArea):
    """
    Modern chart widget for network/video performance.
    """

    def __init__(self) -> None:
        super().__init__()
        self._history: deque[PerformanceDataPoint] = deque(maxlen=CHART_MAX_HISTORY)
        self.max_latency = 100.0
        self.max_fps = 120.0
        self.max_bandwidth = 50.0
        self._cur_latency_text = "--"
        self._cur_fps_text = "--"
        self._cur_bw_text = "--"
        self.device_colors = {}
        self.color_palette = [
            (1.0, 0.4, 0.0, 1.0),
            (1.0, 0.0, 0.4, 1.0),
            (0.8, 0.0, 1.0, 1.0),
            (0.0, 1.0, 0.8, 1.0),
            (1.0, 0.8, 0.0, 1.0),
            (0.5, 1.0, 0.0, 1.0),
        ]
        self._hover_x: float | None = None
        self._hover_index: int | None = None
        self.set_size_request(300, 160)
        self.set_vexpand(False)
        self.set_hexpand(True)
        self.set_draw_func(self._on_draw)
        motion_controller = Gtk.EventControllerMotion()
        motion_controller.connect("motion", self._on_motion)
        motion_controller.connect("leave", self._on_leave)
        self.add_controller(motion_controller)
        
    def _get_device_color(self, name):
        # Remove sufixos de estado para manter a cor consistente
        base_name = name.split('(')[0].strip()
        if base_name not in self.device_colors:
            idx = len(self.device_colors) % len(self.color_palette)
            self.device_colors[base_name] = self.color_palette[idx]
        return self.device_colors[base_name]
        
    def add_data_point(self, latency: float, fps: float, bandwidth: float, users: int = 0, device_latencies: dict = None):
        if latency > self.max_latency: self.max_latency = latency * 1.2
        if fps > self.max_fps: self.max_fps = fps * 1.2
        if bandwidth > self.max_bandwidth: self.max_bandwidth = bandwidth * 1.2
        if device_latencies:
            for lat in device_latencies.values():
                if lat > self.max_latency: self.max_latency = lat * 1.2
        point = PerformanceDataPoint(
            latency=latency,
            fps=fps,
            bandwidth=bandwidth,
            device_latencies=device_latencies or {},
            latency_text=f"{latency:.0f} ms",
            fps_text=f"{fps:.0f} FPS",
            bandwidth_text=f"{bandwidth:.1f} Mbps",
            users_count=users
        )
        self._history.append(point)
        self._cur_latency_text = point.latency_text
        self._cur_fps_text = point.fps_text
        self._cur_bw_text = point.bandwidth_text
        self.queue_draw()

    def _on_motion(self, controller, x, y):
        self._hover_x = x
        self._update_hover_index()
        self.queue_draw()

    def _on_leave(self, controller):
        self._hover_x = None
        self._hover_index = None
        self.queue_draw()

    def _update_hover_index(self) -> None:
        if self._hover_x is None or not self._history:
            self._hover_index = None
            return
        width = self.get_width()
        margin_left = 40
        margin_right = 10
        chart_width = width - margin_left - margin_right
        if chart_width <= 0:
            self._hover_index = None
            return
        if self._hover_x < margin_left or self._hover_x > width - margin_right:
            self._hover_index = None
            return
        num_points = len(self._history)
        x_step = chart_width / max(CHART_MAX_HISTORY - 1, 1)
        start_x = margin_left + chart_width - (num_points - 1) * x_step
        relative_x = self._hover_x - start_x
        index = round(relative_x / x_step) if x_step > 0 else 0
        index = max(0, min(num_points - 1, index))
        self._hover_index = index

    def _on_draw(self, area, cr, width, height):
        try:
            cr.set_source_rgba(0.12, 0.12, 0.12, 1.0)
            cr.rectangle(0, 0, width, height)
            cr.fill()
            margin_left = 40
            margin_right = 10
            margin_top = 20
            margin_bottom = 30
            chart_width = width - margin_left - margin_right
            chart_height = height - margin_top - margin_bottom
            if chart_width <= 0 or chart_height <= 0: return
            cr.set_source_rgba(0.3, 0.3, 0.3, 0.3)
            cr.set_line_width(1)
            for i in range(4):
                y = margin_top + (chart_height * i / 3)
                cr.move_to(margin_left, y)
                cr.line_to(margin_left + chart_width, y)
                cr.stroke()
            if not self._history:
                cr.set_source_rgba(0.5, 0.5, 0.5, 1)
                cr.set_font_size(14)
                text = _("Waiting for data...")
                extents = cr.text_extents(text)
                cr.move_to(margin_left + (chart_width - extents.width)/2, margin_top + chart_height/2)
                cr.show_text(text)
                return
            lat_vals = [p.latency for p in self._history]
            fps_vals = [p.fps for p in self._history]
            bw_vals = [p.bandwidth for p in self._history]
            lat_norm = [v / max(1, self.max_latency) for v in lat_vals]
            fps_norm = [v / max(1, self.max_fps) for v in fps_vals]
            bw_norm = [v / max(1, self.max_bandwidth) for v in bw_vals]
            self._draw_line(cr, chart_width, chart_height, margin_left, margin_top, bw_norm, (0.0, 0.6, 1.0, 1.0), fill=True)
            self._draw_line(cr, chart_width, chart_height, margin_left, margin_top, fps_norm, (0.0, 0.8, 0.2, 1.0), fill=False)
            active_devices = set()
            for p in self._history:
                active_devices.update(p.device_latencies.keys())
            if not active_devices:
                self._draw_line(cr, chart_width, chart_height, margin_left, margin_top, lat_norm, (1.0, 0.4, 0.0, 1.0))
            else:
                for dev_name in active_devices:
                    dev_vals = []
                    for p in self._history:
                        val = p.device_latencies.get(dev_name, 0) 
                        dev_vals.append(val / max(1, self.max_latency))
                    color = self._get_device_color(dev_name)
                    self._draw_line(cr, chart_width, chart_height, margin_left, margin_top, dev_vals, color, fill=False)
            self._draw_legend(cr, width, height, margin_left, active_devices)
            if self._hover_index is not None and 0 <= self._hover_index < len(self._history):
                self._draw_tooltip(cr, width, height, margin_left, margin_top, chart_width, chart_height)
            if self._history:
                last_point = self._history[-1]
                if last_point.users_count > 0:
                    text = _("{} Active Devices").format(last_point.users_count)
                    cr.set_font_size(14)
                    ext = cr.text_extents(text)
                    box_x = width - ext.width - 25
                    box_y = margin_top + 5
                    cr.set_source_rgba(0.2, 0.2, 0.2, 0.8)
                    cr.rectangle(box_x - 5, box_y - 12, ext.width + 10, ext.height + 15)
                    cr.fill()
                    cr.set_source_rgba(1, 1, 1, 1)
                    cr.move_to(box_x, box_y + ext.height)
                    cr.show_text(text)
        except Exception:
            pass

    def _draw_line(self, cr, w, h, mx, my, vals, color, fill=False):
        if not vals: return
        cr.set_source_rgba(*color)
        cr.set_line_width(2)
        x_step = w / max(CHART_MAX_HISTORY - 1, 1)
        sx = mx + w - (len(vals) - 1) * x_step
        for i, v in enumerate(vals): 
            if i == 0:
                cr.move_to(sx + i * x_step, my + h * (1 - v))
            else:
                cr.line_to(sx + i * x_step, my + h * (1 - v))
        cr.stroke()
        if fill:
            cr.set_source_rgba(color[0], color[1], color[2], 0.15)
            for i, v in enumerate(vals): 
                if i == 0:
                    cr.move_to(sx + i * x_step, my + h * (1 - v))
                else:
                    cr.line_to(sx + i * x_step, my + h * (1 - v))
            cr.line_to(sx + (len(vals) - 1) * x_step, my + h)
            cr.line_to(sx, my + h)
            cr.close_path()
            cr.fill()

    def _draw_legend(self, cr, w, h, margin_left, active_devices=None):
        legend_y = h - 10
        def draw_item(label, val_text, color, x_offset):
            cr.set_source_rgba(*color)
            cr.arc(margin_left + x_offset, legend_y - 4, 4, 0, 2*3.14159)
            cr.fill()
            cr.set_source_rgba(0.9, 0.9, 0.9, 1)
            cr.set_font_size(11)
            cr.move_to(margin_left + x_offset + 10, legend_y)
            # Limpar nome para legenda
            clean_label = label.split('(')[0].strip()
            text = f"{clean_label}: {val_text}"
            cr.show_text(text)
            return cr.text_extents(text).width + 30
        offset = 0
        if not active_devices:
            offset += draw_item(_("Latency"), self._cur_latency_text, (1.0, 0.4, 0.0, 1.0), offset)
        else:
            last_point = self._history[-1] if self._history else None
            for dev in active_devices:
                val = last_point.device_latencies.get(dev, 0) if last_point else 0
                color = self._get_device_color(dev)
                offset += draw_item(dev, f"{val:.0f}ms", color, offset)
        offset += draw_item("FPS", self._cur_fps_text, (0.0, 0.8, 0.2, 1.0), offset)
        if offset > w - 100: 
            legend_y -= 15
            offset = 0
        draw_item("BW", self._cur_bw_text, (0.0, 0.6, 1.0, 1.0), offset)

    def _draw_tooltip(self, cr, w, h, mx, my, cw, ch):
        point = list(self._history)[self._hover_index]
        num_points = len(self._history)
        x_step = cw / max(CHART_MAX_HISTORY - 1, 1)
        start_x = mx + cw - (num_points - 1) * x_step
        hover_x = start_x + self._hover_index * x_step
        cr.set_source_rgba(1, 1, 1, 0.4)
        cr.set_line_width(1)
        cr.move_to(hover_x, my)
        cr.line_to(hover_x, my + ch)
        cr.stroke()
        lines = []
        if point.device_latencies:
            for dev, lat in point.device_latencies.items():
                lines.append(f"{dev}: {lat:.0f} ms")
        else:
            lines.append(f"Lat: {point.latency_text}")
        lines.append(f"FPS: {point.fps_text}")
        lines.append(f"BW: {point.bandwidth_text}")
        box_width = 130
        box_height = 20 + (len(lines) * 14)
        tooltip_x = min(w - box_width - 10, max(10, hover_x + 10))
        tooltip_y = my + 10
        cr.set_source_rgba(0.1, 0.1, 0.1, 0.95)
        cr.rectangle(tooltip_x, tooltip_y, box_width, box_height)
        cr.fill()
        cr.set_source_rgba(1, 1, 1, 1)
        cr.set_font_size(10)
        y_off = 15
        for line in lines:
            cr.move_to(tooltip_x + 8, tooltip_y + y_off)
            cr.show_text(line)
            y_off += 14

class PerformanceMonitor(Gtk.Box):
    """
    Wrapper for performance chart.
    Replaces old text box.
    """
    
    def __init__(self, sunshine=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.sunshine = sunshine
        self.add_css_class('card')
        self.set_margin_top(12)
        self.set_margin_bottom(12)
        self.set_margin_start(12)
        self.set_margin_end(12)
        self._header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self._header.set_margin_start(12)
        self._header.set_margin_end(12)
        self._header.set_margin_top(8)
        self._header.set_margin_bottom(4)
        self._title_label = Gtk.Label(label=_("Real-time Monitoring"))
        self._title_label.add_css_class("heading")
        self._title_label.set_halign(Gtk.Align.START)
        self._title_label.set_hexpand(True)
        self._header.append(self._title_label)
        self._status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._status_icon = create_icon_widget("network-idle-symbolic", size=16)
        self._status_label = Gtk.Label(label=_("Disconnected"))
        self._status_label.add_css_class("dim-label")
        self._status_box.append(self._status_icon)
        self._status_box.append(self._status_label)
        self._header.append(self._status_box)
        self.append(self._header)
        self._details_frame = Gtk.Frame()
        self._details_frame.set_margin_top(8)
        self._details_frame.set_margin_bottom(8)
        self._details_frame.set_margin_start(12)
        self._details_frame.set_margin_end(12)
        self._details_frame.set_visible(False)
        self._details_list = Gtk.ListBox()
        self._details_list.add_css_class('boxed-list')
        self._details_frame.set_child(self._details_list)
        self.append(self._details_frame)
        self.chart = PerformanceChartWidget()
        self.append(self.chart)
        
        self.update_timer_active = False
        self._last_fps = 60.0
        self._last_bandwidth = 10.0
        
        # Cache para persistência de dispositivos
        # Key: IP, Value: {'name': str, 'last_seen': float, 'last_latency': float}
        self._known_devices = {} 
        
        self._data_queue = queue.Queue()
        self._worker_thread = None
        self._worker_running = False
        self._worker_event = threading.Event()

    def start_monitoring(self):
        if self.update_timer_active: return
        self.update_timer_active = True
        GLib.timeout_add(100, self._process_data_queue)
        self._start_worker_thread()
        self.update_stats(0, 0, 0, [])

    def stop_monitoring(self): 
        if not self.update_timer_active: return
        self.update_timer_active = False
        self._stop_worker_thread()

    def _start_worker_thread(self):
        if self._worker_running: return
        self._worker_running = True
        self._worker_event.clear()
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            name="PerformanceMonitor-Worker",
            daemon=True
        )
        self._worker_thread.start()

    def _stop_worker_thread(self):
        self._worker_running = False
        self._worker_event.set()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=2.0)

    def _worker_loop(self):
        time.sleep(1)
        while self._worker_running:
            try:
                if not self._worker_running: break
                self._fetch_and_process_data()
                for _ in range(10): # ~1 segundo de pausa
                    if not self._worker_running or self._worker_event.wait(timeout=0.1):
                        break
            except Exception:
                time.sleep(2)

    def _process_data_queue(self):
        if not self.update_timer_active: return False
        try:
            processed_count = 0
            while not self._data_queue.empty() and processed_count < 10:
                try:
                    latency, fps, bandwidth, sessions, device_latencies = self._data_queue.get_nowait()
                    self.update_stats(latency, fps, bandwidth, sessions, device_latencies)
                    processed_count += 1
                except queue.Empty:
                    break
        except Exception:
            pass
        return True

    def _get_auth(self):
        try:
            paths = [
                Path.home() / '.config' / 'big-remoteplay' / 'sunshine' / 'sunshine.conf',
                Path.home() / '.config' / 'sunshine' / 'sunshine.conf',
                Path('/etc') / 'sunshine' / 'sunshine.conf'
            ]
            for p in paths:
                if p.exists():
                    user, pw = "", ""
                    with open(p, 'r') as f:
                        content = f.read()
                        user_match = re.search(r'^sunshine_user\s*=\s*(.+)', content, re.MULTILINE)
                        pw_match = re.search(r'^sunshine_password\s*=\s*(.+)', content, re.MULTILINE)
                        if user_match: user = user_match.group(1).strip()
                        if pw_match: pw = pw_match.group(1).strip()
                    if user and pw: return (user, pw)
        except Exception:
            pass
        return None

    def _ping_host(self, ip):
        if not ip or ip in ['', 'Unknown IP', '0.0.0.0', '127.0.0.1']: return 0.0
        try:
            import platform
            system = platform.system()
            # FORÇAR LOCALE C para garantir ponto decimal e mensagem em inglês
            env = os.environ.copy()
            env["LC_ALL"] = "C"
            
            if system == "Linux": cmd = ["ping", "-c", "1", "-W", "1", "-n", ip]
            elif system == "Darwin": cmd = ["ping", "-c", "1", "-t", "1", "-n", ip]
            elif system == "Windows": cmd = ["ping", "-n", "1", "-w", "1000", ip]
            else: cmd = ["ping", "-c", "1", "-n", ip]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=1.5, env=env)
            
            if result.returncode == 0:
                # Regex robusto para tempo=1.23, time=1.23, ttl... time=1.23
                match = re.search(r'(?:time|tempo|ttl)[=<]([\d\.,]+)\s*ms', result.stdout, re.IGNORECASE)
                if match:
                    val_str = match.group(1).replace(',', '.')
                    return float(val_str)
                # Fallback simples
                match_fallback = re.search(r'([\d\.,]+)\s*ms', result.stdout)
                if match_fallback:
                    val_str = match_fallback.group(1).replace(',', '.')
                    return float(val_str)
            return 0.0
        except Exception:
            return 0.0

    def _detect_sessions_via_ss(self):
        """Retorna um Dicionário {ip: dados} para facilitar busca"""
        found_sessions = {}
        try:
            sunshine_ports = ['47984', '47989', '48010', '47998', '47999', '48000', '48002', '47990', '48001']
            cmd = ["ss", "-tun", "-a"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if result.returncode != 0: return {}
            
            for line in result.stdout.splitlines():
                if 'ESTAB' in line or 'UNCONN' in line:
                    for port in sunshine_ports:
                        if f":{port}" in line:
                            parts = line.split()
                            if len(parts) >= 5:
                                last_part = parts[-1]
                                # Regex mais permissivo para pegar IP
                                ip_match = re.search(r'(\d+\.\d+\.\d+\.\d+)', last_part)
                                if ip_match:
                                    ip = ip_match.group(1)
                                    if ip.startswith('127.') or ip.startswith('0.') or ip == 'localhost': continue
                                    
                                    if ip not in found_sessions:
                                        found_sessions[ip] = {'ip': ip, 'name': _('Guest'), 'latency': 0, 'fps': 60}
                            break
        except Exception:
            pass
        return found_sessions

    def _fetch_and_process_data(self):
        try:
            auth = self._get_auth()
            api_stats = {}
            api_sessions_list = []
            
            # 1. Tentar pegar dados oficiais da API
            if self.sunshine:
                try:
                    api_stats = self.sunshine.get_performance_stats(auth=auth)
                    api_sessions_list = self.sunshine.get_active_sessions(auth=auth)
                except Exception:
                    pass

            def safe_float(v):
                try: return float(v)
                except: return 0.0
                
            # Métricas Globais
            latency_avg = safe_float(api_stats.get('average_latency', 0))
            fps = safe_float(api_stats.get('fps', 0))
            bandwidth = safe_float(api_stats.get('bitrate', 0)) / 1000.0

            # 2. Pegar dados do SS (Sistema Operacional)
            ss_sessions_dict = self._detect_sessions_via_ss()

            # 3. Mesclar API com SS para garantir IPs
            # Normalizar lista da API
            normalized_api_sessions = []
            if api_sessions_list:
                for s in api_sessions_list:
                    if not isinstance(s, dict): continue
                    # Normalizar chaves
                    s_ip = s.get('ip') or s.get('clientAddress') or ''
                    s_name = s.get('name') or s.get('clientName') or _('Guest')
                    
                    # Se API não tem IP, tenta achar no SS
                    if not s_ip and ss_sessions_dict:
                        # Pega o primeiro IP disponível do SS como "chute" se só tiver 1 convidado
                        if len(ss_sessions_dict) == 1:
                            s_ip = list(ss_sessions_dict.keys())[0]
                    
                    normalized_api_sessions.append({'ip': s_ip, 'name': s_name, 'source': 'api'})

            # Adicionar sessões do SS que não estão na API
            current_cycle_ips = set()
            
            # Adicionar da API
            for s in normalized_api_sessions:
                if s['ip']: current_cycle_ips.add(s['ip'])
            
            # Adicionar do SS (se não estiver na API)
            for ip, data in ss_sessions_dict.items():
                if ip not in current_cycle_ips:
                    normalized_api_sessions.append({'ip': ip, 'name': _('Guest'), 'source': 'ss'})
                    current_cycle_ips.add(ip)

            # 4. ATUALIZAR LISTA DE DISPOSITIVOS CONHECIDOS (Persistência)
            # Se um IP apareceu agora, atualizamos o timestamp.
            # Se não apareceu agora, mantemos ele na lista se ele responder ao ping.
            
            now = time.time()
            
            # Inserir novos ou atualizar existentes detectados agora
            for s in normalized_api_sessions:
                ip = s['ip']
                name = s['name']
                if not ip: continue
                
                # Se já conhecemos, preservar nome se for "Guest" agora
                if ip in self._known_devices:
                    if name == _('Guest') and self._known_devices[ip]['name'] != _('Guest'):
                        name = self._known_devices[ip]['name']
                
                self._known_devices[ip] = {
                    'ip': ip,
                    'name': name,
                    'last_seen': now,
                    'status': 'active'
                }

            # 5. PING E LIMPEZA
            # Vamos iterar sobre TODOS os dispositivos conhecidos, não só os ativos
            final_display_list = []
            device_latencies = {}
            
            active_sessions_count = 0
            
            ips_to_remove = []
            
            for ip, data in self._known_devices.items():
                # Verificar se está "ativo" neste ciclo (veio da API ou SS)
                is_active_cycle = ip in current_cycle_ips
                
                # SEMPRE PINGAR para ter dados no gráfico
                # Isso resolve o problema de dados faltando
                lat = self._ping_host(ip)
                
                # Lógica de Persistência:
                # Se pingou > 0: Mantém na lista como 'Online'
                # Se pingou 0:
                #    Se estava ativo no ciclo (API disse que ta lá), mantém (pode ser firewall bloqueando ping)
                #    Se NÃO estava ativo no ciclo, marca para remoção (timeout)
                
                if lat > 0:
                    data['last_latency'] = lat
                    data['last_seen'] = now # Renovamos "visto" se ping responde
                else:
                    # Se falhou ping, usa ultimo conhecido ou 0
                    lat = data.get('last_latency', 0)
                
                # Definir nome de exibição
                display_name = data['name']
                if ip not in display_name:
                    display_name = f"{display_name} ({ip})"
                
                # Adicionar sufixo se estiver apenas em "modo ping" (sem stream ativo)
                if not is_active_cycle and lat > 0:
                    # Opcional: Indicar que está idle, mas o usuário pediu PERPÉTUO
                    pass 
                
                # Se não temos sinal de vida (nem API, nem SS, nem Ping) por X tempo, remover
                if not is_active_cycle and lat == 0 and (now - data['last_seen'] > 30): # 30 segundos tolerância
                    ips_to_remove.append(ip)
                    continue

                # Preparar objeto para a UI
                session_obj = {
                    'ip': ip,
                    'name': display_name,
                    'latency': lat
                }
                
                if is_active_cycle:
                    active_sessions_count += 1
                
                final_display_list.append(session_obj)
                
                # Adicionar ao gráfico se tiver latência
                if lat > 0:
                    device_latencies[display_name] = lat

            # Limpar antigos
            for ip in ips_to_remove:
                del self._known_devices[ip]

            # Calcular médias para linha geral
            if not latency_avg and device_latencies:
                latency_avg = sum(device_latencies.values()) / len(device_latencies)

            # Manter FPS/BW estáveis
            if fps == 0: fps = self._last_fps if self._last_fps > 0 else 60.0
            else: self._last_fps = fps
            
            if bandwidth == 0: bandwidth = self._last_bandwidth if self._last_bandwidth > 0 else 1.0
            else: self._last_bandwidth = bandwidth

            # Enviar para UI
            self._data_queue.put((latency_avg, fps, bandwidth, final_display_list, device_latencies))
            
        except Exception:
            pass

    def update_stats(self, latency, fps, bandwidth, sessions=None, device_latencies=None):
        try:
            if not self.update_timer_active: return
            sessions, device_latencies = sessions or [], device_latencies or {}
            
            # O gráfico recebe device_latencies, que contém TODOS que responderam ao ping
            self.chart.add_data_point(latency, fps, bandwidth, users=len(sessions), device_latencies=device_latencies)
            
            if len(sessions) > 0:
                self.set_connection_status("Sunshine", _("{} devices monitoring").format(len(sessions)), True)
                self._details_frame.set_visible(True)
            else:
                self.set_connection_status("Sunshine", _("Active - No devices"), True)
                self._details_frame.set_visible(False)
            
            self._update_guest_list(sessions)
        except Exception:
            pass

    def _update_guest_list(self, sessions):
        while child := self._details_list.get_first_child():
            self._details_list.remove(child)
        for s in sessions:
            row = Adw.ActionRow()
            full_name = s.get('name', _('Guest'))
            ip = s.get('ip', 'Unknown IP')
            latency = s.get('latency', 0)
            
            # Separar Nome e IP para visual mais limpo
            if '(' in full_name:
                name_part = full_name.split('(')[0].strip()
            else:
                name_part = full_name
                
            row.set_title(name_part)
            row.set_subtitle(f"IP: {ip}")
            
            ping_lbl = Gtk.Label(label=f"{latency:.0f} ms")
            if latency <= 0:
                ping_lbl.set_label("-- ms")
                ping_lbl.add_css_class('error')
            elif latency < 15: ping_lbl.add_css_class('success')
            elif latency < 50: ping_lbl.add_css_class('warning')
            else: ping_lbl.add_css_class('error')
            
            if hasattr(self.chart, '_get_device_color'):
                try:
                    # Usa o nome completo para garantir a mesma cor do gráfico
                    color = self.chart._get_device_color(full_name)
                    da = Gtk.DrawingArea()
                    da.set_content_width(24)
                    da.set_content_height(24)
                    da.set_valign(Gtk.Align.CENTER)
                    def draw_indicator(area, cr, width, height, color=color):
                        cr.set_source_rgba(*color)
                        cr.arc(width/2, height/2, 5, 0, 2 * 3.14159)
                        cr.fill()
                    da.set_draw_func(draw_indicator)
                    row.add_prefix(da)
                except: pass
            
            row.add_suffix(ping_lbl)
            self._details_list.append(row)

    def set_connection_status(self, name, status, conn=True):
        if conn: self._title_label.set_label(_("Connected to {}").format(name))
        else: self._title_label.set_label(_("Real-time Monitoring"))
        self._status_label.set_label(status)
        if conn:
            set_icon(self._status_icon, "network-transmit-receive-symbolic")
            self._status_icon.add_css_class("success")
            self._status_icon.remove_css_class("warning")
        else:
            set_icon(self._status_icon, "network-idle-symbolic")
            self._status_icon.remove_css_class("success")