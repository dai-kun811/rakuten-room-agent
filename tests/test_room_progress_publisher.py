from __future__ import annotations

import base64
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from room_progress_publisher import build_public_payload, publish_progress


class FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None) -> None:
        self.status_code = status_code
        self.payload = payload or {}

    def json(self) -> dict:
        return self.payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self) -> None:
        self.put_body = None

    def get(self, url: str, **_kwargs):
        if "/git/ref/heads/routine-state" in url:
            return FakeResponse(200)
        if "/contents/automation-progress.json" in url:
            return FakeResponse(404)
        raise AssertionError(url)

    def put(self, url: str, *, json: dict, **_kwargs):
        self.put_body = json
        return FakeResponse(201, {"content": {"path": "automation-progress.json"}})


class RoomProgressPublisherTest(unittest.TestCase):
    def test_public_payload_contains_aggregate_only(self) -> None:
        payload = build_public_payload(
            {
                "routine_date": "2026-07-06",
                "followed": 50,
                "liked": 50,
                "goal": 50,
                "attempted": 30,
                "failures": 0,
                "completed": True,
                "candidate_id": "must-not-leak",
                "room_url": "must-not-leak",
            }
        )

        self.assertEqual(payload["followed"], 50)
        self.assertEqual(payload["liked"], 50)
        self.assertNotIn("candidate_id", payload)
        self.assertNotIn("room_url", payload)

    def test_publish_writes_utf8_json_to_state_branch(self) -> None:
        session = FakeSession()
        publish_progress(
            {
                "routine_date": "2026-07-06",
                "followed": 50,
                "liked": 50,
                "goal": 50,
                "completed": True,
            },
            session=session,
            headers={"Authorization": "masked"},
        )

        self.assertEqual(session.put_body["branch"], "routine-state")
        decoded = json.loads(base64.b64decode(session.put_body["content"]).decode("utf-8"))
        self.assertEqual(decoded["routine_date"], "2026-07-06")
        self.assertTrue(decoded["completed"])


if __name__ == "__main__":
    unittest.main()
