from datetime import date
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rakuten_api import Product
from scoring import score_product
from sheets import SHEET_HEADERS, scored_product_to_row


class SheetsTest(unittest.TestCase):
    def test_row_matches_requested_columns(self) -> None:
        scored = score_product(
            Product(
                category="子ども靴",
                name="キッズ シューズ",
                url="https://example.com/shoes",
                price=2500,
                review_count=500,
                review_average=4.6,
                caption="子ども 靴 通園",
                catchcopy="送料無料 人気",
                shop_name="楽天ショップ",
                image_url="https://example.com/image.jpg",
            ),
            date(2026, 6, 8),
        )

        row = scored_product_to_row(scored, today=date(2026, 6, 8))

        self.assertEqual(len(row), len(SHEET_HEADERS))
        self.assertEqual(row[0], "2026-06-08")
        self.assertEqual(row[1], "子ども靴")
        self.assertEqual(row[3], "https://example.com/shoes")


if __name__ == "__main__":
    unittest.main()
