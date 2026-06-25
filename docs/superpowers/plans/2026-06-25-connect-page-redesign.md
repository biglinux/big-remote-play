# Connect Page + Rede Privada Novice Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task (inline; this project forbids subagents). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a non-technical user succeed end-to-end on "Conectar ao servidor": discovery-first layout, fixed host-list clipping, an intent-branched empty state that routes to the real unlock (Rede Privada / PIN), collapsed quality settings; plus a novice-clearer Rede Privada selector and Tailscale form.

**Architecture:** Pure GTK4/libadwaita layout + copy changes in three files. No discovery/connection/VPN logic changes — only structure, prominence, and guidance text. New/changed strings go through `_()`. Verification = ruff + mypy + existing pytest regression guards (static-source assertions, the repo's established UI-guard pattern) + live screenshots on the test VM (192.168.1.21) via the `linux-ui-a11y` `aigui` harness.

**Tech Stack:** Python 3.14, PyGObject (GTK 4 / libadwaita 1.x), pytest, ruff, mypy, gettext.

**Spec:** `docs/superpowers/specs/2026-06-25-connect-page-redesign-design.md`

---

## File Structure

- Modify `src/big_remote_play/ui/guest_view.py`
  - `setup_ui()` — move client settings into a collapsed `Adw.ExpanderRow` with a live summary.
  - `create_discover_page()` — single column (drop side helper card), fixed guidance subtitle, clipping fix on the host scroller, footer hint line.
  - `discover_hosts()` / `on_hosts_discovered` / `update_hosts_list()` — render the compact intent-branched empty state.
  - New helper `_build_discover_empty_state()` and `_quality_summary()`.
- Modify `src/big_remote_play/ui/main_window.py`
  - `create_vpn_selector_page()` — plain-language role-framing intro; wrap the comparison table in a collapsed `Gtk.Expander`.
- Modify `src/big_remote_play/ui/private_network_view.py`
  - `_build()` / `_build_form()` — Tailscale: prominent "Entrar com o navegador" primary button; move the auth-key row into a collapsed `Gtk.Expander`.
- Modify `tests/test_guest_view_a11y_regressions.py` — add static guards for the new empty-state navigation hooks and quality expander.
- Create `tests/test_connect_redesign_regressions.py` — static-source guards covering clipping fix, single column, selector framing, browser-login prominence.

---

## Pre-flight

- [ ] **Step 0a: Branch (we are on `main`)**

Run:
```bash
cd /home/bruno/codigo-pacotes/big-remote-play
git checkout -b feat/connect-page-novice-redesign
```
Expected: `Switched to a new branch 'feat/connect-page-novice-redesign'`

- [ ] **Step 0b: Baseline green**

Run: `ruff check src/ tests/ && python -m pytest -q`
Expected: lint clean; `103 passed`.

---

## Task 1: Host-list clipping fix (Discover page)

**Files:**
- Modify: `src/big_remote_play/ui/guest_view.py` (`create_discover_page`, the `host_scroll` block ~419-425)
- Test: `tests/test_connect_redesign_regressions.py`

- [ ] **Step 1: Write the failing guard test**

Create `tests/test_connect_redesign_regressions.py`:
```python
"""Static source guards for the Connect page + Rede Privada novice redesign."""

from pathlib import Path

GUEST = Path("src/big_remote_play/ui/guest_view.py")
MAIN = Path("src/big_remote_play/ui/main_window.py")
PNV = Path("src/big_remote_play/ui/private_network_view.py")


def test_host_scroll_has_breathing_room_and_no_tall_min() -> None:
    src = GUEST.read_text()
    # The host list scroller must not force a tall empty box, and the list must
    # have vertical margins so the boxed-list card corners are not clipped.
    assert "host_scroll.set_min_content_height(120)" in src
    assert "self.hosts_list.set_margin_top(6)" in src
    assert "self.hosts_list.set_margin_bottom(6)" in src
```

- [ ] **Step 2: Run it, verify it fails**

Run: `python -m pytest tests/test_connect_redesign_regressions.py::test_host_scroll_has_breathing_room_and_no_tall_min -v`
Expected: FAIL (strings not present yet).

- [ ] **Step 3: Apply the clipping fix**

In `create_discover_page`, the host-list margins block currently is:
```python
        for m in ["start", "end"]:
            getattr(self.hosts_list, f"set_margin_{m}")(12)
```
Replace with (add top/bottom margins so card corners are not flush with the viewport):
```python
        for m in ["start", "end"]:
            getattr(self.hosts_list, f"set_margin_{m}")(12)
        # Top/bottom margin so the boxed-list card's rounded corners and the
        # first/last rows are not clipped by the ScrolledWindow viewport edge.
        self.hosts_list.set_margin_top(6)
        self.hosts_list.set_margin_bottom(6)
```
And the `host_scroll` block currently:
```python
        host_scroll = Gtk.ScrolledWindow()
        host_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        host_scroll.set_max_content_height(400)
        host_scroll.set_min_content_height(200)
        host_scroll.set_vexpand(True)
        host_scroll.set_propagate_natural_height(True)
        host_scroll.set_child(self.hosts_list)
```
Replace the min height so few hosts/empty state stay compact (grows naturally up to max, scrolls only on real overflow):
```python
        host_scroll = Gtk.ScrolledWindow()
        host_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        host_scroll.set_max_content_height(400)
        host_scroll.set_min_content_height(120)
        host_scroll.set_vexpand(False)
        host_scroll.set_propagate_natural_height(True)
        host_scroll.set_child(self.hosts_list)
```

- [ ] **Step 4: Run the guard + format/lint**

Run:
```bash
ruff format src/big_remote_play/ui/guest_view.py
ruff check src/big_remote_play/ui/guest_view.py
python -m pytest tests/test_connect_redesign_regressions.py -q
```
Expected: lint clean; guard PASSES.

- [ ] **Step 5: Commit**

```bash
git add src/big_remote_play/ui/guest_view.py tests/test_connect_redesign_regressions.py
git commit -m "fix(connect): stop host-list clipping; compact natural-height scroller"
```

---

## Task 2: Intent-branched empty state

**Files:**
- Modify: `src/big_remote_play/ui/guest_view.py` (new `_build_discover_empty_state`; used by `discover_hosts`/`on_hosts_discovered`/`update_hosts_list`)
- Test: `tests/test_connect_redesign_regressions.py`, `tests/test_guest_view_a11y_regressions.py`

- [ ] **Step 1: Write the failing guard test**

Append to `tests/test_connect_redesign_regressions.py`:
```python
def test_empty_state_routes_novice_to_real_unlocks() -> None:
    src = GUEST.read_text()
    assert "_build_discover_empty_state" in src
    # Re-scan, Private Network, PIN are all reachable from the empty state.
    assert 'navigate_to("vpn_selector")' in src
    assert 'self.method_stack.set_visible_child_name("pin")' in src
    assert 'self.method_stack.set_visible_child_name("manual")' in src


def test_empty_state_button_has_accessible_label() -> None:
    src = GUEST.read_text()
    # Icon/short-label actions in the empty state expose accessible names.
    assert src.count("Gtk.AccessibleProperty.LABEL") >= 4
```

- [ ] **Step 2: Run it, verify it fails**

Run: `python -m pytest tests/test_connect_redesign_regressions.py -k empty_state -v`
Expected: FAIL.

- [ ] **Step 3: Add the empty-state builder**

Add this method to `GuestView` (place it just before `create_discover_page`):
```python
    def _go_to_private_network(self) -> None:
        root = self._root_window()
        if hasattr(root, "navigate_to"):
            root.navigate_to("vpn_selector")

    def _build_discover_empty_state(self) -> Gtk.Widget:
        """Compact, plain-language empty state that routes a non-technical user to
        the path that actually unlocks their case: same-network rescan, Private
        Network for internet play (the real enabler), or a friend-dictated PIN.
        Manual/IP is the de-emphasized advanced fallback."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        box.add_css_class("helper-card")
        box.set_halign(Gtk.Align.CENTER)
        box.set_margin_top(8)
        box.set_margin_bottom(8)

        title = Gtk.Label(label=_("No host found yet"))
        title.add_css_class("title-4")
        title.set_halign(Gtk.Align.CENTER)
        box.append(title)

        def option(icon_name, text, button_label, accessible, on_click, highlight=False):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            row.add_css_class("helper-row")
            icon = create_icon_widget(icon_name, size=18)
            icon.add_css_class("accent" if highlight else "dim-label")
            icon.set_valign(Gtk.Align.CENTER)
            row.append(icon)
            lbl = Gtk.Label(label=text)
            lbl.set_halign(Gtk.Align.START)
            lbl.set_wrap(True)
            lbl.set_hexpand(True)
            lbl.set_xalign(0)
            row.append(lbl)
            btn = Gtk.Button(label=button_label)
            btn.add_css_class("pill")
            if highlight:
                btn.add_css_class("suggested-action")
            else:
                btn.add_css_class("flat")
            btn.set_valign(Gtk.Align.CENTER)
            btn.update_property([Gtk.AccessibleProperty.LABEL], [accessible])
            btn.connect("clicked", lambda _b: on_click())
            row.append(btn)
            box.append(row)

        option(
            "view-refresh-symbolic",
            _("Same house or network? Search again."),
            _("Search"),
            _("Search the local network again"),
            self.discover_hosts,
        )
        option(
            "network-vpn-symbolic",
            _("Playing with a friend over the internet? You need a Private Network (just once)."),
            _("Set up Private Network"),
            _("Open Private Network setup"),
            self._go_to_private_network,
            highlight=True,
        )
        option(
            "dialog-password-symbolic",
            _("Did the host give you a 6-digit code?"),
            _("Connect with PIN"),
            _("Switch to PIN connection"),
            lambda: self.method_stack.set_visible_child_name("pin"),
        )
        option(
            "network-wired-symbolic",
            _("I know the IP address"),
            _("Manual"),
            _("Switch to manual connection"),
            lambda: self.method_stack.set_visible_child_name("manual"),
        )
        return box
```

- [ ] **Step 4: Use it everywhere the old placeholder rendered**

In `discover_hosts` and `on_hosts_discovered`, the current "no hosts" branch builds a `box` with `set_size_request(-1, 150)` and a `title-2` label. Replace those empty/idle renderings so that, when there are no hosts (and not actively scanning), `self.hosts_list` is cleared and the empty-state widget is shown instead of the tall placeholder. Concretely, in `update_hosts_list`, when `hosts` is empty, clear the list and append a single non-selectable row whose child is `self._build_discover_empty_state()`; keep the spinner/"scanning" state unchanged during an active scan.

Read `update_hosts_list` (guest_view.py ~503-517) and apply:
```python
    def update_hosts_list(self, hosts):
        while child := self.hosts_list.get_first_child():
            self.hosts_list.remove(child)
        if not hosts:
            placeholder = Gtk.ListBoxRow()
            placeholder.set_selectable(False)
            placeholder.set_activatable(False)
            placeholder.set_child(self._build_discover_empty_state())
            self.hosts_list.append(placeholder)
            self.main_connect_btn.set_sensitive(False)
            return
        for host in hosts:
            self.hosts_list.append(self.create_host_row_custom(host))
```
(Keep the existing scanning/spinner path in `discover_hosts`/`on_hosts_discovered`; only the "finished, zero hosts" outcome routes through this empty state. If those methods set a `set_size_request(-1, 150)` placeholder for the empty result, replace that call with `self.update_hosts_list([])`.)

- [ ] **Step 5: Verify import availability**

`create_icon_widget` is already imported in `guest_view.py` (used elsewhere). Confirm:
Run: `grep -n "create_icon_widget" src/big_remote_play/ui/guest_view.py | head -1`
Expected: an existing import line. If absent, add `from big_remote_play.utils.icons import create_icon_widget`.

- [ ] **Step 6: Lint + guards + full suite**

Run:
```bash
ruff format src/big_remote_play/ui/guest_view.py
ruff check src/big_remote_play/ui/guest_view.py
python -m pytest tests/test_connect_redesign_regressions.py tests/test_guest_view_a11y_regressions.py -q
python -m pytest -q
```
Expected: lint clean; guards PASS; full suite still green (`>=103 passed`).

- [ ] **Step 7: Commit**

```bash
git add src/big_remote_play/ui/guest_view.py tests/test_connect_redesign_regressions.py
git commit -m "feat(connect): intent-branched empty state routes novices to Rede Privada/PIN"
```

---

## Task 3: Single column + guidance subtitle + footer hint

**Files:**
- Modify: `src/big_remote_play/ui/guest_view.py` (`create_discover_page` — drop side helper card column; add fixed subtitle + footer line)
- Test: `tests/test_connect_redesign_regressions.py`

- [ ] **Step 1: Write the failing guard**

Append:
```python
def test_discover_is_single_column_with_guidance() -> None:
    src = GUEST.read_text()
    # Side helper-card column removed from the discover page.
    assert "create_helper_card(" not in src
    # Fixed automatic-discovery guidance subtitle present.
    assert "o host aparece aqui automaticamente" in src or "appears here automatically" in src
```

- [ ] **Step 2: Run it, verify it fails**

Run: `python -m pytest tests/test_connect_redesign_regressions.py -k single_column -v`
Expected: FAIL (`create_helper_card(` still present).

- [ ] **Step 3: Replace the two-column return with a single column + subtitle + footer**

In `create_discover_page`, the description label currently:
```python
        desc = Gtk.Label(label=_("Scroll to list all found devices."))
```
Change to the automatic-discovery guidance:
```python
        desc = Gtk.Label(label=_("On the local network or with a Private Network set up, the host appears here automatically."))
        desc.set_wrap(True)
        desc.set_xalign(0)
```
Remove the side helper card + columns block at the end:
```python
        # Side helper card: what to try if the host doesn't show up (mockup 01).
        helper = create_helper_card(
            _("If you can't find the host"),
            "dialog-question-symbolic",
            [
                ("network-wireless-symbolic", _("Check your local network"), _("Make sure the host and this device are on the same Wi-Fi or wired network.")),
                ("network-wired-symbolic", _("Use the manual connection"), _("Enter the host IP address or hostname and port to connect directly.")),
                ("dialog-password-symbolic", _("Use the PIN code"), _("Ask the host for the PIN code and connect quickly and securely.")),
            ],
        )
        helper.set_valign(Gtk.Align.START)
        helper.set_size_request(280, -1)

        columns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
        columns.append(box)
        columns.append(helper)
        return columns
```
Replace with a quiet footer line appended to `box`, then return `box`:
```python
        # Quiet footer (shown alongside a populated list): the same two real
        # fallbacks the empty state offers, without promoting Manual/IP.
        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        footer.set_halign(Gtk.Align.CENTER)
        footer.set_margin_top(4)
        hint = Gtk.Label(label=_("Can't find your friend's PC?"))
        hint.add_css_class("dim-label")
        hint.add_css_class("caption")
        footer.append(hint)
        pn_link = Gtk.Button(label=_("Set up Private Network"))
        pn_link.add_css_class("flat")
        pn_link.add_css_class("caption")
        pn_link.update_property([Gtk.AccessibleProperty.LABEL], [_("Open Private Network setup")])
        pn_link.connect("clicked", lambda _b: self._go_to_private_network())
        footer.append(pn_link)
        pin_link = Gtk.Button(label=_("PIN code"))
        pin_link.add_css_class("flat")
        pin_link.add_css_class("caption")
        pin_link.update_property([Gtk.AccessibleProperty.LABEL], [_("Switch to PIN connection")])
        pin_link.connect("clicked", lambda _b: self.method_stack.set_visible_child_name("pin"))
        footer.append(pin_link)
        box.append(footer)
        box.set_hexpand(True)
        return box
```
(Note `box.append(action)` already adds the Connect button area above the footer; keep that ordering: header → host_scroll → action → footer.)

- [ ] **Step 4: Remove the now-unused `create_helper_card` import if present**

Run: `grep -n "create_helper_card" src/big_remote_play/ui/guest_view.py`
If the only remaining hit is the import line, remove `create_helper_card` from that import. Re-run ruff to confirm no unused-import warning.

- [ ] **Step 5: Lint + guards + suite**

Run:
```bash
ruff format src/big_remote_play/ui/guest_view.py
ruff check src/big_remote_play/ui/guest_view.py
python -m pytest tests/test_connect_redesign_regressions.py -q && python -m pytest -q
```
Expected: clean; guards PASS; suite green.

- [ ] **Step 6: Commit**

```bash
git add src/big_remote_play/ui/guest_view.py tests/test_connect_redesign_regressions.py
git commit -m "feat(connect): single-column discover with guidance subtitle + quiet footer"
```

---

## Task 4: Collapse client settings into a quality expander

**Files:**
- Modify: `src/big_remote_play/ui/guest_view.py` (`setup_ui` — wrap the settings rows in an `Adw.ExpanderRow` with a live summary; add `_quality_summary`)
- Test: `tests/test_connect_redesign_regressions.py`

- [ ] **Step 1: Write the failing guard**

Append:
```python
def test_client_settings_collapsed_behind_quality_expander() -> None:
    src = GUEST.read_text()
    assert "Adw.ExpanderRow" in src
    assert "_quality_summary" in src
    assert "Ajustar qualidade" in src or "Adjust quality" in src
```

- [ ] **Step 2: Run it, verify it fails**

Run: `python -m pytest tests/test_connect_redesign_regressions.py -k quality -v`
Expected: FAIL.

- [ ] **Step 3: Add a summary helper**

Add to `GuestView`:
```python
    def _quality_summary(self) -> str:
        """One-line summary for the collapsed quality expander, e.g.
        '1080p · 60 FPS · Áudio'. Reads the current row selections."""
        res_item = self.resolution_row.get_selected_item()
        res = res_item.get_string() if res_item is not None else "1080p"
        fps_item = self.fps_row.get_selected_item()
        fps = fps_item.get_string() if fps_item is not None else "60 FPS"
        audio = _("Audio") if self.audio_row.get_active() else _("No audio")
        return f"{res} · {fps} · {audio}"
```

- [ ] **Step 4: Wrap the settings rows in a collapsed ExpanderRow**

In `setup_ui`, the rows are currently added directly to `settings_group` (a `PreferencesGroup`). Introduce an `Adw.ExpanderRow` as the single child of `settings_group`, and add each existing row to the expander instead of the group. Concretely:

After `settings_group` is created and the reset suffix set, insert:
```python
        self.quality_expander = Adw.ExpanderRow()
        self.quality_expander.set_title(_("Adjust quality"))
        self.quality_expander.set_subtitle(self._quality_summary())
        self.quality_expander.set_expanded(False)
        settings_group.add(self.quality_expander)
```
Then change every `settings_group.add(<row>)` in this method to `self.quality_expander.add_row(<row>)` for: `resolution_row`, `scale_row`, `fps_row`, `apply_settings_btn`, `bitrate_row`, `display_mode_row`, `audio_row`, `hw_decode_row`, and the `advanced_button`.
Finally, keep the summary live: at the end of `setup_ui` (after `connect_settings_signals()`), connect updates:
```python
        for _row in (self.resolution_row, self.fps_row):
            _row.connect("notify::selected-item", lambda *_a: self.quality_expander.set_subtitle(self._quality_summary()))
        self.audio_row.connect("notify::active", lambda *_a: self.quality_expander.set_subtitle(self._quality_summary()))
```
(`Gtk.Button`/`apply_settings_btn` added via `add_row` renders as a row; if libadwaita rejects a bare button as a row child, wrap it in an `Adw.ActionRow` with the button as suffix. Verify at runtime in Step 6.)

- [ ] **Step 5: Lint + guard**

Run:
```bash
ruff format src/big_remote_play/ui/guest_view.py
ruff check src/big_remote_play/ui/guest_view.py
python -m pytest tests/test_connect_redesign_regressions.py -k quality -q
```
Expected: clean; guard PASS.

- [ ] **Step 6: Headless construction smoke (catches add_row child errors)**

Run on the test VM (deploy the file first, see Task 7 deploy step) or locally if a display is available:
```bash
python - <<'PY'
import gi; gi.require_version("Gtk","4.0"); gi.require_version("Adw","1")
from gi.repository import Adw
Adw.init()
from big_remote_play.ui.guest_view import GuestView
GuestView()  # constructs setup_ui(); raises if add_row child is invalid
print("OK")
PY
```
Expected: `OK`. If it raises on `apply_settings_btn`, wrap that button in an `Adw.ActionRow` suffix and re-run.

- [ ] **Step 7: Full suite + commit**

```bash
python -m pytest -q
git add src/big_remote_play/ui/guest_view.py tests/test_connect_redesign_regressions.py
git commit -m "feat(connect): collapse client settings into a quality expander with live summary"
```

---

## Task 5: Rede Privada selector — role framing + collapsed comparison

**Files:**
- Modify: `src/big_remote_play/ui/main_window.py` (`create_vpn_selector_page`)
- Test: `tests/test_connect_redesign_regressions.py`

- [ ] **Step 1: Write the failing guard**

Append:
```python
def test_vpn_selector_has_role_framing_and_collapsed_comparison() -> None:
    src = MAIN.read_text()
    assert "Gtk.Expander" in src  # comparison table is collapsible
    assert "Quem tem o jogo cria" in src or "the one with the game creates" in src
```

- [ ] **Step 2: Run it, verify it fails**

Run: `python -m pytest tests/test_connect_redesign_regressions.py -k vpn_selector -v`
Expected: FAIL.

- [ ] **Step 3: Add the role-framing intro before the cards**

In `create_vpn_selector_page`, immediately after `box = Gtk.Box(...)` and before `cards_box`, insert:
```python
        intro = Gtk.Label(
            label=_("To play over the internet, you set up a Private Network once: the one with the game creates it, the friend joins. After that, the PC appears automatically under Connect.")
        )
        intro.add_css_class("dim-label")
        intro.set_wrap(True)
        intro.set_xalign(0)
        box.append(intro)
```

- [ ] **Step 4: Collapse the comparison table**

Replace the comparison block:
```python
        compare_group = Adw.PreferencesGroup()
        compare_group.set_title(_("Quick Comparison"))
        compare_group.set_header_suffix(create_icon_widget("preferences-other-symbolic", size=18))
        compare_group.add(
            create_comparison_table( ... )
        )
        box.append(compare_group)
```
with a collapsed `Gtk.Expander` wrapping the same table (keep the `create_comparison_table(...)` call verbatim):
```python
        comparison_expander = Gtk.Expander(label=_("Compare the options"))
        comparison_expander.set_expanded(False)
        comparison_expander.set_child(
            create_comparison_table(
                ["Tailscale", "ZeroTier", "Headscale"],
                [
                    (_("Setup difficulty"), [_("Beginner"), _("Intermediate"), _("Advanced")]),
                    (_("Hosting model"), [_("Cloud (free)"), _("Cloud (free)"), _("Self-hosted")]),
                    (_("Best for"), [_("Personal use and friends"), _("Flexible networks and teams"), _("Corporate environments")]),
                ],
            )
        )
        box.append(comparison_expander)
```

- [ ] **Step 5: Lint + guard + suite**

Run:
```bash
ruff format src/big_remote_play/ui/main_window.py
ruff check src/big_remote_play/ui/main_window.py
python -m pytest tests/test_connect_redesign_regressions.py -k vpn_selector -q && python -m pytest -q
```
Expected: clean; guard PASS; suite green.

- [ ] **Step 6: Commit**

```bash
git add src/big_remote_play/ui/main_window.py tests/test_connect_redesign_regressions.py
git commit -m "feat(vpn): plain-language role framing + collapsed comparison on selector"
```

---

## Task 6: Tailscale form — prominent browser login, auth key advanced

**Files:**
- Modify: `src/big_remote_play/ui/private_network_view.py` (`_build` / `_build_form`, Tailscale branch)
- Test: `tests/test_connect_redesign_regressions.py`

The connect/create primary button is `self._btn_action` (created in `_build` ~line 388), wired to `self._on_action` (line 490). `_on_action` reads `self._e_authkey.get_text().strip()` (line 574); an empty key triggers Tailscale browser login. We reuse `_on_action` for the new prominent button — no logic change.

- [ ] **Step 1: Write the failing guard**

Append:
```python
def test_tailscale_browser_login_is_prominent_and_key_is_advanced() -> None:
    src = PNV.read_text()
    assert "Gtk.Expander" in src  # auth key tucked behind an advanced disclosure
    assert "Entrar com o navegador" in src or "Sign in with browser" in src
```

- [ ] **Step 3: Add a prominent browser-login button (Tailscale) and move the key into an Expander**

In `_build_form`, the Tailscale branch currently:
```python
        elif self.vpn_id == "tailscale":
            self._e_authkey = Adw.PasswordEntryRow(title=_("Auth Key (optional – leave empty for browser login)"))
            self._form_group.add(self._e_authkey)
            self._form_rows.append(self._e_authkey)
            link = Adw.ActionRow(title=_("Get auth key"), subtitle=_("login.tailscale.com/admin/settings/keys"))
            ...
```
Change so the key + link live inside a collapsed advanced section instead of the main group. Build a `Gtk.Expander` titled `_("I have an auth key")`, set expanded False, put a small box containing `self._e_authkey` (re-titled `_("Auth Key")`) and the existing `link` row, and add that expander to the form area. Keep `self._e_authkey` assigned (the connect handler reads it).

Then in `_build`, where `self._btn_action` is appended to `btn_box` (~line 393), for the Tailscale provider add a prominent primary button BEFORE `self._btn_action`:
```python
        if self.vpn_id == "tailscale":
            self._btn_browser = Gtk.Button(label=_("Sign in with browser"))
            self._btn_browser.add_css_class("suggested-action")
            self._btn_browser.add_css_class("pill")
            self._btn_browser.set_size_request(220, 48)
            self._btn_browser.update_property([Gtk.AccessibleProperty.LABEL], [_("Sign in with browser")])
            # Browser login = the existing _on_action flow with an empty auth key.
            self._btn_browser.connect("clicked", self._on_action)
            btn_box.append(self._btn_browser)
```
`_on_action` (line 490) reads `self._e_authkey` (line 574); since the key now lives in a collapsed advanced expander and starts empty, clicking this button triggers Tailscale browser login with no typing. No logic change. (Append order: place `_btn_browser` before `_btn_action` so the browser button reads as primary; optionally drop the `suggested-action` class from `_btn_action` for Tailscale so there is a single visual primary.)

- [ ] **Step 4: Lint + guard**

Run:
```bash
ruff format src/big_remote_play/ui/private_network_view.py
ruff check src/big_remote_play/ui/private_network_view.py
python -m pytest tests/test_connect_redesign_regressions.py -k tailscale -q
```
Expected: clean; guard PASS.

- [ ] **Step 5: Headless construction smoke**

```bash
python - <<'PY'
import gi; gi.require_version("Gtk","4.0"); gi.require_version("Adw","1")
from gi.repository import Adw
Adw.init()
from big_remote_play.ui.private_network_view import PrivateNetworkView
PrivateNetworkView(None, mode="connect", vpn_provider="tailscale")
PrivateNetworkView(None, mode="create", vpn_provider="tailscale")
print("OK")
PY
```
Expected: `OK` (run on the VM after deploy if no local display). Fix any constructor arg mismatch by matching the real `PrivateNetworkView.__init__` signature.

- [ ] **Step 6: Full suite + commit**

```bash
python -m pytest -q
git add src/big_remote_play/ui/private_network_view.py tests/test_connect_redesign_regressions.py
git commit -m "feat(vpn): prominent browser login on Tailscale form; auth key now advanced"
```

---

## Task 7: Live verification on the test VM + i18n

**Files:** none (verification). Uses `linux-ui-a11y` `aigui` against `bruno@192.168.1.21` (pass `big`). See `[[test-vm-aigui]]` memory for the single-instance/`.mo` gotchas.

- [ ] **Step 1: Deploy changed files to the VM package**

```bash
PKG=/usr/lib/python3.14/site-packages/big_remote_play
for f in ui/guest_view.py ui/main_window.py ui/private_network_view.py; do
  sshpass -p big scp -o StrictHostKeyChecking=no "src/big_remote_play/$f" "bruno@192.168.1.21:/tmp/dep_$(basename $f)"
  sshpass -p big ssh -o StrictHostKeyChecking=no bruno@192.168.1.21 "sudo cp $PKG/$f $PKG/$f.bak2 2>/dev/null; sudo cp /tmp/dep_$(basename $f) $PKG/$f"
done
sshpass -p big ssh -o StrictHostKeyChecking=no bruno@192.168.1.21 "python -c 'import big_remote_play.ui.guest_view, big_remote_play.ui.main_window, big_remote_play.ui.private_network_view; print(\"IMPORT_OK\")'"
```
Expected: `IMPORT_OK`.

- [ ] **Step 2: Kill the single-instance primary (cmdline is `big_remote_play`, not the hyphen)**

```bash
sshpass -p big ssh -o StrictHostKeyChecking=no bruno@192.168.1.21 'pkill -9 -f big_remote_play; sleep 2; echo "left=$(pgrep -fc big_remote_play)"'
```
Expected: `left=0`.

- [ ] **Step 3: Launch fresh (forced PT) and screenshot Connect — empty state**

```bash
cd /home/bruno/.claude/skills/linux-ui-a11y
export AIGUI_HOST=bruno@192.168.1.21 AIGUI_PASS=big
scripts/aigui launch brpv -- env LANGUAGE=pt LANG=pt_BR.UTF-8 /usr/bin/big-remote-play
sleep 7
scripts/aigui act "=Conectar ao servidor" >/dev/null 2>&1 || scripts/aigui tap 171 137
sleep 3
scripts/aigui --out /tmp/verify_connect_empty.png shot
```
Then `Read` `/tmp/verify_connect_empty.png`. Expected: single column; compact empty state with the 4 routed options (Procurar, Configurar Rede Privada highlighted, Conectar com PIN, Manual); no clipped card corners; quality settings collapsed under "Ajustar qualidade".

- [ ] **Step 4: Screenshot Rede Privada selector + Tailscale form**

```bash
scripts/aigui act "=Configurar Rede Privada" >/dev/null 2>&1
sleep 3
scripts/aigui --out /tmp/verify_vpn_selector.png shot
```
`Read` it. Expected: role-framing intro on top; comparison table collapsed behind "Comparar as opções". Then pick Tailscale and screenshot the form; expect "Entrar com o navegador" prominent and auth key under an "advanced" expander.

- [ ] **Step 5: Verify host-list NOT clipped with items**

Inject sample rows via a headless render (same pattern as the earlier peer-row check) OR, if real hosts exist on the VM LAN, screenshot the populated list. Confirm first/last rows and card corners are fully visible.

- [ ] **Step 6: i18n — confirm new strings extract and are wrapped**

Run locally:
```bash
grep -nE '_\("(No host found yet|Set up Private Network|Adjust quality|Sign in with browser|Compare the options)' src/big_remote_play/ui/*.py
```
Expected: each new user-facing string is inside `_()`. (CI auto-translator fills catalogs on push — no manual `.po` edits.)

- [ ] **Step 7: Restore VM to packaged state (leave clean) — optional**

The `.bak2` copies are the pre-deploy packaged files. To revert: `sudo cp $PKG/<f>.bak2 $PKG/<f>`. Otherwise leave the improved code deployed. Stop the app: `pkill -9 -f big_remote_play`.

---

## Task 8: Final gate + finish

- [ ] **Step 1: Whole-project gate**

Run:
```bash
cd /home/bruno/codigo-pacotes/big-remote-play
ruff check src/ tests/ && ruff format --check src/ tests/
python -m mypy src/big_remote_play/ui/guest_view.py src/big_remote_play/ui/main_window.py src/big_remote_play/ui/private_network_view.py 2>&1 | tail -15
python -m pytest -q
```
Expected: lint clean; mypy introduces no NEW errors vs baseline (pre-existing GUI-binding warnings allowed); all tests pass.

- [ ] **Step 2: Fresh-eyes diff review**

Run: `git diff main...HEAD` and re-read each hunk against the spec's success criteria (novice path, clipping, single column, collapsed settings, selector framing, browser login). Scope: correctness + stated requirements only.

- [ ] **Step 3: Finish the branch**

Invoke `superpowers:finishing-a-development-branch` to choose merge/PR. Do NOT push or open a PR without explicit user approval (per project rules).

---

## Self-Review (author)

- **Spec coverage:** clipping (T1), intent-branched empty state → Rede Privada/PIN (T2), single column + guidance subtitle + footer (T3), quality expander (T4), selector role framing + collapsed comparison (T5), Tailscale browser-login + advanced key (T6), live novice verification + i18n (T7), gate (T8). All spec sections mapped.
- **Placeholders:** none — each code step shows the code; navigation hooks (`navigate_to("vpn_selector")`, `set_visible_child_name`) are concrete and verified to exist.
- **Type/name consistency:** `_build_discover_empty_state`, `_go_to_private_network`, `_quality_summary`, `quality_expander`, `method_stack`, `main_connect_btn`, `resolution_row`/`fps_row`/`audio_row` match existing `guest_view.py` symbols. Rede Privada handler `_on_action` (line 490), button `_btn_action` (~388), key `_e_authkey` (574) verified in `private_network_view.py`. No undefined references remain.
