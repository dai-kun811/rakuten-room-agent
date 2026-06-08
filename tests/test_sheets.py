from datetime import date
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rakuten_api import Product
from scoring import score_product
from sheets import SHEET_HEADERS, error_row, normalize_product_url, scored_product_to_row


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
        self.assertEqual(row[8], "通園準備")
        self.assertIn("通園準備", row[9])
        self.assertIn("サイズ", row[10])
        self.assertIsInstance(row[11], int)
        self.assertEqual(len(row[14].split()), 5)
        self.assertIn("#とらパパ厳選", row[14])

    def test_error_row_matches_requested_columns(self) -> None:
        row = error_row(today=date(2026, 6, 8), reason="楽天API取得0件")

        self.assertEqual(len(row), len(SHEET_HEADERS))
        self.assertEqual(row[1], "ERROR")
        self.assertEqual(row[7], "ERROR")
        self.assertIn("楽天API取得0件", row[12])

    def test_normalize_product_url_ignores_query_and_trailing_slash(self) -> None:
        self.assertEqual(
            normalize_product_url("https://item.rakuten.co.jp/shop/item/?scid=abc"),
            "https://item.rakuten.co.jp/shop/item",
        )

    def test_existing_url_reader_includes_old_rows_and_ignores_error_rows(self) -> None:
        class FakeSheetsClient:
            def read_values(self, _range_name: str) -> list[list[str]]:
                return [
                    SHEET_HEADERS,
                    ["2026-01-01", "絵本", "絵本", "https://item.rakuten.co.jp/shop/book/?x=1"],
                    ["2026-06-08", "ERROR", "商品データなし", ""],
                ]

        urls = SheetsLikeMixin.read_existing_urls(FakeSheetsClient(), "Sheet1")

        self.assertEqual(urls, {"https://item.rakuten.co.jp/shop/book"})


class SheetsLikeMixin:
    from sheets import SheetsClient

    read_existing_urls = SheetsClient.read_existing_urls


if __name__ == "__main__":
    unittest.main()
