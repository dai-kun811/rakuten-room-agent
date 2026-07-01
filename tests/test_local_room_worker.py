from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from local_room_worker import append_ledger_event, load_reserved_urls, ready_items


class LocalRoomWorkerTest(unittest.TestCase):
    def test_ready_items_excludes_review_and_incomplete_rows(self) -> None:
        report = {
            "items": [
                {"status": "ready", "product_url": "https://example.com/a", "body": "本文"},
                {"status": "needs_review", "product_url": "https://example.com/b", "body": "本文"},
                {"status": "ready", "product_url": "", "body": "本文"},
            ]
        }
        self.assertEqual([item["product_url"] for item in ready_items(report)], ["https://example.com/a"])

    def test_ledger_reserves_url_before_posting(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.jsonl"
            append_ledger_event(
                {
                    "normalized_url": "https://item.rakuten.co.jp/shop/item/?x=1",
                    "status": "reserved",
                },
                path,
            )
            path.write_text(path.read_text(encoding="utf-8") + "not-json\n", encoding="utf-8")
            self.assertEqual(
                load_reserved_urls(path),
                {"https://item.rakuten.co.jp/shop/item"},
            )

    def test_retry_failed_details_only_reopens_matching_failure(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.jsonl"
            events = [
                {"normalized_url": "https://example.com/retry", "status": "reserved"},
                {
                    "normalized_url": "https://example.com/retry",
                    "status": "failed",
                    "detail": "ModuleNotFoundError",
                },
                {
                    "normalized_url": "https://example.com/keep",
                    "status": "failed",
                    "detail": "投稿後の完了表示を確認できませんでした。",
                },
            ]
            path.write_text(
                "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events),
                encoding="utf-8",
            )

            reserved = load_reserved_urls(
                path,
                retry_failed_details={"ModuleNotFoundError"},
            )
            self.assertEqual(reserved, {"https://example.com/keep"})

    def test_ledger_event_is_json_without_auth_material(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.jsonl"
            append_ledger_event(
                {"normalized_url": "https://example.com/item", "status": "posted"},
                path,
            )
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "posted")
            self.assertNotIn("cookie", json.dumps(payload).lower())


if __name__ == "__main__":
    unittest.main()
