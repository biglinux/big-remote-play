# Connect-to-server page redesign — "Descoberta em foco"

Date: 2026-06-25
Scope: `src/big_remote_play/ui/guest_view.py` (Connect page) and the Rede Privada
flow — `main_window.create_vpn_selector_page` + `private_network_view.py` (layout/
copy/prominence only).
Goal: let a non-technical user succeed end-to-end — reduce cognitive load on
"Conectar ao servidor", fix the host-list clipping, and make the Private Network
hand-off (the real enabler for internet play) novice-clear. **No change** to
discovery, connection, reconnect, VPN install/connect, or settings-persistence
logic — reuse existing handlers, rows, and the network paths unchanged.

## Problems (current state)

1. **Host-list clipping.** `self.hosts_list` (`Gtk.ListBox` + `boxed-list`) sits
   flush inside a fixed `Gtk.ScrolledWindow` (`min_content_height=200`,
   `max_content_height=400`). With no inner padding the card's rounded
   top/bottom edges and the first/last overflowing rows are visually cut.
2. **Cognitive overload on first load.** Three equally-weighted connection
   methods (Descobrir / Manual / Código PIN) as tabs, an always-expanded
   7-row "Configurações do Cliente" group, and a side helper card all compete
   for attention before the user does the one thing they came for: pick a host.
3. **No guidance — and worse, the wrong fallback for novices.** Nothing explains
   that discovery is automatic only when both PCs share a local network or a
   Private Network (VPN). The common case for a non-technical user is "play with
   a friend over the internet" — where discovery (and PIN, which uses
   broadcast/multicast) finds nothing until a Private Network exists. Pointing
   that user to **Manual** (type an IP) sends them to the *most* technical option
   they cannot complete. The real unlock is **Rede Privada (VPN) setup**, and the
   friendliest connect identifier is the **PIN** (6 digits a friend reads aloud),
   not an IP.

## Decisions (approved)

- Layout direction: **discovery-first** (list is the centerpiece).
- Manual & PIN: keep the existing `Adw.ViewStack` (lowest risk) but make
  **Descobrir** the visually primary tab; the empty-state and a footer line also
  route to Manual/PIN.
- Guidance copy: **fixed subtitle** under "Descobrir hosts" + **intent-branched
  empty state** that routes the novice to the path that actually unlocks their
  case (Private Network for internet play; PIN for a friend-dictated code),
  with Manual/IP demoted to an advanced option.
- Client settings: **single collapsed expander** with a live summary.
- **Plain language, minimal jargon:** "VPN" → "Rede Privada"; "host" → "o PC do
  amigo / este PC"; avoid "IP" in primary copy (only under the advanced option).

## Design

### 1. Method selector (top)
- Reuse `create_stack_tab_strip` over the existing `method_stack`
  (discover/manual/pin). Style so Descobrir reads as the default/primary tab;
  Manual and Código PIN remain available but visually quieter.
- Keep the existing help button (`help-about-symbolic`) in the switcher bar.

### 2. Discover page — single column, list-centered
- **Remove the side helper-card column.** The list + primary action take the
  full content width (the `Adw.Clamp` max stays ~1040, content single-column).
- Header: `Descobrir hosts` (heading) + fixed subtitle:
  *"Na rede local ou com a VPN configurada, o host aparece aqui
  automaticamente."* + refresh button (with tooltip + accessible label).
- **Host list — clipping fix:**
  - Lower `min_content_height` so few hosts render compact (no tall empty box).
  - Add vertical margin/padding inside the scroller so the `boxed-list` card
    edges are not flush with the viewport (no clipped corners).
  - Keep `propagate_natural_height=True` and a sane `max_content_height` so the
    list grows naturally and only scrolls when it genuinely overflows.
- **Empty state — intent-branched (the core novice fix):** replaces the current
  tall empty box and the `set_size_request(-1, 150)` placeholder with a compact
  card that offers, in plain language, the three real paths in priority order:
  - 🏠 *"Estão na mesma casa/rede? Toque em ⟳ para procurar de novo."*
    → re-runs `discover_hosts()`.
  - 🌐 *"Jogando com um amigo pela internet? Vocês precisam de uma Rede Privada
    (uma vez só)."* → **highlighted** button **"Configurar Rede Privada"** →
    `self._root_window().navigate_to("vpn_selector")` (guarded with `hasattr`).
    This is the actual unlock; after it, hosts appear automatically in Descobrir.
  - 🔑 *"O anfitrião te passou um código de 6 dígitos?"* → button
    **"Conectar com PIN"** → `method_stack.set_visible_child_name("pin")`.
  - Advanced, de-emphasized: *"Sei o endereço IP"* → `method_stack` → manual.
- **Primary action:** the existing `main_connect_btn` ("Conectar"), enabled only
  when a host is selected, directly under the list (shown once hosts exist).
- **Footer hint line (when hosts ARE listed):** one quiet line —
  *"Não encontrou o PC do amigo? Configure uma Rede Privada · Código PIN"* —
  links that route as above. Replaces the removed side helper card. Manual/IP is
  not promoted here; it lives in the empty-state advanced option and the switcher.

### 3. Client settings → collapsed expander
- Replace the always-open `settings_group` body with a single
  `Adw.ExpanderRow`-style disclosure titled **"Ajustar qualidade"** and a live
  subtitle summary, e.g. `1080p · 60 FPS · Áudio` (built from current
  resolution/fps/audio values; updated when they change).
- Expanding reveals the existing rows unchanged: Resolução, Resolução Nativa,
  Taxa de Quadros, Bitrate, Modo de Exibição, Áudio, Decodificação de Hardware,
  and the "Configurações avançadas do cliente" button.
- Collapsed by default. The reset button stays as the group/expander suffix.
- `apply_settings_btn` ("Aplicar e Reconectar") keeps its current
  visible-only-when-connected behavior, inside the expander.

## Components touched

- `setup_ui()` — move client settings into the collapsed expander; keep
  page composition (perf monitor, switcher box, settings).
- `create_discover_page()` — single column, fixed guidance subtitle, compact
  guiding empty state, clipping fix on the host scroller, footer hint line;
  drop the side helper-card column.
- `discover_hosts()` / `on_hosts_discovered` / `update_hosts_list()` — adjust the
  empty/placeholder rendering to the compact guiding state; discovery logic
  unchanged.
- A small helper to compute the quality summary string for the expander subtitle.
- Navigation hooks used by the empty state (all already exist): `discover_hosts()`,
  `self._root_window().navigate_to("vpn_selector")` (the main window exposes
  `navigate_to`; guard with `hasattr` for non-main roots/tests),
  `self.method_stack.set_visible_child_name("pin"|"manual")`.

Manual page, PIN page, connection handlers, reconnect, and settings persistence
are unchanged. No discovery/PIN/network logic changes — only what the empty state
*points the user toward*.

## Rede Privada (private network) — novice flow

The Connect page now routes the "internet" novice to **Configurar Rede Privada**,
so that destination must also be novice-clear. Two light-touch changes
(no change to VPN install/connect logic).

### A. VPN selector (`main_window.create_vpn_selector_page`)
- **Role framing at the top, plain language:** a short intro before the cards —
  *"Para jogar pela internet, vocês criam uma Rede Privada (uma vez só). Quem tem
  o jogo cria a rede; o amigo entra. Depois, o PC aparece sozinho em Conectar."*
- **Keep the 3 provider cards** (Tailscale stays "Recomendado · Iniciante"), but
  soften jargon in the card descriptions ("VPN de malha", "NAT e firewalls",
  "auto-hospedado com DNS Cloudflare" → plainer or moved to the comparison).
- **Collapse the "Comparação rápida" table** behind a disclosure ("▸ Comparar as
  opções"), collapsed by default — it invites analysis the novice doesn't need.
- "Como funciona" strip stays.

### B. Create/Connect form (`private_network_view`, Tailscale path)
- **Make browser login the prominent primary action:** a clear
  **"Entrar com o navegador"** button (triggers the existing connect flow with an
  empty auth key — the path already supported). No typing for the novice.
- **Demote the auth key:** move `_e_authkey` ("Auth Key") into an **advanced,
  collapsed** disclosure ("▸ Tenho uma chave de autenticação"). Keep the "Get auth
  key" link inside it.
- ZeroTier/Headscale forms unchanged in fields (their audiences are advanced); they
  still benefit from the role-framing intro on the selector.

Scope of Rede Privada changes is **layout/copy/prominence only** — VPN install,
connect, history, and status logic are unchanged.

## Out of scope

- Discovery/connection/reconnect logic, network code, settings storage.
- Manual and PIN page internals (only their prominence in the switcher changes).
- Translations: any new/changed strings go through `_()`; the CI auto-translator
  fills catalogs on push (existing project workflow).

## Success criteria

- Host list shows no clipped card corners or cut rows for 0, 1, few, and many
  hosts (verified via live screenshots on the test VM).
- First load presents one clear path (pick a host → Conectar); secondary methods
  and quality settings are present but not competing for first attention.
- **Novice success path:** a user who doesn't understand networks, playing with a
  friend over the internet, reaches a working connection without typing an IP —
  the empty state routes them to "Configurar Rede Privada" (one-time) or to PIN,
  in plain language. Manual/IP never blocks the primary path.
- Rede Privada: selector leads with a plain-language role framing; the
  recommended path (Tailscale) is obvious; the comparison table no longer demands
  attention up front; on the Tailscale form, browser login is the prominent
  action and the auth key is tucked behind "advanced".
- No regression in existing tests (`test_guest_view_a11y_regressions.py`,
  `test_drop_guest_validation.py`, CSS/premium-layout regressions, and any
  private-network tests); icon-only controls keep accessible names/tooltips;
  changed/new strings go through `_()`.
