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
    score_product,
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
        self.assertEqual(score_sales(100), 7)
        self.assertEqual(score_sales(301), 13)
        self.assertEqual(score_sales(1001), 17)
        self.assertEqual(score_sales(5001), 20)

    def test_rating_score_buckets(self) -> None:
        self.assertEqual(score_rating(4.3), 8)
        self.assertEqual(score_rating(4.5), 12)
        self.assertEqual(score_rating(4.7), 15)

    def test_price_score_prioritizes_room_friendly_prices(self) -> None:
        self.assertEqual(score_price(3000), 10)
        self.assertEqual(score_price(8000), 10)
        self.assertEqual(score_price(1500), 7)
        self.assertEqual(score_price(12000), 7)
        self.assertEqual(score_price(900), 3)

    def test_low_review_count_and_rating_are_filtered_out(self) -> None:
        with patch.dict("os.environ", {"ENABLE_RELAXED_FALLBACK": "false"}):
            selected = filter_and_score_products(
                [
                    product(
                        name="ベビー 時短 グッズ",
                        url="https://example.com/ok",
                        review_count=5001,
                        review_average=4.7,
                        caption="育児 ベビー キッズ 子ども 知育 時短 毎日 収納 セット サイズ 6月 梅雨 防水",
                    ),
                    product(name="レビュー不足", url="https://example.com/low-review", review_count=99),
                    product(name="評価不足", url="https://example.com/low-rating", review_average=4.29),
                ],
                date(2026, 6, 8),
            )

        self.assertEqual([item.product.url for item in selected], ["https://example.com/ok"])

    def test_selects_five_products_across_relaxed_tiers(self) -> None:
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
        self.assertTrue(all(0 <= item.total_score <= 100 for item in selected))

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


    def test_recommendation_reason_does_not_use_review_count_or_rating_copy(self) -> None:
        scored = score_product(
            product(
                category="ベビー用消耗品",
                name="おしりふき 大容量 まとめ買い セット",
                url="https://example.com/wipes",
                review_count=1200,
                review_average=4.8,
                caption="おしりふき 大容量 まとめ買い セール",
            ),
            date(2026, 6, 9),
        )

        reason = scored.recommendation_reason

        self.assertIn("おしりふき", reason)
        self.assertIn("個数・収納場所・1個あたり価格", reason)
        self.assertNotIn("レビュー", reason)
        self.assertNotIn("評価", reason)
        self.assertNotIn("総合スコア", reason)

    def test_recommendation_reason_uses_product_specific_anchor(self) -> None:
        alpha = score_product(
            product(name="Alpha Stock Wipes", url="https://example.com/alpha"),
            date(2026, 6, 9),
        )
        beta = score_product(
            product(name="Beta Night Diapers", url="https://example.com/beta"),
            date(2026, 6, 9),
        )

        self.assertIn("Alpha Stock Wipes", alpha.recommendation_reason)
        self.assertIn("Beta Night Diapers", beta.recommendation_reason)
        self.assertNotEqual(alpha.recommendation_reason, beta.recommendation_reason)

    def test_recommendation_reason_frames_marketing_decision(self) -> None:
        scored = score_product(
            product(name="夜用おむつ まとめ買い セット", url="https://example.com/diapers"),
            date(2026, 6, 9),
        )

        reason = scored.recommendation_reason

        self.assertIn("紙おむつ", reason)
        self.assertIn("購入前に確認", reason)
        self.assertIn("サイズ・枚数・1枚あたり価格", reason)
        self.assertNotIn("レビュー", reason)
        self.assertNotIn("評価", reason)

    def test_recommendation_reason_is_one_sentence_and_keeps_toys_out_of_gift_logic(self) -> None:
        scored = score_product(
            product(
                category="知育玩具",
                name="2歳向け 木製 積み木 知育玩具",
                url="https://example.com/blocks",
                caption="2歳 木製 積み木 知育玩具",
            ),
            date(2026, 6, 9),
        )

        reason = scored.recommendation_reason

        self.assertEqual(reason.count("。"), 1)
        self.assertIn("積む・並べる", reason)
        self.assertIn("対象年齢", reason)
        self.assertNotIn("贈った後", reason)

    def test_recommendation_reason_uses_outing_purchase_check(self) -> None:
        scored = score_product(
            product(
                category="外出グッズ",
                name="ベビーカー バッグ 外出 収納",
                url="https://example.com/outing",
                caption="ベビーカー 外出 荷物 整理",
            ),
            date(2026, 6, 9),
        )

        reason = scored.recommendation_reason

        self.assertIn("ベビーカー周り", reason)
        self.assertIn("取り付け方法", reason)

    def test_specific_product_types_do_not_fall_back_to_consumable_reason(self) -> None:
        cases = [
            (
                product(
                    category="知育玩具",
                    name="積み木 音が鳴る 木製 知育玩具",
                    url="https://example.com/blocks",
                    caption="1歳 積み木 木のおもちゃ 音が鳴る 名入れ",
                ),
                "振る・積む",
            ),
            (
                product(
                    category="キッズ用品",
                    name="キッズカメラ ゲームなし スマホ転送 SDカード",
                    url="https://example.com/camera",
                    caption="写真 撮影 外出 旅行 スマホ転送 SDカード ゲームなし",
                ),
                "キッズカメラ",
            ),
            (
                product(
                    category="ベビー用品",
                    name="ホワイトノイズ 授乳ライト 寝かしつけ",
                    url="https://example.com/sleep",
                    caption="夜泣き 胎内音 睡眠 スピーカー ライト 電源",
                ),
                "ホワイトノイズ",
            ),
            (
                product(
                    category="寝かしつけ用品",
                    name="おくるみ スワドル モロー反射 新生児",
                    url="https://example.com/swaddle",
                    caption="おくるみ スワドル モロー反射 新生児 夜 洗える",
                ),
                "スワドル",
            ),
            (
                product(
                    category="ベビー用品",
                    name="ハンズフリー授乳 ママ代行ミルク屋さん",
                    url="https://example.com/nursing",
                    caption="ハンズフリー授乳 ミルクサポート 哺乳瓶ホルダー",
                ),
                "ハンズフリー",
            ),
            (
                product(
                    category="寝かしつけ用品",
                    name="抱っこ布団 日本製 ダブルガーゼ",
                    url="https://example.com/bedding",
                    caption="抱っこ布団 ねんねクッション ダブルガーゼ 綿100 洗える",
                ),
                "抱っこ布団",
            ),
        ]

        for item, expected in cases:
            reason = score_product(item, date(2026, 6, 9)).recommendation_reason
            self.assertIn(expected, reason)
            self.assertNotIn("消耗品", reason)
            self.assertNotIn("買い忘れ", reason)
            self.assertNotIn("ストック需要", reason)
            self.assertNotIn("購入単位", reason)


if __name__ == "__main__":
    unittest.main()
