"""Guided setup: two questions, then the right place to continue.

The guide never implements a flow of its own. It asks what the person wants
to do and where the other device is, then hands over to Share, Connect, the
internet page or a connection page, which do the real work. Its pages are
pushed onto Home's navigation view, so Back always goes one question back.

    What do you want to do?  →  Where is the other device?
        →  this computer is checked; what the task needs is installed here
        same network  →  Share / Connect
        somewhere else →  a secure connection:
            one already works  →  use it  →  Share / Connect
            one needs a step   →  the internet page, which offers that step
            none               →  Tailscale (recommended) · ZeroTier code · advanced
"""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, Pango  # type: ignore

from big_remote_play.private_network.models import ConnectionState, ProviderId
from big_remote_play.private_network.plan import ConnectionPlan, PlanKind, plan_connection
from big_remote_play.utils.i18n import _
from big_remote_play.utils.icons import create_icon_widget

from .components import icon_tile
from .network_common import PROVIDER_ICONS, Worker, state_pill

ROLES = ("host", "guest")


def choice_card(title: str, description: str, icon: str, on_click: Callable[[], object], *, badge: str = "", tone: str = "accent") -> Gtk.Button:
    """A large, keyboard-reachable choice with its consequence in one line."""
    button = Gtk.Button(hexpand=True)
    button.add_css_class("role-card")
    button.add_css_class("brp-guided-choice")
    content = Gtk.Box(spacing=16)
    content.append(icon_tile(icon, large=True, tone=tone))
    texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True, valign=Gtk.Align.CENTER)
    heading = Gtk.Box(spacing=8)
    label = Gtk.Label(label=title, xalign=0, wrap=True)
    label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    label.add_css_class("title-3")
    heading.append(label)
    if badge:
        pill = Gtk.Label(label=badge, valign=Gtk.Align.CENTER)
        pill.add_css_class("state-pill")
        pill.add_css_class("online")
        pill.add_css_class("caption-heading")
        heading.append(pill)
    texts.append(heading)
    body = Gtk.Label(label=description, xalign=0, wrap=True)
    body.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    body.add_css_class("dim-label")
    texts.append(body)
    content.append(texts)
    arrow = create_icon_widget("go-next-symbolic", size=16, css_class="brp-choice-arrow")
    arrow.set_valign(Gtk.Align.CENTER)
    content.append(arrow)
    button.set_child(content)
    accessible = f"{title}. {badge}" if badge else title
    button.update_property([Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION], [accessible, description])
    button.connect("clicked", lambda _button: on_click())
    button._brp_title = title  # type: ignore[attr-defined]
    return button


def question_page(tag: str, title: str, question: str, explanation: str, *children: Gtk.Widget) -> Adw.NavigationPage:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    for edge in ("top", "bottom", "start", "end"):
        getattr(box, f"set_margin_{edge}")(24)
    heading = Gtk.Label(label=question, xalign=0, wrap=True)
    heading.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    heading.add_css_class("title-1")
    box.append(heading)
    text = Gtk.Label(label=explanation, xalign=0, wrap=True, visible=bool(explanation))
    text.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    text.add_css_class("dim-label")
    box.append(text)
    # The answers sit together, a little apart from the question.
    choices = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    choices.add_css_class("brp-guided-choices")
    for child in children:
        choices.append(child)
    box.append(choices)
    clamp = Adw.Clamp(maximum_size=720, tightening_threshold=520, child=box)
    scroll = Gtk.ScrolledWindow(vexpand=True, child=clamp)
    scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    page = Adw.NavigationPage(child=scroll, title=title, tag=tag)
    page._brp_heading = heading  # type: ignore[attr-defined]
    page._brp_explanation = text  # type: ignore[attr-defined]
    return page


class GuidedSetup:
    """Pushes the guide's questions onto ``main_window.home_navigation``."""

    def __init__(self, main_window, *, service_factory: Callable | None = None) -> None:
        from big_remote_play.private_network.service import default_service

        self.window = main_window
        self.navigation: Adw.NavigationView = main_window.home_navigation
        self.service_factory = service_factory or default_service
        self.role: str | None = None
        self.plan: ConnectionPlan | None = None
        self._worker = Worker()
        self._zerotier_panel = None

    # ── the questions ──────────────────────────────────────────────────────
    def start(self) -> None:
        self.navigation.pop_to_tag("choices")
        self._push(
            question_page(
                "guided-role",
                _("Guided setup"),
                _("What do you want to do?"),
                "",
                choice_card(_("Share my game"), _("This is the computer that will run the game."), "brp-host-symbolic", lambda: self.choose_role("host")),
                choice_card(_("Connect to another computer"), _("I will play using another computer."), "brp-client-symbolic", lambda: self.choose_role("guest"), tone="guest"),
            )
        )

    def choose_role(self, role: str) -> None:
        if role not in ROLES:
            return
        self.role = role
        self._push(
            question_page(
                "guided-place",
                _("Guided setup"),
                _("Where is the other device?"),
                "",
                choice_card(
                    _("On the same network"),
                    _("Both devices are connected to the same Wi-Fi or router."),
                    "brp-network-wireless-symbolic",
                    self.same_network,
                    badge=_("Simplest"),
                ),
                choice_card(_("Somewhere else"), _("The other device is in another house, city or network."), "brp-globe-symbolic", self.somewhere_else),
            )
        )

    def same_network(self) -> None:
        """Nothing to connect: get this computer ready, then open the task."""
        self.prepare(self.finish)

    def somewhere_else(self) -> None:
        self.prepare(self.detect)

    # ── getting this computer ready ────────────────────────────────────────
    def prepare(self, then: Callable[[], object]) -> None:
        """Check what the task needs and install what is missing, in place.

        Nothing is installed without the button; once everything is there the
        guide continues by itself, so nobody has to come back and click again.
        """
        from big_remote_play.utils import dependencies

        from .dependency_installer import ComponentChecklist

        self._then = then
        self._continue_ready = Gtk.Button(label=_("Continue"), halign=Gtk.Align.START, visible=False)
        self._continue_ready.add_css_class("suggested-action")
        self._continue_ready.add_css_class("pill")
        self._continue_ready.connect("clicked", lambda _button: self._components_ready(force=True))
        self.checklist = ComponentChecklist(dependencies.ROLE_COMPONENTS.get(self.role or "host", ()), on_ready=self._components_ready)
        self._push(
            question_page(
                "guided-ready",
                _("Guided setup"),
                _("Let's get this computer ready"),
                _("Big Remote Play checks what this computer needs and installs what is missing. You may be asked for your password once."),
                self.checklist,
                self._continue_ready,
            )
        )

    def _components_ready(self, *, force: bool = False) -> None:
        self._continue_ready.set_visible(True)
        # Only from the page that is on screen: Back must not be overtaken.
        if force or visible_tag(self.navigation) == "guided-ready":
            self._then()

    # ── looking at what this computer already has ─────────────────────────
    def detect(self) -> None:
        self._result = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self._result.set_accessible_role(Gtk.AccessibleRole.STATUS)
        spinner_row = Gtk.Box(spacing=12)
        spinner_row.append(Adw.Spinner())
        spinner_row.append(Gtk.Label(label=_("Looking for a connection on this computer…"), xalign=0, wrap=True))
        self._result.append(spinner_row)
        page = question_page(
            "guided-detect",
            _("Guided setup"),
            _("Let's create a secure connection"),
            _("To play over the internet, the two computers need to be able to find each other. Big Remote Play can set this up for you."),
            self._result,
        )
        self._detect_page = page
        self._push(page)
        preferred = self._preferred()

        def load():
            service = self.service_factory()
            statuses = service.overview()
            internet = True if any(status.connected for status in statuses) else service.internet_available()
            return plan_connection(statuses, internet=internet, preferred=preferred)

        self._worker.submit(load, self._show_detection, failed=lambda _error: self.choose_method())

    def _preferred(self) -> ProviderId | None:
        try:
            return ProviderId(self.window._vpn_choice) if getattr(self.window, "_vpn_choice", None) else None
        except ValueError:
            return None

    def _show_detection(self, plan: ConnectionPlan) -> None:
        self.plan = plan
        if plan.kind in (PlanKind.INSTALL, PlanKind.SET_UP):
            # Nothing is set up yet: skip straight to the choice.
            self.navigation.pop()
            self.choose_method()
            return
        box = self._result
        while child := box.get_first_child():
            box.remove(child)
        heading = self._detect_page._brp_heading  # type: ignore[attr-defined]
        self._detect_page._brp_explanation.set_visible(False)  # type: ignore[attr-defined]
        status = plan.status
        network = (status.network_name or next((item.name for item in status.networks if item.state is ConnectionState.CONNECTED), "")) if status is not None else ""
        name = network or plan.provider.display_name
        row = Adw.ActionRow(title=plan.provider.display_name, subtitle=name if name != plan.provider.display_name else "", use_markup=False)
        row.add_prefix(icon_tile(PROVIDER_ICONS[plan.provider]))
        buttons = Gtk.Box(spacing=12)
        other = Gtk.Button(label=_("Choose another option"))
        other.add_css_class("pill")
        other.connect("clicked", lambda _button: self.choose_method())
        if plan.kind is PlanKind.READY:
            heading.set_label(_("We found a connection that is ready"))
            row.add_suffix(state_pill(ConnectionState.CONNECTED))
            use = Gtk.Button(label=_("Use this connection"))
            use.connect("clicked", lambda _button: self.finish(internet=True))
        else:
            from .remote_connection import plan_words

            _icon, _tone, title, body, _action = plan_words(plan)
            heading.set_label(title)
            row.set_subtitle(body)
            row.set_subtitle_lines(0)
            use = Gtk.Button(label=_("Continue"))
            # The internet page owns every fix (turn on, start, allow, sign in).
            use.connect("clicked", lambda _button: self.open_internet_page())
        use.add_css_class("suggested-action")
        use.add_css_class("pill")
        buttons.append(use)
        buttons.append(other)
        rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        rows.add_css_class("boxed-list")
        rows.add_css_class("brp-boxed")
        rows.append(row)
        box.append(rows)
        box.append(buttons)
        use.grab_focus()

    # ── choosing a method ──────────────────────────────────────────────────
    def choose_method(self) -> None:
        self._push(
            question_page(
                "guided-method",
                _("Guided setup"),
                _("How do you want to connect?"),
                _("Both computers must use the same option."),
                choice_card(_("Tailscale"), _("Sign in with your account and connect your devices."), "brp-tailscale-symbolic", lambda: self.open_method(ProviderId.TAILSCALE), badge=_("Recommended")),
                choice_card(_("I already use ZeroTier"), _("Join using the code of your network."), "brp-zerotier-symbolic", self.zerotier_code),
                choice_card(_("Advanced options"), _("Your own Headscale server and settings for experienced users."), "brp-preferences-symbolic", lambda: self.open_method(ProviderId.HEADSCALE)),
            )
        )

    def open_method(self, provider: ProviderId) -> None:
        """The provider's own connection page; it returns to the task when done."""
        self._return_to_task()
        self.navigation.pop_to_tag("choices")
        self.window._apply_vpn_selection(provider.value, destination="connect_private")

    def open_internet_page(self) -> None:
        self._return_to_task()
        self.navigation.pop_to_tag("choices")
        self.window.navigate_to("vpn_selector")

    def zerotier_code(self) -> None:
        from .zerotier_join import ZeroTierJoinPanel

        check = self.window.system_check
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        if not _installed(check.has_zerotier):
            from .private_network_view import InstallSection

            content.append(InstallSection("zerotier", self.window, on_installed=self._rebuild_zerotier))
        else:
            self._continue = Gtk.Button(label=_("Continue"), halign=Gtk.Align.CENTER, visible=False)
            self._continue.add_css_class("suggested-action")
            self._continue.add_css_class("pill")
            self._continue.connect("clicked", lambda _button: self.finish(internet=True))
            self._zerotier_panel = ZeroTierJoinPanel(self.window, on_connected=self._zerotier_connected, on_change=self._zerotier_changed)
            content.append(self._zerotier_panel)
            content.append(self._continue)
            no_code = Gtk.Button(label=_("I don't have a code"), halign=Gtk.Align.CENTER)
            no_code.add_css_class("flat")
            no_code.connect("clicked", lambda _button: self._explain_no_code())
            content.append(no_code)
        self._push(question_page("guided-zerotier", _("Guided setup"), _("Did you receive a network code?"), _("The owner of the network sends it to you."), content))

    def _rebuild_zerotier(self) -> None:
        self.navigation.pop()
        self.zerotier_code()

    def _zerotier_connected(self, _snapshot) -> None:
        self._continue.set_visible(True)
        self._continue.grab_focus()

    def _zerotier_changed(self, snapshot) -> None:
        # "Continue" belongs to the network on screen, not to an earlier one.
        from big_remote_play.private_network.zerotier_join import JoinPhase

        self._continue.set_visible(snapshot.phase is JoinPhase.CONNECTED)

    def _explain_no_code(self) -> None:
        dialog = Adw.AlertDialog(
            heading=_("No network code?"),
            body=_(
                "Ask the person who created the ZeroTier network to send it. If nobody has a network yet, one of you creates it for free in ZeroTier Central, or you use Tailscale, the simplest way to start."
            ),
        )
        dialog.add_response("close", _("Close"))
        dialog.add_response("create", _("Create a ZeroTier network"))
        dialog.add_response("tailscale", _("Use Tailscale"))
        dialog.set_response_appearance("tailscale", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect("response", self._on_no_code_response)
        dialog.present(self.window)

    def _on_no_code_response(self, _dialog, response: str) -> None:
        if response == "tailscale":
            self.open_method(ProviderId.TAILSCALE)
        elif response == "create":
            self.show_create_zerotier_steps()

    def show_create_zerotier_steps(self) -> None:
        from .private_network_view import show_create_zerotier_steps

        show_create_zerotier_steps(self.window)

    # ── done ───────────────────────────────────────────────────────────────
    def _return_to_task(self) -> None:
        if self.role in ROLES:
            self.window._network_return_page = self.role

    def finish(self, *, internet: bool = False) -> None:
        """Open Share or Connect, which check and install what they need."""
        role = self.role or "host"
        if internet:
            self._return_to_task()
        self._worker.cancel()
        if self._zerotier_panel is not None:
            self._zerotier_panel.close()
        self.navigation.pop_to_tag("choices")
        self.window._select_home_role(role)

    def _push(self, page: Adw.NavigationPage) -> None:
        # Asking again replaces the later answers instead of stacking copies.
        existing = self.navigation.find_page(page.get_tag() or "")
        if existing is not None:
            self.navigation.pop_to_page(existing)
            self.navigation.pop()
        self.navigation.push(page)


def visible_tag(navigation: Adw.NavigationView) -> str:
    page = navigation.get_visible_page()
    return (page.get_tag() or "") if page is not None else ""


def _installed(check) -> bool:
    try:
        return bool(check())
    except Exception:
        return False


__all__ = ["GuidedSetup", "choice_card", "question_page"]
