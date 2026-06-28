from datetime import date
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from generation_report import GenerationReportItem, build_report_payload, ensure_no_secret_fields
from rakuten_api import Product
from fixed_rule_generator import FixedRulePostGenerator, GenerationContext
from scoring import score_product
from sheets import (
    GOOGLE_READ_RETRIES,
    SHEET_HEADERS,
    SheetsClient,
    error_row,
    normalize_product_url,
    scored_product_to_row,
    target_sheet_for_status,
)


class SheetsTest(unittest.TestCase):
    def test_read_values_retries_transient_google_timeouts(self) -> None:
        request = MagicMock()
        request.execute.return_value = {"values": [["header"]]}
        values_resource = MagicMock()
        values_resource.get.return_value = request
        spreadsheets_resource = MagicMock()
        spreadsheets_resource.values.return_value = values_resource
        service = MagicMock()
        service.spreadsheets.return_value = spreadsheets_resource
        client = SheetsClient.__new__(SheetsClient)
        client.spreadsheet_id = "spreadsheet-id"
        client.service = service

        self.assertEqual(client.read_values("Sheet1!A1:A1"), [["header"]])
        request.execute.assert_called_once_with(num_retries=GOOGLE_READ_RETRIES)

    def test_room_post_log_returns_reserved_urls(self) -> None:
        client = SheetsClient.__new__(SheetsClient)
        client.read_values = MagicMock(
            return_value=[
                ["日時", "実行ID", "正規化URL", "状態", "詳細", "商品名"],
                ["2026-06-28", "run1", "https://item.rakuten.co.jp/shop/item/?x=1", "reserved"],
                ["2026-06-28", "run1", "", "failed"],
            ]
        )

        self.assertEqual(
            client.read_reserved_room_urls("ROOM_Post_Log"),
            {"https://item.rakuten.co.jp/shop/item"},
        )

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

    def test_ready_and_needs_review_target_different_sheets(self) -> None:
        self.assertEqual(
            target_sheet_for_status(
                "ready",
                output_sheet_name="ROOM_Posts_v2",
                review_sheet_name="ROOM_Posts_Review",
            ),
            "ROOM_Posts_v2",
        )
        self.assertEqual(
            target_sheet_for_status(
                "needs_review",
                output_sheet_name="ROOM_Posts_v2",
                review_sheet_name="ROOM_Posts_Review",
            ),
            "ROOM_Posts_Review",
        )

    def test_generation_report_keeps_34_column_shape_and_no_secret_fields(self) -> None:
        scored = score_product(
            Product(
                category="ベビー用品",
                name="抱っこ布団 日本製 ダブルガーゼ",
                url="https://example.com/bedding",
                price=3980,
                review_count=500,
                review_average=4.6,
                caption="抱っこ布団 ねんねクッション ダブルガーゼ 綿100 洗える 背中スイッチ対策",
                catchcopy="抱っこ布団 ねんねクッション",
                shop_name="楽天ショップ",
                image_url="https://example.com/image.jpg",
            ),
            date(2026, 6, 16),
        )
        generated = FixedRulePostGenerator().generate(
            scored,
            context=GenerationContext(),
        )
        row = scored_product_to_row(
            scored,
            generated,
            today=date(2026, 6, 16),
            run_id="run123",
        )
        payload = build_report_payload(
            run_id="run123",
            executed_at=date(2026, 6, 16),
            generation_mode="fallback",
            output_sheet_name="ROOM_Posts_v2",
            review_sheet_name="ROOM_Posts_Review",
            fetch_report=None,
            items=[
                GenerationReportItem(
                    scored=scored,
                    generated=generated,
                    row=row,
                    write_sheet="ROOM_Posts_v2",
                    duplicate_result="重複なし",
                )
            ],
        )

        ensure_no_secret_fields(payload)
        self.assertEqual(payload["sheet_column_count"], 34)
        self.assertEqual(payload["items"][0]["sheet_row_column_count"], 34)
        self.assertEqual(payload["items"][0]["write_sheet"], "ROOM_Posts_v2")

    def test_unknown_row_is_not_targeted_to_normal_output_sheet(self) -> None:
        scored = score_product(
            Product(
                category="ベビー用品",
                name="分類できない育児用品",
                url="https://example.com/unknown",
                price=1000,
                review_count=10,
                review_average=4.0,
                caption="分類できない育児用品",
                catchcopy="",
                shop_name="楽天ショップ",
                image_url="https://example.com/image.jpg",
            ),
            date(2026, 6, 16),
        )
        generated = FixedRulePostGenerator().generate(scored, context=GenerationContext())

        self.assertEqual(generated.status, "needs_review")
        self.assertEqual(
            target_sheet_for_status(
                generated.status,
                output_sheet_name="ROOM_Posts_v2",
                review_sheet_name="ROOM_Posts_Review",
            ),
            "ROOM_Posts_Review",
        )

    def test_main_reads_legacy_sheet_but_does_not_append_to_it(self) -> None:
        main_source = (Path(__file__).resolve().parents[1] / "src" / "main.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("read_existing_urls(source_sheet_name)", main_source)
        self.assertNotIn("append_products(source_sheet_name", main_source)
        self.assertNotIn("append_error(source_sheet_name", main_source)


class SheetsLikeMixin:
    from sheets import SheetsClient

    read_existing_urls = SheetsClient.read_existing_urls


if __name__ == "__main__":
    unittest.main()
