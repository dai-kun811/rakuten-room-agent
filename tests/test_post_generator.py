from datetime import date
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_generator import (
    BANNED_EXPRESSIONS,
    BRAND_HASHTAG,
    build_hashtags,
    build_post_text,
    purchase_checkpoints,
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
                caption="ベビー キッズ 子ども 知育",
                catchcopy="送料無料 人気",
                shop_name="楽天ショップ",
                image_url="https://example.com/image.jpg",
            ),
            date(2026, 6, 8),
        )

        post_text = build_post_text(scored)

        self.assertTrue(all(expression not in post_text for expression in BANNED_EXPRESSIONS))
        self.assertIn("【遊びながら考える力を育てやすい◎", post_text)
        self.assertIn("【投稿文】", post_text)
        self.assertIn("選びやすい理由", post_text)
        self.assertIn("歳頃", post_text)
        self.assertIn("親目線", post_text)
        self.assertIn("おすすめ", post_text)
        self.assertNotIn("レビュー1,200件", post_text)
        self.assertNotIn("評価4.70", post_text)
        self.assertGreaterEqual(len(post_text), 200)
        self.assertLessEqual(len(post_text), 380)

    def test_hashtags_include_required_tags(self) -> None:
        scored = score_product(
            Product(
                category="絵本",
                name="3歳から楽しめる 知育 絵本",
                url="https://example.com/book",
                price=1200,
                review_count=400,
                review_average=4.6,
                caption="読み聞かせ おうち遊び 3歳",
                catchcopy="プレゼントにもおすすめ",
                shop_name="楽天ショップ",
                image_url="https://example.com/image.jpg",
            ),
            date(2026, 6, 8),
        )

        hashtags = build_hashtags(scored)
        tags = hashtags.split()

        self.assertEqual(len(tags), 5)
        self.assertEqual(tags[-1], BRAND_HASHTAG)
        self.assertIn("#プレゼントにおすすめ", tags)
        self.assertIn("#絵本", tags)
        self.assertIn("#おうち遊び", tags)
        self.assertIn("#1歳誕生日プレゼント", tags)

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

    def test_hashtags_avoid_broad_low_intent_tags(self) -> None:
        scored = score_product(
            Product(
                category="育児時短グッズ",
                name="共働き家庭向け 時短 ベビーグッズ",
                url="https://example.com/time",
                price=2500,
                review_count=300,
                review_average=4.5,
                caption="育児 時短 ベビー 共働き",
                catchcopy="おすすめ",
                shop_name="楽天ショップ",
                image_url="https://example.com/image.jpg",
            ),
            date(2026, 6, 8),
        )

        tags = build_hashtags(scored).split()

        self.assertEqual(tags, ["#おすすめ品", "#ベビーグッズ", "#育児時短", "#共働き育児", "#とらパパ厳選"])
        self.assertNotIn("#子育て", tags)
        self.assertNotIn("#育児", tags)
        self.assertNotIn("#ママ", tags)
        self.assertNotIn("#パパ", tags)

    def test_post_text_uses_some_emoji(self) -> None:
        scored = score_product(
            Product(
                category="おうち遊び",
                name="室内遊び 知育おもちゃ",
                url="https://example.com/home",
                price=1800,
                review_count=250,
                review_average=4.4,
                caption="おうち遊び 知育",
                catchcopy="人気",
                shop_name="楽天ショップ",
                image_url="https://example.com/image.jpg",
            ),
            date(2026, 6, 8),
        )

        post_text = build_post_text(scored)

        self.assertTrue(any(emoji in post_text for emoji in ["✨", "🧸", "📝", "😊", "🎁"]))

    def test_purchase_checkpoints_are_available_as_column_value(self) -> None:
        scored = score_product(
            Product(
                category="知育玩具",
                name="リング 知育 おもちゃ",
                url="https://example.com/ring",
                price=3980,
                review_count=500,
                review_average=4.6,
                caption="1歳 リング 小さな部品",
                catchcopy="人気",
                shop_name="楽天ショップ",
                image_url="https://example.com/image.jpg",
            ),
            date(2026, 6, 8),
        )

        checkpoints = purchase_checkpoints(scored.product, "知育玩具")
        post_text = build_post_text(scored)

        self.assertEqual(checkpoints, "対象年齢、誤飲リスク")
        self.assertIn("購入前は対象年齢、誤飲リスクだけ確認", post_text)


if __name__ == "__main__":
    unittest.main()
