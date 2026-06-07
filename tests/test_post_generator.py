from datetime import date
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_generator import BANNED_EXPRESSIONS, REQUIRED_HASHTAGS, build_hashtags, build_post_text
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
        self.assertIn("レビュー1,200件", post_text)
        self.assertIn("評価4.70", post_text)

    def test_hashtags_include_required_tags(self) -> None:
        hashtags = build_hashtags("絵本", "Aランク")

        for tag in REQUIRED_HASHTAGS:
            self.assertIn(tag, hashtags)
        self.assertLessEqual(len(hashtags.split()), 10)


if __name__ == "__main__":
    unittest.main()
