# Router, NAT and firewall

Why playing over the internet sometimes fails, explained without networking jargon, and the safest fix for each case. In Big Remote Play the same information, with what your computer shows, is under **Play over the internet → Internet, router and firewall**.

## The recommended path: a private network

Install **Tailscale**, **ZeroTier** or **Headscale** on both computers and join the same network (see the [user guide](user-guide.md#play-over-the-internet)). This normally works behind any home router, including CGNAT, and needs **no port forwarding**. Sunshine stays invisible to the rest of the internet.

Use the rest of this page only if you choose direct access without a VPN, or to understand an error.

## Words you may meet

| Term | In plain words |
|---|---|
| **NAT** | Your router shares one internet address among every device at home. Other people on the internet cannot start a connection to a device inside unless the router is told where to send it. |
| **Port forwarding** | A router rule: “traffic arriving on port X goes to this computer”. |
| **UPnP** | A way for a program to ask the router to create that rule automatically. Many routers allow it; some disable it; it can silently fail. |
| **CGNAT** | Your internet provider also shares one public address among many customers. Then even a correct port forward on your router cannot work: the traffic stops at the provider. |
| **IPv6** | The newer internet address system. Devices often get their own global address, so NAT is not needed, but the router's IPv6 firewall still blocks incoming connections by default. |
| **DHCP reservation** | A router setting that gives a computer the same local address every time. Port forwards point at an address, so it must not change. |

## Is it CGNAT?

Signs that you are behind CGNAT:

- the router's status page shows a **WAN address starting with 100.64 to 100.127** (the `100.64.0.0/10` range), or a private range such as `10.x`/`192.168.x`;
- the WAN address differs from the public address that “what is my IP” websites show;
- you are on mobile data or a hotspot.

Big Remote Play shows “Possible CGNAT” when this computer itself has a carrier-grade address. It cannot see your router's WAN address, so “not detected” does not prove there is no CGNAT upstream.

**Fix:** use a private network (it works through CGNAT), or ask your provider for a public IPv4 address. Port forwarding will not help.

## IPv6

If both computers have global IPv6 addresses, a private network often finds a **direct** path even behind CGNAT. For direct access without a VPN you would need to allow the Sunshine ports in the router's IPv6 firewall for this one computer. Big Remote Play never changes router or IPv6 firewall settings.

## Direct access without a VPN (advanced)

This exposes Sunshine's streaming ports to the whole internet. Keep Sunshine updated, approve only trusted devices and prefer a private network for routine use.

1. **Give the game computer a fixed local address** with a DHCP reservation in the router.
2. **Forward only the streaming ports** to that address. With Sunshine's default base port 47989:

   | Protocol | Ports | Use |
   |---|---|---|
   | TCP | 47984, 47989, 48010 | pairing (HTTPS), HTTP, RTSP |
   | UDP | 47998, 47999, 48000 | video, control, audio |

   If you changed Sunshine's port, every number moves with it (TCP base−5, base, base+21; UDP base+9 to base+11).
3. **Never forward 47990** (base+1). It is Sunshine's administration panel.
4. **Allow the same ports in the computer's firewall.** In Share → Advanced, Big Remote Play can add the rules after showing you the exact list; the rules are permanent and the administration port stays closed.
5. **Test from a different network**, for example a phone on mobile data. A local test does not prove outside access.
6. **Remove the forwards** when you no longer need them.

### UPnP

Share → Advanced → **Try automatic port forwarding (UPnP)** tells Sunshine to ask the router for the forwards when sharing starts. It is off by default. The router may refuse, and a successful request does not prove access from outside. If you also allow the web UI from anywhere, Sunshine may map that port too — the app warns about that combination.

## Firewall on this computer

Linux distributions use different tools. Big Remote Play detects **firewalld** and **ufw** by their service state; rules managed directly with nftables or iptables need administrator rights to read and are not inspected.

- Over a **private network**, traffic arrives on the VPN interface (`tailscale0`, `zt…`). If a firewall blocks it, allow the streaming ports; nothing on the router changes.
- Big Remote Play never disables a firewall and never creates a rule without asking.
