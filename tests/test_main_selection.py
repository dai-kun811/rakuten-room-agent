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
from main import diversify_products, generate_until_ready
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

    def test_generate_until_ready_replaces_quality_review_candidates(self) -> None:
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
            target_ready=5,
        )

        self.assertEqual(len(results), 7)
        self.assertEqual(
            sum(generated.status == "ready" for _, generated in results),
            5,
        )


if __name__ == "__main__":
    unittest.main()
