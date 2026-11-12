"""Small official X API client used by the scheduled ROOM X publisher.

The module deliberately implements only the endpoints needed by this project:
OAuth refresh, one-shot image upload, and Post creation.  Browser scripting is
not a supported fallback.
"""
from __future__ import annotations

import base64
import ctypes
import json
import os
from ctypes import wintypes
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

import requests


TOKEN_URL = "https://api.x.com/2/oauth2/token"
CREATE_POST_URL = "https://api.x.com/2/tweets"
UPLOAD_MEDIA_URL = "https://api.x.com/2/media/upload"
REQUIRED_SCOPES = frozenset(
    {"tweet.read", "users.read", "tweet.write", "media.write", "offline.access"}
)


class XApiError(RuntimeError):
    """A confirmed X API or local configuration failure."""


class XPostResultUncertain(XApiError):
    """The create request crossed the submit boundary without a clear result."""


@dataclass(frozen=True)
class OAuthTokenBundle:
    access_token: str
    refresh_token: str
    expires_at: str
    scopes: tuple[str, ...]
    token_type: str = "bearer"

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "OAuthTokenBundle":
        scopes = value.get("scopes", ())
        if isinstance(scopes, str):
            scopes = scopes.split()
        bundle = cls(
            access_token=str(value.get("access_token", "")).strip(),
            refresh_token=str(value.get("refresh_token", "")).strip(),
            expires_at=str(value.get("expires_at", "")).strip(),
            scopes=tuple(sorted(str(item).strip() for item in scopes if str(item).strip())),
            token_type=str(value.get("token_type", "bearer")).strip().lower(),
        )
        if not bundle.access_token or not bundle.refresh_token or not bundle.expires_at:
            raise XApiError("OAuth token bundle is incomplete")
        missing = REQUIRED_SCOPES.difference(bundle.scopes)
        if missing:
            raise XApiError(f"OAuth token is missing required scopes: {','.join(sorted(missing))}")
        try:
            datetime.fromisoformat(bundle.expires_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise XApiError("OAuth token expiry is invalid") from exc
        return bundle

    def is_expiring(self, *, now: datetime | None = None) -> bool:
        current = now or datetime.now(timezone.utc)
        expiry = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
        return expiry <= current + timedelta(minutes=5)


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes) -> tuple[_DataBlob, Any]:
    buffer = ctypes.create_string_buffer(data)
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def _dpapi(data: bytes, *, protect: bool) -> bytes:
    if os.name != "nt":
        raise XApiError("DPAPI token storage is available only on Windows")
    input_blob, input_buffer = _blob(data)
    output_blob = _DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL
    function = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    description = "rakuten-room X OAuth" if protect else None
    flags = 0x1  # CRYPTPROTECT_UI_FORBIDDEN
    if protect:
        ok = function(
            ctypes.byref(input_blob),
            description,
            None,
            None,
            None,
            flags,
            ctypes.byref(output_blob),
        )
    else:
        ok = function(
            ctypes.byref(input_blob),
            None,
            None,
            None,
            None,
            flags,
            ctypes.byref(output_blob),
        )
    if not ok:
        error = ctypes.get_last_error()
        raise XApiError(f"Windows DPAPI failed: {ctypes.FormatError(error)} ({error})")
    # Keep the ctypes allocation referenced through the native call.
    del input_buffer
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)


class DpapiTokenStore:
    """Current-Windows-user encrypted token storage with atomic replacement."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> OAuthTokenBundle:
        if not self.path.is_file():
            raise XApiError("X OAuth token store is not configured")
        try:
            clear = _dpapi(self.path.read_bytes(), protect=False)
            return OAuthTokenBundle.from_mapping(json.loads(clear.decode("utf-8")))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise XApiError("X OAuth token store cannot be read") from exc

    def save(self, bundle: OAuthTokenBundle) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        encrypted = _dpapi(
            json.dumps(asdict(bundle), ensure_ascii=False, sort_keys=True).encode("utf-8"),
            protect=True,
        )
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_bytes(encrypted)
        os.replace(temporary, self.path)


class XApiClient:
    def __init__(self, *, session: requests.Session | None = None, timeout_seconds: int = 30):
        self.session = session or requests.Session()
        self.timeout_seconds = timeout_seconds

    def refresh_token(
        self,
        bundle: OAuthTokenBundle,
        *,
        client_id: str,
        client_secret: str = "",
        now: datetime | None = None,
    ) -> OAuthTokenBundle:
        if not bundle.is_expiring(now=now):
            return bundle
        if not client_id.strip():
            raise XApiError("X OAuth client ID is not configured")
        data = {
            "refresh_token": bundle.refresh_token,
            "grant_type": "refresh_token",
            "client_id": client_id.strip(),
        }
        auth = (client_id.strip(), client_secret) if client_secret else None
        try:
            response = self.session.post(
                TOKEN_URL,
                data=data,
                auth=auth,
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise XApiError("X OAuth refresh request failed") from exc
        if response.status_code != 200:
            raise XApiError(f"X OAuth refresh was rejected (HTTP {response.status_code})")
        try:
            payload = response.json()
            scope = payload.get("scope", " ".join(bundle.scopes))
            refreshed = OAuthTokenBundle.from_mapping(
                {
                    "access_token": payload["access_token"],
                    "refresh_token": payload.get("refresh_token", bundle.refresh_token),
                    "expires_at": (
                        (now or datetime.now(timezone.utc))
                        + timedelta(seconds=int(payload.get("expires_in", 7200)))
                    ).isoformat(),
                    "scopes": scope,
                    "token_type": payload.get("token_type", "bearer"),
                }
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise XApiError("X OAuth refresh response was invalid") from exc
        return refreshed

    def upload_image(self, access_token: str, path: Path) -> str:
        image_path = Path(path)
        if image_path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
            raise XApiError("helpful visual must be JPG, PNG, GIF, or WEBP")
        try:
            content = image_path.read_bytes()
        except OSError as exc:
            raise XApiError("helpful visual cannot be read") from exc
        if not content or len(content) > 5 * 1024 * 1024:
            raise XApiError("helpful visual must be between 1 byte and 5 MB")
        try:
            response = self.session.post(
                UPLOAD_MEDIA_URL,
                json={
                    "media": base64.b64encode(content).decode("ascii"),
                    "media_category": "tweet_image",
                },
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise XApiError("X media upload request failed before Post creation") from exc
        if response.status_code != 200:
            raise XApiError(f"X media upload was rejected (HTTP {response.status_code})")
        try:
            media_id = str(response.json()["data"]["id"]).strip()
        except (KeyError, TypeError, ValueError) as exc:
            raise XApiError("X media upload response was invalid") from exc
        if not media_id:
            raise XApiError("X media upload response did not include an ID")
        return media_id

    def create_post(
        self,
        access_token: str,
        *,
        text: str,
        paid_partnership: bool,
        media_ids: tuple[str, ...] = (),
    ) -> Mapping[str, Any]:
        payload: dict[str, Any] = {"text": text}
        if paid_partnership:
            payload["paid_partnership"] = True
        if media_ids:
            payload["media"] = {"media_ids": list(media_ids)}
        try:
            response = self.session.post(
                CREATE_POST_URL,
                json=payload,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise XPostResultUncertain("X Post result is uncertain; automatic retry is stopped") from exc
        if response.status_code >= 500:
            raise XPostResultUncertain(
                f"X returned HTTP {response.status_code}; automatic retry is stopped"
            )
        if response.status_code != 201:
            raise XApiError(f"X Post creation was rejected (HTTP {response.status_code})")
        try:
            data = response.json()["data"]
            post_id = str(data["id"]).strip()
        except (KeyError, TypeError, ValueError) as exc:
            raise XPostResultUncertain(
                "X accepted the request but returned an invalid result; automatic retry is stopped"
            ) from exc
        if not post_id:
            raise XPostResultUncertain("X response omitted the Post ID; automatic retry is stopped")
        return data
