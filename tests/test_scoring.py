from datetime import date
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rakuten_api import Product
from scoring import (
    classify_product,
    filter_and_score_products,
    score_price,
    score_rating,
    score_sales,
    score_terms,
)


def product(
    *,
    name: str,
    url: str,
    review_count: int = 200,
    review_average: float = 4.5,
    category: str = "育児便利グッズ",
    caption: str = "育児 ベビー キッズ 時短",
) -> Product:
    return Product(
        category=category,
        name=name,
        url=url,
        price=2980,
        review_count=review_count,
        review_average=review_average,
        caption=caption,
        catchcopy="送料無料 人気 セット",
        shop_name="楽天ショップ",
        image_url="https://example.com/image.jpg",
    )


class ScoringTest(unittest.TestCase):
    def test_sales_score_buckets(self) -> None:
        self.assertEqual(score_sales(100), 10)
        self.assertEqual(score_sales(301), 20)
        self.assertEqual(score_sales(1001), 25)
        self.assertEqual(score_sales(5001), 30)

    def test_rating_score_buckets(self) -> None:
        self.assertEqual(score_rating(4.3), 10)
        self.assertEqual(score_rating(4.5), 15)
        self.assertEqual(score_rating(4.7), 20)

    def test_price_score_prioritizes_room_friendly_prices(self) -> None:
        self.assertEqual(score_price(3000), 15)
        self.assertEqual(score_price(8000), 15)
        self.assertEqual(score_price(1500), 10)
        self.assertEqual(score_price(12000), 10)
        self.assertEqual(score_price(900), 3)

    def test_low_review_count_and_rating_are_filtered_out(self) -> None:
        with patch.dict("os.environ", {"ENABLE_RELAXED_FALLBACK": "false"}):
            selected = filter_and_score_products(
                [
                    product(
                        name="ベビー 時短 グッズ",
                        url="https://example.com/ok",
                        review_count=1200,
                        review_average=4.7,
                    ),
                    product(name="レビュー不足", url="https://example.com/low-review", review_count=99),
                    product(name="評価不足", url="https://example.com/low-rating", review_average=4.29),
                ],
                date(2026, 6, 8),
            )

        self.assertEqual([item.product.url for item in selected], ["https://example.com/ok"])

    def test_expands_to_70_points_when_80_points_are_less_than_five(self) -> None:
        selected = filter_and_score_products(
            [
                product(
                    name=f"育児 ベビー グッズ {index}",
                    url=f"https://example.com/{index}",
                    review_count=1001,
                    review_average=4.3,
                    caption="育児 ベビー キッズ 子ども 知育 梅雨 防水",
                )
                for index in range(6)
            ],
            date(2026, 6, 8),
        )

        self.assertEqual(len(selected), 5)
        self.assertTrue(all(item.total_score >= 70 for item in selected))

    def test_relaxed_fallback_can_select_at_least_one_debug_product(self) -> None:
        selected = filter_and_score_products(
            [
                product(
                    name="商品情報が少ないベビー用品",
                    url="https://example.com/debug",
                    review_count=0,
                    review_average=0,
                    caption="ベビー",
                    category="ベビー用品",
                )
            ],
            date(2026, 6, 8),
        )

        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].selection_tier, "debug_minimum")

    def test_product_rank_rules(self) -> None:
        self.assertEqual(classify_product(product(name="A", url="a", review_count=1000), 0), "Aランク")
        self.assertEqual(classify_product(product(name="C", url="c", review_count=200), 5), "Cランク")
        self.assertEqual(classify_product(product(name="B", url="b", review_count=200), 0), "Bランク")

    def test_score_terms_caps_score(self) -> None:
        self.assertEqual(score_terms("育児 ベビー キッズ 知育 子ども", ["育児", "ベビー", "キッズ"], points_per_match=10, max_score=20), 20)


if __name__ == "__main__":
    unittest.main()
