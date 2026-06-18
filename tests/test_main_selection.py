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
from main import diversify_products
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


if __name__ == "__main__":
    unittest.main()
