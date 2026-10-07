"""**Set up Headscale**: a step-by-step wizard, as pages of Connect your devices.

Written for someone who has never heard of DNS or HTTPS: every page asks one
thing in plain words, fills in what Big Remote Play already knows, and checks
the step before it is marked done. Technical words live in expanders.

Two paths:

* **This computer** — find the public address, give it a name (a domain is
  recommended; DigitalPlat and Cloudflare are guided), install and configure
  Headscale behind Caddy (automatic HTTPS) through the reviewed PolicyKit
  helper, create the private network and add this computer;
* **Another server** — check the server step by step (name, port, HTTPS,
  Headscale), guide its setup when it is not ready, then add this computer
  with an API key, a one-time key, or the owner's approval.

Progress is saved without secrets (:class:`SetupStore`), so the setup
continues after a restart. Blocking work runs on one :class:`Worker`; results
after the wizard was left are dropped.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
import getpass
import re
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk, Pango  # type: ignore

from big_remote_play.private_network import dns_check, public_address
from big_remote_play.private_network.headscale_server import (
    DEFAULT_USER,
    LocalServer,
    ServerCheck,
    SetupProgress,
    SetupStore,
    check_server,
    parse_server_address,
    preauth_key_kind,
    valid_user_name,
    web_ports_in_use,
)
from big_remote_play.private_network.models import ProviderId
from big_remote_play.utils.i18n import _
from big_remote_play.utils.uri import open_uri

from .components import note
from .network_common import Worker, copy_row, in_stack, loading_row, message_row, push_page

if TYPE_CHECKING:
    from big_remote_play.private_network.service import PrivateNetworkService

DIGITALPLAT_URL = "https://dashboard.digitalplat.org"
CLOUDFLARE_URL = "https://dash.cloudflare.com"
HEADSCALE_RELEASES = "https://github.com/juanfont/headscale/releases"
HTTPS_WAIT_SECONDS = 90.0


def step_name(step: str) -> str:
    return {
        "server": _("Server"),
        "domain": _("Domain"),
        "dns": _("DNS"),
        "https": _("Secure connection (HTTPS)"),
        "headscale": _("Headscale"),
        "this_computer": _("This computer"),
        "another_device": _("Add another device"),
    }[step]


def server_problem_words(check: ServerCheck) -> tuple[str, str]:
    """``(sentence, detail)`` for a failed server check, in plain words."""
    port = check.address.port if check.address is not None else 443
    return {
        "invalid": (_("This is not a server address."), _("Example: headscale.example.com or 203.0.113.10")),
        "dns": (_("This name does not lead to a server yet."), _("If you just set up the domain, wait a little and check again.")),
        "refused": (_("The Headscale server is not reachable."), _("Connection refused on port {port}.").format(port=port)),
        "timeout": (_("The Headscale server is not reachable."), _("No answer on port {port}. A firewall or the router may block it.").format(port=port)),
        "unreachable": (_("The Headscale server is not reachable."), _("The network cannot reach it on port {port}.").format(port=port)),
        "tls_invalid": (_("The server's secure certificate is not valid."), _("HTTPS is not set up correctly for this address.")),
        "tls_failed": (_("A secure connection could not be made."), _("HTTPS is not set up correctly for this address.")),
        "not_headscale": (_("Something answers there, but it is not a Headscale server."), _("Check that Headscale runs behind this address.")),
    }.get(check.problem, (_("The server could not be checked."), ""))


def record_name(domain: str, zone: str) -> str:
    """What to type in the DNS record's Name field: ``headscale``, or ``@`` for the zone itself."""
    if not zone or domain == zone:
        return "@"
    if domain.endswith("." + zone):
        return domain[: -len(zone) - 1]
    return domain.split(".", 1)[0]


def default_user_name() -> str:
    try:
        name = re.sub(r"[^a-z0-9._-]", "", getpass.getuser().lower())
    except Exception:
        name = ""
    return name if valid_user_name(name) else DEFAULT_USER


def _label(text: str, *classes: str) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0, wrap=True)
    label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    for css in classes:
        label.add_css_class(css)
    return label


def _button(text: str, callback: Callable[[], object], *, suggested: bool = False, destructive: bool = False) -> Gtk.Button:
    button = Gtk.Button(label=text)
    button.add_css_class("pill")
    if suggested:
        button.add_css_class("suggested-action")
    if destructive:
        button.add_css_class("destructive-action")
    button.connect("clicked", lambda _button: callback())
    return button


def _actions(*buttons: Gtk.Widget) -> Gtk.FlowBox:
    """Buttons side by side, wrapping one under the other on a narrow window."""
    flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, min_children_per_line=1, max_children_per_line=3, homogeneous=False, halign=Gtk.Align.START)
    flow.set_column_spacing(12)
    flow.set_row_spacing(8)
    for button in buttons:
        flow.append(button)
        button.get_parent().set_focusable(False)
    return flow


def _group(title: str = "", description: str = "", *rows: Gtk.Widget) -> Adw.PreferencesGroup:
    group = Adw.PreferencesGroup(title=title, description=description)
    for row in rows:
        group.add(row)
    return group


def _value_row(title: str, value: str, toast: Callable[[str], None], *, subtitle: str = "") -> Adw.ActionRow:
    row = copy_row(title, value, toast=toast, subtitle=subtitle or value)
    return row


class HeadscaleWizard:
    """Controller of the wizard's pages. One instance per opening."""

    def __init__(
        self,
        main_window,
        navigation: Adw.NavigationView,
        *,
        service_factory: "Callable[[], PrivateNetworkService] | None" = None,
        store: SetupStore | None = None,
        local: LocalServer | None = None,
        deps: SimpleNamespace | None = None,
        on_finished: Callable[[], object] | None = None,
        on_restart: Callable[[], object] | None = None,
    ) -> None:
        from big_remote_play.private_network.service import default_service

        self.window = main_window
        self.navigation = navigation
        self.service_factory = service_factory or default_service
        self.store = store or SetupStore()
        self.local = local or LocalServer()
        self.on_finished = on_finished or (lambda: None)
        self.on_restart = on_restart
        defaults = SimpleNamespace(
            detect=lambda force: public_address.cached_public_addresses(force=force),
            nameservers=lambda zone, expected: dns_check.check_nameservers(zone, expected=expected),
            zone_of=lambda domain: dns_check.zone_of(dns_check.DnsClient(), domain),
            record=lambda name, expected, rtype: dns_check.check_record(name, expected, rtype=rtype),
            check_server=lambda address, connect_host: check_server(address, connect_host=connect_host),
            web_ports=web_ports_in_use,
            open_uri=open_uri,
            sleep=time.sleep,
            clock=time.monotonic,
        )
        if deps is not None:
            for key, value in vars(deps).items():
                setattr(defaults, key, value)
        self.deps = defaults
        self.progress = self.store.load()
        self.report: public_address.PublicAddressReport | None = None
        self.worker = Worker()
        self.root: Adw.NavigationPage | None = None
        self.pages: dict[str, Adw.NavigationPage] = {}
        self.last_check: ServerCheck | None = None
        self.switch_dialog: Adw.AlertDialog | None = None
        self.approval = None

    # ── lifecycle ──────────────────────────────────────────────────────────
    def present(self, *, server: str = "") -> Adw.NavigationPage:
        """The wizard's first page; ``server`` (a known address) goes straight to checking it."""
        self.root = self._start_page()
        push_page(self.navigation, self.root, on_closed=self.close)
        if server:
            self.progress = replace(self.progress, mode="another_server")
            self._push(self._remote_page(prefill=server))
        return self.root

    def close(self) -> None:
        self.worker.close()

    def toast(self, text: str) -> None:
        show = getattr(self.window, "show_toast", None)
        if callable(show):
            show(text)

    def _save(self, progress: SetupProgress) -> None:
        self.progress = progress
        try:
            self.store.save(progress)
        except OSError:
            self.toast(_("The setup progress could not be saved."))

    def _mark(self, *steps: str) -> None:
        progress = self.progress
        for step in steps:
            progress = progress.mark(step)
        self._save(progress)

    def _push(self, page: Adw.NavigationPage) -> Adw.NavigationPage:
        tag = page.get_tag() or ""
        if tag:
            # A step visited again (the server check after setting up its
            # domain) replaces its earlier page: tags are unique in a stack.
            existing = self.navigation.find_page(tag)
            if existing is not None and existing is not self.root and in_stack(self.navigation, existing):
                previous = self.navigation.get_previous_page(existing)
                if previous is not None:
                    self.navigation.pop_to_page(previous)
            self.pages[tag] = page
        self.navigation.push(page)
        return page

    def _later(self, callback: Callable[[], object]) -> Callable[[], None]:
        """A callback safe to call from a worker thread (dropped once the wizard closed)."""
        generation = self.worker.generation

        def run() -> bool:
            if not self.worker._closed and generation == self.worker.generation:
                callback()
            return False

        return lambda: GLib.idle_add(run) and None

    # ── page frame ─────────────────────────────────────────────────────────
    def _page(self, tag: str, title: str, heading: str, body: str, *widgets: Gtk.Widget, step: str = "") -> Adw.NavigationPage:
        from .network_devices import page

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        if step and step in self.progress.steps:
            steps = self.progress.steps
            caption = _label(_("Step {number} of {total} · {name}").format(number=steps.index(step) + 1, total=len(steps), name=step_name(step)), "dim-label", "caption-heading")
            box.append(caption)
        heading_label = _label(heading, "title-2")
        heading_label.set_accessible_role(Gtk.AccessibleRole.HEADING)
        box.append(heading_label)
        if body:
            box.append(_label(body, "dim-label"))
        for widget in widgets:
            box.append(widget)
        return page(title, box, tag=tag)

    def _progress_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title=_("Headscale setup"))
        upcoming = self.progress.next_step
        for step in self.progress.steps:
            done = self.progress.is_done(step)
            word = _("Done") if done else (_("Next") if step == upcoming else _("Not yet"))
            row = Adw.ActionRow(title=step_name(step), subtitle=word, use_markup=False)
            from big_remote_play.utils.icons import create_icon_widget

            icon = create_icon_widget("brp-emblem-ok-symbolic" if done else ("go-next-symbolic" if step == upcoming else "brp-media-record-symbolic"), size=16, css_class="brp-row-icon")
            icon.set_valign(Gtk.Align.CENTER)
            if done:
                icon.add_css_class("success")
            elif step != upcoming:
                icon.add_css_class("dim-label")
            row.add_prefix(icon)
            group.add(row)
        return group

    # ── start ──────────────────────────────────────────────────────────────
    def _start_page(self) -> Adw.NavigationPage:
        from .guided_setup import choice_card

        widgets: list[Gtk.Widget] = []
        if self.progress.started:
            widgets.append(self._progress_group())
            widgets.append(_actions(_button(_("Continue setup"), self.resume, suggested=True), _button(_("Start over"), self._confirm_restart)))
        else:
            widgets.append(_label(_("Where will your Headscale server run?"), "title-3"))
            widgets.append(note(_("The Headscale server needs to stay available so your devices can find each other."), "brp-network-server-symbolic"))
            widgets.append(choice_card(_("Another server"), _("Use a VPS or another computer that stays online."), "brp-network-server-symbolic", self.choose_another_server, badge=_("Recommended")))
            widgets.append(choice_card(_("This computer"), _("Use this PC as the Headscale server."), "brp-computer-symbolic", self.choose_this_computer))
        return self._page("hs-setup", _("Set up Headscale"), _("Set up Headscale"), _("Headscale gives you your own private network server."), *widgets)

    def resume(self) -> None:
        """Open the page of the first step that is not verified yet."""
        progress = self.progress
        step = progress.next_step
        builder: Callable[[], Adw.NavigationPage]
        if step == "this_computer":
            builder = self._this_computer_page
        elif step == "another_device" or not step:
            builder = self._another_device_page
        elif progress.mode == "this_computer":
            if step == "server":
                self.choose_this_computer()
                return
            if not progress.address_kind:
                builder = self._address_page
            elif step in ("domain", "dns"):
                if not progress.domain:
                    builder = self._have_domain_page
                elif step == "domain" and progress.dns_manager == "cloudflare":
                    builder = self._cloudflare_page
                else:
                    builder = self._record_page
            else:
                builder = self._install_page
        elif step in ("domain", "dns") and progress.address_kind == "domain" and progress.domain and progress.server_ip:
            builder = self._record_page
        else:
            prefill = progress.domain or progress.server_ip
            builder = lambda: self._remote_page(prefill=prefill)  # noqa: E731
        self._push(builder())

    def _confirm_restart(self) -> None:
        from .network_common import confirm

        def restart() -> None:
            self.store.clear()
            self.progress = SetupProgress()
            if self.root is not None and in_stack(self.navigation, self.root):
                previous = self.navigation.get_previous_page(self.root)
                if previous is not None:
                    self.navigation.pop_to_page(previous)
            if self.on_restart is not None:
                GLib.idle_add(lambda: self.on_restart() and False)  # type: ignore[misc]

        confirm(self.navigation, _("Start over?"), _("The finished steps are forgotten. Nothing on the server is removed."), _("Start over"), restart)

    def choose_this_computer(self) -> None:
        self._save(replace(self.progress, mode="this_computer"))
        self._push(self._internet_page())

    def choose_another_server(self) -> None:
        self._save(replace(SetupProgress(), mode="another_server"))
        self._push(self._remote_page())

    # ── this computer: its internet address ────────────────────────────────
    def _internet_page(self) -> Adw.NavigationPage:
        self.internet_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        page = self._page(
            "hs-internet",
            _("Your internet connection"),
            _("Checking your internet connection"),
            _("Big Remote Play looks for the address the internet sees for this computer."),
            self.internet_box,
            step="server",
        )
        self._detect(force=False)
        return page

    def _detect(self, *, force: bool) -> None:
        self._replace(self.internet_box, _group("", "", loading_row(_("Checking your internet connection…"))))
        self.worker.submit(lambda: self.deps.detect(force), self._show_report, failed=lambda _error: self._show_report(None))

    @staticmethod
    def _replace(box: Gtk.Box, *widgets: Gtk.Widget) -> None:
        while child := box.get_first_child():
            box.remove(child)
        for widget in widgets:
            box.append(widget)

    def _show_report(self, report) -> None:
        self.report = report
        widgets: list[Gtk.Widget] = []
        if report is None or not report.ipv4:
            widgets.append(_group("", "", message_row(_("We could not find your public address"), _("Check that this computer is connected to the internet."), "dialog-warning-symbolic")))
            widgets.append(_actions(_button(_("Check again"), lambda: self._detect(force=True), suggested=True), _button(_("Use another server"), self.choose_another_server)))
        else:
            rows = [_value_row(_("Your public IP"), report.ipv4, self.toast, subtitle=f"{report.ipv4} · {_('Detected automatically')}")]
            if report.ipv6_on_this_computer:
                rows.append(_value_row(_("Public IPv6"), report.ipv6, self.toast, subtitle=f"{report.ipv6} · {_('Detected automatically')}"))
            widgets.append(_group("", "", *rows))
            if report.cgnat:
                warning = _group(_("Your internet provider may block incoming connections."), _("Headscale may work better on a VPS."))
                widgets.append(warning)
                widgets.append(_actions(_button(_("Use another server"), self.choose_another_server, suggested=True), _button(_("Continue anyway"), self._accept_address)))
            else:
                widgets.append(_actions(_button(_("Next"), self._accept_address, suggested=True)))
        widgets.append(self._technical(self._report_details(report)))
        self._replace(self.internet_box, *widgets)

    @staticmethod
    def _report_details(report) -> list[str]:
        if report is None:
            return [_("No answer from the address services.")]
        lines = [
            f"IPv4: {report.ipv4 or '-'} ({report.ipv4_source or 'not found'})",
            f"IPv6: {report.ipv6 or '-'} ({report.ipv6_source or 'not found'}{', on this computer' if report.ipv6_on_this_computer else ''})",
            "Local IPv4: " + (", ".join(report.local_ipv4) or "-"),
            f"Router (UPnP): {report.router_external or 'unknown'}",
            f"CGNAT: {'suspected' if report.cgnat else ('no' if report.cgnat is False else 'unknown')} {report.cgnat_reason}".strip(),
        ]
        if report.rejected:
            lines.append("Not public, ignored: " + ", ".join(report.rejected))
        return lines

    def _technical(self, lines: Sequence[str], *, title: str = "") -> Adw.PreferencesGroup:
        expander = Adw.ExpanderRow(title=title or _("Technical details"), use_markup=False)
        row = Adw.ActionRow(title="\n".join(lines) or _("No details reported."), use_markup=False)
        row.set_title_lines(0)
        row.set_title_selectable(True)
        expander.add_row(row)
        return _group("", "", expander)

    def _accept_address(self) -> None:
        report = self.report
        if report is None or not report.ipv4:
            return
        ipv6 = report.ipv6 if report.ipv6_on_this_computer else ""
        self._save(replace(self.progress, server_ip=report.ipv4, server_ipv6=ipv6).mark("server"))
        self._push(self._address_page())

    # ── how it is reached ──────────────────────────────────────────────────
    def _address_page(self) -> Adw.NavigationPage:
        from .guided_setup import choice_card

        return self._page(
            "hs-address",
            _("Server address"),
            _("How should your Headscale server be reached?"),
            "",
            choice_card(_("Use a domain"), _("Easier and safer. Example: headscale.example.com"), "brp-network-workgroup-symbolic", self._use_domain, badge=_("Recommended")),
            choice_card(_("Use the detected address"), _("Advanced. HTTPS certificates can be harder to configure with an IP address."), "brp-address-symbolic", self._use_ip),
            step="domain",
        )

    def _use_domain(self) -> None:
        self._save(replace(self.progress, address_kind="domain"))
        self._push(self._have_domain_page())

    def _use_ip(self) -> None:
        self._save(replace(self.progress, address_kind="ip", domain="").unmark("domain", "dns"))
        self._push(self._install_page())

    def _have_domain_page(self) -> Adw.NavigationPage:
        from .guided_setup import choice_card

        return self._page(
            "hs-have-domain",
            _("Domain"),
            _("Do you already have a domain?"),
            _("A domain is a name like example.com. Your server gets a name inside it, like headscale.example.com."),
            choice_card(_("Yes"), _("I already own a domain"), "brp-emblem-ok-symbolic", lambda: self._push(self._domain_entry_page())),
            choice_card(_("No"), _("Help me get one"), "brp-help-browser-symbolic", lambda: self._push(self._get_domain_page())),
            step="domain",
        )

    def _domain_entry_page(self) -> Adw.NavigationPage:
        self.domain_entry = Adw.EntryRow(title=_("Domain or subdomain"), use_markup=False)
        self.domain_entry.set_text(self.progress.domain)
        self.domain_entry.connect("entry-activated", lambda _row: self._submit_domain())
        hint = _label(_("Example: headscale.example.com"), "dim-label", "caption")
        self.cloudflare_check = Gtk.CheckButton(label=_("Cloudflare"))
        other = Gtk.CheckButton(label=_("Other"), group=self.cloudflare_check)
        (other if self.progress.dns_manager == "other" else self.cloudflare_check).set_active(True)
        manager = _group(_("Where do you manage DNS?"), _("DNS is where your domain's records live."))
        for check in (self.cloudflare_check, other):
            row = Adw.ActionRow(title=check.get_label() or "", use_markup=False, activatable_widget=check)
            row.add_prefix(check)
            manager.add(row)
        self.domain_status = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        return self._page(
            "hs-domain",
            _("Domain"),
            _("Your domain"),
            "",
            _group("", "", self.domain_entry),
            hint,
            manager,
            self.domain_status,
            _actions(_button(_("Continue"), self._submit_domain, suggested=True)),
            step="domain",
        )

    def _submit_domain(self) -> None:
        try:
            domain = dns_check.normalize_domain(self.domain_entry.get_text())
        except ValueError:
            self.domain_entry.add_css_class("error")
            self._replace(self.domain_status, _group("", "", message_row(_("This is not a domain."), _("Example: headscale.example.com"), "dialog-warning-symbolic")))
            return
        self.domain_entry.remove_css_class("error")
        manager = "cloudflare" if self.cloudflare_check.get_active() else "other"
        self._save(replace(self.progress, domain=domain, has_domain=True, dns_manager=manager, registrar=""))
        self._replace(self.domain_status, _group("", "", loading_row(_("Checking your domain…"))))

        def work():
            zone = self.deps.zone_of(domain)
            return zone, self.deps.nameservers(zone, ())

        def done(result) -> None:
            zone, check = result
            self._zone = zone
            if check.state == "missing":
                self._replace(self.domain_status, _group("", "", message_row(_("This domain does not exist yet."), _("Check the name, or register it first."), "dialog-warning-symbolic")))
                return
            if check.state == "error":
                self._replace(self.domain_status, _group("", "", message_row(_("The domain could not be checked."), _("Check your internet connection and try again."), "dialog-warning-symbolic")))
                return
            self._replace(self.domain_status)
            if manager == "cloudflare" and check.state != "cloudflare":
                self._push(self._cloudflare_page())
                return
            self._mark("domain")
            self._push(self._record_page())

        self.worker.submit(work, done, failed=lambda _error: done(("", dns_check.NameserverCheck("error"))))

    # ── no domain yet: DigitalPlat ─────────────────────────────────────────
    def _get_domain_page(self) -> Adw.NavigationPage:
        self.registered_entry = Adw.EntryRow(title=_("The domain you registered"), use_markup=False)
        if self.progress.registrar == "digitalplat" and self.progress.domain.count(".") >= 2:
            self.registered_entry.set_text(self.progress.domain.split(".", 1)[1])
        steps = [
            (_("Create or sign in to your DigitalPlat account."), [_button(_("Open DigitalPlat"), lambda: self.deps.open_uri(self.navigation, DIGITALPLAT_URL))]),
            (
                _("Choose Register, check the domain name you want, and read the current availability and rules shown by DigitalPlat."),
                [_group("", "", self.registered_entry), _label(_("Example: mydomain.dpdns.org"), "dim-label", "caption")],
            ),
            (
                _("DigitalPlat needs the nameservers from your DNS provider. Cloudflare is free; Big Remote Play guides you."),
                [_button(_("Set up Cloudflare"), self._digitalplat_to_cloudflare, suggested=True)],
            ),
        ]
        intro = _group(
            "DigitalPlat",
            _("DigitalPlat can provide domains that can be delegated to a DNS provider."),
        )
        return self._page(
            "hs-get-domain",
            _("Get a domain"),
            _("Get a domain"),
            _("You can use a domain you already own or register one with a domain provider."),
            intro,
            self._digitalplat_guide(steps),
            step="domain",
        )

    def _digitalplat_guide(self, steps) -> "StepGuide":
        self.digitalplat_guide = StepGuide(steps, on_next=self._digitalplat_next)
        return self.digitalplat_guide

    def _digitalplat_next(self, index: int) -> bool:
        """Leaving step 2 needs the registered domain."""
        if index != 1:
            return True
        try:
            dns_check.normalize_domain(self.registered_entry.get_text())
        except ValueError:
            self.registered_entry.add_css_class("error")
            self.toast(_("Type the domain you registered, for example mydomain.dpdns.org."))
            return False
        self.registered_entry.remove_css_class("error")
        return True

    def _digitalplat_to_cloudflare(self) -> None:
        try:
            registered = dns_check.normalize_domain(self.registered_entry.get_text())
        except ValueError:
            self.toast(_("Type the domain you registered, for example mydomain.dpdns.org."))
            return
        self._zone = registered
        self._save(replace(self.progress, domain=f"headscale.{registered}", has_domain=False, registrar="digitalplat", dns_manager="cloudflare"))
        self._push(self._cloudflare_page())

    # ── Cloudflare ─────────────────────────────────────────────────────────
    def _zone_name(self) -> str:
        zone = getattr(self, "_zone", "")
        if zone and (self.progress.domain == zone or self.progress.domain.endswith("." + zone)):
            return zone
        parts = self.progress.domain.split(".")
        return ".".join(parts[1:]) if len(parts) > 2 else self.progress.domain

    def _cloudflare_page(self) -> Adw.NavigationPage:
        zone = self._zone_name()
        digitalplat = self.progress.registrar == "digitalplat"
        self.ns_entry = Adw.EntryRow(title=_("Paste the two nameservers (optional)"), use_markup=False)
        self.ns_entry.set_text(" ".join(self.progress.nameservers))
        self.ns_status = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        example = _group("", "", message_row("xxxx.ns.cloudflare.com", "yyyy.ns.cloudflare.com", "brp-network-server-symbolic"))
        where = _("Copy these two Cloudflare nameservers to DigitalPlat.") if digitalplat else _("Copy these two Cloudflare nameservers to the company where you registered your domain.")
        third: list[Gtk.Widget] = [self._nameserver_rows()]
        if digitalplat:
            third.append(_button(_("Open DigitalPlat"), lambda: self.deps.open_uri(self.navigation, DIGITALPLAT_URL)))
        steps = [
            (
                _("Add your domain to Cloudflare."),
                [_group("", "", _value_row(_("Your domain"), zone, self.toast)), _button(_("Open Cloudflare"), lambda: self.deps.open_uri(self.navigation, CLOUDFLARE_URL))],
            ),
            (_("Cloudflare will show two nameservers."), [example, _group("", "", self.ns_entry), _label(_("Pasting them lets Big Remote Play follow your progress."), "dim-label", "caption")]),
            (where, third),
            (_("Big Remote Play checks your domain."), [self.ns_status, _actions(_button(_("Check again"), self._check_nameservers))]),
        ]
        guide = StepGuide(steps, on_next=self._cloudflare_next, on_show=lambda index: self._check_nameservers() if index == 3 else None)
        self.cloudflare_guide = guide
        return self._page("hs-cloudflare", _("Cloudflare"), _("Set up DNS with Cloudflare"), _("Cloudflare will manage your domain's records. Its free plan is enough."), guide, step="domain")

    def _nameserver_rows(self) -> Gtk.Widget:
        self.ns_copy_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._fill_nameserver_rows()
        return self.ns_copy_box

    def _fill_nameserver_rows(self) -> None:
        rows = [_value_row(_("Nameserver"), name, self.toast) for name in self.progress.nameservers]
        self._replace(self.ns_copy_box, *([_group("", "", *rows)] if rows else []))

    def _cloudflare_next(self, index: int) -> bool:
        if index == 1:
            names = dns_check.parse_nameservers(self.ns_entry.get_text())
            if names and not all(dns_check.is_cloudflare_nameserver(name) for name in names):
                self.ns_entry.add_css_class("error")
                self.toast(_("These are not Cloudflare nameservers. They end in .ns.cloudflare.com."))
                return False
            self.ns_entry.remove_css_class("error")
            self._save(replace(self.progress, nameservers=names))
            self._fill_nameserver_rows()
        return True

    def _check_nameservers(self) -> None:
        zone = self._zone_name()
        expected = self.progress.nameservers
        self._replace(self.ns_status, _group("", "", loading_row(_("Checking nameservers…"))))

        def done(check) -> None:
            if check.state in ("cloudflare", "expected"):
                self._mark("domain")
                self._replace(
                    self.ns_status,
                    _group("", "", message_row(_("Cloudflare is now managing your domain"), zone, "brp-emblem-ok-symbolic")),
                    _actions(_button(_("Next"), lambda: self._push(self._record_page()), suggested=True)),
                )
                return
            if check.state == "missing":
                rows = [message_row(_("This domain does not exist yet."), _("Check the name you registered."), "dialog-warning-symbolic")]
            elif check.state == "error":
                rows = [message_row(_("The domain could not be checked."), _("Check your internet connection and try again."), "dialog-warning-symbolic")]
            else:
                rows = [message_row(_("Your domain still uses other nameservers."), _("This can take a little while.") + "\n" + ", ".join(check.nameservers), "brp-network-idle-symbolic")]
            self._replace(self.ns_status, _group("", "", *rows))

        self.worker.submit(lambda: self.deps.nameservers(zone, expected), done, failed=lambda _error: done(dns_check.NameserverCheck("error")))

    # ── the record ─────────────────────────────────────────────────────────
    def _record_page(self) -> Adw.NavigationPage:
        progress = self.progress
        cloudflare = progress.dns_manager == "cloudflare"
        name = record_name(progress.domain, self._zone_name())
        widgets: list[Gtk.Widget] = []
        self.server_ip_entry = None
        if not progress.server_ip:
            self.server_ip_entry = Adw.EntryRow(title=_("The server's public IP address"), use_markup=False)
            widgets.append(_group(_("Where is the server?"), _("Your VPS provider shows it in its control panel."), self.server_ip_entry))
        summary = _group(
            "",
            "",
            _value_row(_("Domain"), progress.domain, self.toast),
            _value_row(_("Points to"), progress.server_ip or "—", self.toast),
        )
        widgets.append(summary)
        record_rows = [
            _value_row(_("Type"), "A", self.toast),
            _value_row(_("Name"), name, self.toast),
            _value_row(_("IPv4 address"), progress.server_ip or "—", self.toast),
        ]
        if cloudflare:
            proxy = Adw.ActionRow(title=_("Proxy status"), subtitle=_("DNS only — the cloud must be gray."), use_markup=False)
            proxy.set_subtitle_lines(0)
            record_rows.append(proxy)
        where = _("In Cloudflare: DNS → Records → Add record.") if cloudflare else _("Add it where you manage your domain's DNS.")
        widgets.append(_group(_("Add this to Cloudflare.") if cloudflare else _("Add this record."), where, *record_rows))
        if cloudflare:
            widgets.append(_group(_("Keep Proxy status = DNS only"), _("Headscale needs a direct connection to your server. Do not use Cloudflare Tunnel for it either.")))
            widgets.append(
                self._technical(
                    [
                        _(
                            "Cloudflare's proxy (the orange cloud) only passes ordinary web requests. Tailscale clients keep a long-lived, upgraded HTTP connection to Headscale, which the proxy and Cloudflare Tunnel do not carry reliably. With DNS only, the name simply points at your server."
                        )
                    ],
                    title=_("Why?"),
                )
            )
        if progress.server_ipv6:
            widgets.append(
                _group(
                    _("Optional: IPv6"),
                    _("Add this only if your router lets connections in over IPv6. A wrong IPv6 record can break the connection."),
                    _value_row(_("Type"), "AAAA", self.toast),
                    _value_row(_("Name"), name, self.toast),
                    _value_row(_("IPv6 address"), progress.server_ipv6, self.toast),
                )
            )
        self.record_status = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        widgets.append(self.record_status)
        buttons = [_button(_("I added the record"), self._check_record, suggested=True)]
        if cloudflare:
            buttons.append(_button(_("Open Cloudflare"), lambda: self.deps.open_uri(self.navigation, CLOUDFLARE_URL)))
        widgets.append(_actions(*buttons))
        return self._page("hs-record", _("DNS"), _("Point your domain to the server"), "", *widgets, step="dns")

    def _check_record(self) -> None:
        progress = self.progress
        if self.server_ip_entry is not None:
            typed = self.server_ip_entry.get_text().strip()
            if public_address.address_kind(typed) != "public":
                self.server_ip_entry.add_css_class("error")
                self._replace(
                    self.record_status, _group("", "", message_row(_("This is not a public IP address."), _("Addresses like 192.168.x.x only work inside a home network."), "dialog-warning-symbolic"))
                )
                return
            self.server_ip_entry.remove_css_class("error")
            self._save(replace(progress, server_ip=typed))
            progress = self.progress
        domain, ipv4, ipv6 = progress.domain, progress.server_ip, progress.server_ipv6
        self._replace(self.record_status, _group("", "", loading_row(_("Checking your domain…"))))

        def work():
            a = self.deps.record(domain, ipv4, "A")
            aaaa = self.deps.record(domain, ipv6, "AAAA") if ipv6 else self.deps.record(domain, ipv4, "AAAA")
            return a, aaaa

        def done(result) -> None:
            a, aaaa = result
            # An AAAA record is optional, but a wrong one breaks IPv6 clients.
            aaaa_bad = aaaa.state in ("wrong", "proxied") and (bool(ipv6) or aaaa.found)
            if a.state == "ready" and not aaaa_bad:
                self._mark("domain", "dns")
                after = self._install_page if progress.mode == "this_computer" else (lambda: self._remote_page(prefill=progress.domain, check=True))
                self._replace(
                    self.record_status,
                    _group("", "", message_row(_("Domain is ready"), f"{domain} → {ipv4}", "brp-emblem-ok-symbolic")),
                    _actions(_button(_("Next"), lambda: self._push(after()), suggested=True)),
                )
                return
            self._replace(self.record_status, _group("", "", *self._record_problem(a, aaaa if aaaa_bad else None, ipv4)), _actions(_button(_("Check again"), self._check_record)))

        self.worker.submit(work, done, failed=lambda _error: done((dns_check.RecordCheck("error"), dns_check.RecordCheck("missing"))))

    @staticmethod
    def _record_problem(a, aaaa, expected: str) -> list[Gtk.Widget]:
        rows: list[Gtk.Widget] = []
        if a.state == "proxied":
            rows.append(message_row(_("Cloudflare's proxy is on for this record."), _("Click the orange cloud in Cloudflare so it turns gray (DNS only)."), "dialog-warning-symbolic"))
        elif a.state == "wrong":
            rows.append(
                message_row(
                    _("Your domain is not pointing to this server yet."), _("It points to {found}, not {expected}.").format(found=", ".join(a.found), expected=expected), "dialog-warning-symbolic"
                )
            )
        elif a.state == "missing":
            rows.append(message_row(_("We are still waiting for DNS"), _("This can take a little while."), "brp-network-idle-symbolic"))
        elif a.state == "error":
            rows.append(message_row(_("The domain could not be checked."), _("Check your internet connection and try again."), "dialog-warning-symbolic"))
        if aaaa is not None:
            rows.append(message_row(_("The IPv6 record points somewhere else."), _("Fix it or delete it: {found}").format(found=", ".join(aaaa.found)), "dialog-warning-symbolic"))
        return rows

    # ── this computer: install and configure ───────────────────────────────
    def _install_page(self) -> Adw.NavigationPage:
        from .dependency_installer import ComponentChecklist

        self.phase_rows: dict[str, Adw.ActionRow] = {}
        phases = _group(_("What happens"), "")
        for key, title in (
            ("install", _("Install Headscale and Caddy")),
            ("configure", _("Configure and start the server")),
            ("firewall", _("Allow web connections in the firewall")),
            ("https", _("Check the secure connection (HTTPS)")),
            ("network", _("Create your private network")),
        ):
            row = Adw.ActionRow(title=title, subtitle=_("Not yet"), use_markup=False)
            row.set_subtitle_lines(0)
            phases.add(row)
            self.phase_rows[key] = row
        self.checklist = ComponentChecklist(["headscale_server", "caddy"], on_ready=self._components_ready, install_label=_("Install and continue"), show_heading=False)
        self.setup_button = _button(_("Set up the server"), self._run_local_setup, suggested=True)
        self.setup_button.set_sensitive(False)
        self._components_ok = False
        self._ports_ok = False
        self.preflight_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.install_status = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        secure = note(_("Big Remote Play will configure HTTPS for you."), "brp-dialog-password-symbolic")
        firewall = note(_("It also allows ports 80 and 443 in this computer's firewall, when a firewall is active."), "brp-firewall-symbolic")
        address = _value_row(_("Server address"), self.progress.server_url, self.toast, subtitle=self.progress.server_url)
        details = self._technical(
            [
                "Headscale listens on this computer only (127.0.0.1, port 8080 or the first free of 18080-18089); Caddy serves it at " + self.progress.server_url + " (ports 80 and 443 TCP).",
                "Caddy obtains the HTTPS certificate from Let's Encrypt automatically.",
                "UDP 3478 (STUN/DERP) is not needed: Tailscale's public relays are used.",
                "Files: /etc/headscale/config.yaml (original kept as config.yaml.brp-backup), /etc/caddy/conf.d/big-remote-play-headscale.caddy.",
            ]
        )
        GLib.idle_add(lambda: self._preflight() and False)
        return self._page(
            "hs-install",
            _("Install Headscale"),
            _("Install Headscale"),
            _("Big Remote Play installs the server, gives it a secure address and starts it. Your password will be requested."),
            _group("", "", address),
            self.preflight_box,
            secure,
            firewall,
            self.checklist,
            phases,
            self.install_status,
            _actions(self.setup_button),
            details,
            step="https",
        )

    def _components_ready(self) -> None:
        self._phase("install", "done")
        self._components_ok = True
        self._sync_setup_button()
        if getattr(self.checklist, "installed_here", False) and self._ports_ok:
            self._run_local_setup()  # installed just now: go on without another click

    def _sync_setup_button(self) -> None:
        self.setup_button.set_sensitive(self._components_ok and self._ports_ok)

    def _preflight(self) -> None:
        """Before installing anything: are the web ports free, and is a server already there?"""
        url = self.progress.server_url
        self._ports_ok = False
        self._sync_setup_button()
        self._replace(self.preflight_box, _group("", "", loading_row(_("Checking this computer…"))))

        def work():
            busy = self.deps.web_ports()
            existing = self.deps.check_server(url, "") if url else None
            return busy, existing

        def done(result) -> None:
            busy, existing = result
            if existing is not None and existing.healthy:
                # The address already leads to a working Headscale: use it instead.
                self._replace(
                    self.preflight_box,
                    _group(_("A Headscale server already answers at this address."), url),
                    _actions(_button(_("Use this server"), self._use_existing, suggested=True)),
                )
                return
            if busy:
                self._replace(
                    self.preflight_box,
                    _group(
                        _("Another program already uses the web ports (80 and 443) on this computer."),
                        _("Big Remote Play cannot add a secure address next to it. Use another server, or stop that program first."),
                    ),
                    _actions(_button(_("Use another server"), self.choose_another_server, suggested=True), _button(_("Check again"), self._preflight)),
                    self._technical([f"busy TCP ports: {', '.join(map(str, busy))}"]),
                )
                return
            self._replace(self.preflight_box)
            self._ports_ok = True
            self._sync_setup_button()

        self.worker.submit(work, done, failed=lambda _error: done(((), None)))

    def _use_existing(self) -> None:
        self._save(replace(self.progress, mode="another_server"))
        self._mark("server", "https", "headscale")
        self._push(self._this_computer_page())

    def _phase(self, key: str, state: str, detail: str = "") -> None:
        row = getattr(self, "phase_rows", {}).get(key)
        if row is None:
            return
        words = {"working": _("Working…"), "done": _("Done"), "failed": _("Did not work"), "waiting": _("Not yet")}
        row.set_subtitle(detail or words.get(state, ""))

    def _run_local_setup(self) -> None:
        host = self.progress.domain or self.progress.server_ip
        url = self.progress.server_url
        if not host:
            return
        self.setup_button.set_sensitive(False)
        self._replace(self.install_status)
        self._phase("configure", "working")
        save_listen = self._later_value(lambda listen: self._save(replace(self.progress, local_listen=listen)))

        def work():
            configured = self.local.run("configure", host)
            if not configured.ok:
                return "configure", configured, None
            listen = configured.data.get("LISTEN", "")
            if listen.startswith("127.0.0.1:"):
                save_listen(listen)
            self._later(lambda: (self._phase("configure", "done"), self._phase("firewall", "working")))()
            opened = self.local.run("firewall")  # the HTTPS check says whether it is reachable
            self._later(lambda: (self._phase("firewall", "done" if opened.ok else "failed"), self._phase("https", "working")))()
            deadline = self.deps.clock() + HTTPS_WAIT_SECONDS
            while True:
                check = self.deps.check_server(url, "127.0.0.1")
                if check.healthy or self.deps.clock() >= deadline:
                    return ("ok" if check.healthy else "https"), configured, check
                self.deps.sleep(3)

        self.worker.submit(work, self._local_setup_done, failed=lambda error: self._local_setup_done(("configure", None, None)))

    def _local_setup_done(self, result) -> None:
        stage, helper, check = result
        self._sync_setup_button()
        if stage == "configure":
            self._phase("configure", "failed")
            self._replace(self.install_status, _group("", "", self._helper_problem(helper)))
            return
        self.last_check = check
        if stage == "https":
            self._phase("https", "failed")
            self._replace(self.install_status, *self._router_help(check))
            return
        self._phase("https", "done")
        self._mark("https")
        self._create_network()

    def _helper_problem(self, helper) -> Gtk.Widget:
        result = getattr(helper, "result", "error")
        words = {
            "not_available": (_("This needs the installed Big Remote Play package."), _("The server setup is not available when Big Remote Play runs from its source folder.")),
            "not_authorized": (_("The password was not given."), _("Nothing was changed. Choose Set up the server to try again.")),
            "not_installed": (_("Headscale or Caddy is not installed."), _("Install them above first.")),
            "other_server": (_("Headscale on this computer is already set up for another address."), _("Nothing was changed. See Advanced on the Headscale page.")),
            "custom_listen": (_("Headscale on this computer has its own settings."), _("Nothing was changed. See Advanced on the Headscale page.")),
            "caddy_conflict": (_("Caddy already has a site with the same file name."), _("Nothing was changed.")),
            "hosts_conflict": (_("This name is already set in /etc/hosts."), _("Nothing was changed.")),
            "ports_busy": (
                _("Another program already uses the web ports (80 and 443) on this computer."),
                _("Big Remote Play cannot add a secure address next to it. Use another server, or stop that program first."),
            ),
            "caddy_invalid": (_("The HTTPS configuration was not accepted."), _("Nothing was enabled. See the technical details.")),
            "not_running": (_("Headscale did not start."), _("See its log in Advanced on the Headscale page.")),
        }
        title, subtitle = words.get(result, (_("The server could not be set up."), _("See the technical details.")))
        row = message_row(title, subtitle, "dialog-warning-symbolic")
        lines = list(getattr(helper, "lines", ()) or ())
        if lines:
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            box.append(_group("", "", row))
            box.append(self._technical(lines))
            return box
        return row

    def _router_help(self, check: ServerCheck | None) -> list[Gtk.Widget]:
        report = self.report
        widgets: list[Gtk.Widget] = []
        if report is not None and report.cgnat:
            widgets.append(
                _group(
                    _("Your internet provider blocks incoming connections."),
                    _("Changing the router will probably not help. A VPS is the reliable way."),
                    message_row(_("The secure certificate could not be obtained."), _("The internet could not reach this computer."), "dialog-warning-symbolic"),
                )
            )
            widgets.append(_actions(_button(_("Use a VPS instead"), self.choose_another_server, suggested=True), _button(_("Check again"), self._run_local_setup)))
        else:
            local = ", ".join(report.local_ipv4) if report is not None and report.local_ipv4 else _("this computer")
            widgets.append(
                _group(
                    _("Your router needs one more step"),
                    _("Ask your router to send web connections to this computer:"),
                    message_row(
                        _("Open your router's settings page"),
                        _("Usually its address is written under the router. Look for “Port forwarding”, “Virtual server” or “NAT”."),
                        "brp-network-server-symbolic",
                    ),
                    message_row(_("Add two rules"), _("Ports 80 and 443 (TCP) → {address}").format(address=local), "brp-address-symbolic"),
                    message_row(_("Come back and check again"), _("Big Remote Play checks it for you."), "brp-view-refresh-symbolic"),
                )
            )
            widgets.append(_actions(_button(_("Check again"), self._run_local_setup, suggested=True), _button(_("Use another server"), self.choose_another_server)))
        if check is not None:
            widgets.append(self._technical(list(check.details)))
        return widgets

    def _create_network(self) -> None:
        self._phase("network", "working")
        name = self.progress.user or default_user_name()
        origin = self.progress.server_url

        def work():
            from big_remote_play.private_network.credentials import CredentialKind
            from big_remote_play.private_network.headscale_api import HeadscaleApi

            user = self.local.run("create-user", name)
            if not user.ok:
                return False, user
            key = self.local.run("create-apikey")
            if not key.ok or not key.secret:
                return False, key
            service = self.service_factory()
            service.credentials.save(CredentialKind.HEADSCALE_API_KEY, key.secret, scope=origin)
            test = HeadscaleApi(self.progress.local_api, key.secret).test()
            return test.ok, user

        def done(result) -> None:
            ok, helper = result
            if not ok:
                self._phase("network", "failed")
                self._replace(self.install_status, _group("", "", self._helper_problem(helper)))
                return
            self._phase("network", "done")
            self._save(replace(self.progress, user=name).mark("headscale"))
            self._replace(
                self.install_status,
                _group("", "", message_row(_("Your Headscale server is ready"), self.progress.server_url, "brp-emblem-ok-symbolic")),
                _actions(_button(_("Next"), lambda: self._push(self._this_computer_page()), suggested=True)),
            )

        self.worker.submit(work, done, failed=lambda _error: done((False, None)))

    # ── another server ─────────────────────────────────────────────────────
    def _remote_page(self, *, prefill: str = "", check: bool = False) -> Adw.NavigationPage:
        self.remote_entry = Adw.EntryRow(title=_("Server IP or domain"), use_markup=False)
        self.remote_entry.set_text(prefill or self.progress.server_url)
        self.remote_entry.connect("entry-activated", lambda _row: self.check_remote())
        self.remote_status = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        page = self._page(
            "hs-remote",
            _("Another server"),
            _("What is the server address?"),
            _("The address of the computer or VPS that runs Headscale."),
            _group("", "", self.remote_entry),
            _label(_("Example: headscale.example.com or 203.0.113.10"), "dim-label", "caption"),
            _actions(_button(_("Check server"), self.check_remote, suggested=True)),
            self.remote_status,
            step="server",
        )
        if check:
            GLib.idle_add(lambda: self.check_remote() and False)
        return page

    def check_remote(self) -> None:
        text = self.remote_entry.get_text().strip()
        try:
            address = parse_server_address(text)
        except ValueError:
            self.remote_entry.add_css_class("error")
            sentence, detail = server_problem_words(ServerCheck(None, problem="invalid"))
            self._replace(self.remote_status, _group("", "", message_row(sentence, detail, "dialog-warning-symbolic")))
            return
        self.remote_entry.remove_css_class("error")
        self._replace(self.remote_status, _group("", "", loading_row(_("Checking the server…"))))

        def work():
            check = self.deps.check_server(address.url, "")
            if check.healthy or check.problem in ("invalid", "dns"):
                return check, False
            # A name that leads to this very computer is checked directly: a
            # router without hairpin NAT answers this computer's own public
            # address itself (with its own certificate) instead of forwarding.
            targets = set(check.resolved) | ({address.host} if address.is_ip else set())
            if not targets & self._addresses_of_this_computer():
                return check, False
            return self.deps.check_server(address.url, "127.0.0.1"), True

        self.worker.submit(work, lambda result: self._show_remote(address, *result), failed=lambda _error: self._show_remote(address, ServerCheck(address, problem="error")))

    def _addresses_of_this_computer(self) -> set[str]:
        try:
            report = self.deps.detect(False)
        except Exception:
            return set()
        if report is None:
            return set()
        return {value for value in (report.ipv4, report.ipv6, *report.local_ipv4, *report.local_ipv6) if value}

    def _show_remote(self, address, check: ServerCheck, here: bool = False) -> None:
        self.last_check = check
        rows: list[Gtk.Widget] = []
        for value, title in (
            ("" if address.is_ip else check.dns, _("Name found")),
            (check.port, _("Server answers")),
            (check.https, _("Secure connection (HTTPS)")),
            (check.headscale, _("Headscale answers")),
        ):
            if not value:
                continue
            word = {"ok": _("Yes"), "failed": _("No"), "skipped": _("Not checked")}.get(value, "")
            icon = {"ok": "brp-emblem-ok-symbolic", "failed": "dialog-warning-symbolic"}.get(value, "brp-media-record-symbolic")
            rows.append(message_row(title, word, icon))
        widgets: list[Gtk.Widget] = []
        progress = replace(
            self.progress, mode="another_server", address_kind="ip" if address.is_ip else "domain", domain="" if address.is_ip else address.host, on_this_computer=here and check.healthy
        )
        if address.is_ip:
            progress = replace(progress, server_ip=address.host)
        self._save(progress)
        if check.healthy:
            steps = ["server", "https", "headscale"] + ([] if address.is_ip else ["domain", "dns"])
            self._mark(*steps)
            version = _("Headscale {version}").format(version=check.version) if check.version else ""
            widgets.append(_group(_("Server found"), version, *rows))
            if here:
                widgets.append(
                    note(
                        _(
                            "This server runs on this computer. Your router does not send this computer's own traffic back to it, so Big Remote Play checked it directly and will connect to it directly too."
                        ),
                        "brp-computer-symbolic",
                    )
                )
            on_next = self._pin_and_continue if here and not address.is_ip else (lambda: self._push(self._this_computer_page()))
            widgets.append(_actions(_button(_("Next"), on_next, suggested=True)))
        else:
            sentence, detail = server_problem_words(check)
            widgets.append(_group(sentence, detail, *rows))
            buttons = []
            if check.problem == "dns" or (address.is_ip and check.problem in ("tls_invalid", "tls_failed")):
                buttons.append(_button(_("Set up a domain for it"), self._remote_domain, suggested=True))
            buttons.append(_button(_("Guided server setup"), lambda: self._push(self._guided_server_page())))
            buttons.append(_button(_("Check again"), self.check_remote))
            buttons.append(_button(_("Use a different address"), self._different_address))
            buttons.append(_button(_("Start over"), self._confirm_restart))
            widgets.append(_actions(*buttons))
        widgets.append(self._technical(list(check.details)))
        self._replace(self.remote_status, *widgets)

    def _pin_and_continue(self) -> None:
        """Point the server's name at this computer (127.0.0.1), then go on.

        The Tailscale app and the API calls use the public name; without this
        line the router would answer them itself.
        """
        host = self.progress.domain

        def done(result) -> None:
            if getattr(result, "ok", False):
                self._push(self._this_computer_page())
            else:
                self._replace(self.remote_status, _group("", "", self._helper_problem(result)))

        self.worker.submit(lambda: self.local.run("hosts-pin", host), done, failed=lambda _error: done(None))

    def _different_address(self) -> None:
        self.remote_entry.set_text("")
        self._replace(self.remote_status)
        self.remote_entry.grab_focus()

    def _remote_domain(self) -> None:
        self._save(replace(self.progress, address_kind="domain"))
        self._push(self._have_domain_page())

    def _guided_server_page(self) -> Adw.NavigationPage:
        host = self.progress.domain or self.progress.server_ip or "headscale.example.com"
        url = f"https://{host}"
        user = self.progress.user or DEFAULT_USER
        steps = [
            (_("Use a Linux server that stays on"), _("A small VPS is enough. Debian, Ubuntu and Arch are fine."), []),
            (_("Point a domain to it"), _("Recommended. Big Remote Play can guide you."), []),
            (
                _("Install Headscale and Caddy"),
                _("On Arch Linux:"),
                [("sudo pacman -S headscale caddy", _("Command"))],
            ),
            (
                _("Or on Debian and Ubuntu"),
                _("Download the .deb for your server from Headscale's releases page, then install it and Caddy."),
                [("sudo apt install ./headscale_*.deb caddy", _("Command"))],
            ),
            (_("Set the server address"), "", [(f"sudo sed -i 's|^server_url:.*|server_url: {url}|' /etc/headscale/config.yaml", _("Command"))]),
            (
                _("Turn on HTTPS with Caddy"),
                _("On a new server this replaces Caddy's example page."),
                [(f"printf '{host} {{\\n\\treverse_proxy 127.0.0.1:8080\\n}}\\n' | sudo tee /etc/caddy/Caddyfile", _("Command"))],
            ),
            (
                _("Start both and open the ports"),
                _("Allow ports 80 and 443 (TCP) in the server's firewall and in your VPS provider's panel."),
                [("sudo systemctl enable --now headscale caddy && sudo systemctl restart caddy", _("Command"))],
            ),
            (
                _("Create your network and an API key"),
                _("Keep the API key: Big Remote Play asks for it in the next step. It manages the server; never give it to a guest."),
                [(f"sudo headscale users create {user}", _("Command")), ("sudo headscale apikeys create --expiration 90d", _("Command"))],
            ),
        ]
        groups: list[Gtk.Widget] = []
        for index, (title, text, commands) in enumerate(steps, start=1):
            rows = [copy_row(label, command, icon="brp-text-x-generic-symbolic", toast=self.toast) for command, label in commands]
            groups.append(_group(f"{index}. {title}", text, *rows))
            if index == 2:
                groups.append(_actions(_button(_("Set up a domain"), self._remote_domain)))
        open_releases = _button(_("Open Headscale releases"), lambda: self.deps.open_uri(self.navigation, HEADSCALE_RELEASES))
        back = _button(_("Check server"), self._guided_check, suggested=True)
        return self._page(
            "hs-guided-server",
            _("Set up the server"),
            _("Set up the server"),
            _("Big Remote Play cannot reach into another computer. Follow these steps there; each one can be copied."),
            *groups,
            _actions(back, open_releases),
            step="headscale",
        )

    def _guided_check(self) -> None:
        page = self.pages.get("hs-remote")
        if page is not None and in_stack(self.navigation, page):
            self.navigation.pop_to_page(page)
            self.check_remote()
        else:
            self._push(self._remote_page(prefill=self.progress.server_url, check=True))

    # ── add this computer ──────────────────────────────────────────────────
    def _api_key(self) -> str:
        from big_remote_play.private_network.credentials import CredentialKind

        try:
            return self.service_factory().credentials.secret(CredentialKind.HEADSCALE_API_KEY, self.progress.server_url) or ""
        except Exception:
            return ""

    def _this_computer_page(self) -> Adw.NavigationPage:
        self.this_status = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        widgets: list[Gtk.Widget] = [_group("", "", _value_row(_("Server"), self.progress.server_url, self.toast))]
        self.api_entry = Adw.PasswordEntryRow(title=_("API key"))
        self.key_entry = Adw.PasswordEntryRow(title=_("Key for this computer"))
        local = self.progress.mode == "this_computer"
        if local:
            widgets.append(_actions(_button(_("Add this computer"), lambda: self.add_this_computer(), suggested=True)))
        else:
            widgets.append(_label(_("How do you want to add this computer?"), "title-3"))
            widgets.append(
                _group(
                    _("I manage this server"),
                    _("Paste the server's API key. Big Remote Play then does the rest. It is kept in the system keyring."),
                    self.api_entry,
                )
            )
            widgets.append(_actions(_button(_("Use the API key"), self._use_api_key, suggested=True)))
            widgets.append(_group(_("I have a key from the server owner"), _("A key that adds one computer. It is used once and not saved."), self.key_entry))
            widgets.append(_actions(_button(_("Use this key"), self._use_preauth_key)))
            widgets.append(_group(_("Ask the server owner to approve"), _("This computer asks to join; the owner approves it on the server.")))
            widgets.append(_actions(_button(_("Ask to join"), self._ask_owner)))
        widgets.append(self.this_status)
        return self._page("hs-this-computer", _("This computer"), _("Add this computer"), _("This computer joins your Headscale network."), *widgets, step="this_computer")

    def _use_api_key(self) -> None:
        from big_remote_play.private_network.credentials import CredentialError, CredentialKind

        text = self.api_entry.get_text().strip()
        if preauth_key_kind(text) == "preauth":
            self._replace(self.this_status, _group("", "", message_row(_("This key adds one computer; it is not an API key."), _("Use it in the second option instead."), "dialog-warning-symbolic")))
            return
        origin = self.progress.server_url

        def work():
            from big_remote_play.private_network.headscale_api import HeadscaleApi

            service = self.service_factory()
            service.credentials.save(CredentialKind.HEADSCALE_API_KEY, text, scope=origin)
            return HeadscaleApi(origin, text).test()

        def done(result) -> None:
            self.api_entry.set_text("")
            if getattr(result, "ok", False):
                self.add_this_computer()
            else:
                self._replace(self.this_status, _group("", "", message_row(_("The server did not accept this API key."), _("Check that you copied all of it."), "dialog-warning-symbolic")))

        def failed(error) -> None:
            self.api_entry.set_text("")
            reason = getattr(error, "reason", "")
            sentence = _("This is not an API key.") if isinstance(error, CredentialError) or reason else _("The API key could not be saved.")
            self._replace(self.this_status, _group("", "", message_row(sentence, "", "dialog-warning-symbolic")))

        self.worker.submit(work, done, failed=failed)

    def _use_preauth_key(self) -> None:
        text = self.key_entry.get_text().strip()
        kind = preauth_key_kind(text)
        if kind == "api":
            self._replace(
                self.this_status, _group("", "", message_row(_("This is an administrator key."), _("Use it in the first option, or ask for a key that adds one computer."), "dialog-warning-symbolic"))
            )
            return
        if kind != "preauth":
            self._replace(self.this_status, _group("", "", message_row(_("This does not look like a key that adds a computer."), "", "dialog-warning-symbolic")))
            return
        self.key_entry.set_text("")
        self._connect(auth_key=text)

    def _ask_owner(self) -> None:
        self._connect(auth_key="", ask=True)

    def add_this_computer(self) -> None:
        """Create a one-time key with the API key, then join with it."""
        key = self._api_key()
        if not key:
            self._replace(self.this_status, _group("", "", message_row(_("No API key for this server yet."), _("Choose one of the options above."), "dialog-warning-symbolic")))
            return
        local = self.progress.mode == "this_computer"
        origin = self.progress.local_api if local else self.progress.server_url
        name = self.progress.user or default_user_name()

        def work():
            from big_remote_play.private_network.headscale_api import HeadscaleApi

            api = HeadscaleApi(origin, key)
            users, result = api.users()
            if not result.ok:
                return "", result
            user = next((item for item in users if item.name == name), None)
            if user is None:
                user, result = api.create_user(name)
                if user is None:
                    return "", result
            preauth, result = api.create_preauth_key(user.user_id, hours=1)
            return preauth, result

        def done(result) -> None:
            preauth, _api = result
            if not preauth:
                self._replace(self.this_status, _group("", "", message_row(_("The server did not give a key for this computer."), _("Check the API key in Advanced."), "dialog-warning-symbolic")))
                return
            self._save(replace(self.progress, user=name))
            self._connect(auth_key=preauth)

        self._replace(self.this_status, _group("", "", loading_row(_("Adding this computer…"))))
        self.worker.submit(work, done, failed=lambda _error: done(("", None)))

    def _connect(self, *, auth_key: str, ask: bool = False) -> None:
        """Join the Headscale network, switching away from Tailscale after asking."""

        def go() -> None:
            self._join(auth_key=auth_key, ask=ask)

        def check_other():
            service = self.service_factory()
            return service.status(ProviderId.TAILSCALE).connected

        def decided(tailscale_active: bool) -> None:
            if not tailscale_active:
                go()
                return
            dialog = Adw.AlertDialog(
                heading=_("Switch from Tailscale to Headscale?"),
                body=_("Only one Tailscale-based network can be active on this computer at a time. Tailscale stays saved and can be turned on again later. ZeroTier is not affected."),
            )
            dialog.set_body_use_markup(False)
            dialog.add_response("cancel", _("Cancel"))
            dialog.add_response("switch", _("Switch"))
            dialog.set_response_appearance("switch", Adw.ResponseAppearance.SUGGESTED)
            dialog.set_close_response("cancel")
            dialog.connect("response", lambda _dialog, response: go() if response == "switch" else self._replace(self.this_status))
            dialog.present(self.navigation)
            self.switch_dialog = dialog

        self._replace(self.this_status, _group("", "", loading_row(_("Adding this computer…"))))
        self.worker.submit(check_other, decided, failed=lambda _error: go())

    def _join(self, *, auth_key: str, ask: bool) -> None:
        server = self.progress.server_url
        on_url = self._later_value(self._show_approval) if ask else None

        def work():
            service = self.service_factory()
            manager = service.manager
            try:
                profiles = manager.list_tailscale_profiles().profiles
            except Exception:
                profiles = ()
            connection = manager.connect_tailscale(login_server=server, auth_key=auth_key, add_account=bool(profiles), on_auth_url=on_url)
            if connection.connected:
                try:
                    selected = manager.list_tailscale_profiles().selected
                    if selected is not None:
                        manager.set_tailscale_metadata(selected.profile_id, provider="headscale", login_server=server)
                except Exception:
                    pass
            return service.status(ProviderId.HEADSCALE).connected

        def done(connected: bool) -> None:
            if connected:
                self._mark("this_computer")
                self._replace(
                    self.this_status,
                    _group("", "", message_row(_("This computer is connected"), server, "brp-emblem-ok-symbolic")),
                    _actions(_button(_("Next"), lambda: self._push(self._another_device_page()), suggested=True)),
                )
                changed = getattr(self.window, "_refresh_private_network_status", None)
                if callable(changed):
                    changed()
                return
            self._replace(self.this_status, _group("", "", message_row(_("This computer could not join."), _("Try again, or choose another option."), "dialog-warning-symbolic")))

        self.worker.submit(work, done, failed=lambda _error: done(False))

    def _later_value(self, callback: Callable[[str], object]) -> Callable[[str], None]:
        generation = self.worker.generation

        def call(value: str) -> None:
            def run() -> bool:
                if not self.worker._closed and generation <= self.worker.generation:
                    callback(value)
                return False

            GLib.idle_add(run)

        return call

    def _show_approval(self, url: str) -> None:
        from big_remote_play.private_network.headscale_api import registration_key

        key = registration_key(url)
        user = self.progress.user or "USER"
        rows: list[Gtk.Widget] = [message_row(_("Waiting for the server owner"), _("Send them this command. This page continues by itself once they run it."), "brp-network-idle-symbolic")]
        if key:
            rows.append(copy_row(_("Command for the server owner"), f"headscale auth register --auth-id {key} --user {user}", icon="brp-text-x-generic-symbolic", toast=self.toast))
        rows.append(copy_row(_("Sign-in link"), url, toast=self.toast))
        self._replace(self.this_status, _group("", "", *rows))

    # ── add another device ─────────────────────────────────────────────────
    def _another_device_page(self) -> Adw.NavigationPage:
        server = self.progress.server_url
        steps = _group(
            _("On the other computer"),
            "",
            message_row(_("1. Install Big Remote Play"), _("Or the Tailscale app on a phone."), "brp-computer-symbolic"),
            message_row(_("2. Open Connect your devices"), "", "brp-network-private-symbolic"),
            message_row(_("3. Choose Headscale"), _("Then Set up → Another server."), "brp-headscale-symbolic"),
            message_row(_("4. Use this server"), server, "brp-network-server-symbolic"),
        )
        copy = _button(_("Copy address"), lambda: self._copy(server), suggested=True)
        self.key_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.device_status = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        widgets: list[Gtk.Widget] = [steps, _actions(copy)]
        if self._api_key():
            widgets.append(
                _group(
                    _("A key for the other computer"),
                    _("It adds one computer and stops working after one hour. It is not an administrator key."),
                )
            )
            widgets.append(_actions(_button(_("Create a one-time key"), self._create_device_key)))
            widgets.append(self.key_box)
        if self._api_key():
            from .network_devices import ApprovalGroup

            origin = self.progress.local_api if self.progress.mode == "this_computer" else self.progress.server_url
            key = self._api_key()

            def api():
                from big_remote_play.private_network.headscale_api import HeadscaleApi

                return HeadscaleApi(origin, key)

            def approved(name: str) -> None:
                self._mark("another_device")

            self.approval = ApprovalGroup(api, preferred_user=self.progress.user, on_done=approved)
            widgets.append(self.approval)
        widgets.append(self.device_status)
        widgets.append(_actions(_button(_("Check for the other device"), self._check_devices), _button(_("Finish"), self.finish, suggested=True)))
        return self._page("hs-another-device", _("Add another device"), _("Add another device"), "", *widgets, step="another_device")

    def _copy(self, text: str) -> None:
        from .network_common import copy_to_clipboard

        copy_to_clipboard(self.navigation, text, self.toast)

    def _create_device_key(self) -> None:
        key = self._api_key()
        origin = self.progress.local_api if self.progress.mode == "this_computer" else self.progress.server_url
        name = self.progress.user or default_user_name()

        def work():
            from big_remote_play.private_network.headscale_api import HeadscaleApi

            api = HeadscaleApi(origin, key)
            users, result = api.users()
            user = next((item for item in users if item.name == name), None) if result.ok else None
            if user is None:
                return ""
            preauth, _result = api.create_preauth_key(user.user_id, hours=1)
            return preauth

        def done(preauth: str) -> None:
            if not preauth:
                self._replace(self.key_box, _group("", "", message_row(_("The server did not give a key."), _("Check the API key in Advanced."), "dialog-warning-symbolic")))
                return
            row = copy_row(_("One-time key"), preauth, icon="brp-dialog-password-symbolic", toast=self.toast, subtitle=_("Hidden. Use Copy."))
            self._replace(self.key_box, _group(_("Key created"), _("It is shown only now and is not stored."), row))

        self.worker.submit(work, done, failed=lambda _error: done(""))

    def _check_devices(self) -> None:
        def work():
            return len(self.service_factory().status(ProviderId.HEADSCALE).peers)

        def done(count: int) -> None:
            if count > 0:
                self._mark("another_device")
                self._replace(self.device_status, _group("", "", message_row(_("Your other device is connected"), "", "brp-emblem-ok-symbolic")))
            else:
                self._replace(self.device_status, _group("", "", message_row(_("No other device yet"), _("It appears here once it joins."), "brp-network-idle-symbolic")))

        self.worker.submit(work, done, failed=lambda _error: done(0))

    def finish(self) -> None:
        """Back to the Headscale page, which now shows the connection."""
        previous = self.navigation.get_previous_page(self.root) if self.root is not None and in_stack(self.navigation, self.root) else None
        if previous is not None:
            self.navigation.pop_to_page(previous)
        self.on_finished()


class StepGuide(Gtk.Box):
    """“Step 1 of 4” inside one page: one instruction at a time, Back and Next."""

    __gtype_name__ = "BrpStepGuide"

    def __init__(self, steps: list[tuple[str, list[Gtk.Widget]]], *, on_next: Callable[[int], bool] | None = None, on_show: Callable[[int], object] | None = None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.add_css_class("card")
        self.add_css_class("padded")
        self.steps = steps
        self.on_next = on_next or (lambda _index: True)
        self.on_show = on_show or (lambda _index: None)
        self.index = 0
        self.counter = _label("", "dim-label", "caption-heading")
        self.text = _label("", "title-4")
        self.text.set_accessible_role(Gtk.AccessibleRole.HEADING)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, vhomogeneous=False)
        for number, (_text, widgets) in enumerate(steps):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            for widget in widgets:
                if isinstance(widget, Gtk.Button) and widget.get_parent() is None:
                    widget = _actions(widget)
                box.append(widget)
            self.stack.add_named(box, str(number))
        self.back = Gtk.Button(label=_("Back"))
        self.back.connect("clicked", lambda _button: self.show(self.index - 1))
        self.next = Gtk.Button(label=_("Next"))
        self.next.add_css_class("suggested-action")
        self.next.connect("clicked", lambda _button: self.advance())
        buttons = Gtk.Box(spacing=12, halign=Gtk.Align.END)
        buttons.append(self.back)
        buttons.append(self.next)
        for widget in (self.counter, self.text, self.stack, buttons):
            self.append(widget)
        self.show(0)

    def advance(self) -> None:
        if self.on_next(self.index):
            self.show(self.index + 1)

    def show(self, index: int) -> None:
        index = max(0, min(index, len(self.steps) - 1))
        self.index = index
        self.counter.set_label(_("Step {number} of {total}").format(number=index + 1, total=len(self.steps)))
        self.text.set_label(self.steps[index][0])
        self.stack.set_visible_child_name(str(index))
        self.back.set_sensitive(index > 0)
        self.next.set_visible(index < len(self.steps) - 1)
        self.on_show(index)


__all__ = ["HeadscaleWizard", "StepGuide", "record_name", "server_problem_words", "step_name"]
