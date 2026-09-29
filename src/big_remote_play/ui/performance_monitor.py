#!/usr/bin/env python3
"""
Performance chart widget using Cairo drawing.
Replaces the old text-based monitor with a modern visual chart.
"""

from __future__ import annotations
from collections import deque
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
import time
import threading
import subprocess
import queue
import os
from big_remote_play import paths
import gi
import socket

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, GLib, Adw  # type: ignore
from big_remote_play.utils.i18n import _
from big_remote_play.utils.connection_health import (
    SAMPLE_INTERVAL_SECONDS,
    ConnectionInfo,
    Health,
    LatencyWindow,
    Quality,
    Transport,
    ping_once,
    route_to,
    transport_for,
)

CHART_MAX_HISTORY = 60

# Reverse-DNS lookups run here with a per-call timeout so a slow resolver never
# blocks the monitor worker, and never via socket.setdefaulttimeout (which would
# mutate the timeout for every socket in the process, including the Sunshine API).
_dns_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="brp-revdns")
# How often the worker looks at Sunshine's log and the handshakes (cheap: a
# stat, a small read and one `ss`). Pings run on their own, slower interval.
POLL_SECONDS = 3.0
ROUTE_CACHE_SECONDS = 60.0


from big_remote_play.utils.icons import create_icon_widget, set_icon


@dataclass
class PerformanceDataPoint:
    """Single data point for performance chart."""

    latency: float
    fps: float
    bandwidth: float
    device_latencies: dict[str, float]
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
        self.device_colors: dict[str, tuple[float, float, float, float]] = {}
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
        self.set_size_request(220, 160)
        self.set_focusable(True)
        self.update_property([Gtk.AccessibleProperty.LABEL], [_("Connection history")])
        self.set_vexpand(False)
        self.set_hexpand(True)
        self.set_draw_func(self._on_draw)
        motion_controller = Gtk.EventControllerMotion()
        motion_controller.connect("motion", self._on_motion)
        motion_controller.connect("leave", self._on_leave)
        self.add_controller(motion_controller)

    def _get_device_color(self, name):
        # Strip state suffixes to keep the color consistent
        base_name = name.split("(")[0].strip()
        if base_name not in self.device_colors:
            idx = len(self.device_colors) % len(self.color_palette)
            self.device_colors[base_name] = self.color_palette[idx]
        return self.device_colors[base_name]

    def add_data_point(
        self,
        latency: float,
        fps: float,
        bandwidth: float,
        users: int = 0,
        device_latencies: dict[str, float] | None = None,
        bw_text_override: str | None = None,
    ):
        if latency > self.max_latency:
            self.max_latency = latency * 1.2
        if fps > self.max_fps:
            self.max_fps = fps * 1.2
        if bandwidth > self.max_bandwidth:
            self.max_bandwidth = bandwidth * 1.2
        if device_latencies:
            for lat in device_latencies.values():
                if lat > self.max_latency:
                    self.max_latency = lat * 1.2

        bw_txt = bw_text_override if bw_text_override else f"{bandwidth:.1f} Mbps"

        point = PerformanceDataPoint(
            latency=latency,
            fps=fps,
            bandwidth=bandwidth,
            device_latencies=device_latencies or {},
            latency_text=f"{latency:.0f} ms",
            fps_text=f"{fps:.0f} FPS" if fps > 0 else "—",
            bandwidth_text=bw_txt,
            users_count=users,
        )
        self._history.append(point)
        self._cur_latency_text = point.latency_text
        self._cur_fps_text = point.fps_text
        self._cur_bw_text = point.bandwidth_text
        # The chart text is Cairo-drawn and otherwise invisible to AT-SPI; expose
        # the current values so a screen reader can read them.
        self.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("Latency {}, FPS {} (target), bandwidth {} (target)").format(self._cur_latency_text, self._cur_fps_text, self._cur_bw_text)],
        )
        self.queue_draw()

    def _on_motion(self, _controller, x, y):
        self._hover_x = x
        self._update_hover_index()
        self.queue_draw()

    def _on_leave(self, _controller):
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

    def _on_draw(self, _area, cr, width, height):
        try:
            # Let the native card provide its background in light, dark and
            # high-contrast themes. A hard-coded black rectangle breaks all three.
            cr.set_source_rgba(0, 0, 0, 0)
            margin_left = 40
            margin_right = 10
            margin_top = 20
            margin_bottom = 30
            chart_width = width - margin_left - margin_right
            chart_height = height - margin_top - margin_bottom
            if chart_width <= 0 or chart_height <= 0:
                return
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
                cr.move_to(margin_left + (chart_width - extents.width) / 2, margin_top + chart_height / 2)
                cr.show_text(text)
                return
            lat_vals = [p.latency for p in self._history]
            fps_vals = [p.fps for p in self._history]
            bw_vals = [p.bandwidth for p in self._history]
            lat_norm = [v / max(1, self.max_latency) for v in lat_vals]
            fps_norm = [v / max(1, self.max_fps) for v in fps_vals]
            bw_norm = [v / max(1, self.max_bandwidth) for v in bw_vals]
            self._draw_line(cr, chart_width, chart_height, margin_left, margin_top, bw_norm, (0.0, 0.6, 1.0, 1.0), fill=True)
            if any(v > 0 for v in fps_vals):
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
            # Text metrics live in native labels below the header; assistive
            # technologies and text scaling cannot read Cairo-only legends.
            if self._hover_index is not None and 0 <= self._hover_index < len(self._history):
                self._draw_tooltip(cr, width, height, margin_left, margin_top, chart_width, chart_height)
            if self._history:
                last_point = self._history[-1]
                if last_point.users_count > 0:
                    text = _("Active devices: {}").format(last_point.users_count)
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
        if not vals:
            return
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
            cr.arc(margin_left + x_offset, legend_y - 4, 4, 0, 2 * 3.14159)
            cr.fill()
            cr.set_source_rgba(0.9, 0.9, 0.9, 1)
            cr.set_font_size(11)
            cr.move_to(margin_left + x_offset + 10, legend_y)
            # Clean name for the legend
            clean_label = label.split("(")[0].strip()
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
        # Sunshine exposes no FPS/bandwidth telemetry, so these lines reflect the
        # configured targets, not measurements — label them honestly.
        offset += draw_item(_("FPS (target)"), self._cur_fps_text, (0.0, 0.8, 0.2, 1.0), offset)
        if offset > w - 100:
            legend_y -= 15
            offset = 0
        draw_item(_("BW (target)"), self._cur_bw_text, (0.0, 0.6, 1.0, 1.0), offset)

    def _draw_tooltip(self, cr, w, h, mx, my, cw, ch):
        hover_index = self._hover_index
        if hover_index is None:
            return
        point = list(self._history)[hover_index]
        num_points = len(self._history)
        x_step = cw / max(CHART_MAX_HISTORY - 1, 1)
        start_x = mx + cw - (num_points - 1) * x_step
        hover_x = start_x + hover_index * x_step
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
        self.hostname_cache = {}
        self.add_css_class("card")
        self.add_css_class("brp-monitor")
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
        self._title_label.set_wrap(True)
        self._title_label.set_hexpand(True)
        self._header.append(self._title_label)
        self._status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._status_icon = create_icon_widget("brp-network-idle-symbolic", size=16)
        self._status_label = Gtk.Label(label=_("Disconnected"), wrap=True, xalign=0)
        self._status_label.add_css_class("dim-label")
        self._status_box.append(self._status_icon)
        self._status_box.append(self._status_label)
        self._header.append(self._status_box)
        self.append(self._header)
        metrics = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True, column_spacing=8, row_spacing=8)
        metrics.set_min_children_per_line(1)
        metrics.set_max_children_per_line(3)
        self.metric_values = {}
        for key, title in (("latency", _("Latency")), ("fps", _("FPS (target)")), ("bandwidth", _("BW (target)"))):
            tile = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            tile.add_css_class("brp-metric")
            label = Gtk.Label(label=title, wrap=True, xalign=0)
            label.add_css_class("caption")
            label.add_css_class("dim-label")
            value = Gtk.Label(label="—", xalign=0)
            value.add_css_class("title-3")
            tile.append(label)
            tile.append(value)
            metrics.append(tile)
            self.metric_values[key] = value
        self.append(metrics)
        explanation = Gtk.Label(label=_("FPS and bandwidth are configured targets, not measurements."), wrap=True, xalign=0)
        explanation.add_css_class("caption")
        explanation.add_css_class("dim-label")
        explanation.set_margin_start(12)
        explanation.set_margin_end(12)
        self.append(explanation)
        self._details_frame = Gtk.Frame()
        self._details_frame.set_margin_top(8)
        self._details_frame.set_margin_bottom(8)
        self._details_frame.set_margin_start(12)
        self._details_frame.set_margin_end(12)
        self._details_frame.set_visible(False)
        self._details_list = Gtk.ListBox()
        self._details_list.add_css_class("boxed-list")
        self._details_frame.set_child(self._details_list)
        self.append(self._details_frame)
        self.chart = PerformanceChartWidget()
        history = Adw.ExpanderRow(title=_("Connection history"), use_markup=False)
        history.add_row(self.chart)
        history_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        history_list.add_css_class("boxed-list")
        history_list.append(history)
        self.append(history_list)

        self.update_timer_active = False
        self._target_fps = 0.0
        self._target_bw = 0.0
        self._last_fps = 0.0
        self._last_bandwidth = 10.0

        # Who is measured: Sunshine's live sessions (Share) or the one computer
        # this one streams from (Connect). Never a device that merely answers.
        self._tracker = None
        self._peer: tuple[str, str, float] | None = None
        self._windows: dict[str, LatencyWindow] = {}
        self._routes: dict[str, tuple[Transport, float]] = {}
        self._names: dict[str, str] = {}
        self._tailnet: tuple[Transport, float] | None = None
        self._last_sample = 0.0
        self._listeners: list = []
        self.connections: list[ConnectionInfo] = []

        self._data_queue = queue.Queue()
        self._worker_thread = None
        self._worker_running = False
        self._worker_event = threading.Event()

    def set_target_fps(self, fps):
        """Sets the expected FPS for idle display"""
        try:
            val = float(fps)
            if val >= 0:
                self._target_fps = val
                # Update current view if idle (fps == 0 or default 60)
                if self._last_fps == 60.0 or self._last_fps == 0:
                    self._last_fps = val
        except Exception:
            pass

    def set_target_bandwidth(self, mbps):
        try:
            val = float(mbps)
            self._target_bw = val
            # If idle (default 10), update
            if self._last_bandwidth == 10.0 or self._last_bandwidth == 0:
                self._last_bandwidth = val
        except Exception:
            pass

    def start_monitoring(self):
        if self.update_timer_active:
            return
        self.update_timer_active = True
        GLib.timeout_add(100, self._process_data_queue)
        self._start_worker_thread()
        self.update_connections([])

    def stop_monitoring(self):
        if not self.update_timer_active:
            return
        self.update_timer_active = False
        self._stop_worker_thread()

    def _start_worker_thread(self):
        if self._worker_running:
            return
        self._worker_running = True
        self._worker_event.clear()
        self._worker_thread = threading.Thread(target=self._worker_loop, name="PerformanceMonitor-Worker", daemon=True)
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
                if not self._worker_running:
                    break
                self._fetch_and_process_data()
                for _ in range(int(POLL_SECONDS * 10)):
                    if not self._worker_running or self._worker_event.wait(timeout=0.1):
                        break
            except Exception:
                time.sleep(2)

    def _process_data_queue(self):
        if not self.update_timer_active:
            return False
        try:
            processed_count = 0
            while not self._data_queue.empty() and processed_count < 10:
                try:
                    self.update_connections(self._data_queue.get_nowait())
                    processed_count += 1
                except queue.Empty:
                    break
        except Exception:
            pass
        return True

    def _resolve_hostname(self, ip):
        """Resolves hostname with caching to avoid lag"""
        if not ip or ip in ["0.0.0.0"]:  # nosec B104 (rejects the wildcard address, does not bind)
            return None
        if ip in ["127.0.0.1", "::1", "localhost"]:
            return "Localhost"
        if ip in self.hostname_cache:
            return self.hostname_cache[ip]

        try:
            # Bounded reverse lookup in a helper thread; never mutate the global
            # default socket timeout (that would affect the Sunshine API too).
            future = _dns_pool.submit(socket.gethostbyaddr, ip)
            hostname = future.result(timeout=0.5)[0]
            # Remove domain part if it looks like a local domain
            if ".local" in hostname:
                hostname = hostname.split(".")[0]
            self.hostname_cache[ip] = hostname
            return hostname
        except (FutureTimeoutError, OSError, socket.herror, socket.gaierror):
            # Cache failure too to avoid retrying constantly
            self.hostname_cache[ip] = None
            return None

    def _sunshine_auth(self) -> tuple[str, str] | None:
        """Reads Sunshine admin credentials from the system keyring, or None."""
        from big_remote_play.utils.sunshine_credentials import load_sunshine_credentials

        return load_sunshine_credentials()

    def _prompt_disconnect(self, session_id, ip):
        """Offers a gentle app close vs a forceful IP eviction."""
        dialog = Adw.AlertDialog(
            heading=_("Disconnect Guest"),
            body=_("End the running game gently, or forcefully evict the network address? Ending the game keeps the device paired."),
        )
        root = self.get_root()
        parent = root if isinstance(root, Gtk.Widget) else self
        dialog.add_response("cancel", _("Cancel"))
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        if self.sunshine:
            dialog.add_response("close", _("End Game"))
            dialog.set_response_appearance("close", Adw.ResponseAppearance.SUGGESTED)
        if ip:
            dialog.add_response("evict", _("Evict IP"))
            dialog.set_response_appearance("evict", Adw.ResponseAppearance.DESTRUCTIVE)

        def on_resp(d, r):
            if r == "close":
                self._close_running_app()
            elif r == "evict":
                self._disconnect_session(session_id, ip)

        dialog.connect("response", on_resp)
        dialog.present(parent)

    def _close_running_app(self):
        """Gentle stop via Sunshine API (POST /api/apps/close), no root."""
        if not self.sunshine:
            return
        sunshine = self.sunshine
        self.set_sensitive(False)
        auth = self._sunshine_auth()

        def work():
            ok = sunshine.close_app(auth=auth)
            GLib.idle_add(self._on_disconnect_done, ok)

        threading.Thread(target=work, daemon=True).start()

    def _disconnect_session(self, session_id, ip):
        # We need at least an IP or session_id to try something
        if not ip and not session_id:
            return

        self.set_sensitive(False)

        def do_disconnect():
            success = False

            # System-level socket kill is the real mechanism to drop a live stream.
            # Sunshine exposes no session-termination REST endpoint; unpairing a
            # client would not end an in-progress stream.
            if ip:
                try:
                    script_path = paths.script_path("drop_guest.sh")

                    if os.path.exists(script_path):
                        cmd = ["pkexec", script_path, ip]
                        # No timeout: pkexec blocks on the polkit auth dialog (user-paced)
                        res = subprocess.run(cmd, capture_output=True, text=True)
                        if res.returncode == 0:
                            success = True
                except Exception:
                    pass

            GLib.idle_add(self._on_disconnect_done, success)

        threading.Thread(target=do_disconnect, daemon=True).start()

    def _on_disconnect_done(self, success):
        self.set_sensitive(True)
        if success:
            # Force immediate update
            self._process_data_queue()
        else:
            # Show error (optional, toast would be better but we are inside widget)
            pass

    # ── who is connected ───────────────────────────────────────────────────
    def add_listener(self, callback) -> None:
        """``callback(list[ConnectionInfo])`` on the GTK thread after each update."""
        self._listeners.append(callback)

    def set_peer(self, address: str | None, name: str = "") -> None:
        """Connect: measure the computer this one is streaming from (or stop)."""
        if not address:
            self._peer = None
        elif self._peer is None or self._peer[0] != address:
            self._peer = (address, name or address, time.time())
            self._windows.pop(address, None)
            self._last_sample = 0.0

    def _session_tracker(self):
        if self._tracker is None and self.sunshine is not None:
            from big_remote_play.host.sunshine_sessions import SessionTracker

            sunshine = self.sunshine
            try:
                base_port = int(getattr(sunshine, "api_port", 47990)) - 1
            except (TypeError, ValueError):
                base_port = 47989
            self._tracker = SessionTracker(sunshine.config_dir / "sunshine.log", base_port=base_port, running=sunshine.is_running)
        return self._tracker

    def _targets(self) -> list[tuple[str, str, float | None, object]]:
        if self._peer is not None:
            return [(*self._peer, "")]
        tracker = self._session_tracker()
        if tracker is None:
            return []
        sessions = tracker.poll()
        return [(session.address, self._display_name(session.address), session.started_at, session) for session in sessions]

    @staticmethod
    def _session_details(session, newest: bool) -> tuple[str, tuple[tuple[str, str], ...]]:
        """Technical words and fixable problems for one live Sunshine session.

        The client's request is known only for the newest session (Sunshine
        runs the prep command once per session start).
        """
        from big_remote_play.host.stream_display import read_client_report

        parts = [session.video.summary()] if session.video is not None and session.video.encoder else []
        warnings: list[tuple[str, str]] = []
        report = read_client_report() if newest else None
        if report is not None and report.started_at >= session.started_at - 60:
            parts.append(report.summary())
            if report.scaled:
                warnings.append(("scaled", f"{report.width}x{report.height}|{report.screen_width}x{report.screen_height}"))
            if report.hdr_sent_as_sdr:
                warnings.append(("hdr_as_sdr", report.screen))
        return " · ".join(part for part in parts if part), tuple(warnings)

    def _display_name(self, address: str) -> str:
        if not address:
            return _("Connected device")
        if address not in self._names:
            from big_remote_play.private_network.devices import DevicePreferences

            name = DevicePreferences().display_name(address, "") or self._peer_name(address) or self._resolve_hostname(address) or ""
            self._names[address] = name or _("Device at {address}").format(address=address)
        return self._names[address]

    @staticmethod
    def _peer_name(address: str) -> str:
        """The name a private network gives this address, if any."""
        try:
            from big_remote_play.private_network.service import default_service

            for status in default_service().overview():
                for peer in status.peers:
                    if address in peer.addresses and peer.name:
                        return peer.name
        except Exception:  # a name is a nicety; never break the monitor for it
            return ""
        return ""

    def _transport(self, address: str, now: float) -> Transport:
        cached = self._routes.get(address)
        if cached is not None and now - cached[1] < ROUTE_CACHE_SECONDS:
            return cached[0]
        route = route_to(address)
        tailnet = Transport.TAILSCALE
        if route is not None and route.device.startswith("tailscale"):
            tailnet = self._tailnet_kind(now)
        transport = transport_for(address, route, tailnet=tailnet)
        self._routes[address] = (transport, now)
        return transport

    def _tailnet_kind(self, now: float) -> Transport:
        """Tailscale or a Headscale server: the same interface, told apart once."""
        if self._tailnet is not None and now - self._tailnet[1] < 300:
            return self._tailnet[0]
        kind = Transport.TAILSCALE
        try:
            from big_remote_play.private_network.models import ProviderId
            from big_remote_play.private_network.service import default_service

            if default_service().status(ProviderId.HEADSCALE).connected:
                kind = Transport.HEADSCALE
        except Exception:
            pass
        self._tailnet = (kind, now)
        return kind

    def _fetch_and_process_data(self):
        """Collect the current connections (worker thread; no GTK here)."""
        try:
            now = time.time()
            targets = self._targets()
            sample = now - self._last_sample >= SAMPLE_INTERVAL_SECONDS - 0.5
            if sample:
                self._last_sample = now
            infos: list[ConnectionInfo] = []
            newest = max((target[2] or 0 for target in targets), default=0)
            for address, name, started, session in targets:
                video, warnings = ("", ()) if isinstance(session, str) else self._session_details(session, (started or 0) >= newest)
                health = Health(Quality.MEASURING)
                transport = Transport.UNKNOWN
                if address:
                    window = self._windows.setdefault(address, LatencyWindow())
                    health = window.add(ping_once(address)) if sample else window.health
                    transport = self._transport(address, now)
                infos.append(ConnectionInfo(name, address, transport, health, True, started, video, warnings))
            current = {target[0] for target in targets}
            for address in list(self._windows):
                if address not in current:
                    del self._windows[address]
            self._data_queue.put(infos)
        except Exception:
            pass

    def update_connections(self, infos: list[ConnectionInfo]) -> None:
        """Show the measured connections (GTK thread)."""
        if not self.update_timer_active:
            return
        self.connections = list(infos)
        latencies = {info.device_name: info.health.latency_ms for info in infos if info.health.latency_ms is not None}
        average = sum(latencies.values()) / len(latencies) if latencies else 0.0
        self.metric_values["latency"].set_label(f"{average:.0f} ms" if latencies else "—")
        self.metric_values["fps"].set_label(f"{self._target_fps:.0f} FPS" if self._target_fps > 0 else "—")
        self.metric_values["bandwidth"].set_label(f"{self._target_bw:g} Mbps" if self._target_bw > 0 else _("Unlimited"))
        # Only chart real measurements, never a flat line of targets.
        if latencies:
            self.chart.add_data_point(average, self._target_fps, self._target_bw, users=len(infos), device_latencies=latencies)
        if self._peer is None:
            if len(infos) == 1:
                self.set_connection_status(infos[0].device_name, _("Active Connection"), True)
            elif infos:
                self.set_connection_status("Sunshine", _("Connected devices: {}").format(len(infos)), True)
            else:
                self.set_connection_status("Sunshine", _("Active - No devices"), True)
        self._details_frame.set_visible(bool(infos))
        self._update_guest_list(infos)
        for listener in list(self._listeners):
            listener(list(infos))

    def _update_guest_list(self, infos: list[ConnectionInfo]) -> None:
        from .connection_cards import DeviceConnectionCard

        while child := self._details_list.get_first_child():
            self._details_list.remove(child)
        for info in infos:
            card = DeviceConnectionCard(info)
            for edge in ("top", "bottom", "start", "end"):
                getattr(card, f"set_margin_{edge}")(10)
            if self.sunshine is not None:
                disc_btn = Gtk.Button(valign=Gtk.Align.CENTER)
                disc_btn.set_child(create_icon_widget("brp-network-offline-symbolic", size=16))
                disc_btn.add_css_class("flat")
                disc_btn.set_tooltip_text(_("Disconnect this specific guest (Admin)"))
                disc_btn.update_property([Gtk.AccessibleProperty.LABEL], [_("Disconnect guest")])
                disc_btn.connect("clicked", lambda _b, ip=info.address: self._prompt_disconnect(None, ip))
                card.append(disc_btn)
            row = Gtk.ListBoxRow(activatable=False, child=card)
            self._details_list.append(row)

    def set_connection_status(self, name, status, conn=True):
        if conn:
            self._title_label.set_label(_("Connected to {}").format(name))
        else:
            self._title_label.set_label(_("Real-time Monitoring"))
        self._status_label.set_label(status)
        if conn:
            set_icon(self._status_icon, "brp-network-transmit-receive-symbolic")
            self._status_icon.add_css_class("success")
            self._status_icon.remove_css_class("warning")
        else:
            set_icon(self._status_icon, "brp-network-idle-symbolic")
            self._status_icon.remove_css_class("success")
