from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from room_shadow_replay import products_from_generation_report, replay_generation_report


def source_report() -> dict:
    items = []
    products = (
        ("厚手 おしりふき 80枚 20個", "wipes-1"),
        ("木製積み木 24ピース 1歳", "blocks-1"),
        ("ベビー スリーパー 6重ガーゼ 綿 洗える", "sleep-1"),
        ("セルフミルク 赤ちゃんが自分で飲む 哺乳瓶ホルダー", "feeding-1"),
    )
    for name, slug in products:
        items.append(
            {
                "product_name": name,
                "product_url": f"https://item.rakuten.co.jp/test/{slug}",
                "sheet_row": {
                    "カテゴリ": "育児実用品",
                    "価格": "3000",
                    "レビュー件数": "500",
                    "評価": "4.6",
                    "ショップ名": "テスト店",
                    "検索キーワード": name,
                    "画像URL": "https://example.com/item.jpg",
                },
            }
        )
    return {"items": items}


class RoomShadowReplayTests(unittest.TestCase):
    def test_product_snapshots_ignore_old_generated_copy(self) -> None:
        report = source_report()
        report["items"][0]["body"] = "古い生成本文を入力根拠へ混ぜない"
        products = products_from_generation_report(report)
        self.assertEqual(len(products), 4)
        self.assertTrue(all("古い生成本文" not in product.text for product in products))

    def test_replay_writes_only_local_reports_and_routes_risky_feeding_to_review(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report = replay_generation_report(
                source_report(),
                routine_date=date(2026, 10, 6),
                source_actions_run_id="123",
                output_dir=Path(temporary),
            )
        self.assertEqual(report["output_sheet_name"], "SHADOW_ONLY_NO_WRITE")
        self.assertTrue(all(item["write_sheet"] == "" for item in report["items"]))
        risky = [item for item in report["items"] if "セルフミルク" in item["product_name"]]
        if risky:
            self.assertEqual(risky[0]["status"], "needs_review")
            self.assertTrue(
                any("manual_review_required" in reason for reason in risky[0]["review_reasons"])
            )


if __name__ == "__main__":
    unittest.main()
