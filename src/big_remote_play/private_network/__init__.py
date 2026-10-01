"""Private-network (VPN overlay) integration, independent of GTK.

Layers, from the UI down:

* :mod:`.service` — the facade the UI calls from worker threads;
* :mod:`.tailscale`, :mod:`.zerotier`, :mod:`.headscale` — one provider each,
  reporting a :class:`~.models.ProviderStatus` and only the actions it supports;
* :mod:`.tailscale_api`, :mod:`.zerotier_api`, :mod:`.headscale_api` — REST
  clients built on :mod:`.http`, which keeps credentials in headers and never
  follows redirects;
* :mod:`.credentials` — administrative API credentials in the Secret Service;
* :mod:`.history` — local record of streaming sessions that really started;
* :mod:`.diagnostics` — bounded reachability checks.

Nothing here imports GTK, so every module is testable without a display.
"""
