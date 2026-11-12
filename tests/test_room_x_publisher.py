from __future__ import annotations

import json
from datetime import date
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from room_x_api import XPostResultUncertain
from room_x_content_planner import plan_x_content, release_promotion
from room_x_publisher import XPublishError, publish_document


class FakeClient:
    def __init__(self, *, uncertain: bool = False):
        self.uncertain = uncertain
        self.uploads: list[Path] = []
        self.posts: list[dict[str, object]] = []

    def upload_image(self, access_token: str, path: Path) -> str:
        self.uploads.append(path)
        return "media-1"

    def create_post(self, access_token: str, **payload):
        self.posts.append(payload)
        if self.uncertain:
            raise XPostResultUncertain("uncertain")
        return {"id": "123456789"}


def promotion_document(day: date) -> dict[str, object]:
    plan = plan_x_content(
        {
            "version": 1,
            "plan_date": day.isoformat(),
            "recent_promoted_product_ids": [],
            "room_candidates": [
                {
                    "slot": "morning",
                    "product_id": "shop/item",
                    "product_url": "https://item.rakuten.co.jp/shop/item",
                    "title": "対象商品",
                    "category": "育児用品",
                    "quality_score": 95,
                    "ready": True,
                    "review_reasons": [],
                    "promo_copy": "朝の準備を整えやすい育児用品です。 #PR",
                }
            ],
        }
    )
    return release_promotion(
        plan,
        {
            "status": "posted",
            "slot": "morning",
            "product_id": "shop/item",
            "product_url": "https://item.rakuten.co.jp/shop/item",
            "room_detail_product_url": "https://item.rakuten.co.jp/shop/item",
            "posted_evidence_id": "evidence-1",
            "room_detail_url": "https://room.rakuten.co.jp/tora_papa/1700000000000000",
        },
        durable_claim={
            "claim_id": "claim-1",
            "state": "claimed",
            "idempotency_key": plan["idempotency_key"],
            "plan_id": plan["plan_id"],
        },
    )


def helpful_document(day: date) -> dict[str, object]:
    return plan_x_content(
        {
            "version": 1,
            "plan_date": day.isoformat(),
            "recent_promoted_product_ids": [],
            "recent_helpful_content_hashes": [],
            "helpful_cards": [
                {
                    "card_id": "friday-1",
                    "pattern_id": "before_after_setup_v1",
                    "hook": "朝の準備を一か所にまとめる工夫",
                    "points": ["前夜に袋を置く", "使った物を同じ場所へ戻す"],
                    "caution": "家庭の動線に合わせて無理なく調整してください。",
                    "question": "朝いちばん探しがちな物は何ですか？",
                    "source_urls": ["https://example.org/source"],
                    "claims_reviewed": True,
                    "original_visual_ready": True,
                }
            ],
        }
    )


class RoomXPublisherTest(unittest.TestCase):
    def test_promotion_forces_paid_partnership_and_has_no_media(self) -> None:
        day = date(2026, 11, 14)  # Saturday
        with tempfile.TemporaryDirectory(dir=Path(".local/test-tmp")) as temporary:
            client = FakeClient()
            result = publish_document(
                promotion_document(day),
                state_dir=Path(temporary),
                client=client,
                access_token="secret",
                today=day,
            )
        self.assertEqual(result["status"], "posted")
        self.assertTrue(client.posts[0]["paid_partnership"])
        self.assertEqual(client.posts[0]["media_ids"], ())

    def test_helpful_post_requires_and_uploads_reviewed_visual(self) -> None:
        day = date(2026, 11, 13)  # Friday
        with tempfile.TemporaryDirectory(dir=Path(".local/test-tmp")) as temporary:
            visual = Path(temporary) / "card.png"
            visual.write_bytes(b"png")
            client = FakeClient()
            result = publish_document(
                helpful_document(day),
                state_dir=Path(temporary) / "state",
                client=client,
                access_token="secret",
                media_path=visual,
                today=day,
            )
        self.assertEqual(result["status"], "posted")
        self.assertEqual(client.posts[0]["media_ids"], ("media-1",))
        self.assertFalse(client.posts[0]["paid_partnership"])

    def test_duplicate_returns_receipt_without_second_api_call(self) -> None:
        day = date(2026, 11, 14)
        with tempfile.TemporaryDirectory(dir=Path(".local/test-tmp")) as temporary:
            state = Path(temporary)
            first_client = FakeClient()
            publish_document(
                promotion_document(day),
                state_dir=state,
                client=first_client,
                access_token="secret",
                today=day,
            )
            second_client = FakeClient()
            result = publish_document(
                promotion_document(day),
                state_dir=state,
                client=second_client,
                access_token="secret",
                today=day,
            )
        self.assertEqual(result["status"], "already_posted")
        self.assertEqual(second_client.posts, [])

    def test_uncertain_result_blocks_automatic_retry(self) -> None:
        day = date(2026, 11, 14)
        with tempfile.TemporaryDirectory(dir=Path(".local/test-tmp")) as temporary:
            state = Path(temporary)
            with self.assertRaisesRegex(XPublishError, "uncertain"):
                publish_document(
                    promotion_document(day),
                    state_dir=state,
                    client=FakeClient(uncertain=True),
                    access_token="secret",
                    today=day,
                )
            with self.assertRaisesRegex(XPublishError, "user verification"):
                publish_document(
                    promotion_document(day),
                    state_dir=state,
                    client=FakeClient(),
                    access_token="secret",
                    today=day,
                )
            self.assertEqual(len(list((state / "uncertain").glob("*.json"))), 1)

    def test_wrong_day_fails_before_creating_claim(self) -> None:
        scheduled = date(2026, 11, 14)
        with tempfile.TemporaryDirectory(dir=Path(".local/test-tmp")) as temporary:
            state = Path(temporary)
            with self.assertRaisesRegex(XPublishError, "today"):
                publish_document(
                    promotion_document(scheduled),
                    state_dir=state,
                    client=FakeClient(),
                    access_token="secret",
                    today=date(2026, 11, 15),
                )
            self.assertFalse((state / "claims").exists())

    def test_tampered_promotion_release_fails_before_creating_claim(self) -> None:
        day = date(2026, 11, 14)
        document = promotion_document(day)
        document["copy"] = "差し替えられた本文 #PR"
        with tempfile.TemporaryDirectory(dir=Path(".local/test-tmp")) as temporary:
            state = Path(temporary)
            with self.assertRaisesRegex(XPublishError, "integrity"):
                publish_document(
                    document,
                    state_dir=state,
                    client=FakeClient(),
                    access_token="secret",
                    today=day,
                )
            self.assertFalse((state / "claims").exists())

    def test_helpful_without_visual_fails_closed(self) -> None:
        day = date(2026, 11, 13)
        with tempfile.TemporaryDirectory(dir=Path(".local/test-tmp")) as temporary:
            with self.assertRaisesRegex(XPublishError, "requires"):
                publish_document(
                    helpful_document(day),
                    state_dir=Path(temporary),
                    client=FakeClient(),
                    access_token="secret",
                    today=day,
                )


if __name__ == "__main__":
    unittest.main()
