from datetime import date
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_generator import (
    BANNED_EXPRESSIONS,
    REQUIRED_HASHTAGS,
    build_hashtags,
    build_post_text,
    shorten_product_name,
)
from rakuten_api import Product
from scoring import score_product


class PostGeneratorTest(unittest.TestCase):
    def test_post_text_does_not_use_banned_firsthand_expressions(self) -> None:
        scored = score_product(
            Product(
                category="知育玩具",
                name="知育 おもちゃ セット",
                url="https://example.com/item",
                price=3980,
                review_count=1200,
                review_average=4.7,
                caption="ベビー キッズ 子ども プレゼント",
                catchcopy="送料無料 人気 ギフト",
                shop_name="楽天ショップ",
                image_url="https://example.com/image.jpg",
            ),
            date(2026, 6, 8),
        )

        post_text = build_post_text(scored)

        self.assertTrue(all(expression not in post_text for expression in BANNED_EXPRESSIONS))
        self.assertIn("【タイトル】", post_text)
        self.assertIn("【投稿文】", post_text)
        self.assertNotIn("レビュー1,200件", post_text)
        self.assertNotIn("評価4.70", post_text)
        self.assertGreaterEqual(len(post_text), 200)
        self.assertLessEqual(len(post_text), 350)

    def test_hashtags_include_required_tags(self) -> None:
        hashtags = build_hashtags("絵本", "Aランク")

        for tag in REQUIRED_HASHTAGS:
            self.assertIn(tag, hashtags)
        self.assertEqual(len(hashtags.split()), 5)

    def test_long_product_name_is_shortened(self) -> None:
        name = "【送料無料】知育 おもちゃ セット 木製 パズル 積み木 プレゼント 子ども 幼児"

        shortened = shorten_product_name(name)

        self.assertLessEqual(len(shortened), 31)
        self.assertNotIn("【", shortened)
        self.assertTrue(shortened.endswith("..."))

    def test_post_text_avoids_ai_like_phrases(self) -> None:
        scored = score_product(
            Product(
                category="育児便利グッズ",
                name="育児 便利 グッズ",
                url="https://example.com/item",
                price=1980,
                review_count=5000,
                review_average=4.8,
                caption="育児 ベビー 時短",
                catchcopy="人気 送料無料",
                shop_name="楽天ショップ",
                image_url="https://example.com/image.jpg",
            ),
            date(2026, 6, 8),
        )

        post_text = build_post_text(scored)

        forbidden = [
            "総合スコア",
            "訴求語",
            "比較材料",
            "確認できる情報が多い",
            "必要な条件を短時間で見比べやすい",
            "まずは商品ページで",
        ]
        self.assertTrue(all(phrase not in post_text for phrase in forbidden))


if __name__ == "__main__":
    unittest.main()
