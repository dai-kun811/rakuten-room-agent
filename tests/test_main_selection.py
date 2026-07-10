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
    diversify_products,
    generate_until_ready,
    is_supported_room_product,
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
        self.assertEqual(SEARCH_KEYWORDS_PER_CATEGORY, 2)
        self.assertEqual(SEARCH_PAGES_PER_KEYWORD, 2)

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

    def test_generate_until_ready_fills_all_three_post_slots(self) -> None:
        candidates = [
            scored(f"おしりふき 厚手 {index}", f"https://example.com/wipes-{index}", 100 - index)
            for index in range(7)
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

        self.assertEqual(POST_SLOTS, ("morning", "noon", "evening"))
        self.assertEqual(len(results), 5)
        self.assertEqual(
            sum(generated.status == "ready" for _, generated in results),
            3,
        )


if __name__ == "__main__":
    unittest.main()
