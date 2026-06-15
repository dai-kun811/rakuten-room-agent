from datetime import date
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rakuten_api import Product
from fixed_rule_generator import FixedRulePostGenerator, GenerationContext
from scoring import score_product
from sheets import SHEET_HEADERS, error_row, normalize_product_url, scored_product_to_row


class SheetsTest(unittest.TestCase):
    def test_row_matches_requested_columns(self) -> None:
        scored = score_product(
            Product(
                category="ベビー用消耗品",
                name="厚手 おしりふき 80枚 20個",
                url="https://example.com/wipes",
                price=2500,
                review_count=500,
                review_average=4.6,
                caption="厚手 おしりふき 80枚 20個 おむつ替え",
                catchcopy="水分量 まとめ買い",
                shop_name="楽天ショップ",
                image_url="https://example.com/image.jpg",
            ),
            date(2026, 6, 8),
        )

        generated = FixedRulePostGenerator().generate(
            scored,
            context=GenerationContext(),
        )
        row = scored_product_to_row(
            scored,
            generated,
            today=date(2026, 6, 8),
            run_id="run123",
        )

        self.assertEqual(len(row), len(SHEET_HEADERS))
        self.assertEqual(row[0], "2026-06-08")
        self.assertEqual(row[1], "run123")
        self.assertEqual(row[2], "ベビー用消耗品")
        self.assertEqual(row[5], "https://example.com/wipes")
        self.assertEqual(row[12], "wipes")
        self.assertIn("枚数", row[21])
        self.assertIsInstance(row[23], int)
        self.assertNotIn("選定条件=", row[22])
        self.assertEqual(row[22], generated.recommendation_reason)
        self.assertEqual(len(row[30].split()), 5)
        self.assertIn("#とらパパ厳選", row[30])
        self.assertEqual(row[27], "ready")
        self.assertEqual(row[33], "fallback")

    def test_error_row_matches_requested_columns(self) -> None:
        row = error_row(today=date(2026, 6, 8), run_id="run123", reason="楽天API取得0件")

        self.assertEqual(len(row), len(SHEET_HEADERS))
        self.assertEqual(row[1], "run123")
        self.assertEqual(row[2], "ERROR")
        self.assertEqual(row[27], "ERROR")
        self.assertIn("楽天API取得0件", row[22])

    def test_normalize_product_url_ignores_query_and_trailing_slash(self) -> None:
        self.assertEqual(
            normalize_product_url("https://item.rakuten.co.jp/shop/item/?scid=abc"),
            "https://item.rakuten.co.jp/shop/item",
        )

    def test_existing_url_reader_includes_old_rows_and_ignores_error_rows(self) -> None:
        class FakeSheetsClient:
            def read_values(self, _range_name: str) -> list[list[str]]:
                return [
                    ["日付", "カテゴリ", "商品名", "商品URL"],
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
