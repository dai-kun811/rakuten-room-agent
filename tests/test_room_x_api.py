from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from urllib.parse import parse_qs, urlparse

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from room_x_api import DpapiTokenStore, OAuthTokenBundle, XApiClient, XApiError, XPostResultUncertain
from room_x_auth_setup import build_authorization_request, exchange_authorization_code


class FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, object]):
        self.status_code = status_code
        self.payload = payload

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        return self.response


class RoomXApiTest(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "DPAPI is Windows-only")
    def test_dpapi_token_store_round_trip_does_not_write_plaintext(self) -> None:
        bundle = OAuthTokenBundle.from_mapping(
            {
                "access_token": "access-secret",
                "refresh_token": "refresh-secret",
                "expires_at": "2026-11-13T00:00:00+00:00",
                "scopes": "tweet.read users.read tweet.write media.write offline.access",
            }
        )
        with tempfile.TemporaryDirectory(dir=Path(".local/test-tmp")) as temporary:
            path = Path(temporary) / "token.dpapi"
            store = DpapiTokenStore(path)
            store.save(bundle)
            encrypted = path.read_bytes()
            loaded = store.load()
        self.assertNotIn(b"access-secret", encrypted)
        self.assertEqual(loaded, bundle)

    def test_pkce_request_has_exact_minimum_scopes_and_loopback_callback(self) -> None:
        url, state, verifier = build_authorization_request(
            client_id="client",
            redirect_uri="http://127.0.0.1:8765/callback",
            state="state-1",
            verifier="verifier-1",
        )
        query = parse_qs(urlparse(url).query)
        self.assertEqual(query["state"], [state])
        self.assertEqual(query["redirect_uri"], ["http://127.0.0.1:8765/callback"])
        self.assertEqual(
            set(query["scope"][0].split()),
            {"tweet.read", "users.read", "tweet.write", "media.write", "offline.access"},
        )
        self.assertEqual(verifier, "verifier-1")

    def test_code_exchange_requires_refresh_token_and_all_scopes(self) -> None:
        session = FakeSession(
            FakeResponse(
                200,
                {
                    "access_token": "access",
                    "refresh_token": "refresh",
                    "expires_in": 7200,
                    "scope": "tweet.read users.read tweet.write media.write offline.access",
                    "token_type": "bearer",
                },
            )
        )
        bundle = exchange_authorization_code(
            session,
            client_id="client",
            client_secret="",
            redirect_uri="http://127.0.0.1:8765/callback",
            code="code",
            verifier="verifier",
            now=datetime(2026, 11, 12, tzinfo=timezone.utc),
        )
        self.assertEqual(bundle.refresh_token, "refresh")
        self.assertIn("offline.access", bundle.scopes)

    def test_create_promotion_sends_paid_partnership_field(self) -> None:
        session = FakeSession(FakeResponse(201, {"data": {"id": "post-1"}}))
        result = XApiClient(session=session).create_post(
            "token", text="copy #PR", paid_partnership=True
        )
        self.assertEqual(result["id"], "post-1")
        self.assertTrue(session.calls[0][1]["json"]["paid_partnership"])

    def test_create_helpful_omits_paid_partnership_field(self) -> None:
        session = FakeSession(FakeResponse(201, {"data": {"id": "post-1"}}))
        XApiClient(session=session).create_post(
            "token", text="helpful", paid_partnership=False, media_ids=("media-1",)
        )
        payload = session.calls[0][1]["json"]
        self.assertNotIn("paid_partnership", payload)
        self.assertEqual(payload["media"], {"media_ids": ["media-1"]})

    def test_network_error_after_create_boundary_is_uncertain(self) -> None:
        session = FakeSession(error=requests.Timeout("timeout"))
        with self.assertRaises(XPostResultUncertain):
            XApiClient(session=session).create_post(
                "token", text="copy", paid_partnership=False
            )

    def test_server_error_after_create_boundary_is_uncertain(self) -> None:
        session = FakeSession(FakeResponse(503, {"title": "unavailable"}))
        with self.assertRaises(XPostResultUncertain):
            XApiClient(session=session).create_post(
                "token", text="copy", paid_partnership=False
            )

    def test_token_bundle_rejects_missing_scope(self) -> None:
        with self.assertRaisesRegex(XApiError, "media.write"):
            OAuthTokenBundle.from_mapping(
                {
                    "access_token": "a",
                    "refresh_token": "r",
                    "expires_at": "2026-11-12T00:00:00+00:00",
                    "scopes": "tweet.read users.read tweet.write offline.access",
                }
            )


if __name__ == "__main__":
    unittest.main()
