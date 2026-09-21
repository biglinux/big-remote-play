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
from .components import action_row, preferences_dialog

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


def build_direct_internet_dialog() -> Adw.PreferencesDialog:
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
    return preferences_dialog(
        _("Direct connection with a domain"),
        [warning, connection, domain, dns, ports, play],
        description=_(
            "Advanced alternative without a VPN. A domain only helps find the public address; video travels directly between the computers, not through Cloudflare’s web proxy. Nothing is configured automatically by this guide."
        ),
        height=660,
    )


def build_headscale_hosting_dialog() -> Adw.PreferencesDialog:
    """Self-hosting a control server still creates a private Tailscale network."""
    role = _section(
        _("This still creates a VPN"),
        _(
            "Headscale is a self-hosted control server for a private network. Its domain points to that control server, not directly to the game stream. For direct game access without a VPN, use the separate guide on the provider selection page."
        ),
    )
    prepare = _section(
        _("1. Prepare the VPN control server"),
        _(
            "The bundled installer uses a server you administer, Docker, a domain and Cloudflare DNS credentials. This is a custom deployment, not Headscale’s recommended installation. Check the official requirements first; do not reuse another service’s ports or credentials without reviewing them."
        ),
    )
    configure = _section(
        _("2. Follow the Headscale server requirements"),
        _(
            "Configure DNS, TLS and the control-server ports according to the Headscale documentation and your installation. These are not Sunshine’s streaming ports. The installer’s UPnP attempt may fail and does not bypass your internet provider’s restrictions."
        ),
    )
    configure.add(_link(_("Headscale documentation"), HEADSCALE_DOCS))
    join = _section(
        _("3. Join the private network on every computer"),
        _(
            "Use the administrator’s server address and authorization method. Once the computers can reach one another privately, share on the game computer and connect using its VPN address. Keep the Sunshine administration panel private."
        ),
    )
    return preferences_dialog(
        _("Host a Headscale VPN server"),
        [role, prepare, configure, join],
        description=_("For administrators who want to run their own VPN. This is separate from direct internet play through a public domain."),
        height=620,
    )
