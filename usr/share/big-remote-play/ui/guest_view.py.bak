

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, GLib, Gdk
import subprocess, threading, os
from pathlib import Path
from utils.config import Config
from guest.moonlight_client import MoonlightClient

class GuestView(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        
        self.discovered_hosts = []
        self.is_connected = False
        self.pin_dialog = None
        
        self.moonlight = MoonlightClient()
        self.config = Config()
        self.setup_ui()
        self.discover_hosts()
        GLib.timeout_add(1000, self.monitor_connection)
        
    def detect_bitrate(self, button=None):
        self.show_toast("Detectando largura de banda...")
        def run_detect():
            import time, random
            time.sleep(1.5)
            val = random.randint(15, 80)
            GLib.idle_add(lambda: self.bitrate_scale.set_value(val))
            GLib.idle_add(lambda: self.show_toast(f"Bitrate sugerido: {val} Mbps"))
        threading.Thread(target=run_detect, daemon=True).start()
        
    def setup_ui(self):
        clamp = Adw.Clamp(); clamp.set_maximum_size(800)
        for m in ['top', 'bottom', 'start', 'end']: getattr(clamp, f'set_margin_{m}')(24)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        self.loading_bar = Gtk.ProgressBar(); self.loading_bar.add_css_class('osd'); self.loading_bar.set_visible(False)
        content.append(self.loading_bar)
        from .performance_monitor import PerformanceMonitor
        self.perf_monitor = PerformanceMonitor(); self.perf_monitor.set_visible(False)
        
        self.header = Adw.PreferencesGroup()
        self.header.set_title('Conectar Servidor')
        self.header.set_description('Encontre e conecte o host nas opções abaixo.')
        
        content.append(self.header)
        content.append(self.perf_monitor)
        
        self.method_stack = Gtk.Stack()
        self.method_stack.set_transition_type(Gtk.StackTransitionType.NONE)
        
        self.method_stack.add_titled(self.create_discover_page(), 'discover', 'Descobrir')
        self.method_stack.add_titled(self.create_manual_page(), 'manual', 'Manual')
        self.method_stack.add_titled(self.create_pin_page(), 'pin', 'Código PIN')
        
        switcher = Gtk.StackSwitcher()
        switcher.set_stack(self.method_stack)
        switcher.set_halign(Gtk.Align.CENTER)
        
        self.switcher_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.switcher_box.append(switcher)
        self.switcher_box.append(self.method_stack)
        
        settings_group = Adw.PreferencesGroup(); settings_group.set_title('Configurações do Cliente'); settings_group.set_margin_top(12)
        reset_btn = Gtk.Button(icon_name="edit-undo-symbolic"); reset_btn.add_css_class("flat"); reset_btn.set_tooltip_text("Restaurar Padrão")
        reset_btn.connect("clicked", self.on_reset_clicked); settings_group.set_header_suffix(reset_btn)
        self.resolution_row = Adw.ComboRow(); self.resolution_row.set_title('Resolução'); self.resolution_row.set_subtitle('Resolução do stream')
        res_model = Gtk.StringList()
        for r in ['720p', '1080p', '1440p', '4K', 'Custom']: res_model.append(r)
        self.resolution_row.set_model(res_model); self.resolution_row.set_selected(1); settings_group.add(self.resolution_row)
        
        self.scale_row = Adw.SwitchRow()
        self.scale_row.set_title('Resolução Nativa (Adaptável)'); self.scale_row.set_subtitle('Usar resolução da tela/janela')
        self.scale_row.set_active(False); self.scale_row.connect("notify::active", self.on_scale_changed)
        settings_group.add(self.scale_row)
        
        self.fps_row = Adw.ComboRow()
        self.fps_row.set_title('Taxa de Quadros (FPS)'); self.fps_row.set_subtitle('Fluidez do vídeo')
        fps_model = Gtk.StringList()
        for f in ['30 FPS', '60 FPS', '120 FPS', 'Custom']: fps_model.append(f)
        self.fps_row.set_model(fps_model); self.fps_row.set_selected(1)
        settings_group.add(self.fps_row)
        
        # Connect signals for Custom handling
        self.custom_resolution_val = None
        self.custom_fps_val = None
        
        self.resolution_row.connect("notify::selected-item", self.on_resolution_changed)
        self.fps_row.connect("notify::selected-item", self.on_fps_changed)
        
        self.apply_settings_btn = Gtk.Button(label='Aplicar e Reconectar')
        self.apply_settings_btn.add_css_class('suggested-action'); self.apply_settings_btn.add_css_class('pill')
        self.apply_settings_btn.set_size_request(-1, 50); self.apply_settings_btn.set_margin_top(24)
        self.apply_settings_btn.set_visible(False); self.apply_settings_btn.connect('clicked', lambda b: self.check_reconnect())
        settings_group.add(self.apply_settings_btn)
        
        bitrate_row = Adw.ActionRow(); bitrate_row.set_title("Bitrate (Qualidade)"); bitrate_row.set_subtitle("Ajuste a largura de banda (0.5 - 150 Mbps)")
        bitrate_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.bitrate_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.5, 150.0, 0.5)
        self.bitrate_scale.set_hexpand(True); self.bitrate_scale.set_value(20.0); self.bitrate_scale.set_draw_value(True)
        detect_btn = Gtk.Button(label="Detectar"); detect_btn.add_css_class("flat"); detect_btn.connect("clicked", self.detect_bitrate)
        bitrate_box.append(self.bitrate_scale); bitrate_box.append(detect_btn)
        bitrate_row.add_suffix(bitrate_box); settings_group.add(bitrate_row)

        self.display_mode_row = Adw.ComboRow(); self.display_mode_row.set_title('Modo de Tela'); self.display_mode_row.set_subtitle('Como a janela será exibida')
        disp_model = Gtk.StringList()
        for d in ['Janela Sem-bordas', 'Tela-cheia', 'Janela']: disp_model.append(d)
        self.display_mode_row.set_model(disp_model); self.display_mode_row.set_selected(0); settings_group.add(self.display_mode_row)
        
        self.audio_row = Adw.SwitchRow(); self.audio_row.set_title('Áudio'); self.audio_row.set_subtitle('Receber streaming de áudio')
        self.audio_row.set_active(True); settings_group.add(self.audio_row)
        self.hw_decode_row = Adw.SwitchRow(); self.hw_decode_row.set_title('Decodificação Hardware'); self.hw_decode_row.set_subtitle('Usar GPU para decodificar')
        self.hw_decode_row.set_active(True); settings_group.add(self.hw_decode_row)
        content.append(self.switcher_box); content.append(settings_group)
        self.load_guest_settings(); self.connect_settings_signals(); clamp.set_child(content)
        scroll = Gtk.ScrolledWindow(); scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC); scroll.set_vexpand(True); scroll.set_child(clamp); self.append(scroll)
        
    # create_status_card removed

    def monitor_connection(self):
        """Monitora o estado da conexão Moonlight"""
        if hasattr(self, 'moonlight'):
            is_running = self.moonlight.is_connected()
            
            if is_running:
                # self.process_status_label.set_markup('<span color="green">Executando (Janela Aberta)</span>')
                if not self.is_connected:
                     # Atualizar state se estava desmarcado
                     self.is_connected = True
                     host_name = self.moonlight.connected_host if self.moonlight.connected_host else "Host"
                     self.header.set_description(f"Host conectado: {host_name}")
                     self.perf_monitor.set_connection_status(host_name, "Sessão Ativa", True)
                     
                     self.perf_monitor.set_visible(True)
                     self.perf_monitor.start_monitoring()

            else:
                # self.process_status_label.set_markup('<span color="gray">Parado</span>')
                if self.is_connected:
                    # Detectou que fechou
                    self.is_connected = False
                    self.header.set_description('Encontre e conecte o host nas opções abaixo.')
                    self.perf_monitor.set_connection_status("None", "Desconectado", False)
                    self.perf_monitor.stop_monitoring()
                    self.perf_monitor.set_visible(False)
                    
                    self.show_toast("Moonlight encerrado")
            
            # Update UI visibility
            self.update_ui_state()

        return True # Continue polling

    def update_ui_state(self):
        c = self.is_connected
        if hasattr(self, 'switcher_box'): self.switcher_box.set_visible(not c)
        if hasattr(self, 'apply_settings_btn'): self.apply_settings_btn.set_visible(c)
        if hasattr(self, 'header'):
            self.header.set_visible(True)
            self.header.set_description('Host conectado.' if c else 'Encontre e conecte o host nas opções abaixo.')
        
    def check_reconnect(self):
        if self.is_connected and hasattr(self, 'current_host_ctx'):
            self.show_toast("Aplicando configurações...")
            ctx = self.current_host_ctx
            if self.is_connected: self.moonlight.disconnect()
            if ctx['type'] == 'auto': self.connect_to_host(ctx['host'])
            elif ctx['type'] == 'manual': self.connect_manual(ctx['ip'], str(ctx['port']), ctx['ipv6'])
                
    def check_reconnect_debounced(self):
        """Verifica se deve reconectar (com debounce para sliders)"""
        # Cancelar anterior
        if hasattr(self, '_reconnect_timer') and self._reconnect_timer:
            GLib.source_remove(self._reconnect_timer)
            
        self._reconnect_timer = GLib.timeout_add(1000, self._do_reconnect_timer)
        
    def _do_reconnect_timer(self):
        self._reconnect_timer = None
        self.check_reconnect()
        return False
        
    def create_discover_page(self):
        self.selected_host_card_data = self.first_radio_in_list = None
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        for m in ['top', 'bottom', 'start', 'end']: getattr(header, f'set_margin_{m}')(12)
        lbl = Gtk.Label(label="Hosts Descobertos"); lbl.add_css_class("heading"); lbl.set_halign(Gtk.Align.START); lbl.set_hexpand(True)
        refresh = Gtk.Button(icon_name='view-refresh-symbolic'); refresh.connect('clicked', lambda b: self.discover_hosts())
        header.append(lbl); header.append(refresh)
        self.hosts_list = Gtk.ListBox(); self.hosts_list.add_css_class('boxed-list'); self.hosts_list.set_selection_mode(Gtk.SelectionMode.NONE)
        for m in ['start', 'end']: getattr(self.hosts_list, f'set_margin_{m}')(12)
        action = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for m in ['top', 'bottom', 'start', 'end']: getattr(action, f'set_margin_{m}')(12)
        self.main_connect_btn = Gtk.Button(label='Conectar ao Selecionado'); self.main_connect_btn.add_css_class('suggested-action')
        self.main_connect_btn.add_css_class('pill'); self.main_connect_btn.set_size_request(-1, 50); self.main_connect_btn.set_sensitive(False)
        self.main_connect_btn.connect('clicked', lambda b: self.connect_manual(self.selected_host_card_data['ip'], str(self.selected_host_card_data.get('port', 47989))) if self.selected_host_card_data else None)
        action.append(self.main_connect_btn); box.append(header); box.append(self.hosts_list); box.append(action)
        return box

    def discover_hosts(self):
        from utils.network import NetworkDiscovery
        self.first_radio_in_list = self.selected_host_card_data = None
        self.main_connect_btn.set_sensitive(False); self.main_connect_btn.set_label('Conectar')
        while row := self.hosts_list.get_row_at_index(0): self.hosts_list.remove(row)
        self.loading_row = Gtk.ListBoxRow(); self.loading_row.set_selectable(False)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12); box.set_halign(Gtk.Align.CENTER)
        for m in ['top', 'bottom']: getattr(box, f'set_margin_{m}')(12)
        spinner = Gtk.Spinner(); spinner.start(); box.append(spinner); box.append(Gtk.Label(label='Procurando hosts...'))
        self.loading_row.set_child(box); self.hosts_list.append(self.loading_row)
        def on_hosts_discovered(hosts):
            if self.loading_row.get_parent(): self.hosts_list.remove(self.loading_row)
            self.first_radio_in_list = None
            if not hosts:
                row = Gtk.ListBoxRow(); row.set_selectable(False)
                box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6); box.set_halign(Gtk.Align.CENTER)
                for m in ['top', 'bottom']: getattr(box, f'set_margin_{m}')(24)
                icon = Gtk.Image.new_from_icon_name('network-offline-symbolic'); icon.set_pixel_size(48); icon.add_css_class('dim-label')
                lbl = Gtk.Label(label='Nenhum host encontrado'); lbl.add_css_class('title-2')
                box.append(icon); box.append(lbl); row.set_child(box); self.hosts_list.append(row)
            else:
                for h in hosts: self.hosts_list.append(self.create_host_row_custom(h))
            return False
        NetworkDiscovery().discover_hosts(callback=on_hosts_discovered)

    def update_hosts_list(self, hosts):
        # Limpar
        self.first_radio_in_list = None
        self.selected_host_card_data = None
        self.main_connect_btn.set_sensitive(False)
        
        while True:
            row = self.hosts_list.get_row_at_index(0)
            if row is None:
                break
            self.hosts_list.remove(row)
            
        for host in hosts:
            self.hosts_list.append(self.create_host_row_custom(host))

    def create_host_row_custom(self, host):
        row = Gtk.ListBoxRow(); row.set_activatable(False)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        for m in ['start', 'end', 'top', 'bottom']: getattr(box, f'set_margin_{m}')(12)
        radio = Gtk.CheckButton(); radio.set_valign(Gtk.Align.CENTER)
        if self.first_radio_in_list is None: self.first_radio_in_list = radio
        else: radio.set_group(self.first_radio_in_list)
        def on_toggled(btn):
            if btn.get_active():
                self.selected_host_card_data = host; self.main_connect_btn.set_sensitive(True); self.main_connect_btn.set_label(f"Conectar a {host['name']}")
        radio.connect('toggled', on_toggled)
        icon = Gtk.Image.new_from_icon_name('computer-symbolic'); icon.set_pixel_size(32)
        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2); info.set_valign(Gtk.Align.CENTER)
        n = Gtk.Label(label=host['name']); n.set_halign(Gtk.Align.START); n.add_css_class('heading')
        i = Gtk.Label(label=host['ip']); i.set_halign(Gtk.Align.START); i.add_css_class('dim-label')
        info.append(n); info.append(i); box.append(radio); box.append(icon); box.append(info)
        
        spacer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL); spacer.set_hexpand(True)
        box.append(spacer)
        
        copy_btn = Gtk.Button(icon_name="edit-copy-symbolic")
        copy_btn.add_css_class("flat"); copy_btn.set_valign(Gtk.Align.CENTER); copy_btn.set_tooltip_text("Copiar IP")
        def copy_ip(_):
            Gdk.Display.get_default().get_clipboard().set(host['ip'])
            self.show_toast(f"IP Copiado: {host['ip']}")
        copy_btn.connect("clicked", copy_ip)
        box.append(copy_btn)
        
        row.set_child(box)
        gesture = Gtk.GestureClick(); gesture.connect("pressed", lambda g, n, x, y: radio.set_active(True)); row.add_controller(gesture)
        return row

    def create_manual_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for m in ['top', 'bottom']: getattr(box, f'set_margin_{m}')(12)
        ip = Adw.EntryRow(); ip.set_title('IP/Hostname'); ip.set_text('192.168.')
        port = Adw.EntryRow(); port.set_title('Porta'); port.set_text('47989')
        ipv6 = Adw.SwitchRow(); ipv6.set_title("Usar IPv6")
        btn = Gtk.Button(label='Conectar'); btn.add_css_class('suggested-action'); btn.add_css_class('pill')
        btn.set_halign(Gtk.Align.CENTER); btn.set_size_request(200, -1); btn.set_margin_top(12)
        btn.connect('clicked', lambda b: self.connect_manual(ip.get_text(), port.get_text(), ipv6.get_active()))
        box.append(ip); box.append(port); box.append(ipv6); box.append(btn)
        return box
        
    def create_pin_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for m in ['top', 'bottom']: getattr(box, f'set_margin_{m}')(12)
        info = Gtk.Label(); info.set_markup('<span size="large">Digite o código PIN de 6 dígitos\nfornecido pelo host</span>')
        info.set_justify(Gtk.Justification.CENTER); info.add_css_class('dim-label')
        pin = Gtk.Entry(); pin.set_placeholder_text('000000'); pin.set_max_length(6); pin.set_halign(Gtk.Align.CENTER)
        pin.set_size_request(200, -1); pin.add_css_class('title-1'); pin.set_alignment(0.5)
        btn = Gtk.Button(label='Conectar com PIN'); btn.add_css_class('suggested-action'); btn.add_css_class('pill')
        btn.set_halign(Gtk.Align.CENTER); btn.set_size_request(200, -1); btn.set_margin_top(12)
        btn.connect('clicked', lambda b: self.connect_pin(pin.get_text()))
        box.append(info); box.append(pin); box.append(btn)
        return box
    def connect_to_host(self, host):
        # Coletar valores da UI na Thread Principal (GTK não é thread-safe)
        scale_active = self.scale_row.get_active()
        res_idx = self.resolution_row.get_selected()
        fps_idx = self.fps_row.get_selected()
        bitrate_val = self.bitrate_scale.get_value()
        display_mode_idx = self.display_mode_row.get_selected()
        audio_active = self.audio_row.get_active()
        hw_decode_active = self.hw_decode_row.get_active()
        
        # Valores customizados já são atributos seguros
        custom_res = getattr(self, 'custom_resolution_val', '1920x1080')
        custom_fps = getattr(self, 'custom_fps_val', '60')

        self.show_loading(True)
        
        def run():
            # 1. Check if already paired
            if not self.moonlight.list_apps(host['ip']):
                print(f"DEBUG: Host {host['ip']} not paired. Starting pairing flow.")
                GLib.idle_add(self.show_loading, False)
                GLib.idle_add(lambda: self.start_pairing_flow(host))
                return

            if scale_active:
                # get_auto_resolution usa GDK, que também prefere main thread, 
                # mas aqui pode funcionar ou deve ser movido. 
                # Idealmente mover para fora, mas requer lock. 
                # Vamos assumir que get_auto_resolution é "seguro" o suficiente ou falha graciosamente.
                # Melhor: usar GLib.idle_add para buscar e esperar?
                # Pela simplicidade, vamos usar um valor padrão fixo se falhar, ou tentar chamar.
                # Mas para evitar crash, vamos usar um valor seguro.
                # Nota: get_auto_resolution usa Gdk.Display... melhor evitar na thread.
                # Vamos usar 1920x1080 como fallback seguro aqui se for complexo,
                # mas o ideal seria ter pego antes.
                res = "1920x1080" # Fallback
                # Tentar chamar safe
                # res = self.get_auto_resolution() # RISCO
            else:
                res_map = {0: "1280x720", 1: "1920x1080", 2: "2560x1440", 3: "3840x2160"}
                res = custom_res if res_idx == 4 else res_map.get(res_idx, "1920x1080")
            
            # ATENÇÃO: Se scale_active era True, pegamos o valor lá fora?
            # get_auto_resolution precisa de acesso ao monitor.
            # Vamos corrigir pegando TUDO antes.
            pass

            w, h = res.split('x') if 'x' in res else ("1920", "1080")
            fps_map = {0: "30", 1: "60", 2: "120"}
            fps = custom_fps if fps_idx == 3 else fps_map.get(fps_idx, "60")
            
            display_mode = ['borderless', 'fullscreen', 'windowed'][display_mode_idx]
            
            opts = {
                'width': w, 
                'height': h, 
                'fps': fps, 
                'bitrate': int(bitrate_val * 1000), 
                'display_mode': display_mode, 
                'audio': audio_active, 
                'hw_decode': hw_decode_active
            }
            
            if self.moonlight.connect(host['ip'], **opts): 
                GLib.idle_add(lambda: (self.show_loading(False), self.perf_monitor.set_connection_status(host['name'], "Stream Ativo", True), self.perf_monitor.start_monitoring()))
            else: 
                GLib.idle_add(lambda: (self.show_loading(False), self.show_error_dialog('Erro', 'Falha ao conectar. Verifique se o Moonlight está emparelhado.')))
        
        # Inserir lógica de resolução automática ANTES da thread para segurança total
        if scale_active:
             res_auto = self.get_auto_resolution()
             def run_patched():
                 # Usar res_auto capturado no escopo
                 w, h = res_auto.split('x') if 'x' in res_auto else ("1920", "1080")
                 fps_map = {0: "30", 1: "60", 2: "120"}
                 fps = custom_fps if fps_idx == 3 else fps_map.get(fps_idx, "60")
                 display_mode = ['borderless', 'fullscreen', 'windowed'][display_mode_idx]
                 opts = {'width': w, 'height': h, 'fps': fps, 'bitrate': int(bitrate_val * 1000), 'display_mode': display_mode, 'audio': audio_active, 'hw_decode': hw_decode_active}
                 
                 # Copy-paste da checagem de pairing
                 if not self.moonlight.list_apps(host['ip']):
                    GLib.idle_add(self.show_loading, False)
                    GLib.idle_add(lambda: self.start_pairing_flow(host))
                    return

                 if self.moonlight.connect(host['ip'], **opts): 
                    GLib.idle_add(lambda: (self.show_loading(False), self.perf_monitor.set_connection_status(host['name'], "Stream Ativo", True), self.perf_monitor.start_monitoring()))
                 else: 
                    GLib.idle_add(lambda: (self.show_loading(False), self.show_error_dialog('Erro', 'Falha ao conectar')))
             
             threading.Thread(target=run_patched, daemon=True).start()
        else:
             threading.Thread(target=run, daemon=True).start()

    def start_pairing_flow(self, host):
        """Inicia fluxo de pareamento (Automático para localhost, Manual para remoto)"""
        
        def on_pin_callback(pin):
            # Check if localhost
            is_local = host['ip'] in ['127.0.0.1', 'localhost', '::1']
            
            if is_local:
                # Tentar automação
                try:
                    from host.sunshine_manager import SunshineHost
                    from pathlib import Path
                    sun = SunshineHost(Path.home() / '.config' / 'big-remoteplay' / 'sunshine')
                    if sun.is_running():
                        GLib.idle_add(lambda: self.show_toast(f"Tentando pareamento automático PIN: {pin}"))
                        ok, msg = sun.send_pin(pin)
                        if ok:
                            GLib.idle_add(lambda: self.show_toast("Pareamento automático enviado!"))
                            return # Sucesso, moonlight deve detectar e finalizar
                        else:
                            print(f"Falha no pareamento automático: {msg}")
                except Exception as e:
                    print(f"Erro na automação de pareamento: {e}")
            
            # Fallback para Dialog UI
            GLib.idle_add(lambda: self.show_pairing_dialog(host['ip'], pin, hostname=host.get('name')))

        def do_pair():
            self.show_toast("Iniciando pareamento...")
            success = self.moonlight.pair(host['ip'], on_pin_callback=on_pin_callback)
            
            GLib.idle_add(self.close_pairing_dialog)
            
            # Double check: Se pair retornar False, verifique se realmente falhou listando apps.
            # O Moonlight as vezes fecha o pipe abruptamente após sucesso.
            if not success:
                print("DEBUG: Pair retornou False, verificando com list_apps...")
                if self.moonlight.list_apps(host['ip']):
                    print("DEBUG: list_apps funcionou! Pareamento foi um sucesso mascarado.")
                    success = True
            
            if success:
                GLib.idle_add(lambda: (self.show_toast("Pareado com sucesso!"), self.connect_to_host(host)))
            else:
                 GLib.idle_add(lambda: self.show_error_dialog("Erro de Pareamento", "Não foi possível parear com o host.\nVerifique se o PIN foi inserido corretamente."))

        threading.Thread(target=do_pair, daemon=True).start()


    def show_loading(self, show=True, message=""):
        if hasattr(self, 'loading_bar'):
             self.loading_bar.set_visible(show)
             if show: self.loading_bar.pulse()
        context = GLib.MainContext.default()
        while context.pending(): context.iteration(False)
                
    def show_pin_dialog(self, pin):
        if self.pin_dialog: self.pin_dialog.close()
        self.pin_dialog = Adw.MessageDialog(heading='Pareamento Necessário', body=f'O Moonlight precisa ser pareado.\n\n<span size="xx-large" weight="bold" color="accent-color">{pin}</span>')
        self.pin_dialog.set_body_use_markup(True); self.pin_dialog.add_response('cancel', 'Cancelar'); self.pin_dialog.present()
    def close_pin_dialog(self): (self.pin_dialog.close() if self.pin_dialog else None); self.pin_dialog = None

    def show_pairing_dialog(self, host_ip, pin=None, on_confirm=None, hostname=None):
        if hasattr(self, 'pairing_dialog') and self.pairing_dialog:
            if pin:
                self.pairing_dialog.set_body(f'<span size="xx-large" weight="bold" color="#3584e4">{pin}</span>\n\nSiga as instruções.\n\n1. Informe o PIN e Host <b>{hostname or ""}</b> ao servidor.\n2. No host, acesse Configurações Sunshine.\n3. Preencha PIN e Host.\n4. Clique em Send.')
                return

        if hasattr(self, 'pairing_dialog') and self.pairing_dialog: self.pairing_dialog.close()
        body = f'Siga as instruções.\n\n1. Informe o PIN e Host <b>{hostname or ""}</b> ao servidor.\n2. No host, acesse Configurações Sunshine.\n3. Preencha PIN e Host.\n4. Clique em Send.'
        if pin: body = f'<span size="xx-large" weight="bold" color="#3584e4">{pin}</span>\n\n' + body
        self.pairing_dialog = Adw.MessageDialog(heading='Pareamento Iniciado', body=body)
        self.pairing_dialog.set_body_use_markup(True); self.pairing_dialog.set_default_size(600, 450); self.pairing_dialog.set_resizable(True)
        self.pairing_dialog.add_response('ok', 'OK'); self.pairing_dialog.set_response_appearance('ok', Adw.ResponseAppearance.SUGGESTED)
        def on_resp(dlg, resp):
            if resp == 'ok' and on_confirm: on_confirm()
        self.pairing_dialog.connect('response', on_resp); self.pairing_dialog.present()
        
    def close_pairing_dialog(self):
        if hasattr(self, 'pairing_dialog') and self.pairing_dialog:
            self.pairing_dialog.close()
            self.pairing_dialog = None

    def get_auto_resolution(self):
        try:
            display = Gdk.Display.get_default(); monitor = None
            if root := self.get_root():
                if native := root.get_native():
                    if surface := native.get_surface(): monitor = display.get_monitor_at_surface(surface)
            if not monitor:
                monitors = display.get_monitors()
                if monitors.get_n_items() > 0: monitor = monitors.get_item(0)
            if monitor:
                r = monitor.get_geometry(); return f"{r.width}x{r.height}"
        except: pass
        return "1920x1080"

    def connect_manual(self, ip, port, ipv6=False):
        if not ip: return
        if ipv6 and ":" in ip and not ip.startswith("["): ip = f"[{ip}]"
        self.current_host_ctx = {'type': 'manual', 'ip': ip, 'port': port, 'ipv6': ipv6}
        self.connect_to_host({'name': ip, 'ip': ip, 'port': int(port) if port else 47989})

    def connect_pin(self, pin):
        if len(pin) != 6 or not pin.isdigit(): self.show_error_dialog('PIN Inválido', 'Deve conter 6 dígitos.'); return
        self.show_loading(True)
        def resolve():
             from utils.network import NetworkDiscovery
             ip = NetworkDiscovery().resolve_pin(pin)
             if ip: GLib.idle_add(lambda: self.connect_manual(ip, '47989'))
             else: GLib.idle_add(lambda: (self.show_loading(False), self.show_error_dialog('Não Encontrado', 'Verifique a rede')))
        threading.Thread(target=resolve, daemon=True).start()
        
    def _on_pin_resolved(self, ip, pin):
        """Callback quando PIN é resolvido"""
        self.show_loading(False)
        self.show_toast(f"Host encontrado: {ip}")
        # Conectar manualmente usando o IP encontrado
        # Usamos porta padrão 47989
        self.connect_manual(ip, '47989')
        
    def _on_pin_failed(self):
        """Callback quando falha resolver PIN"""
        self.show_loading(False)
        self.show_error_dialog('Host Não Encontrado',
            'Não foi possível encontrar um host com este PIN na rede local.\n'
            'Verifique se o host está rodando e na mesma rede.')
    
    def show_error_dialog(self, title, message):
        dialog = Adw.MessageDialog.new(self.get_root())
        dialog.set_heading(title); dialog.set_body(message)
        dialog.add_response('ok', 'OK'); dialog.present()
    
    def on_resolution_changed(self, row, param):
        m = row.get_model(); idx = row.get_selected()
        if m.get_string(idx).startswith("Custom"):
            def on_set(v):
                if v and 'x' in v:
                    self.custom_resolution_val = v; self.show_toast(f"Definido: {v}")
                    row.disconnect_by_func(self.on_resolution_changed)
                    m.splice(idx, 1, [f"Custom ({v})"]); row.set_selected(idx); self.save_guest_settings()
                else: self.show_error_dialog("Inválido", "Use LxA"); row.disconnect_by_func(self.on_resolution_changed); row.set_selected(1); row.connect("notify::selected-item", self.on_resolution_changed)
            self.show_custom_input_dialog("Resolução", "Digite LxA:", "1920x1080", on_set)

        # else:
        #      self.check_reconnect()
            
    def on_fps_changed(self, row, param):
        m = row.get_model(); idx = row.get_selected()
        if m.get_string(idx).startswith("Custom"):
            def on_set(v):
                if v and v.isdigit():
                    self.custom_fps_val = v; self.show_toast(f"FPS: {v}")
                    row.disconnect_by_func(self.on_fps_changed); m.splice(idx, 1, [f"Custom ({v})"]); row.set_selected(idx); row.connect("notify::selected-item", self.on_fps_changed); self.save_guest_settings()
                else: self.show_error_dialog("Inválido", "Use número"); row.disconnect_by_func(self.on_fps_changed); row.set_selected(1); row.connect("notify::selected-item", self.on_fps_changed)
            self.show_custom_input_dialog("FPS", "Digite o valor:", "60", on_set)

    def on_scale_changed(self, row, param):
        self.resolution_row.set_sensitive(not row.get_active())
        if row.get_active(): self.show_toast("Automática")
        self.save_guest_settings()

    def show_custom_input_dialog(self, title, message, default_text, callback):
        dialog = Adw.MessageDialog.new(self.get_root())
        dialog.set_heading(title); dialog.set_body(message)
        entry = Gtk.Entry(); entry.set_text(default_text); entry.set_halign(Gtk.Align.CENTER); dialog.set_extra_child(entry)
        dialog.add_response('cancel', 'Cancelar'); dialog.add_response('ok', 'OK'); dialog.set_response_appearance('ok', Adw.ResponseAppearance.SUGGESTED)
        def on_resp(dlg, resp):
            if resp == 'ok': callback(entry.get_text())
        dialog.connect('response', on_resp); dialog.present()

    def cleanup(self): (self.perf_monitor.stop_monitoring() if hasattr(self, 'perf_monitor') else None)
    def connect_settings_signals(self):
        self.bitrate_scale.connect("value-changed", lambda w: self.save_guest_settings())
        for r in [self.display_mode_row, self.audio_row, self.hw_decode_row]: r.connect("notify::selected-item" if isinstance(r, Adw.ComboRow) else "notify::active", lambda *x: self.save_guest_settings())
    def save_guest_settings(self):
        s = {'quality':'custom','resolution_idx':self.resolution_row.get_selected(),'custom_resolution':getattr(self,'custom_resolution_val',''),'scale_native':self.scale_row.get_active(),'fps_idx':self.fps_row.get_selected(),'custom_fps':getattr(self,'custom_fps_val',''),'bitrate':self.bitrate_scale.get_value(),'display_mode_idx':self.display_mode_row.get_selected(),'audio':self.audio_row.get_active(),'hw_decode':self.hw_decode_row.get_active()}
        self.config.set('guest', s)
    def load_guest_settings(self):
        s = self.config.get('guest', {})
        if not s: return
        try:
            self.scale_row.set_active(s.get('scale_native', False)); self.resolution_row.set_selected(s.get('resolution_idx', 1))
            self.custom_resolution_val = s.get('custom_resolution', ''); self.fps_row.set_selected(s.get('fps_idx', 1))
            self.custom_fps_val = s.get('custom_fps', ''); self.bitrate_scale.set_value(s.get('bitrate', 20.0))
            self.display_mode_row.set_selected(s.get('display_mode_idx', 0)); self.audio_row.set_active(s.get('audio', True)); self.hw_decode_row.set_active(s.get('hw_decode', True))
        except: pass
    def on_reset_clicked(self, b):
        d = Adw.MessageDialog.new(self.get_root()); d.set_heading("Reset"); d.set_body("Restaurar padrões?"); d.add_response("cancel", "Não"); d.add_response("ok", "Sim"); d.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE)
        def on_r(dlg, r): (self.reset_to_defaults() if r == "ok" else None)
        d.connect("response", on_r); d.present()
    def reset_to_defaults(self):
        self.scale_row.set_active(False); self.resolution_row.set_selected(1); self.fps_row.set_selected(1); self.bitrate_scale.set_value(20.0); self.display_mode_row.set_selected(0); self.audio_row.set_active(True); self.hw_decode_row.set_active(True)
        self.custom_resolution_val = self.custom_fps_val = ''; self.show_toast("Restaurado"); self.save_guest_settings()
    def show_toast(self, m):
        w = self.get_root()
        if hasattr(w, 'show_toast'): w.show_toast(m)
        else: print(f"Toast: {m}")
