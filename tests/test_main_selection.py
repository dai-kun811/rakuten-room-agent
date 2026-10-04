from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from datetime import date, timezone
import zoneinfo
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fixed_rule_generator import classify_product_type
zoneinfo.ZoneInfo = lambda _key: timezone.utc
from main import (
    POST_SLOTS,
    SEARCH_KEYWORDS_PER_CATEGORY,
    SEARCH_PAGES_PER_KEYWORD,
    TARGET_READY_POSTS,
    apply_recovery_reuse,
    diversify_products,
    exclude_non_room_candidates,
    generate_until_ready,
    is_supported_room_product,
    parse_blocked_urls,
    selection_axis,
)
from rakuten_api import Product
from scoring import score_product


def scored(name: str, url: str, total_score: int):
    product = Product(
        category="test",
        name=name,
        url=url,
        price=3000,
        review_count=500,
        review_average=4.6,
        caption=name,
        catchcopy=name,
        shop_name="test shop",
        image_url="https://example.com/image.jpg",
    )
    return replace(score_product(product, date(2026, 6, 19)), total_score=total_score)


class MainSelectionTest(unittest.TestCase):
    def test_daily_search_uses_additional_keywords_and_pages(self) -> None:
        self.assertEqual(SEARCH_KEYWORDS_PER_CATEGORY, 6)
        self.assertEqual(SEARCH_PAGES_PER_KEYWORD, 3)

    def test_excludes_non_room_candidates_before_selection(self) -> None:
        candidates = [
            scored("パンパース 新生児 紙おむつ", "https://example.com/baby-diaper", 100).product,
            scored("ふるさと納税 おしりふき まとめ買い", "https://example.com/furusato-wipes", 99).product,
            scored("ペット おむつ 犬用 紙おむつ", "https://example.com/pet-diaper", 99).product,
            scored("大人用紙おむつ 介護 パンツ", "https://example.com/adult-diaper", 98).product,
        ]

        kept, removed = exclude_non_room_candidates(candidates)

        self.assertEqual(removed, 3)
        self.assertEqual([item.url for item in kept], ["https://example.com/baby-diaper"])

    def test_parse_blocked_urls_normalizes_recovery_exclusions(self) -> None:
        blocked = parse_blocked_urls(
            "https://item.rakuten.co.jp/shop/item/?scid=x,\n"
            "https://item.rakuten.co.jp/shop/other/"
        )

        self.assertEqual(
            blocked,
            {
                "https://item.rakuten.co.jp/shop/item",
                "https://item.rakuten.co.jp/shop/other",
            },
        )

    def test_recovery_reuse_is_manual_only_and_keeps_other_history(self) -> None:
        existing = {"https://example.com/keep", "https://example.com/reuse"}

        self.assertEqual(
            apply_recovery_reuse(
                existing,
                {"https://example.com/reuse"},
                event_name="workflow_dispatch",
            ),
            {"https://example.com/keep"},
        )
        with self.assertRaises(RuntimeError):
            apply_recovery_reuse(
                existing,
                {"https://example.com/reuse"},
                event_name="schedule",
            )

    def test_diversify_products_prefers_unique_product_types_per_day(self) -> None:
        candidates = [
            scored("おしりふき まとめ買い 80枚", "https://example.com/wipes-a", 100),
            scored("おしりふき 厚手 シート 60枚", "https://example.com/wipes-b", 99),
            scored("抱っこ布団 ねんねクッション", "https://example.com/bedding", 80),
            scored("スワドル おくるみ モロー反射", "https://example.com/swaddle", 70),
        ]

        selected = diversify_products(candidates, recent_history=[], limit=3)
        selected_types = [classify_product_type(item.product) for item in selected]

        self.assertEqual(len(selected), 3)
        self.assertEqual(selected_types.count("wipes"), 1, selected_types)
        self.assertEqual(len(set(selected_types)), 3, selected_types)

    def test_diversify_products_allows_second_same_type_only_when_needed(self) -> None:
        candidates = [
            scored("おしりふき まとめ買い 80枚", "https://example.com/wipes-a", 100),
            scored("おしりふき 厚手 シート 60枚", "https://example.com/wipes-b", 99),
            scored("抱っこ布団 ねんねクッション", "https://example.com/bedding", 80),
        ]

        selected = diversify_products(candidates, recent_history=[], limit=3)
        selected_types = [classify_product_type(item.product) for item in selected]

        self.assertEqual(len(selected), 3)
        self.assertEqual(selected_types.count("wipes"), 2, selected_types)
        self.assertEqual(selected_types.count("baby_bedding"), 1, selected_types)

    def test_diversify_products_fills_limit_when_only_one_type_exists(self) -> None:
        candidates = [
            scored(f"おしりふき 厚手 {index}", f"https://example.com/wipes-{index}", 100 - index)
            for index in range(5)
        ]

        selected = diversify_products(candidates, recent_history=[], limit=5)

        self.assertEqual(len(selected), 5)

    def test_diversify_products_prioritizes_postable_supported_types(self) -> None:
        candidates = [
            scored("キッズ 手袋 外遊び 防寒 通園", "https://example.com/gloves", 120),
            scored("マグネットブロック 48ピース 知育", "https://example.com/blocks", 80),
            scored("授乳ライト ホワイトノイズ コードレス", "https://example.com/light", 79),
            scored("紙おむつ パンツタイプ Mサイズ", "https://example.com/diaper", 78),
        ]

        selected = diversify_products(candidates, recent_history=[], limit=3)

        self.assertFalse(is_supported_room_product(candidates[0].product))
        self.assertEqual(
            [classify_product_type(item.product) for item in selected],
            ["magnetic_blocks", "sleep_light", "diaper"],
        )

    def test_baby_bedding_cover_is_unsupported_before_generation(self) -> None:
        cover = scored(
            "抱っこ布団カバー 日本製 mayu ねんねクッション専用",
            "https://example.com/bedding-cover",
            100,
        )

        self.assertEqual(classify_product_type(cover.product), "unknown")
        self.assertFalse(is_supported_room_product(cover.product))

    def test_diversify_products_mixes_daily_pain_and_discovery_axes(self) -> None:
        candidates = [
            scored("おしりふき 厚手 80枚", "https://example.com/wipes", 100),
            scored("紙おむつ パンツ Mサイズ", "https://example.com/diaper", 99),
            scored("スワドル おくるみ モロー反射", "https://example.com/swaddle", 80),
            scored("マグネットブロック 48ピース 知育", "https://example.com/blocks", 70),
        ]

        selected = diversify_products(candidates, recent_history=[], limit=3)

        self.assertEqual(
            {selection_axis(item) for item in selected},
            {"daily_need", "pain_solver", "discovery"},
        )

    def test_generate_until_ready_fills_all_six_post_slots(self) -> None:
        candidates = [
            scored("おしりふき 厚手 失敗1", "https://example.com/fail-1", 110),
            scored("紙おむつ パンツ 失敗2", "https://example.com/fail-2", 109),
            scored("おしりふき 厚手 80枚", "https://example.com/wipes", 100),
            scored("紙おむつ パンツ Mサイズ", "https://example.com/diaper", 99),
            scored("粉ミルク 800g", "https://example.com/formula", 98),
            scored("マグネットブロック 48ピース", "https://example.com/blocks", 97),
            scored("授乳ライト ホワイトノイズ", "https://example.com/light", 96),
            scored("ベビーローション 保湿", "https://example.com/care", 95),
        ]

        class Generated:
            def __init__(self, status: str) -> None:
                self.status = status

        class Generator:
            def __init__(self) -> None:
                self.calls = 0

            def generate(self, item, *, context, season):
                del item, context, season
                self.calls += 1
                return Generated("needs_review" if self.calls <= 2 else "ready")

        generator = Generator()
        results = generate_until_ready(
            candidates,
            generator=generator,
            context=object(),
            target_ready=TARGET_READY_POSTS,
        )

        self.assertEqual(
            POST_SLOTS,
            ("morning_1", "morning_2", "noon_1", "noon_2", "evening_1", "evening_2"),
        )
        self.assertEqual(len(results), 8)
        self.assertEqual(
            sum(generated.status == "ready" for _, generated in results),
            6,
        )

    def test_generate_until_ready_searches_beyond_former_48_candidate_window(self) -> None:
        candidates = [
            *[
                scored(f"おしりふき 厚手 {index}", f"https://example.com/wipes-{index}", 200 - index)
                for index in range(48)
            ],
            scored("おしりふき 厚手 最終", "https://example.com/ready-wipes", 100),
            scored("紙おむつ パンツ Mサイズ", "https://example.com/ready-diaper", 99),
            scored("粉ミルク 800g", "https://example.com/ready-formula", 98),
            scored("マグネットブロック 48ピース", "https://example.com/ready-blocks", 97),
            scored("授乳ライト ホワイトノイズ", "https://example.com/ready-light", 96),
            scored("ベビーローション 保湿", "https://example.com/ready-care", 95),
        ]

        class Generated:
            def __init__(self, status: str) -> None:
                self.status = status

        class Generator:
            def __init__(self) -> None:
                self.calls = 0

            def generate(self, item, *, context, season):
                del item, context, season
                self.calls += 1
                return Generated("needs_review" if self.calls <= 48 else "ready")

        generator = Generator()
        results = generate_until_ready(
            candidates,
            generator=generator,
            context=object(),
            target_ready=TARGET_READY_POSTS,
        )

        self.assertEqual(generator.calls, 54)
        self.assertEqual(
            sum(generated.status == "ready" for _, generated in results),
            6,
        )

    def test_generate_until_ready_skips_unsupported_products(self) -> None:
        candidates = [
            scored("キッズ 手袋 外遊び 防寒 通園", "https://example.com/gloves", 120),
            scored("おしりふき 厚手 80枚", "https://example.com/wipes", 100),
            scored("紙おむつ パンツ Mサイズ", "https://example.com/diaper", 99),
            scored("粉ミルク 800g", "https://example.com/formula", 98),
            scored("マグネットブロック 48ピース", "https://example.com/blocks", 97),
            scored("授乳ライト ホワイトノイズ", "https://example.com/light", 96),
            scored("ベビーローション 保湿", "https://example.com/care", 95),
        ]

        class Generated:
            status = "ready"

        class Generator:
            def __init__(self) -> None:
                self.names: list[str] = []

            def generate(self, item, *, context, season):
                del context, season
                self.names.append(item.product.name)
                return Generated()

        generator = Generator()
        results = generate_until_ready(
            candidates,
            generator=generator,
            context=object(),
            target_ready=TARGET_READY_POSTS,
        )

        self.assertEqual(len(results), TARGET_READY_POSTS)
        self.assertEqual(len(generator.names), TARGET_READY_POSTS)
        self.assertNotIn("キッズ 手袋 外遊び 防寒 通園", generator.names)

    def test_generate_until_ready_never_fills_slots_with_a_repeated_type(self) -> None:
        candidates = [
            scored(f"キッズカメラ {index}", f"https://example.com/camera-{index}", 100 - index)
            for index in range(9)
        ]

        class Generated:
            status = "ready"

        class Generator:
            def generate(self, item, *, context, season):
                del item, context, season
                return Generated()

        results = generate_until_ready(
            candidates,
            generator=Generator(),
            context=object(),
            target_ready=TARGET_READY_POSTS,
        )

        ready_types = [
            classify_product_type(item.product)
            for item, generated in results
            if generated.status == "ready"
        ]
        self.assertEqual(ready_types, ["kids_camera"])

    def test_generate_until_ready_uses_a_later_distinct_type_before_backup(self) -> None:
        candidates = [
            scored("おしりふき 厚手 80枚", "https://example.com/wipes-a", 100),
            scored("おしりふき 厚手 60枚", "https://example.com/wipes-b", 99),
            scored("マグネットブロック 48ピース 知育", "https://example.com/blocks", 80),
        ]

        class Generated:
            status = "ready"

        class Generator:
            def generate(self, item, *, context, season):
                del item, context, season
                return Generated()

        results = generate_until_ready(
            candidates,
            generator=Generator(),
            context=object(),
            target_ready=2,
        )

        self.assertEqual(
            [classify_product_type(item.product) for item, _generated in results],
            ["wipes", "magnetic_blocks"],
        )


if __name__ == "__main__":
    unittest.main()
