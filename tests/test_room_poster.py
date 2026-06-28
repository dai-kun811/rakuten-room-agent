from __future__ import annotations

import base64
import json
import sys
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from room_poster import (
    RoomPostError,
    build_room_comment,
    decode_storage_state,
    post_ready_items,
    write_room_post_report,
)


def encoded_state() -> str:
    payload = {"cookies": [{"name": "session", "value": "secret"}], "origins": []}
    return base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")


def report_item(url: str, *, status: str = "ready") -> SimpleNamespace:
    product = SimpleNamespace(url=url, name="テスト商品")
    generated = SimpleNamespace(
        status=status,
        body="【テスト用の商品紹介】本文です。",
        hashtags=["#育児用品", "#とらパパ厳選"],
    )
    return SimpleNamespace(scored=SimpleNamespace(product=product), generated=generated)


class FakeSheetsClient:
    def __init__(self, reserved: set[str] | None = None) -> None:
        self.reserved = reserved or set()
        self.events: list[dict[str, str]] = []
        self.ensured = False

    def ensure_room_post_log(self, _sheet_name: str) -> None:
        self.ensured = True

    def read_reserved_room_urls(self, _sheet_name: str) -> set[str]:
        return set(self.reserved)

    def append_room_post_event(self, _sheet_name: str, **event: str) -> None:
        self.events.append(event)


class FakePoster:
    posted: list[tuple[str, str]] = []

    def __init__(self, _storage_state: dict, *, headless: bool) -> None:
        self.headless = headless

    def post(self, product_url: str, comment: str) -> None:
        self.posted.append((product_url, comment))


class FailingPoster(FakePoster):
    def post(self, product_url: str, comment: str) -> None:
        raise RoomPostError("投稿画面を確認できません。")


class RoomPosterTest(unittest.TestCase):
    def setUp(self) -> None:
        FakePoster.posted = []

    def test_decode_storage_state_requires_cookie_list(self) -> None:
        self.assertEqual(decode_storage_state(encoded_state())["cookies"][0]["name"], "session")
        with self.assertRaises(RoomPostError):
            decode_storage_state(base64.b64encode(b"{}").decode("ascii"))
        with self.assertRaises(RoomPostError):
            decode_storage_state("not-base64")

    def test_build_room_comment_appends_hashtags(self) -> None:
        self.assertEqual(
            build_room_comment("本文", ["#育児", "#ROOM"]),
            "本文\n\n#育児 #ROOM",
        )

    @patch("room_poster.RoomPoster", FakePoster)
    def test_ready_item_is_reserved_before_posting(self) -> None:
        sheets = FakeSheetsClient()
        results = post_ready_items(
            [report_item("https://item.rakuten.co.jp/shop/item/?scid=x")],
            sheets_client=sheets,
            run_id="run123",
            executed_at=datetime(2026, 6, 28, 10, 0),
            log_sheet_name="ROOM_Post_Log",
            auth_state_b64=encoded_state(),
            headless=True,
        )

        self.assertTrue(sheets.ensured)
        self.assertEqual([event["status"] for event in sheets.events], ["reserved", "posted"])
        self.assertEqual(results[0].status, "posted")
        self.assertEqual(len(FakePoster.posted), 1)

    @patch("room_poster.RoomPoster", FakePoster)
    def test_reserved_url_is_not_posted_twice(self) -> None:
        url = "https://item.rakuten.co.jp/shop/item"
        sheets = FakeSheetsClient({url})
        results = post_ready_items(
            [report_item(url)],
            sheets_client=sheets,
            run_id="run123",
            executed_at=datetime(2026, 6, 28, 10, 0),
            log_sheet_name="ROOM_Post_Log",
            auth_state_b64=encoded_state(),
            headless=True,
        )

        self.assertEqual(results[0].status, "skipped")
        self.assertEqual(sheets.events, [])
        self.assertEqual(FakePoster.posted, [])

    @patch("room_poster.RoomPoster", FailingPoster)
    def test_failed_post_is_logged_and_stops_the_run(self) -> None:
        sheets = FakeSheetsClient()
        results = post_ready_items(
            [report_item("https://item.rakuten.co.jp/shop/item")],
            sheets_client=sheets,
            run_id="run123",
            executed_at=datetime(2026, 6, 28, 10, 0),
            log_sheet_name="ROOM_Post_Log",
            auth_state_b64=encoded_state(),
            headless=True,
        )

        self.assertEqual([event["status"] for event in sheets.events], ["reserved", "failed"])
        self.assertEqual(results[0].status, "failed")

    def test_no_ready_item_does_not_require_browser_auth(self) -> None:
        sheets = FakeSheetsClient()
        results = post_ready_items(
            [report_item("https://item.rakuten.co.jp/shop/item", status="needs_review")],
            sheets_client=sheets,
            run_id="run123",
            executed_at=datetime(2026, 6, 28, 10, 0),
            log_sheet_name="ROOM_Post_Log",
            auth_state_b64="",
            headless=True,
        )

        self.assertEqual(results, [])
        self.assertFalse(sheets.ensured)

    def test_post_report_contains_status_counts_only(self) -> None:
        from room_poster import RoomPostResult
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as directory:
            path = write_room_post_report(
                Path(directory),
                run_id="run123",
                executed_at=datetime(2026, 6, 28, 10, 0),
                results=[RoomPostResult("https://example.com/item", "posted")],
            )
            payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["posted"], 1)
        self.assertNotIn("cookies", json.dumps(payload))


if __name__ == "__main__":
    unittest.main()
