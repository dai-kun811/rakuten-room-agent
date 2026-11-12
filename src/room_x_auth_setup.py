"""One-time interactive OAuth 2.0 PKCE setup for the official X API.

Running this module changes the connected X account authorization, so it is
never invoked by the scheduled worker.  The resulting access and refresh
tokens are encrypted for the current Windows user with DPAPI.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import threading
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from room_x_api import DpapiTokenStore, OAuthTokenBundle, REQUIRED_SCOPES, TOKEN_URL, XApiError


AUTHORIZE_URL = "https://x.com/i/oauth2/authorize"


def build_authorization_request(
    *, client_id: str, redirect_uri: str, state: str | None = None, verifier: str | None = None
) -> tuple[str, str, str]:
    if not client_id.strip():
        raise XApiError("X OAuth client ID is required")
    parsed = urlparse(redirect_uri)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise XApiError("OAuth callback must be an HTTP loopback URL")
    actual_state = state or secrets.token_urlsafe(32)
    actual_verifier = verifier or secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(actual_verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id.strip(),
            "redirect_uri": redirect_uri,
            "scope": " ".join(sorted(REQUIRED_SCOPES)),
            "state": actual_state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    return f"{AUTHORIZE_URL}?{query}", actual_state, actual_verifier


def exchange_authorization_code(
    session: requests.Session,
    *,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    code: str,
    verifier: str,
    now: datetime | None = None,
) -> OAuthTokenBundle:
    auth = (client_id, client_secret) if client_secret else None
    try:
        response = session.post(
            TOKEN_URL,
            data={
                "code": code,
                "grant_type": "authorization_code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "code_verifier": verifier,
            },
            auth=auth,
            timeout=30,
        )
    except requests.RequestException as exc:
        raise XApiError("X OAuth code exchange failed") from exc
    if response.status_code != 200:
        raise XApiError(f"X OAuth authorization was rejected (HTTP {response.status_code})")
    try:
        payload: dict[str, Any] = response.json()
        issued = now or datetime.now(timezone.utc)
        return OAuthTokenBundle.from_mapping(
            {
                "access_token": payload["access_token"],
                "refresh_token": payload["refresh_token"],
                "expires_at": (issued + timedelta(seconds=int(payload["expires_in"]))).isoformat(),
                "scopes": payload.get("scope", ""),
                "token_type": payload.get("token_type", "bearer"),
            }
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise XApiError("X OAuth token response was invalid") from exc


def wait_for_callback(*, host: str, port: int, expected_state: str, timeout: int = 180) -> str:
    result: dict[str, str] = {}

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            query = parse_qs(urlparse(self.path).query)
            if query.get("state", [""])[0] != expected_state:
                self.send_response(400)
                body = "Authorization state did not match. You may close this window."
            elif not query.get("code", [""])[0]:
                self.send_response(400)
                body = "Authorization code was not returned. You may close this window."
            else:
                result["code"] = query["code"][0]
                self.send_response(200)
                body = "X authorization received. You may close this window."
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))

        def log_message(self, format: str, *args: object) -> None:
            return

    server = HTTPServer((host, port), CallbackHandler)
    timer = threading.Timer(timeout, server.shutdown)
    timer.start()
    try:
        server.handle_request()
    finally:
        timer.cancel()
        server.server_close()
    if "code" not in result:
        raise XApiError("X OAuth authorization was not completed")
    return result["code"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Authorize the official X API for scheduled posting")
    parser.add_argument("--client-id", default=os.environ.get("X_OAUTH_CLIENT_ID", ""))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--token-store",
        type=Path,
        default=Path(".local/room-worker/x-publisher/oauth-token.dpapi"),
    )
    args = parser.parse_args()
    redirect_uri = f"http://{args.host}:{args.port}/callback"
    try:
        authorization_url, state, verifier = build_authorization_request(
            client_id=args.client_id,
            redirect_uri=redirect_uri,
        )
        print("Xの認証画面を開きます。投稿権限と画像権限を確認してください。")
        if not webbrowser.open(authorization_url):
            print(authorization_url)
        code = wait_for_callback(host=args.host, port=args.port, expected_state=state)
        with requests.Session() as session:
            bundle = exchange_authorization_code(
                session,
                client_id=args.client_id,
                client_secret=os.environ.get("X_OAUTH_CLIENT_SECRET", ""),
                redirect_uri=redirect_uri,
                code=code,
                verifier=verifier,
            )
        DpapiTokenStore(args.token_store).save(bundle)
    except XApiError as exc:
        print(json.dumps({"status": "stopped", "reason": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"status": "authorized", "token_store": str(args.token_store)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
