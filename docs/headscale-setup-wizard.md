# Headscale setup wizard

**Connect your devices → Headscale → Set up** opens a step-by-step wizard. It is
written so that someone who has never heard of DNS or HTTPS can follow it by
reading the screen. Every page asks one thing, fills in what Big Remote Play
already knows, and checks the step before it is marked done. Technical words
are kept in **Technical details** and **Why?** expanders and on the Headscale
page's **Advanced** tab.

## The flow

```text
Set up Headscale
  "Where will your Headscale server run?"
  ├─ Another server (Recommended) ─► What is the server address?  [Check server]
  │      name found ✓  server answers ✓  HTTPS ✓  Headscale answers ✓
  │      ├─ problem ─► human sentence + Technical details
  │      │     ├─ Guided server setup (commands with the address filled in)
  │      │     └─ Set up a domain for it ─► (domain flow, "Points to" = that server)
  │      └─ Server found ─► Add this computer ─► Add another device ─► Finish
  └─ This computer
         Checking your internet connection ─► Your public IP (Detected automatically) [Copy]
         │   CGNAT evidence ─► "Your internet provider may block incoming connections."
         │                     [Use another server] [Continue anyway]
         How should your Headscale server be reached?
         ├─ Use a domain (Recommended)
         │    Do you already have a domain?
         │    ├─ Yes ─► Domain or subdomain + Where do you manage DNS? (Cloudflare | Other)
         │    └─ No  ─► Get a domain: DigitalPlat, step 1-3 ─► [Set up Cloudflare]
         │    Cloudflare: add domain → two nameservers → copy them to DigitalPlat → checked
         │    Point your domain to the server: A  headscale → 203.0.113.10, DNS only (gray cloud)
         │    [I added the record] ─► Domain is ready
         └─ Use the detected address (Advanced)
         Install Headscale: Headscale + Caddy ─► configure ─► HTTPS checked ─► network created
         │   not reachable ─► "Your router needs one more step" (or, with CGNAT, use a VPS)
         Add this computer ─► Add another device ─► Finish
```

Every page is pushed on the **Connect your devices** navigation, with the
header's **Back**. A caption says *Step 3 of 7 · DNS*.

## Progress

The first page shows the progress once a setup started:

```text
Headscale setup
  ✓ Server                     Done
  ✓ Domain                     Done
  ✓ DNS                        Done
  ✓ Secure connection (HTTPS)  Done
  ✓ Headscale                  Done
  → This computer              Next
  ○ Add another device         Not yet
[Continue setup]  [Start over]
```

With the detected address instead of a domain, *Domain* and *DNS* do not apply
and are left out. **Continue setup** opens the first step that is not verified.
**Start over** forgets the steps (after asking). It never removes anything from a
server.

The progress is saved in `~/.config/big-remote-play/headscale-setup.json`
(0600, atomic write): the choices (where, domain or address, DNS manager,
registrar), the domain, the address the record must point at, the optional
IPv6, nameservers the person pasted, the Headscale user, and when each step was
verified. **It never contains a key.** The Headscale card shows *Setup
incomplete · Continue setup* until the setup is finished. A damaged file
simply starts over.

### A check mark means it was checked

| Step | Marked when |
|---|---|
| Server | this computer: a public IPv4 was found and accepted · another server: the server check passed |
| Domain | the zone exists in DNS, and for Cloudflare its nameservers are Cloudflare's (or the two the person pasted) |
| DNS | the `A` record points at the expected address and only there, is not proxied, and no `AAAA` points elsewhere |
| Secure connection (HTTPS) | a TLS handshake with a certificate valid for the name succeeded |
| Headscale | `GET /health` answered `{"status":"pass"}` (this computer: and the network and API key were created and the key works) |
| This computer | `tailscale status` reports the Headscale profile connected |
| Add another device | Headscale lists at least one other device |

**Next** does not skip a check. Where a page has no check (a choice), it does
not mark anything.

## Public IP detection

`private_network/public_address.py`, used only when the person chooses
**This computer**, on a worker, and cached for 5 minutes (**Check again**
forces a new lookup):

1. **STUN** (RFC 5389 binding request, 20 bytes, no attributes) to
   `stun.cloudflare.com:3478`, then `stun.l.google.com:19302`, for IPv4 and
   for IPv6. The answer is the address the server saw.
2. Fallback **HTTPS**: `https://1.1.1.1/cdn-cgi/trace`, then
   `https://api.ipify.org?format=json`, and the IPv6 equivalents. No redirects,
   4 KB limit, 2.5 s timeout.
3. Every answer goes through `ipaddress`: private (192.168/16, 10/8,
   172.16/12), shared CGNAT (100.64/10), loopback, link-local, reserved and
   multicast are **never** taken as public. They are kept only in the technical
   details as "not public, ignored".

Nothing personal is sent: the STUN request has no content, and the HTTPS
requests have no cookies or identifiers.

IPv6 is suggested (an optional `AAAA` record) only when the IPv6 the internet
sees is an address of this computer's own interface (no IPv6 NAT). Even then,
the page warns to add it only if the router lets connections in over IPv6. A
wrong `AAAA` breaks IPv6 clients, so it is never added by default.

### CGNAT

Reported only on evidence (`assess_cgnat`):

- this computer's own interface has an address in 100.64.0.0/10;
- the router, asked through UPnP (`upnpc -s`, when installed), reports an
  internet address that is private or shared;
- the router reports a public address different from the one STUN saw.

With evidence: *Your internet provider may block incoming connections.
Headscale may work better on a VPS.* with **Use another server** and
**Continue anyway**. Without evidence nothing is claimed. If HTTPS later cannot
be reached from outside, the page explains CGNAT or the router depending on what
was found.

## Address: domain recommended

**Use a domain** is recommended. A certificate for a name is what Let's Encrypt
issues routinely, and the name survives an address change. **Use the detected
address** is marked *Advanced*: HTTPS certificates for a bare IP are harder,
and the HTTPS check says plainly if it did not work.

The server address is never typed twice:

- this computer + domain → `https://<domain>`;
- this computer + address → `https://<public IPv4>`;
- another server → what the person typed there.

The public IP of *this* computer is never used as the address of another
server. On the **Another server** path the record's *Points to* is the
server's address (the IP typed, or asked on the record page when only a name
was given).

## DNS checks (no `dig`)

`private_network/dns_check.py` uses **DNS over HTTPS** (JSON) at
`cloudflare-dns.com`, falling back to `dns.google`. Only the name being
checked is sent. Public resolvers are used on purpose: the local resolver may
cache an old answer for hours while the person is waiting for their change.

- **Nameservers**: the zone of a name is found from the SOA in the authority
  section (`headscale.me.dpdns.org` → `me.dpdns.org`). Its NS set is
  *Cloudflare* when every name ends in `.ns.cloudflare.com`, or *expected*
  when it contains the two the person pasted.
- **Records**: `A`/`AAAA` must contain the expected address and nothing else.
  An answer inside Cloudflare's published proxy ranges means the orange cloud
  is on, and the page says *Click the orange cloud in Cloudflare so it turns gray
  (DNS only)*.
- Results in words: *Domain is ready* · *We are still waiting for DNS — This can
  take a little while.* · *Your domain is not pointing to this server yet. It
  points to …* · *The domain could not be checked*. **Check again** repeats
  the check.

## DigitalPlat (no domain yet)

**Get a domain** explains that a domain can be registered with a provider and
offers DigitalPlat ([dashboard.digitalplat.org](https://dashboard.digitalplat.org)),
which gives free names (for example `*.dpdns.org`) that are delegated to a DNS
provider. The steps stay on screen while the browser is open:

1. *Create or sign in to your DigitalPlat account.* **Open DigitalPlat**
2. *Choose Register, check the domain name you want, and read the current
   availability and rules shown by DigitalPlat.* The page asks for the domain
   registered (for example `mydomain.dpdns.org`). The server becomes
   `headscale.mydomain.dpdns.org`.
3. *DigitalPlat needs the nameservers from your DNS provider.* **Set up
   Cloudflare**

DigitalPlat only delegates the name. Records are never created there. The
wizard sends the person to Cloudflare for them.

## Cloudflare

**Set up DNS with Cloudflare** ([dash.cloudflare.com](https://dash.cloudflare.com)),
four steps inside the page:

1. *Add your domain to Cloudflare.* The zone, with **Copy**. **Open Cloudflare**
2. *Cloudflare will show two nameservers.* (`xxxx.ns.cloudflare.com`,
   `yyyy.ns.cloudflare.com`). An optional field takes them, so the check can
   compare exactly. Names that are not Cloudflare's are refused.
3. *Copy these two Cloudflare nameservers to DigitalPlat.* (or to the
   registrar), with **Copy** for each and **Open DigitalPlat**.
4. *Checking nameservers…* → *Cloudflare is now managing your domain*.

When the domain already uses Cloudflare's nameservers, the guide is skipped.
The record page then shows exactly what to add, with **Copy** on every value:

| Field | Value |
|---|---|
| Type | `A` |
| Name | `headscale` (the part before the zone; `@` for the zone itself) |
| IPv4 address | the detected public IP (or the server's) |
| Proxy status | **DNS only** — *the cloud must be gray.* |

*Keep Proxy status = DNS only: Headscale needs a direct connection to your
server. Do not use Cloudflare Tunnel for it either.* **Why?** explains that the
proxy only passes ordinary web requests, while Tailscale clients keep a
long-lived, upgraded HTTP connection to Headscale.

## Installing Headscale on this computer

**Install Headscale** first checks this computer, before anything is
installed:

- **Web ports.** HTTPS needs ports 80 and 443 for Caddy. If another program
  already listens there (a web server, a Docker container), the page says
  *Another program already uses the web ports (80 and 443) on this computer.*
  It keeps **Set up the server** off and offers **Use another server**.
  When Caddy's own service is running, the ports are its own and a site is
  added. This check runs as the user (`ss -ltnH`), so it cannot see who
  listens. The helper repeats it as root and names the programs.
- **A server already there.** If the address already answers as a working
  Headscale, the page says *A Headscale server already answers at this
  address.* and offers **Use this server** (the *Another server* path)
  instead of installing a second one.

Then it shows Headscale and Caddy with **Installed** or **Not installed**. It reuses the shared installer (Pamac, or the reviewed
`install-components.sh` with the new component ids `headscale_server` and
`caddy`). After an installation it continues by itself. Otherwise
**Set up the server** runs the rest:

1. `pkexec headscale-server-helper.sh configure <host>`. It refuses with
   `RESULT=ports_busy` before changing anything when ports 80/443 belong to
   another program. Headscale's own listener moves to the first free loopback
   port (8080, else 18080–18089); the metrics and gRPC listeners move the same
   way, but only from their defaults. The port chosen is reported (`LISTEN`)
   and saved in the progress file, so the wizard reaches the local API there.
   Then it:
   - points `server_url` at `https://<host>`;
   - moves `dns.base_domain` off `example.com` (or off the server's own name) to
     `brp.internal`, as Headscale requires;
   - writes `/etc/caddy/conf.d/big-remote-play-headscale.caddy`
     (`<host> { reverse_proxy 127.0.0.1:<port> }`, with the port chosen
     above, for example `reverse_proxy 127.0.0.1:18080` when 8080 is taken),
     validates the Caddy configuration, enables and starts both services, and
     waits for `http://127.0.0.1:<port>/health`.
2. `firewall`: allows HTTP and HTTPS in firewalld or ufw, when one is active.
3. HTTPS check, for up to 90 s while Caddy obtains the certificate:
   `check_server(https://<host>, connect_host=127.0.0.1)`. The certificate is
   verified for the *name*, so success also proves that Let's Encrypt reached
   this computer from the internet. It also works when the router does not loop
   this computer's own traffic back.
4. `create-user <name>` (the login name, else `big-remote-play`) and
   `create-apikey` (90 days). The key goes through a pipe into the system
   keyring and is tested against the local API.

Progress rows: *Install Headscale and Caddy*, *Configure and start the
server*, *Check the secure connection (HTTPS)*, *Create your private
network*, each *Not yet / Working… / Done / Did not work*.

When HTTPS cannot be reached:

- **Router**: *Your router needs one more step.* Open its settings page, add
  two rules (ports 80 and 443 TCP → this computer's LAN address), come back and
  **Check again**.
- **CGNAT**: *Your internet provider blocks incoming connections. Changing the
  router will probably not help.* **Use a VPS instead**.

Advanced details: Headscale listens only on the loopback port chosen above
(127.0.0.1, 8080 or the first free one of 18080–18089), and Caddy is the only
listener on the network (80 and 443/TCP). UDP 3478 is not needed, because the embedded
DERP stays off and Tailscale's public relays are used.

### The helper and its safety

`usr/share/big-remote-play/scripts/headscale-server-helper.sh`, PolicyKit
action `br.com.biglinux.remoteplay.headscale-server` (`auth_admin_keep`,
active local session only):

- one command per run: `configure HOST`, `firewall`, `create-user NAME`,
  `create-apikey`, `status`, `unconfigure`, `hosts-pin HOST`,
  `hosts-unpin HOST`. Arguments are validated in the
  script (host: lower-case domain or IPv4; name: `[a-z0-9][a-z0-9._-]{0,62}`).
  Anything else exits with `RESULT=invalid` before touching a file;
- an existing Headscale configuration is changed only while `server_url` is
  still the package default or the address this helper wrote, and only while
  `listen_addr` is 127.0.0.1 with one of the helper's ports (8080 or
  18080–18089); a custom listen address is refused with
  `RESULT=custom_listen`. An existing Caddy site file of the same name
  is replaced only if this helper wrote it. Otherwise nothing changes and the
  page says so;
- the original configuration is kept once as `config.yaml.brp-backup`, and
  **Remove the server setup** (Advanced) restores it, removes the site file,
  stops Headscale, and disables Caddy again when it was the helper that
  enabled it;
- files are written atomically with the original owner and mode;
- the API key appears once on the helper's stdout as `BRP_DATA API_KEY=…`. The
  application never logs it, never shows it, and stores it only in the keyring.

The helper uses the distribution's packages, handles no Cloudflare or other
DNS token and keeps Headscale off the network.

## Another server

**What is the server address?** accepts a domain, `https://…`, an IPv4 or
`[IPv6]:port`. `http://`, paths, user info and spaces are refused before any
check. **Check server** (`check_server`) reports, in this order:

| Step | Failure in words | Technical detail |
|---|---|---|
| Name found | *This name does not lead to a server yet.* | resolver error |
| Server answers | *The Headscale server is not reachable.* | *Connection refused on port 443.* / no answer / no route |
| Secure connection (HTTPS) | *The server's secure certificate is not valid.* / *A secure connection could not be made.* | verify message (expired, hostname mismatch, self-signed) |
| Headscale answers | *Something answers there, but it is not a Headscale server.* | HTTP status of `/health` |

When the check fails and the name leads to **this computer** (its public
address or a local one), for example Headscale in a container here, it is
checked again through 127.0.0.1 with the same name and certificate. Routers
without hairpin NAT answer a computer's own public address themselves, with
their own self-signed certificate, so the first check always fails there. If the
direct check passes, the page says *This server runs on this computer…*.
**Next** then asks once (PolicyKit) to add `127.0.0.1 <name>  # big-remote-play
headscale` to `/etc/hosts` (`hosts-pin`), so the API calls and the Tailscale app
reach it directly too. A name someone else already set there is left alone.

A failed check always offers a way out: **Guided server setup**,
**Check again**, **Use a different address** and **Start over**.

**Guided server setup** lists the commands for a VPS with the typed address
already in them: install (Arch, or Debian/Ubuntu `.deb` from the releases
page), `server_url`, Caddy, ports, `headscale users create`, `headscale
apikeys create`. Each one can be copied. Big Remote Play does not connect to
another computer: no SSH is used.

## Adding this computer

- **This computer** (or a remote server whose API key is known): the API key
  creates a **pre-auth key** for the user (single use, 1 hour), and
  `tailscale up --login-server=https://<host> --auth-key=file:…` joins with it.
  The key travels as a file reference, never in argv.
- **Another server**, three choices:
  - *I manage this server*: paste the **API key**. It is stored in the
    keyring, tested, and then used like above (creating the user if needed);
  - *I have a key from the server owner*: a **pre-auth key**, used once and
    never saved;
  - *Ask the server owner to approve*: this computer asks to join. The page
    shows the command for the owner
    (`headscale auth register --auth-id <id> --user <user>`) and continues by
    itself once it is approved.
- If Tailscale is active, the wizard first asks **Switch from Tailscale to
  Headscale?**. Joining adds a new profile; the Tailscale account stays saved.
- The profile is recorded as Headscale (`provider`, `login_server`), so the
  cards and pages attribute it correctly.

### API key or pre-auth key: never confused

- an API key (`hskey-api-…`) administers the server and is stored only in the
  keyring;
- a pre-auth key (`hskey-auth-…`, or 48 hex characters before 0.28) adds one
  computer and is never stored;
- each field refuses the other kind with a sentence that says which is which,
  and `CredentialStore` refuses to save a pre-auth key as an API key;
- **Add another device** offers *Create a one-time key*: a pre-auth key for one
  hour, shown as *Hidden. Use Copy.* An API key is never shown to a guest.

## Add another device

**Approve a device.** A phone or computer joining with the Tailscale app
(*Use an alternate server*) waits at the sign-in and shows a code, with the
command `headscale auth register --auth-id hskey-authreq-… --user USERNAME`.
Headscale has no API or command that lists waiting devices (checked against
0.29.3's `/api/v1`: only `auth/register`, `auth/approve` and `auth/reject`, by
code), so the code has to come from that screen. **Approve a device** (on this
step, and on the Headscale page's Devices tab when the API key works) accepts
the whole line, the link, the code, or only its 24 characters. It registers
the device through `POST /api/v1/auth/register` for the network's user
(never the `USERNAME` placeholder) and says *pixel-8 was added to your
network.* An expired code says to start the sign-in again on that device.

On the other computer: 1. install Big Remote Play (or the Tailscale app on a
phone), 2. open Connect your devices, 3. choose Headscale → Set up → Another
server, 4. use this server, with **Copy address**. **Check for the other
device** marks the step once Headscale lists another device. **Finish** returns
to the Headscale page, which now shows the connection.

## The Headscale page's Advanced tab

**Headscale server**:
- **Server address** and `server_url`, and the **API endpoint**;
- **Test connection**;
- **Continue setup** while unfinished.

For a server on this computer it also shows:
- `listen_addr`, the configuration file, the Caddy file and the database;
- DERP (Tailscale's relays);
- the `systemd` state of both services;
- **Server log** (`journalctl -u headscale`, redacted);
- **Remove the server setup**.

API keys stay under **API access**; users, pre-auth keys and approvals are under
**Manage network** and **Add device**.

**Test connection** answers in words (*The Headscale server works.* with the
version and certificate, or the failure sentence above), with the technical
detail in an expander, for example *tcp 203.0.113.10:443: refused*.

## Code

| Module | Role |
|---|---|
| `private_network/public_address.py` | STUN, HTTPS fallback, validation, CGNAT, cache |
| `private_network/dns_check.py` | DoH client, domain normalization, nameservers, records, Cloudflare proxy ranges |
| `private_network/headscale_server.py` | `check_server`, `LocalServer` (helper), `SetupProgress`/`SetupStore`, key kinds |
| `private_network/headscale_api.py` | users (`create_user`), pre-auth keys, nodes, registration approval |
| `ui/headscale_wizard.py` | the pages, `StepGuide` ("Step 1 of 4") |
| `ui/provider_page.py` | Set up → wizard; Advanced → Headscale server |
| `usr/share/big-remote-play/scripts/headscale-server-helper.sh`, `usr/share/polkit-1/actions/br.com.biglinux.remoteplay.headscale-server.policy` | the privileged part |

## Tests

- `tests/test_headscale_setup.py` (no GTK):
  - public IPv4 by STUN and by HTTPS, IPv6 only when it is this computer's;
  - private and shared addresses refused, CGNAT evidence cases, the cache, STUN decoding;
  - DNS: Cloudflare or expected nameservers for a subdomain's zone, other nameservers, missing domain, both resolvers down, the fallback resolver;
  - A and AAAA validation (ready, wrong, extra address, proxied, missing);
  - domain and server-address normalization, including invalid ones;
  - server check for every failure and the healthy case, and the loopback check;
  - progress order, persistence (0600, no secret), damaged file, start over;
  - API and pre-auth keys never swapped;
  - helper output parsing (the API key never in text or data), refusals;
  - the real helper refusing injected arguments and unknown commands.
- `tests/test_headscale_wizard.py`, every outside effect faked:
  - start; public IP shown and accepted; private never offered; CGNAT; IPv6;
  - existing Cloudflare domain to record to ready, with nothing retyped;
  - invalid domain; record problems (pending, proxied, wrong, bad AAAA) not marked done;
  - DigitalPlat → Cloudflare → nameservers checked; Cloudflare rejecting foreign nameservers;
  - detected address skipping the domain steps;
  - local server configured, checked, network created, key in the keyring and not in the file;
  - no password; router and CGNAT help;
  - server problems in words; invalid address; guided setup with the address filled in;
  - healthy server then joining with a one-time key; keys not swappable; owner approval command;
  - switching from Tailscale asks first; another device and its short-lived key;
  - resume after restart; start over; no check mark without a check;
  - returning to the server check after setting up its domain; Advanced Test connection.

## Limits

- This computer often runs other services. When ports 80/443 are already
  served by something else (another Caddy, nginx, a container), Big Remote
  Play does not try to share them. Use another server, or add Headscale to that
  existing reverse proxy yourself (the **Guided server setup** shows the Caddy
  lines). Containers and web applications often hold 80, 443 and 8080; a
  Headscale bound to a taken port would restart in a loop, which is why the
  helper checks the web ports first and picks a free loopback port for
  Headscale.

- Not run here against real services: a real Let's Encrypt certificate, a real
  router's port forwarding, real DigitalPlat or Cloudflare accounts, or a real
  Headscale server joining. These are in [release acceptance](release-testing.md).
- The local setup supports Arch-based systems (BigLinux, Manjaro) through
  pacman/Pamac packages. On other distributions the guided server setup is the
  path.
- From inside a home network, `https://<domain>` may not reach this same
  computer when the router does not loop traffic back (no "hairpin NAT"). The
  wizard's check avoids that, but this computer's own Tailscale client uses the
  public name. If the router lacks hairpin NAT, joining from this same computer
  can fail even though other devices work.
- An HTTPS certificate for a bare IP address depends on the certificate
  authority and Caddy's support. If it fails, the check says so and recommends
  a domain.
- CGNAT without UPnP and without a shared address on this computer cannot be
  detected beforehand. The HTTPS check then fails with the router/CGNAT
  explanation.
- DigitalPlat's availability and rules are theirs and may change. The guide
  asks the person to read them.
