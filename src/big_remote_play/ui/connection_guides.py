"""Separate native guides for direct game access and for a Headscale VPN.

These dialogs only explain configuration and open official websites on request.
Opening a guide never opens ports, queries an external IP or changes a service.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw  # type: ignore

from big_remote_play.utils.i18n import _
from big_remote_play.utils.uri import open_uri
from .components import action_row, preferences_dialog, preferences_page

SUNSHINE_NETWORK_DOCS = "https://docs.lizardbyte.dev/projects/sunshine/latest/md_docs_2configuration.html#upnp"
MOONLIGHT_SETUP_DOCS = "https://github.com/moonlight-stream/moonlight-docs/wiki/Setup-Guide"
CLOUDFLARE_DNS_DOCS = "https://developers.cloudflare.com/dns/proxy-status/"
DIGITALPLAT_WEBSITE = "https://domain.digitalplat.org/"
HEADSCALE_DOCS = "https://headscale.net/stable/"


def _section(title: str, text: str) -> Adw.PreferencesGroup:
    group = Adw.PreferencesGroup(title=title)
    row = Adw.ActionRow(title=text, use_markup=False)
    row.set_title_lines(0)
    group.add(row)
    return group


def _link(title: str, url: str) -> Adw.ActionRow:
    row = action_row(title, _("Opens the official website"), "brp-dialog-information-symbolic", lambda: open_uri(row, url))
    return row


def _sheet(as_page: bool, title: str, groups: list[Adw.PreferencesGroup], *, description: str = "", height: int = 520) -> Adw.PreferencesDialog | Adw.NavigationPage:
    """A guide as a sheet, or as a page of Connect your devices (no dialogs for navigation there)."""
    if as_page:
        return preferences_page(title, groups, description=description)
    return preferences_dialog(title, groups, description=description, height=height)


def build_direct_internet_dialog(*, as_page: bool = False) -> Adw.PreferencesDialog | Adw.NavigationPage:
    """Public DNS and direct Sunshine access — deliberately NOT a VPN guide."""
    warning = _section(
        _("More exposed than a private VPN"),
        _(
            "This publishes the game computer’s address and exposes streaming ports to the internet. Cloudflare DNS-only does not protect this traffic. Prefer a VPN for routine use; keep Sunshine updated and approve only trusted devices."
        ),
    )
    connection = _section(
        _("1. Check internet reachability"),
        _(
            "You need a reachable public IPv4 address or working IPv6, plus permission to configure the router and firewall. A domain does not bypass CGNAT or double NAT. Ask your internet provider or use a VPN if incoming connections are blocked."
        ),
    )
    domain = _section(
        _("2. Choose a domain you control"),
        _(
            "Use an existing domain or register one. Services such as DigitalPlat offer free public domain names, subject to availability, renewal rules and terms. This is optional: a reachable public IP also works without a domain."
        ),
    )
    domain.add(_link(_("DigitalPlat — optional free domain"), DIGITALPLAT_WEBSITE))
    dns = _section(
        _("3. Point the name to the game computer"),
        _(
            "Add your domain to Cloudflare and set the nameservers it provides at your domain registrar. Then create an A record for your public IPv4 address, or an AAAA record for reachable IPv6. Select DNS only (gray cloud), not the orange HTTP proxy. Update the record when the address changes. This is not Cloudflare Tunnel or a VPN."
        ),
    )
    dns.add(_link(_("Cloudflare DNS-only documentation"), CLOUDFLARE_DNS_DOCS))
    ports = _section(
        _("4. Allow only the streaming ports"),
        _(
            "Use Sunshine’s port configuration and the official setup guide. With IPv4, forward the streaming ports to the game computer and allow them in its firewall; IPv6 needs appropriate firewall rules. UPnP is only an attempt, not proof that access works. Do not expose the Sunshine administration panel (47990 by default), and do not disable the firewall."
        ),
    )
    ports.add(_link(_("Sunshine port settings"), SUNSHINE_NETWORK_DOCS))
    ports.add(_link(_("Moonlight internet setup guide"), MOONLIGHT_SETUP_DOCS))
    play = _section(
        _("5. Pair and test from another network"),
        _(
            "Pair the devices on a trusted network first. Start sharing, then open Connect → I know the IP address and enter the domain name without https://. Use the configured port if it differs from the default. Test from outside your home network. Remove public port mappings when they are no longer needed."
        ),
    )
    return _sheet(
        as_page,
        _("Direct connection with a domain"),
        [warning, connection, domain, dns, ports, play],
        description=_(
            "Advanced alternative without a VPN. A domain only helps find the public address; video travels directly between the computers, not through Cloudflare’s web proxy. Nothing is configured automatically by this guide."
        ),
        height=660,
    )


def build_headscale_hosting_dialog(*, as_page: bool = False) -> Adw.PreferencesDialog | Adw.NavigationPage:
    """Self-hosting a control server still creates a private Tailscale network."""
    role = _section(
        _("This still creates a VPN"),
        _(
            "Headscale is a self-hosted control server for a private network. Its domain points to that control server, not directly to the game stream. For direct game access without a VPN, use the separate guide on the Connect your devices page."
        ),
    )
    needs = _section(
        _("What you need"),
        _("A Linux server that stays on (a small VPS is enough), a domain or subdomain you control, and SSH access to that server. Big Remote Play never changes a remote server by itself."),
    )
    install = _section(
        _("1. Install Headscale on the server"),
        _(
            "Follow the official installation page; the Debian/Ubuntu package is the recommended method and installs a systemd service. Keep the configuration in /etc/headscale/config.yaml and set server_url to your https address."
        ),
    )
    install.add(_link(_("Headscale documentation"), HEADSCALE_DOCS))
    tls = _section(
        _("2. Domain and HTTPS"),
        _(
            "Point an A (and AAAA, if the server has IPv6) record at the server. Use Headscale's built-in Let's Encrypt support or a reverse proxy that passes WebSocket upgrades. Cloudflare's proxy (orange cloud) and Cloudflare Tunnel are not supported by Headscale: keep that record DNS only."
        ),
    )
    tls.add(_link(_("Cloudflare DNS-only documentation"), CLOUDFLARE_DNS_DOCS))
    ports = _section(
        _("3. Open only the server's ports"),
        _(
            "Allow HTTPS (443/TCP) and, when using the embedded relay, STUN (3478/UDP) on the server firewall. These are not Sunshine's streaming ports, and the game computer needs no open port for this."
        ),
    )
    keys = _section(
        _("4. Connect Big Remote Play"),
        _(
            "On the server, create a user and an API key (“headscale apikeys create --expiration 90d”). In Big Remote Play add the server address and the key under API access. The API key manages the server; a pre-auth key only registers one computer."
        ),
    )
    join = _section(
        _("5. Join the private network on every computer"),
        _(
            "Open Connect your devices → Headscale → Set up, enter the server address, and approve each computer from Add device. Once the computers can reach one another privately, share on the game computer and connect using its private address."
        ),
    )
    return _sheet(
        as_page,
        _("Host a Headscale VPN server"),
        [role, needs, install, tls, ports, keys, join],
        description=_("For administrators who want to run their own VPN. This is separate from direct internet play through a public domain."),
        height=660,
    )


def build_internet_check_dialog(facts_loader=None, firewall_loader=None, *, as_page: bool = False) -> Adw.PreferencesDialog | Adw.NavigationPage:
    """Explain CGNAT, IPv6, UPnP and firewalls, with what this computer shows.

    The facts come from this computer's own interfaces; no external address
    lookup is made and nothing is changed.
    """
    from gi.repository import GLib  # type: ignore

    import threading

    from big_remote_play.integration_contracts import SUNSHINE_DEFAULT_BASE_PORT, sunshine_stream_ports, sunshine_web_ui_port
    from big_remote_play.private_network import diagnostics

    facts_loader = facts_loader or diagnostics.local_network_facts
    firewall_loader = firewall_loader or diagnostics.active_firewall

    this_pc = Adw.PreferencesGroup(title=_("What this computer shows"), description=_("Read from the network interfaces of this computer. Nothing is sent anywhere."))
    loading = Adw.ActionRow(title=_("Reading network interfaces…"), use_markup=False)
    this_pc.add(loading)

    recommended = _section(
        _("Recommended: a private network"),
        _("Tailscale, ZeroTier or Headscale usually work behind any router, including CGNAT, without opening ports and without exposing Sunshine to the internet."),
    )
    cgnat = _section(
        _("CGNAT"),
        _(
            "Some providers share one public address among many customers (carrier-grade NAT). Then no port forwarding can reach your computer. Signs: a router WAN address starting with 100.64–100.127, or a WAN address different from the public address websites see. Use a private network, or ask the provider for a public IPv4 address."
        ),
    )
    ipv6 = _section(
        _("IPv6"),
        _(
            "With a global IPv6 address, computers can often reach each other directly, even behind CGNAT. The router's IPv6 firewall still blocks incoming connections by default; Big Remote Play never changes it."
        ),
    )
    ports_list = sunshine_stream_ports(SUNSHINE_DEFAULT_BASE_PORT)
    ports = _section(
        _("Port forwarding (advanced)"),
        _(
            "Only for direct access without a VPN. Forward TCP {tcp} and UDP {udp} to this computer's fixed local address (a DHCP reservation). Never forward {web}: it is the administration panel."
        ).format(tcp=", ".join(map(str, ports_list["tcp"])), udp=", ".join(map(str, ports_list["udp"])), web=sunshine_web_ui_port(SUNSHINE_DEFAULT_BASE_PORT)),
    )
    ports.add(_link(_("Sunshine port settings"), SUNSHINE_NETWORK_DOCS))
    upnp = _section(
        _("UPnP"),
        _(
            "When enabled in Share → Advanced, the router tries to open the needed ports automatically when sharing starts. It is off by default, may be refused by the router and does not prove that access from outside works."
        ),
    )
    firewall = _section(
        _("Firewall on this computer"),
        _(
            "Over a private network the streaming ports must be allowed on the VPN interface. Share → Advanced can add rules for the streaming ports only, after you confirm; the administration panel stays closed."
        ),
    )

    def apply(facts, active) -> bool:
        this_pc.remove(loading)
        stack = {"dual": _("IPv4 and IPv6"), "ipv4": _("IPv4 only"), "ipv6": _("IPv6 only"), "none": _("No active connection found")}[facts.stack]
        rows = [
            (_("Internet protocols"), stack),
            (
                _("Possible CGNAT"),
                _("Yes — this computer has a carrier-grade NAT address. Port forwarding cannot work here; use a private network.")
                if facts.cgnat_suspected
                else _("Not detected on this computer. The router may still be behind CGNAT."),
            ),
            (_("Private networks active"), ", ".join(facts.overlay_interfaces) if facts.overlay_interfaces else _("None")),
            (_("Firewall service"), active or _("None detected (firewalld and ufw checked)")),
        ]
        for title, subtitle in rows:
            row = Adw.ActionRow(title=title, subtitle=subtitle, use_markup=False)
            row.set_subtitle_lines(0)
            row.set_subtitle_selectable(True)
            this_pc.add(row)
        return False

    def load() -> None:
        facts = facts_loader()
        active = firewall_loader()
        GLib.idle_add(apply, facts, active)

    threading.Thread(target=load, daemon=True).start()
    return _sheet(
        as_page,
        _("Internet, router and firewall"),
        [this_pc, recommended, cgnat, ipv6, ports, upnp, firewall],
        description=_("Why a connection over the internet can fail, and the safest way around each problem."),
        height=680,
    )
