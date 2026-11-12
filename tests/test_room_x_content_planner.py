from __future__ import annotations

import unittest
from datetime import date

from room_x_content_planner import (
    HELPFUL_PATTERN_BY_WEEKDAY,
    XContentPlanError,
    plan_x_content,
    release_promotion,
    x_weighted_length,
)


def candidate(slot: str, product_id: str, quality: int = 90) -> dict:
    return {
        "slot": slot,
        "product_id": product_id,
        "product_url": f"https://item.rakuten.co.jp/shop/{product_id}/",
        "title": f"育児商品 {product_id}",
        "category": "育児用品",
        "quality_score": quality,
        "ready": True,
        "review_reasons": [],
        "promo_copy": f"買う前に確認したい3項目。\n商品 {product_id} の違いを整理しました。\n#PR",
    }


def helpful_card(pattern_id: str, card_id: str = "card-1") -> dict:
    return {
        "card_id": card_id,
        "pattern_id": pattern_id,
        "hook": "朝の支度で、確認が増えすぎるときの3項目。",
        "points": ["持ち物を3つに分ける", "できた物を見えない側へ移す", "最後に親子で1回だけ確認する"],
        "caution": "年齢やその日の様子に合わせ、無理に続けない前提です。",
        "question": "朝の支度で一番忘れやすい物は何ですか？",
        "source_urls": ["https://example.org/parenting-source"],
        "claims_reviewed": True,
        "original_visual_ready": True,
    }


def durable_claim(plan: dict) -> dict:
    return {
        "state": "claimed",
        "claim_id": f"claim-{plan['plan_id']}",
        "idempotency_key": plan["idempotency_key"],
        "plan_id": plan["plan_id"],
    }


def posted_evidence(product_id: str = "abc", slot: str = "morning") -> dict:
    product_url = f"https://item.rakuten.co.jp/shop/{product_id}/"
    return {
        "status": "posted",
        "slot": slot,
        "product_id": product_id,
        "product_url": product_url,
        "room_detail_product_url": product_url,
        "room_detail_url": "https://room.rakuten.co.jp/tora_papa/1700999999999999",
        "posted_evidence_id": f"posted-{slot}-{product_id}",
    }


class RoomXContentPlannerTest(unittest.TestCase):
    def test_promotion_is_prepared_before_post_and_earliest_slot_wins(self) -> None:
        plan = plan_x_content(
            {
                "version": 1,
                "plan_date": "2026-11-12",  # Thursday
                "recent_promoted_product_ids": [],
                "room_candidates": [
                    candidate("noon", "noon-item", 99),
                    candidate("morning", "morning-item", 86),
                    candidate("evening", "evening-item", 100),
                ],
            }
        )
        self.assertEqual(plan["content_kind"], "promotion")
        self.assertEqual(plan["binding"]["slot"], "morning")
        self.assertNotIn("bindings", plan)
        self.assertTrue(plan["prepared_before_room_publication"])
        self.assertEqual(plan["release_gate"], "matching_room_ledger_posted")
        self.assertTrue(plan["requires_pr_hashtag"])
        self.assertTrue(plan["requires_paid_partnership_label"])
        self.assertFalse(plan["delivery"]["automatic_publish"])

    def test_promotion_rejects_review_quality_recent_duplicate_and_missing_pr(self) -> None:
        bad_review = candidate("morning", "a", 95)
        bad_review["review_reasons"] = ["title_mismatch"]
        low_quality = candidate("noon", "b", 84)
        recent = candidate("evening", "c", 99)
        with self.assertRaisesRegex(XContentPlanError, "no eligible"):
            plan_x_content(
                {
                    "version": 1,
                    "plan_date": "2026-11-12",
                    "recent_promoted_product_ids": ["c"],
                    "room_candidates": [bad_review, low_quality, recent],
                }
            )

        missing_pr = candidate("morning", "d")
        missing_pr["promo_copy"] = "広告表示がありません"
        with self.assertRaisesRegex(XContentPlanError, "#PR"):
            plan_x_content(
                {
                    "version": 1,
                    "plan_date": "2026-11-12",
                    "recent_promoted_product_ids": [],
                    "room_candidates": [missing_pr],
                }
            )

        forbidden = candidate("morning", "e")
        forbidden["promo_copy"] = "これで絶対に解決します。 #PR"
        with self.assertRaisesRegex(XContentPlanError, "forbidden claim"):
            plan_x_content(
                {
                    "version": 1,
                    "plan_date": "2026-11-12",
                    "recent_promoted_product_ids": [],
                    "room_candidates": [forbidden],
                }
            )

        embedded_url = candidate("morning", "f")
        embedded_url["promo_copy"] = "詳しくはt.co/exampleを確認。 #PR"
        with self.assertRaisesRegex(XContentPlanError, "must not embed a URL"):
            plan_x_content(
                {
                    "version": 1,
                    "plan_date": "2026-11-12",
                    "recent_promoted_product_ids": [],
                    "room_candidates": [embedded_url],
                }
            )

        with self.assertRaisesRegex(XContentPlanError, "unique product IDs"):
            plan_x_content(
                {
                    "version": 1,
                    "plan_date": "2026-11-12",
                    "recent_promoted_product_ids": [],
                    "room_candidates": [
                        candidate("morning", "same"),
                        candidate("noon", "same"),
                    ],
                }
            )

        overlong = candidate("morning", "g")
        overlong["promo_copy"] = ("育" * 130) + " #PR"
        with self.assertRaisesRegex(XContentPlanError, "leaves no room"):
            plan_x_content(
                {
                    "version": 1,
                    "plan_date": "2026-11-12",
                    "recent_promoted_product_ids": [],
                    "room_candidates": [overlong],
                }
            )

    def test_exact_posted_room_evidence_releases_prepared_copy(self) -> None:
        plan = plan_x_content(
            {
                "version": 1,
                "plan_date": "2026-11-12",
                "recent_promoted_product_ids": [],
                "room_candidates": [candidate("morning", "abc")],
            }
        )
        released = release_promotion(
            plan,
            posted_evidence(),
            durable_claim=durable_claim(plan),
        )
        self.assertEqual(released["release_gate"], "passed")
        self.assertIn("#PR", released["copy"])
        self.assertIn("room.rakuten.co.jp/tora_papa/", released["copy"])
        self.assertTrue(released["requires_paid_partnership_label"])
        self.assertFalse(released["delivery"]["automatic_publish"])

    def test_release_fails_closed_for_unposted_mismatch_duplicate_and_foreign_room(self) -> None:
        plan = plan_x_content(
            {
                "version": 1,
                "plan_date": "2026-11-12",
                "recent_promoted_product_ids": [],
                "room_candidates": [candidate("morning", "abc")],
            }
        )
        base = posted_evidence()
        claim = durable_claim(plan)
        with self.assertRaisesRegex(XContentPlanError, "must be posted"):
            release_promotion(plan, {**base, "status": "uncertain"}, durable_claim=claim)
        with self.assertRaisesRegex(XContentPlanError, "does not match"):
            release_promotion(plan, {**base, "product_id": "other"}, durable_claim=claim)
        with self.assertRaisesRegex(XContentPlanError, "does not match"):
            release_promotion(
                plan,
                {**base, "product_url": "https://item.rakuten.co.jp/shop/other/"},
                durable_claim=claim,
            )
        with self.assertRaisesRegex(XContentPlanError, "ROOM detail evidence"):
            release_promotion(
                plan,
                {**base, "room_detail_product_url": "https://item.rakuten.co.jp/shop/other/"},
                durable_claim=claim,
            )
        with self.assertRaisesRegex(XContentPlanError, "atomic durable claim"):
            release_promotion(plan, base, durable_claim={**claim, "state": "released"})
        with self.assertRaisesRegex(XContentPlanError, "owned ROOM"):
            release_promotion(
                plan,
                {**base, "room_detail_url": "https://room.rakuten.co.jp/other/1700999999999999"},
                durable_claim=claim,
            )
        for invalid_url in (
            "https://room.rakuten.co.jp/tora_papa/abc",
            "https://room.rakuten.co.jp/tora_papa/1700999999999999/extra",
        ):
            with self.assertRaisesRegex(XContentPlanError, "owned ROOM"):
                release_promotion(
                    plan,
                    {**base, "room_detail_url": invalid_url},
                    durable_claim=claim,
                )

        tampered = dict(plan)
        tampered["binding"] = dict(plan["binding"])
        tampered["binding"]["product_id"] = "tampered"
        with self.assertRaisesRegex(XContentPlanError, "integrity"):
            release_promotion(tampered, base, durable_claim=claim)
        with self.assertRaisesRegex(XContentPlanError, "owned ROOM"):
            release_promotion(
                plan,
                {**base, "room_detail_url": "https://room.rakuten.co.jp/tora_papa/1700999999999999?wrong=1"},
                durable_claim=claim,
            )

    def test_only_the_single_preselected_product_can_be_released(self) -> None:
        plan = plan_x_content(
            {
                "version": 1,
                "plan_date": "2026-11-12",
                "recent_promoted_product_ids": [],
                "room_candidates": [
                    candidate("morning", "primary", 86),
                    candidate("noon", "later", 99),
                ],
            }
        )
        self.assertEqual(plan["binding"]["product_id"], "primary")
        with self.assertRaisesRegex(XContentPlanError, "does not match"):
            release_promotion(
                plan,
                {
                    **posted_evidence("later", "noon"),
                },
                durable_claim=durable_claim(plan),
            )

    def test_helpful_day_uses_scheduled_reviewed_pattern_without_promotion(self) -> None:
        plan_day = date(2026, 11, 11)  # Wednesday
        expected = HELPFUL_PATTERN_BY_WEEKDAY[plan_day.weekday()]
        plan = plan_x_content(
            {
                "version": 1,
                "plan_date": plan_day.isoformat(),
                "recent_promoted_product_ids": [],
                "recent_helpful_content_hashes": [],
                "helpful_cards": [helpful_card(expected)],
            }
        )
        self.assertEqual(plan["content_kind"], "helpful")
        self.assertEqual(plan["pattern_id"], expected)
        self.assertFalse(plan["requires_pr_hashtag"])
        self.assertFalse(plan["requires_paid_partnership_label"])
        self.assertFalse(plan["contains_room_link"])
        self.assertNotIn("#PR", plan["copy"])
        self.assertNotIn("http", plan["copy"])
        self.assertEqual(plan["copy"].count("？"), 1)

    def test_four_helpful_weekdays_use_four_distinct_patterns(self) -> None:
        days = (date(2026, 11, 9), date(2026, 11, 11), date(2026, 11, 13), date(2026, 11, 15))
        observed = []
        for plan_day in days:
            expected = HELPFUL_PATTERN_BY_WEEKDAY[plan_day.weekday()]
            plan = plan_x_content(
                {
                    "version": 1,
                    "plan_date": plan_day.isoformat(),
                    "recent_promoted_product_ids": [],
                    "recent_helpful_content_hashes": [],
                    "helpful_cards": [helpful_card(expected, f"card-{plan_day.weekday()}")],
                }
            )
            observed.append(plan["pattern_id"])
        self.assertEqual(len(set(observed)), 4)

    def test_helpful_card_requires_sources_review_original_visual_and_one_question(self) -> None:
        plan_day = date(2026, 11, 11)
        expected = HELPFUL_PATTERN_BY_WEEKDAY[plan_day.weekday()]
        card = helpful_card(expected)
        for field, message in (
            ("claims_reviewed", "claims must be reviewed"),
            ("original_visual_ready", "visual is required"),
        ):
            broken = dict(card)
            broken[field] = False
            with self.assertRaisesRegex(XContentPlanError, message):
                plan_x_content(
                    {
                        "version": 1,
                        "plan_date": plan_day.isoformat(),
                        "recent_promoted_product_ids": [],
                        "recent_helpful_content_hashes": [],
                        "helpful_cards": [broken],
                    }
                )

        broken = dict(card)
        broken["question"] = "何ですか？ いつですか？"
        with self.assertRaisesRegex(XContentPlanError, "exactly one question"):
            plan_x_content(
                {
                    "version": 1,
                    "plan_date": plan_day.isoformat(),
                    "recent_promoted_product_ids": [],
                    "recent_helpful_content_hashes": [],
                    "helpful_cards": [broken],
                }
            )

        for bad_hook in ("商品はこちら #PR", "詳しくはhttps://example.com"):
            broken = dict(card)
            broken["hook"] = bad_hook
            with self.assertRaisesRegex(XContentPlanError, "promotion or links"):
                plan_x_content(
                    {
                        "version": 1,
                        "plan_date": plan_day.isoformat(),
                        "recent_promoted_product_ids": [],
                        "recent_helpful_content_hashes": [],
                        "helpful_cards": [broken],
                    }
                )

    def test_helpful_recent_content_hash_is_not_reused(self) -> None:
        plan_day = date(2026, 11, 11)
        expected = HELPFUL_PATTERN_BY_WEEKDAY[plan_day.weekday()]
        snapshot = {
            "version": 1,
            "plan_date": plan_day.isoformat(),
            "recent_promoted_product_ids": [],
            "recent_helpful_content_hashes": [],
            "helpful_cards": [helpful_card(expected)],
        }
        first = plan_x_content(snapshot)
        snapshot["recent_helpful_content_hashes"] = [first["helpful_content_hash"]]
        with self.assertRaisesRegex(XContentPlanError, "no non-duplicate"):
            plan_x_content(snapshot)

    def test_forbidden_claims_and_overlength_fail_closed(self) -> None:
        plan_day = date(2026, 11, 11)
        expected = HELPFUL_PATTERN_BY_WEEKDAY[plan_day.weekday()]
        broken = helpful_card(expected)
        broken["hook"] = "これで絶対に解決します。"
        with self.assertRaisesRegex(XContentPlanError, "forbidden claim"):
            plan_x_content(
                {
                    "version": 1,
                    "plan_date": plan_day.isoformat(),
                    "recent_promoted_product_ids": [],
                    "recent_helpful_content_hashes": [],
                    "helpful_cards": [broken],
                }
            )

        broken = helpful_card(expected)
        broken["points"] = ["長" * 120, "文" * 120]
        with self.assertRaisesRegex(XContentPlanError, "text limit"):
            plan_x_content(
                {
                    "version": 1,
                    "plan_date": plan_day.isoformat(),
                    "recent_promoted_product_ids": [],
                    "recent_helpful_content_hashes": [],
                    "helpful_cards": [broken],
                }
            )

    def test_x_weighted_length_counts_japanese_and_urls(self) -> None:
        self.assertEqual(x_weighted_length("abc"), 3)
        self.assertEqual(x_weighted_length("育児"), 4)
        self.assertEqual(x_weighted_length("見る https://example.com/very/long/path"), 28)
        self.assertEqual(x_weighted_length("見る http://example.com/path"), 28)

    def test_malformed_top_level_inputs_fail_closed(self) -> None:
        with self.assertRaisesRegex(XContentPlanError, "snapshot must be an object"):
            plan_x_content(None)  # type: ignore[arg-type]
        with self.assertRaisesRegex(XContentPlanError, "version must be 1"):
            plan_x_content({"version": 2, "plan_date": "2026-11-12"})
        with self.assertRaisesRegex(XContentPlanError, "plan_date must be"):
            plan_x_content({"version": 1, "plan_date": "not-a-date"})

    def test_plan_is_deterministic(self) -> None:
        snapshot = {
            "version": 1,
            "plan_date": "2026-11-12",
            "recent_promoted_product_ids": [],
            "room_candidates": [candidate("morning", "abc")],
        }
        first = plan_x_content(snapshot)
        second = plan_x_content(snapshot)
        self.assertEqual(first, second)
        self.assertEqual(len(first["plan_id"]), 16)


if __name__ == "__main__":
    unittest.main()
