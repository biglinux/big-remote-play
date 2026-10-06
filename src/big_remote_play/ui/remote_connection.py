"""**Connect your devices**: the first page of playing over the internet.

Three cards — Tailscale, ZeroTier, Headscale — always shown together. Each is
a small panel of its own provider only: a switch that really turns that
connection on or off, the state in words, its network (or server), this
computer's address there and how many devices are online. The card's body
opens that method's page (:class:`ProviderPage`) on this area's navigation.

Methods are independent, with one real exception: Tailscale and Headscale
share the Tailscale app on this computer, so turning one on turns the other
off, after asking. Reading runs on a :class:`Worker`; results after the page
was left, or for a card whose switch is still working, are dropped.
"""

from __future__ import annotations

from collections.abc import Callable
import time
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk, Pango  # type: ignore

from big_remote_play.private_network.cards import CardSummary
from big_remote_play.private_network.device_list import SimpleState
from big_remote_play.private_network.models import ProviderId
from big_remote_play.private_network.plan import ConnectionPlan, PlanKind
from big_remote_play.utils.i18n import _, ngettext, pgettext
from big_remote_play.utils.icons import create_icon_widget

from .components import action_row, icon_tile, intro

if TYPE_CHECKING:
    from big_remote_play.private_network.service import PrivateNetworkService

from .network_common import PROVIDER_ICONS, Worker
from .provider_page import method_summary, method_tag, state_badge, state_text

METHOD_ORDER = (ProviderId.TAILSCALE, ProviderId.ZEROTIER, ProviderId.HEADSCALE)


def plan_words(plan: ConnectionPlan) -> tuple[str, str, str, str, str]:
    """``(icon, tone, title, body, action)`` for the status card; ``action`` may be ``""``."""
    kind = plan.kind
    if kind is PlanKind.CHECKING:
        return "brp-network-idle-symbolic", "offline", _("Checking this computer…"), _("Looking for a connection that already works."), ""
    if kind is PlanKind.OFFLINE:
        return "brp-network-offline-symbolic", "attention", _("This computer is not connected to the internet"), _("Connect to Wi-Fi or a network cable, then check again."), _("Check again")
    if kind is PlanKind.READY:
        count = plan.online_devices
        body = _("Your other devices can now connect to this computer.") if count == 0 else _("Devices online now: {count}").format(count=count)
        return "brp-emblem-ok-symbolic", "online", _("Ready to play over the internet"), body, _("Add a device or invite a player")
    if kind is PlanKind.WAITING:
        return "brp-network-idle-symbolic", "attention", _("Waiting for approval"), _("The owner of the network needs to allow this computer. Then it connects by itself."), _("Check again")
    if kind is PlanKind.TURNED_OFF:
        return "brp-network-offline-symbolic", "offline", _("Your secure connection is turned off"), _("This computer is already set up. Turn it on to play over the internet."), _("Turn on")
    if kind is PlanKind.SERVICE_STOPPED:
        return "dialog-warning-symbolic", "attention", _("The connection service is stopped"), _("Big Remote Play can start it for you. Your password may be requested."), _("Fix")
    if kind is PlanKind.ALLOW:
        return "dialog-warning-symbolic", "attention", _("Big Remote Play needs your permission"), _("Allow it once to see and use your networks. Your password may be requested."), _("Allow")
    if kind is PlanKind.SIGN_IN:
        return "brp-accounts-symbolic", "attention", _("Sign in to connect this computer"), _("Your browser opens. Use the same account on all your devices."), _("Sign in")
    if kind is PlanKind.INSTALL:
        return "brp-network-private-symbolic", "offline", _("One component is needed"), _("Big Remote Play installs it for you. Your password may be requested."), _("Install")
    if kind is PlanKind.PROBLEM:
        return "dialog-warning-symbolic", "attention", _("We need to fix a setting"), _("Open the connection details to see what is wrong."), _("See details")
    return "brp-network-private-symbolic", "offline", _("Set up a secure connection"), _("It takes about a minute. Then add your other devices."), _("Set up")


def when_text(timestamp: float | None, *, now: float | None = None) -> str:
    """“Today at 20:42”, “Yesterday at 20:42” or a local date."""
    if not timestamp:
        return ""
    moment = GLib.DateTime.new_from_unix_local(int(timestamp))
    today = GLib.DateTime.new_from_unix_local(int(now if now is not None else time.time()))
    clock = moment.format("%H:%M") or ""
    days = (
        GLib.DateTime.new_local(today.get_year(), today.get_month(), today.get_day_of_month(), 0, 0, 0).to_unix()
        - GLib.DateTime.new_local(moment.get_year(), moment.get_month(), moment.get_day_of_month(), 0, 0, 0).to_unix()
    ) // 86400
    if days == 0:
        return _("Today at {time}").format(time=clock)
    if days == 1:
        return _("Yesterday at {time}").format(time=clock)
    return moment.format("%x") or ""


class RemoteConnectionPage(Gtk.ScrolledWindow):
    """The **Connect your devices** page: one card per connection method."""

    __gtype_name__ = "BrpRemoteConnectionPage"

    REFRESH_SECONDS = 10

    def __init__(self, main_window, *, service_factory: "Callable[[], PrivateNetworkService] | None" = None) -> None:
        super().__init__(vexpand=True)
        from big_remote_play.private_network.service import default_service

        self.main_window = main_window
        self.service_factory = service_factory or default_service
        self._summaries: dict[ProviderId, CardSummary] = {}
        self._status_worker = Worker()
        self._switch_workers: dict[ProviderId, Worker] = {}
        self._timer = 0
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._build()
        self._render()
        self.connect("map", lambda *_args: self._on_map())
        self.connect("unmap", lambda *_args: self._stop_timer())
        self.connect("unrealize", lambda *_args: self._close_workers())

    # ── layout ──────────────────────────────────────────────────────────────
    def _build(self) -> None:
        clamp = Adw.Clamp(maximum_size=1100, tightening_threshold=600)
        for edge in ("top", "bottom", "start", "end"):
            getattr(clamp, f"set_margin_{edge}")(24)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        box.append(intro(_("Connect your devices"), _("Connect your computers so Big Remote Play works even when they are in different places."), "brp-network-private-symbolic"))

        # Side by side when there is room, one under the other when narrow.
        self.cards_box = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True, max_children_per_line=3, min_children_per_line=1, column_spacing=16, row_spacing=16)
        self.cards_box.update_property([Gtk.AccessibleProperty.LABEL], [_("Connection methods")])
        self.cards: dict[ProviderId, ProviderCard] = {}
        for provider in METHOD_ORDER:
            card = ProviderCard(provider, on_open=lambda p=provider: self.open(p), on_switch=lambda wanted, p=provider: self.switch(p, wanted))
            self.cards[provider] = card
            self.cards_box.append(card)
            card.get_parent().set_focusable(False)  # the switch and the body are the tab stops
        box.append(self.cards_box)

        advanced = Adw.PreferencesGroup(title=pgettext("connection method view", "Advanced"))
        window = self.main_window
        for title, subtitle, icon, callback in (
            (_("Internet, router and firewall"), _("CGNAT, IPv6, UPnP and when a private network is the better choice."), "brp-firewall-symbolic", getattr(window, "show_internet_check", None)),
            (
                _("Without a private network"),
                _("Direct connection with a domain and router ports. Greater exposure to the internet."),
                "brp-address-symbolic",
                getattr(window, "show_direct_internet_guide", None),
            ),
        ):
            if callable(callback):
                advanced.add(action_row(title, subtitle, icon, callback))
        self.advanced_group = advanced
        box.append(advanced)
        clamp.set_child(box)
        self.set_child(clamp)

    # ── data ───────────────────────────────────────────────────────────────
    def _on_map(self) -> None:
        self.refresh()
        if not self._timer:
            self._timer = GLib.timeout_add_seconds(self.REFRESH_SECONDS, self._tick)

    def _tick(self) -> bool:
        if not self.get_mapped():
            self._timer = 0
            return False
        self.refresh()
        return True

    def _stop_timer(self) -> None:
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0

    def _close_workers(self) -> None:
        self._status_worker.cancel()
        for worker in self._switch_workers.values():
            worker.cancel()

    def refresh(self) -> None:
        """Read every card (off the GTK thread) and redraw the ones not busy."""
        network = str(getattr(self.main_window, "zerotier_network_id", "") or "")

        def apply(summaries) -> None:
            self._summaries = {summary.provider: summary for summary in summaries}
            self._render()

        self._status_worker.submit(lambda: self.service_factory().card_summaries(network), apply, failed=lambda _error: self._render(failed=True))

    def _render(self, *, failed: bool = False) -> None:
        for provider, card in self.cards.items():
            if not card.busy:
                card.set_summary(self._summaries.get(provider), failed=failed)

    def open(self, provider: ProviderId):
        """That method's page, in this window (its setup when it is not set up yet)."""
        summary = self._summaries.get(provider)
        if provider is ProviderId.HEADSCALE and summary is not None and summary.setup_incomplete:
            return self.main_window.open_provider(provider.value, view="setup")
        return self.main_window.open_provider(provider.value)

    # ── the switch ─────────────────────────────────────────────────────────
    def switch(self, provider: ProviderId, wanted: bool) -> None:
        """The person moved a card's switch: do it for real, or lead to what is missing."""
        card = self.cards[provider]
        summary = self._summaries.get(provider)
        if summary is None:
            card.settle(None)
            return
        if not wanted:
            if provider is ProviderId.ZEROTIER and summary.more_networks:
                from .network_common import confirm

                confirm(
                    self,
                    _("Turn off ZeroTier?"),
                    _("Every ZeroTier network on this computer will disconnect. Saved network memberships are kept."),
                    _("Turn off"),
                    lambda: self._run(provider, False),
                )
                card.on_cancel = lambda: card.settle(summary)
                return
            self._run(provider, False)
            return
        if summary.state.key == "not_installed":
            # Install and continue lives on the method's page; it goes on by itself.
            card.settle(summary)
            self.main_window.open_provider(provider.value)
            return
        if summary.state.detail == "permission":
            # The one-time permission (ZeroTier) is the Allow button on the method's page.
            card.settle(summary)
            self.main_window.open_provider(provider.value)
            return
        if not summary.configured or summary.state.detail == "sign_in":
            card.settle(summary)
            self.main_window.open_provider(provider.value, view="setup", auto_start=provider is ProviderId.TAILSCALE)
            return
        if summary.other_active is not None:
            self._confirm_shared_app(provider, summary)
            return
        self._run(provider, True)

    def _confirm_shared_app(self, provider: ProviderId, summary: CardSummary) -> None:
        other = summary.other_active
        assert other is not None
        dialog = Adw.AlertDialog(
            heading=_("Switch from {current} to {wanted}?").format(current=other.display_name, wanted=provider.display_name),
            body=_("Only one Tailscale-based network can be active on this computer at a time. {current} stays saved and can be turned on again later. ZeroTier is not affected.").format(
                current=other.display_name
            ),
        )
        dialog.set_body_use_markup(False)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("switch", _("Switch"))
        dialog.set_response_appearance("switch", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("switch")
        dialog.set_close_response("cancel")

        def respond(_dialog, response) -> None:
            if response == "switch":
                self._run(provider, True)
            else:
                self.cards[provider].settle(summary)

        dialog.connect("response", respond)
        dialog.present(self)
        self._last_dialog = dialog

    def _run(self, provider: ProviderId, wanted: bool) -> None:
        card = self.cards[provider]
        card.set_busy(_("Starting…") if wanted else _("Stopping…"))
        network = str(getattr(self.main_window, "zerotier_network_id", "") or "")

        def work():
            service = self.service_factory()
            done = service.start(provider) if wanted else service.stop(provider)
            summary = next((item for item in service.card_summaries(network) if item.provider is provider), None)
            return bool(done), summary

        def finish(result) -> None:
            _done, summary = result
            if summary is not None:
                self._summaries[provider] = summary
            # The switch shows what is true now, whatever the command answered.
            reached = summary is not None and summary.switch_on == wanted
            card.settle(summary, problem="" if reached else ("connect" if wanted else "disconnect"))
            changed = getattr(self.main_window, "_refresh_private_network_status", None)
            if callable(changed):
                changed()
            if reached:
                self.refresh()  # the other card of the shared Tailscale app changed too

        def failed(_error) -> None:
            card.settle(self._summaries.get(provider), problem="connect" if wanted else "disconnect")

        worker = self._switch_workers.setdefault(provider, Worker())
        worker.submit(work, finish, failed=failed)


class ProviderCard(Gtk.Box):
    """A small panel for one provider: switch, state, network, address, devices."""

    __gtype_name__ = "BrpProviderCard"

    def __init__(self, provider: ProviderId, *, on_open: Callable[[], object], on_switch: Callable[[bool], object]) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.provider = provider
        self.summary: CardSummary | None = None
        self.state: SimpleState | None = None
        self.busy = False
        self.problem = ""
        self.on_cancel: Callable[[], object] | None = None
        self._on_switch = on_switch
        self._syncing = False
        self.add_css_class("card")
        self.add_css_class("brp-provider-card")

        # Provider + switch: the switch is its own control, never part of "open".
        top = Gtk.Box(spacing=12, margin_top=16, margin_start=16, margin_end=16)
        top.append(icon_tile(PROVIDER_ICONS[provider]))
        names = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True, valign=Gtk.Align.CENTER)
        title = Gtk.Label(label=provider.display_name, xalign=0, wrap=True, accessible_role=Gtk.AccessibleRole.HEADING)
        title.add_css_class("title-3")
        names.append(title)
        tag = method_tag(provider)
        if tag:
            badge = Gtk.Label(label=tag, xalign=0, halign=Gtk.Align.START)
            badge.add_css_class("state-pill")
            badge.add_css_class("caption")
            badge.add_css_class("offline")
            names.append(badge)
        top.append(names)
        self.switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.switch.set_tooltip_text(_("Turn {name} on or off").format(name=provider.display_name))
        self.switch.update_property([Gtk.AccessibleProperty.LABEL], [_("{name} connection").format(name=provider.display_name)])
        self.switch.connect("state-set", self._on_state_set)
        top.append(self.switch)
        self.append(top)

        # The body opens the method's page.
        self.body = Gtk.Button(hexpand=True, vexpand=True)
        self.body.add_css_class("flat")
        self.body.add_css_class("brp-card-body")
        self.body.connect("clicked", lambda _button: on_open())
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=6, margin_bottom=6, margin_start=4, margin_end=4)
        self.state_line = Gtk.Box(spacing=8)
        self.spinner = Adw.Spinner(visible=False)
        self.state_line.append(self.spinner)
        self.state_slot = Gtk.Box()
        self.state_line.append(self.state_slot)
        content.append(self.state_line)
        self.network_label = self._line("heading")
        content.append(self.network_label)
        self.extra_label = self._line("dim-label", "caption")
        content.append(self.extra_label)
        address_box = Gtk.Box(spacing=8)
        self.address_label = self._line("monospace")
        self.address_label.set_selectable(False)
        self.address_caption = self._line("dim-label", "caption")
        self.address_caption.set_label(_("This computer"))
        self.address_label.set_visible(True)  # the row's box shows or hides both
        self.address_caption.set_visible(True)
        self.address_caption.set_valign(Gtk.Align.BASELINE)
        address_box.append(self.address_label)
        address_box.append(self.address_caption)
        self.address_box = address_box
        content.append(address_box)
        self.devices_label = self._line()
        content.append(self.devices_label)
        self.problem_label = self._line("error")
        content.append(self.problem_label)
        spacer = Gtk.Box(vexpand=True)
        content.append(spacer)
        action = Gtk.Box(spacing=6, margin_top=6)
        self.action_label = Gtk.Label(xalign=0, hexpand=True, wrap=True)
        self.action_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.action_label.add_css_class("accent")
        action.append(self.action_label)
        action.append(create_icon_widget("go-next-symbolic", size=16))
        content.append(action)
        self.body.set_child(content)
        body_frame = Gtk.Box(margin_top=4, margin_bottom=10, margin_start=10, margin_end=10, vexpand=True)
        body_frame.append(self.body)
        self.append(body_frame)
        self.set_summary(None)

    @staticmethod
    def _line(*classes: str) -> Gtk.Label:
        label = Gtk.Label(xalign=0, wrap=True, visible=False)
        label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        for css in classes:
            label.add_css_class(css)
        return label

    # Older tests and callers look for these names.
    @property
    def open_button(self) -> Gtk.Button:
        return self.body

    # ── the switch ─────────────────────────────────────────────────────────
    def _on_state_set(self, _switch, wanted: bool) -> bool:
        if self._syncing:
            return False
        if self.busy:
            return True
        self.problem = ""
        self._on_switch(wanted)
        return True  # the state follows the connection, set by settle()

    def _set_switch(self, active: bool, *, state: bool | None = None) -> None:
        self._syncing = True
        try:
            self.switch.set_active(active)
            self.switch.set_state(active if state is None else state)
        finally:
            self._syncing = False

    def set_busy(self, word: str) -> None:
        self.busy = True
        self.problem = ""
        self.switch.set_sensitive(False)
        self.spinner.set_visible(True)
        child = self.state_slot.get_first_child()
        if child is not None:
            self.state_slot.remove(child)
        label = Gtk.Label(label=word, xalign=0)
        label.add_css_class("caption-heading")
        self.state_slot.append(label)
        self.problem_label.set_visible(False)
        self.update_property([Gtk.AccessibleProperty.DESCRIPTION], [word])

    def settle(self, summary: CardSummary | None, *, problem: str = "") -> None:
        """End of a switch action (or a cancelled one): show what is true now."""
        self.busy = False
        self.on_cancel = None
        self.problem = problem
        self.switch.set_sensitive(True)
        self.spinner.set_visible(False)
        self.set_summary(summary if summary is not None else self.summary)

    # ── content ────────────────────────────────────────────────────────────
    def set_summary(self, summary: CardSummary | None, *, failed: bool = False) -> None:
        self.summary = summary
        child = self.state_slot.get_first_child()
        if child is not None:
            self.state_slot.remove(child)
        if summary is None:
            label = Gtk.Label(label=_("State unknown") if failed else _("Checking…"), xalign=0)
            label.add_css_class("dim-label")
            self.state_slot.append(label)
            self.state = None
            self._set_switch(False)
            self.switch.set_sensitive(failed)
            for widget in (self.network_label, self.extra_label, self.address_box, self.devices_label, self.problem_label):
                widget.set_visible(False)
            self.action_label.set_label(_("Open"))
            self._describe(label.get_label())
            return
        self.state = summary.state
        self.switch.set_sensitive(True)
        self._set_switch(summary.switch_on)
        word = _("Setup incomplete") if summary.setup_incomplete else state_text(summary.state)
        badge = state_badge(summary.state)
        if summary.setup_incomplete:
            badge = Gtk.Label(label=word, xalign=0)
            badge.add_css_class("caption-heading")
        self.state_slot.append(badge)
        self.remove_css_class("active")
        if summary.switch_on:
            self.add_css_class("active")

        network = network_line(summary)
        self.network_label.set_label(network)
        self.network_label.set_visible(bool(network))
        self.network_label.set_tooltip_text(network_tooltip(summary.provider) if network else None)
        extra = ngettext("+ {count} more network", "+ {count} more networks", summary.more_networks).format(count=summary.more_networks) if summary.more_networks else ""
        if summary.other_active is not None and not summary.switch_on:
            extra = _("{name} is using the Tailscale app now").format(name=summary.other_active.display_name)
        self.extra_label.set_label(extra)
        self.extra_label.set_visible(bool(extra))
        self.address_label.set_label(summary.address)
        self.address_box.set_visible(bool(summary.address))
        devices = devices_line(summary)
        self.devices_label.set_label(devices)
        self.devices_label.set_visible(bool(devices))
        self.devices_label.set_tooltip_text(_("Add a ZeroTier API token in Advanced to count the devices.") if summary.devices.problem == "api_missing" else None)
        problem = {"connect": _("Could not connect"), "disconnect": _("Could not disconnect")}.get(self.problem, "")
        self.problem_label.set_label(problem)
        self.problem_label.set_visible(bool(problem))
        self.action_label.set_label(_("See what happened") if problem else action_text(summary))
        self._describe(", ".join(part for part in (word, network, summary.address, devices, problem) if part))

    def _describe(self, text: str) -> None:
        self.body.update_property(
            [Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION],
            [_("Open {name}").format(name=self.provider.display_name), f"{text}. {method_summary(self.provider)}"],
        )


def network_line(summary: CardSummary) -> str:
    if not summary.network:
        return ""
    if summary.provider is ProviderId.HEADSCALE:
        return _("Server: {name}").format(name=summary.network)
    return _("Network: {name}").format(name=summary.network)


def network_tooltip(provider: ProviderId) -> str:
    return {
        ProviderId.TAILSCALE: _("Your Tailscale network (tailnet)"),
        ProviderId.ZEROTIER: _("The ZeroTier network Big Remote Play uses"),
        ProviderId.HEADSCALE: _("Your Headscale server"),
    }[provider]


def devices_line(summary: CardSummary) -> str:
    """“3 online · 5 devices”, or why it is not known; never a made-up zero."""
    count = summary.devices
    if count.known:
        assert count.total is not None and count.online is not None
        online = ngettext("{count} online", "{count} online", count.online).format(count=count.online)
        total = ngettext("{count} device", "{count} devices", count.total).format(count=count.total)
        return f"{online} · {total}"
    if not summary.switch_on:
        return ""
    if count.problem == "api_failed":
        return _("Could not load devices")
    if count.problem == "api_missing":
        return _("Devices unavailable")
    return ""


def action_text(summary: CardSummary) -> str:
    if summary.setup_incomplete:
        return _("Continue setup")
    key = summary.state.key
    if key == "not_installed":
        return _("Install")
    if key == "connected":
        return _("View devices")
    if key == "attention":
        return _("See what happened")
    if key == "connecting":
        return _("View devices")
    return _("Start to connect") if summary.configured else _("Set up")
