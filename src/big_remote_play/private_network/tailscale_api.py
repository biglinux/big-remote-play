"""Tailscale API v2 client (``https://api.tailscale.com/api/v2``).

Two credential types are supported:

* **API access token** (``tskey-api-…``), owned by a user. It is the only type
  Tailscale accepts for *user invites* and *device invites* (sharing one
  machine), because both are scoped to a person.
* **OAuth client** (``tskey-client-…`` plus a client ID). Least privilege:
  the scopes are chosen when the client is created, and an access token is
  exchanged for at most one hour. Auth keys created with it must carry the
  client's tags.

An OAuth access token is cached only in memory.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import threading
import time
from typing import Any

from .http import ApiErrorKind, ApiResult, JsonHttpClient, Transport, path_segment

API_BASE = "https://api.tailscale.com/api/v2"
ADMIN_CONSOLE = "https://login.tailscale.com/admin"
_EMAIL_RE = re.compile(r"^[^@\s<>\"',;]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,63}$")
_TAG_RE = re.compile(r"^tag:[a-z0-9-]{1,63}$")


def valid_email(value: str) -> bool:
    return _EMAIL_RE.fullmatch((value or "").strip()) is not None


def valid_tags(tags: tuple[str, ...] | list[str]) -> bool:
    return all(_TAG_RE.fullmatch(tag) for tag in tags)


@dataclass(frozen=True)
class ApiDevice:
    device_id: str
    node_id: str
    name: str
    hostname: str
    addresses: tuple[str, ...]
    os: str = ""
    user: str = ""
    connected: bool | None = None
    last_seen: str = ""
    authorized: bool = True
    tags: tuple[str, ...] = ()


class TailscaleApi:
    def __init__(
        self,
        *,
        api_token: str = "",
        oauth_client_id: str = "",
        oauth_client_secret: str = "",
        oauth_tags: tuple[str, ...] = (),
        tailnet: str = "-",
        transport: Transport | None = None,
        clock=time.monotonic,
        timeout: float = 10.0,
    ) -> None:
        self._api_token = api_token
        self._client_id = oauth_client_id
        self._client_secret = oauth_client_secret
        self.oauth_tags = tuple(oauth_tags)
        self._tailnet = tailnet or "-"
        self._clock = clock
        self._lock = threading.Lock()
        self._access_token = ""
        self._expires_at = 0.0
        self._client = JsonHttpClient(API_BASE, auth_headers=self._auth_header, transport=transport, timeout=timeout)

    @property
    def uses_oauth(self) -> bool:
        return not self._api_token and bool(self._client_id and self._client_secret)

    @property
    def can_invite(self) -> bool:
        """User and device invites require a user-owned API access token."""
        return bool(self._api_token)

    def _auth_header(self) -> dict[str, str]:
        if self._api_token:
            return {"Authorization": f"Bearer {self._api_token}"}
        with self._lock:
            if not self._access_token or self._clock() >= self._expires_at:
                self._exchange()
            return {"Authorization": f"Bearer {self._access_token}"}

    def _exchange(self) -> None:
        if not (self._client_id and self._client_secret):
            raise PermissionError("no credential")
        result = self._client.post(
            "/oauth/token",
            form={"client_id": self._client_id, "client_secret": self._client_secret, "grant_type": "client_credentials"},
            authenticated=False,
        )
        data = result.data if isinstance(result.data, dict) else {}
        token = str(data.get("access_token") or "")
        if not result.ok or not token:
            raise PermissionError("OAuth token exchange failed")
        expires_in = data.get("expires_in")
        lifetime = float(expires_in) if isinstance(expires_in, (int, float)) else 3600.0
        self._access_token = token
        self._expires_at = self._clock() + max(60.0, lifetime - 60.0)

    def _tailnet_path(self, suffix: str) -> str:
        tailnet = self._tailnet if self._tailnet == "-" else path_segment(self._tailnet)
        return f"/tailnet/{tailnet}{suffix}"

    # ── queries ────────────────────────────────────────────────────────────
    def test(self) -> ApiResult:
        return self._client.get(self._tailnet_path("/devices"))

    def list_devices(self) -> tuple[list[ApiDevice], ApiResult]:
        result = self._client.get(self._tailnet_path("/devices"))
        if not result.ok:
            return [], result
        items = result.data.get("devices", []) if isinstance(result.data, dict) else []
        return [device for device in (self._device(item) for item in items) if device is not None], result

    @staticmethod
    def _device(item: Any) -> ApiDevice | None:
        if not isinstance(item, dict) or not item.get("id"):
            return None
        name = str(item.get("name") or "")
        return ApiDevice(
            device_id=str(item["id"]),
            node_id=str(item.get("nodeId") or ""),
            name=name.split(".")[0] if name else str(item.get("hostname") or ""),
            hostname=str(item.get("hostname") or ""),
            addresses=tuple(str(address) for address in item.get("addresses") or [] if address),
            os=str(item.get("os") or ""),
            user=str(item.get("user") or ""),
            connected=item.get("connectedToControl") if isinstance(item.get("connectedToControl"), bool) else None,
            last_seen=str(item.get("lastSeen") or ""),
            authorized=bool(item.get("authorized", True)),
            tags=tuple(str(tag) for tag in item.get("tags") or []),
        )

    def device_for_node(self, node_id: str) -> tuple[ApiDevice | None, ApiResult]:
        """The API device matching this computer's ``Self.ID`` from the CLI."""
        devices, result = self.list_devices()
        return next((device for device in devices if device.node_id == node_id), None), result

    # ── actions ────────────────────────────────────────────────────────────
    def create_auth_key(self, *, reusable: bool = False, ephemeral: bool = False, expiry_seconds: int = 3600, description: str = "Big Remote Play") -> tuple[str, ApiResult]:
        """A single-use, pre-approved key that expires quickly by default."""
        create: dict[str, Any] = {"reusable": bool(reusable), "ephemeral": bool(ephemeral), "preauthorized": True}
        if self.uses_oauth:
            if not self.oauth_tags or not valid_tags(self.oauth_tags):
                return "", ApiResult.failure(ApiErrorKind.BAD_REQUEST, "an OAuth client needs tags to create auth keys")
            create["tags"] = list(self.oauth_tags)
        body = {"capabilities": {"devices": {"create": create}}, "expirySeconds": int(max(300, min(expiry_seconds, 90 * 86_400))), "description": description[:50]}
        result = self._client.post(self._tailnet_path("/keys"), json_body=body)
        key = str(result.data.get("key") or "") if result.ok and isinstance(result.data, dict) else ""
        return key, result

    def invite_user(self, email: str, *, role: str = "member") -> tuple[str, ApiResult]:
        """Invite a person to the tailnet; returns the invite link."""
        if not self.can_invite:
            return "", ApiResult.failure(ApiErrorKind.FORBIDDEN, "invites need a user-owned API access token")
        if not valid_email(email):
            return "", ApiResult.failure(ApiErrorKind.BAD_REQUEST, "invalid email")
        result = self._client.post(self._tailnet_path("/user-invites"), json_body=[{"role": role if role in {"member", "admin"} else "member", "email": email.strip()}])
        return self._invite_url(result), result

    def share_device(self, device_id: str, *, email: str = "", multi_use: bool = False) -> tuple[str, ApiResult]:
        """Share only this device; the recipient gets no other access."""
        if not self.can_invite:
            return "", ApiResult.failure(ApiErrorKind.FORBIDDEN, "invites need a user-owned API access token")
        invite: dict[str, Any] = {"multiUse": bool(multi_use), "allowExitNode": False}
        if email:
            if not valid_email(email):
                return "", ApiResult.failure(ApiErrorKind.BAD_REQUEST, "invalid email")
            invite["email"] = email.strip()
        result = self._client.post(f"/device/{path_segment(device_id)}/device-invites", json_body=[invite])
        return self._invite_url(result), result

    @staticmethod
    def _invite_url(result: ApiResult) -> str:
        if not result.ok:
            return ""
        items = result.data if isinstance(result.data, list) else [result.data]
        url = next((str(item.get("inviteUrl")) for item in items if isinstance(item, dict) and item.get("inviteUrl")), "")
        return url if url.startswith("https://") else ""

    def authorize_device(self, device_id: str, authorized: bool = True) -> ApiResult:
        return self._client.post(f"/device/{path_segment(device_id)}/authorized", json_body={"authorized": bool(authorized)})

    def delete_device(self, device_id: str) -> ApiResult:
        return self._client.delete(f"/device/{path_segment(device_id)}")
