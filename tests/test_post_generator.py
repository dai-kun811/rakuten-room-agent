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
    build_room_output,
    purchase_checkpoints,
    shorten_product_name,
)
from rakuten_api import Product
from scoring import score_product


class PostGeneratorTest(unittest.TestCase):
    def test_post_text_uses_requested_title_and_body_format(self) -> None:
        scored = score_product(
            Product(
                category="ベビー用消耗品",
                name="大容量 おしりふき まとめ買い 80枚×20個",
                url="https://example.com/wipes",
                price=3980,
                review_count=1200,
                review_average=4.7,
                caption="おしりふき 大容量 厚手 まとめ買い",
                catchcopy="毎日使う消耗品",
                shop_name="楽天ショップ",
                image_url="https://example.com/image.jpg",
            ),
            date(2026, 6, 9),
        )

        post_text = build_post_text(scored)
        title = post_text.split("投稿文：")[0].replace("タイトル：", "").strip()
        body = post_text.split("投稿文：")[1].strip()

        self.assertIn("タイトル：", post_text)
        self.assertIn("投稿文：", post_text)
        self.assertGreaterEqual(len(title), 14)
        self.assertLessEqual(len(title), 30)
        self.assertNotIn("おしりふき", title)
        self.assertGreaterEqual(len(body), 180)
        self.assertLessEqual(len(body), 280)

    def test_full_output_uses_requested_room_fields_without_review_references(self) -> None:
        scored = score_product(
            Product(
                category="ベビー用消耗品",
                name="大容量 おしりふき まとめ買い 80枚×20個",
                url="https://example.com/wipes",
                price=3980,
                review_count=1200,
                review_average=4.7,
                caption="おしりふき 大容量 厚手 まとめ買い",
                catchcopy="毎日使う消耗品",
                shop_name="楽天ショップ",
                image_url="https://example.com/image.jpg",
            ),
            date(2026, 6, 9),
        )

        output = build_room_output(scored)

        self.assertIn("ハッシュタグ：", output)
        self.assertNotIn("使用した根拠：", output)
        self.assertNotIn("API情報", output)
        self.assertNotIn("レビュー本文なし", output)
        self.assertIn("狙った感情：", output)
        self.assertNotIn("口コミで", output)
        self.assertNotIn("口コミ", output)
        self.assertNotIn("レビュー", output)
        self.assertNotIn("満足理由", output)

    def test_post_text_does_not_use_banned_expressions_or_product_name_title(self) -> None:
        scored = score_product(
            Product(
                category="知育玩具",
                name="RING10 知育リングセット",
                url="https://example.com/ring",
                price=5280,
                review_count=900,
                review_average=4.7,
                caption="1歳 2歳 3歳 指先 知育 リング",
                catchcopy="長く遊べる",
                shop_name="楽天ショップ",
                image_url="https://example.com/image.jpg",
            ),
            date(2026, 6, 9),
        )

        post_text = build_post_text(scored)
        title = post_text.split("投稿文：")[0]

        self.assertTrue(all(expression not in post_text for expression in BANNED_EXPRESSIONS))
        self.assertNotIn("RING10", title)
        self.assertNotIn("知育リングセット", title)
        self.assertIn("指先", post_text)
        self.assertIn("親子", post_text)
        self.assertIn("利用シーン", post_text)
        self.assertIn("集中", post_text)
        self.assertNotIn("口コミ", post_text)
        self.assertNotIn("レビュー", post_text)
        self.assertNotIn("評価", post_text)
        self.assertNotIn("満足理由", post_text)
        self.assertNotIn("商品ページ", post_text)

    def test_consumable_copy_targets_stock_anxiety_and_sale_timing(self) -> None:
        scored = score_product(
            Product(
                category="ベビー用消耗品",
                name="おしりふき 大容量 まとめ買い セット",
                url="https://example.com/wipes",
                price=3280,
                review_count=500,
                review_average=4.6,
                caption="おしりふき 大容量 厚手 まとめ買い セール",
                catchcopy="ストック向き 送料無料",
                shop_name="楽天ショップ",
                image_url="https://example.com/image.jpg",
            ),
            date(2026, 6, 9),
        )

        post_text = build_post_text(scored)

        self.assertIn("ストック", post_text)
        self.assertIn("買い足し", post_text)
        self.assertIn("最後の1パック", post_text)
        self.assertIn("気持ちがラク", post_text)
        self.assertIn("セール", post_text)
        self.assertIn("容量と価格を確認", post_text)
        self.assertNotIn("容量と価格だけでも確認", post_text)
        self.assertNotIn("チェックしてください", post_text)
        self.assertNotIn("プレゼント", post_text)
        self.assertNotIn("口コミ", post_text)
        self.assertNotIn("レビュー", post_text)
        self.assertNotIn("評価", post_text)
        self.assertNotIn("満足理由", post_text)
        self.assertNotIn("商品ページ", post_text)

    def test_shoes_copy_mentions_daycare_walk_and_ease_of_wear(self) -> None:
        scored = score_product(
            Product(
                category="キッズ用品",
                name="保育園用 キッズシューズ 面ファスナー 軽量",
                url="https://example.com/shoes",
                price=2980,
                review_count=420,
                review_average=4.5,
                caption="保育園 面ファスナー 軽量 スニーカー",
                catchcopy="歩きやすい",
                shop_name="楽天ショップ",
                image_url="https://example.com/image.jpg",
            ),
            date(2026, 6, 9),
        )

        post_text = build_post_text(scored)

        self.assertIn("保育園", post_text)
        self.assertIn("履かせやすさ", post_text)
        self.assertIn("歩き", post_text)
        self.assertIn("登園前の脱ぎ履き", post_text)
        self.assertIn("サイズ欠け", post_text)
        self.assertNotIn("口コミ", post_text)
        self.assertNotIn("レビュー", post_text)
        self.assertNotIn("評価", post_text)
        self.assertNotIn("満足理由", post_text)
        self.assertNotIn("商品ページ", post_text)

    def test_gift_pitch_only_used_when_practical_reason_exists(self) -> None:
        gift_scored = score_product(
            Product(
                category="プレゼント向き商品",
                name="出産祝い おむつギフトセット",
                url="https://example.com/gift",
                price=4500,
                review_count=350,
                review_average=4.4,
                caption="出産祝い おむつ ギフト セット",
                catchcopy="すぐ使える実用品",
                shop_name="楽天ショップ",
                image_url="https://example.com/image.jpg",
            ),
            date(2026, 6, 9),
        )
        normal_scored = score_product(
            Product(
                category="知育玩具",
                name="知育リングセット",
                url="https://example.com/toy",
                price=3500,
                review_count=350,
                review_average=4.4,
                caption="知育 リング 1歳",
                catchcopy="親子遊び",
                shop_name="楽天ショップ",
                image_url="https://example.com/image.jpg",
            ),
            date(2026, 6, 9),
        )

        gift_post = build_post_text(gift_scored)
        normal_post = build_post_text(normal_scored)
        gift_tags = build_hashtags(gift_scored).split()
        normal_tags = build_hashtags(normal_scored).split()

        self.assertIn("育児ギフト", gift_post)
        self.assertIn("#出産祝い", gift_tags)
        self.assertNotIn("#出産祝い", normal_tags)
        self.assertNotIn("育児ギフト", normal_post)

    def test_hashtags_include_required_tags_without_broad_low_intent_tags(self) -> None:
        scored = score_product(
            Product(
                category="本",
                name="3歳向け しかけ絵本",
                url="https://example.com/book",
                price=1200,
                review_count=400,
                review_average=4.6,
                caption="3歳 絵本 読み聞かせ",
                catchcopy="親子時間",
                shop_name="楽天ショップ",
                image_url="https://example.com/image.jpg",
            ),
            date(2026, 6, 9),
        )

        tags = build_hashtags(scored).split()

        self.assertEqual(len(tags), 5)
        self.assertEqual(tags[-1], BRAND_HASHTAG)
        self.assertIn("#絵本", tags)
        self.assertIn("#親子遊び", tags)
        self.assertIn("#3歳向け", tags)
        self.assertNotIn("#子育て", tags)
        self.assertNotIn("#育児", tags)
        self.assertNotIn("#ママ", tags)
        self.assertNotIn("#パパ", tags)

    def test_long_product_name_is_shortened(self) -> None:
        name = "限定カラー キッズシューズ 面ファスナー 軽量 通園 通学 まとめ買い対象モデル"

        shortened = shorten_product_name(name)

        self.assertLessEqual(len(shortened), 31)
        self.assertTrue(shortened.endswith("..."))

    def test_purchase_checkpoints_are_available_as_column_value(self) -> None:
        scored = score_product(
            Product(
                category="キッズ用品",
                name="保育園用 キッズシューズ 面ファスナー",
                url="https://example.com/ring",
                price=3980,
                review_count=500,
                review_average=4.6,
                caption="1歳 面ファスナー 軽量 シューズ",
                catchcopy="サイズ選び",
                shop_name="楽天ショップ",
                image_url="https://example.com/image.jpg",
            ),
            date(2026, 6, 9),
        )

        checkpoints = purchase_checkpoints(scored.product, "shoes")

        self.assertIn("サイズ", checkpoints)
        self.assertIn("対象年齢", checkpoints)
        self.assertIn("履き心地", checkpoints)


if __name__ == "__main__":
    unittest.main()
