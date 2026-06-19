from datetime import date
from pathlib import Path
from collections import Counter
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_generator import (
    BANNED_EXPRESSIONS,
    BRAND_HASHTAG,
    GenerationContext,
    build_hashtags,
    build_post_text,
    build_room_output,
    determine_appeal_category,
    purchase_checkpoints,
    shorten_product_name,
)
from rakuten_api import Product
from scoring import score_product


def post_body(post_text: str) -> str:
    return post_text.split("投稿文：")[1].strip()


def post_title(post_text: str) -> str:
    return post_text.split("投稿文：")[0].replace("タイトル：", "").strip()


class PostGeneratorTest(unittest.TestCase):
    def test_execution_copy_is_unique_specific_and_free_of_promotional_claims(self) -> None:
        products = [
            Product(
                category="知育玩具",
                name="口コミ3300件 楽天1位17冠 知育ブロック 組み立て パーツ",
                url=f"https://example.com/block-{index}",
                price=3980,
                review_count=3300,
                review_average=4.7,
                caption="ブロック 組み立て 形を変える 創造遊び",
                catchcopy="高評価 人気",
                shop_name="楽天ショップ",
                image_url="https://example.com/image.jpg",
            )
            for index in range(3)
        ]
        context = GenerationContext()
        posts = [
            build_post_text(score_product(product, date(2026, 6, 12)), context=context)
            for product in products
        ]
        titles = [post_title(post) for post in posts]
        bodies = [post_body(post) for post in posts]
        openings = ["。".join(body.split("。")[:2]) for body in bodies]

        self.assertEqual(len(titles), len(set(titles)))
        self.assertEqual(len(openings), len(set(openings)))
        for body in bodies:
            self.assertGreaterEqual(len(body), 160, body)
            self.assertLessEqual(len(body), 220)
            self.assertIn(body.count("。"), {3, 4})
            self.assertTrue(any(word in body for word in ["ブロック", "組み立て", "形を変える", "パーツ", "創造遊び"]))
            for banned in [
                "口コミ",
                "レビュー",
                "楽天1位",
                "冠",
                "高評価",
                "人気",
                "売れている",
                "見た目だけで決めず",
                "安全に長く遊べる",
                "これなら親子で長く遊べる",
            ]:
                self.assertNotIn(banned, body)

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
        self.assertLessEqual(len(title), 20)
        self.assertNotIn("おしりふき", title)
        self.assertGreaterEqual(len(body), 160)
        self.assertLessEqual(len(body), 260)

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
        self.assertNotIn("利用シーン", post_text)
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
        self.assertIn("焦", post_text)
        self.assertIn("容量", post_text)
        self.assertIn("価格", post_text)
        self.assertIn("ストック", post_text)
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
        self.assertIn("自分で履きたい気持ち", post_text)
        self.assertIn("園用", post_text)
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
        self.assertIn("出産祝いは", gift_post)
        self.assertIn("育児で使える", gift_post)
        self.assertIn("月齢", gift_post)
        self.assertNotIn("かわいさより相手の生活で使われるかが大事", gift_post)
        self.assertIn("#出産祝い", gift_tags)
        self.assertNotIn("#出産祝い", normal_tags)
        self.assertNotIn("育児ギフト", normal_post)

    def test_milk_copy_mentions_last_can_and_parent_relief(self) -> None:
        scored = score_product(
            Product(
                category="ベビー用消耗品",
                name="粉ミルク まとめ買い セット",
                url="https://example.com/milk",
                price=5980,
                review_count=500,
                review_average=4.6,
                caption="ミルク 大容量 まとめ買い セール",
                catchcopy="夜中のストック向き",
                shop_name="楽天ショップ",
                image_url="https://example.com/image.jpg",
            ),
            date(2026, 6, 10),
        )

        post_text = build_post_text(scored)

        self.assertIn("最後の1缶", post_text)
        self.assertIn("夜中や夕方のバタバタ", post_text)
        self.assertIn("ストック", post_text)
        self.assertNotIn("口コミ", post_text)
        self.assertNotIn("レビュー", post_text)

    def test_post_text_includes_product_specific_anchor(self) -> None:
        alpha = score_product(
            Product(
                category="ベビー用消耗品",
                name="Alpha Stock Wipes",
                url="https://example.com/alpha",
                price=3280,
                review_count=500,
                review_average=4.6,
                caption="おしりふき 大容量 まとめ買い",
                catchcopy="ストック向き",
                shop_name="楽天ショップ",
                image_url="https://example.com/image.jpg",
            ),
            date(2026, 6, 10),
        )
        beta = score_product(
            Product(
                category="ベビー用消耗品",
                name="Beta Night Diapers",
                url="https://example.com/beta",
                price=3280,
                review_count=500,
                review_average=4.6,
                caption="おむつ 夜用 まとめ買い",
                catchcopy="ストック向き",
                shop_name="楽天ショップ",
                image_url="https://example.com/image.jpg",
            ),
            date(2026, 6, 10),
        )

        alpha_post = build_post_text(alpha)
        beta_post = build_post_text(beta)

        self.assertIn("おしりふき", alpha_post)
        self.assertIn("おむつ", beta_post)
        self.assertNotEqual(alpha_post, beta_post)

    def test_post_text_moves_from_pain_to_purchase_check(self) -> None:
        scored = score_product(
            Product(
                category="ベビー用消耗品",
                name="夜用おむつ まとめ買い セット",
                url="https://example.com/diapers",
                price=3980,
                review_count=500,
                review_average=4.6,
                caption="おむつ 夜用 大容量 まとめ買い",
                catchcopy="ストック向き 送料無料",
                shop_name="楽天ショップ",
                image_url="https://example.com/image.jpg",
            ),
            date(2026, 6, 10),
        )

        post_text = build_post_text(scored)

        self.assertIn("焦", post_text)
        self.assertIn("夜用おむつ", post_text)
        self.assertIn("見比べて", post_text)
        self.assertIn("ストック", post_text)
        self.assertNotIn("おすすめです", post_text)
        self.assertNotIn("レビュー", post_text)

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

    def test_hashtags_use_intent_specific_fallbacks(self) -> None:
        scored = score_product(
            Product(
                category="ベビー用消耗品",
                name="大容量ティッシュ セット",
                url="https://example.com/tissue",
                price=2480,
                review_count=400,
                review_average=4.6,
                caption="ティッシュ まとめ買い セット",
                catchcopy="ストック向き",
                shop_name="楽天ショップ",
                image_url="https://example.com/image.jpg",
            ),
            date(2026, 6, 9),
        )

        tags = build_hashtags(scored).split()

        self.assertIn("#ストック管理", tags)
        self.assertNotIn("#子ども用品", tags)
        self.assertNotIn("#楽天ROOM", tags)

    def test_outing_feeding_and_storage_copy_use_distinct_scenes(self) -> None:
        products = [
            Product(
                category="外出グッズ",
                name="ベビーカー バッグ 外出 収納",
                url="https://example.com/outing",
                price=2980,
                review_count=300,
                review_average=4.5,
                caption="ベビーカー 外出 荷物 整理",
                catchcopy="子連れ外出",
                shop_name="楽天ショップ",
                image_url="https://example.com/image.jpg",
            ),
            Product(
                category="離乳食グッズ",
                name="離乳食 エプロン 食べこぼし対策",
                url="https://example.com/feeding",
                price=1980,
                review_count=300,
                review_average=4.5,
                caption="離乳食 食器 エプロン 片づけ",
                catchcopy="離乳食準備",
                shop_name="楽天ショップ",
                image_url="https://example.com/image.jpg",
            ),
            Product(
                category="収納",
                name="おもちゃ収納 ラック リビング整理",
                url="https://example.com/storage",
                price=4980,
                review_count=300,
                review_average=4.5,
                caption="おもちゃ 収納 ラック 片づけ",
                catchcopy="リビング整理",
                shop_name="楽天ショップ",
                image_url="https://example.com/image.jpg",
            ),
        ]

        outputs = [build_post_text(score_product(item, date(2026, 6, 9))) for item in products]
        tags = [build_hashtags(score_product(item, date(2026, 6, 9))).split() for item in products]

        self.assertIn("外出", outputs[0])
        self.assertIn("食後", outputs[1])
        self.assertIn("戻す場所", outputs[2])
        self.assertIn("#ベビーカーグッズ", tags[0])
        self.assertNotIn("#おもちゃ収納", tags[0])
        self.assertIn("#子連れ外出", tags[0])
        self.assertIn("#離乳食準備", tags[1])
        self.assertIn("#子ども部屋準備", tags[2])

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

    def test_toy_camera_and_sleep_products_keep_type_specific_copy(self) -> None:
        banned_reason_terms = ["消耗品", "買い忘れ", "ストック需要", "まとめ買い", "毎日切らす"]
        products = [
            Product(
                category="知育玩具",
                name="積み木 音が鳴る 木製 知育玩具 赤ちゃん 安全 大きめサイズ 名入れ",
                url="https://example.com/blocks",
                price=3980,
                review_count=800,
                review_average=4.6,
                caption="1歳 積み木 木のおもちゃ 音が鳴る パーツ 大きめ 名入れ",
                catchcopy="誕生日 プレゼント",
                shop_name="楽天ショップ",
                image_url="https://example.com/image.jpg",
            ),
            Product(
                category="キッズ用品",
                name="【公式】MiNiPiC キッズカメラ ゲームなし スマホ転送 SDカード",
                url="https://example.com/camera",
                price=4980,
                review_count=800,
                review_average=4.6,
                caption="キッズカメラ 写真 撮影 旅行 スマホ転送 SDカード ゲームなし",
                catchcopy="誕生日プレゼント",
                shop_name="楽天ショップ",
                image_url="https://example.com/image.jpg",
            ),
            Product(
                category="ベビー用品",
                name="ラルミー ホワイトノイズ 授乳ライト 寝かしつけ 夜泣き",
                url="https://example.com/sleep",
                price=3980,
                review_count=800,
                review_average=4.6,
                caption="ホワイトノイズ 授乳ライト 胎内音 睡眠 スピーカー",
                catchcopy="出産祝い ギフト",
                shop_name="楽天ショップ",
                image_url="https://example.com/image.jpg",
            ),
        ]

        reasons = [score_product(item, date(2026, 6, 9)).recommendation_reason for item in products]
        posts = [build_post_text(score_product(item, date(2026, 6, 9))) for item in products]
        tags = [build_hashtags(score_product(item, date(2026, 6, 9))).split() for item in products]

        self.assertTrue(all(term not in reasons[0] for term in banned_reason_terms))
        self.assertTrue(all(term not in reasons[1] for term in banned_reason_terms))
        self.assertIn("振る・積む", reasons[0])
        self.assertIn("キッズカメラ", reasons[1])
        self.assertIn("夜の授乳", reasons[2])
        self.assertNotIn("#ベビーカーグッズ", tags[2])
        self.assertIn("#ホワイトノイズ", tags[2])
        self.assertIn("#授乳ライト", tags[2])
        self.assertRegex(posts[0], r"手先|親子|遊び方|対象年齢|パーツ|収納")
        self.assertRegex(posts[1], r"写真|撮る|外出|旅行|スマホ転送|SDカード|ゲームなし")
        self.assertRegex(posts[2], r"寝かしつけ|授乳|ライト|音|寝室|電源")

    def test_unrelated_purchase_checks_do_not_leak_between_product_types(self) -> None:
        toy = Product(
            category="知育玩具",
            name="木のおもちゃ アクティビティキューブ 1歳 名入れ無料 1年保証",
            url="https://example.com/activity",
            price=5980,
            review_count=600,
            review_average=4.7,
            caption="知育玩具 1歳 木のおもちゃ アクティビティキューブ 型はめ ルーピング",
            catchcopy="誕生日プレゼント",
            shop_name="楽天ショップ",
            image_url="https://example.com/image.jpg",
        )
        camera = Product(
            category="キッズ用品",
            name="キッズカメラ ゲームなし スマホ転送 SDカード 充電式",
            url="https://example.com/camera",
            price=4980,
            review_count=600,
            review_average=4.7,
            caption="写真 撮影 外出 旅行 スマホ転送 SDカード ゲームなし",
            catchcopy="誕生日プレゼント",
            shop_name="楽天ショップ",
            image_url="https://example.com/image.jpg",
        )
        sleep = Product(
            category="ベビー用品",
            name="ホワイトノイズ 授乳ライト 寝かしつけ スピーカー",
            url="https://example.com/sleep",
            price=4980,
            review_count=600,
            review_average=4.7,
            caption="夜泣き 胎内音 睡眠 ライト 音 電源",
            catchcopy="出産祝い",
            shop_name="楽天ショップ",
            image_url="https://example.com/image.jpg",
        )

        for product in [toy, camera, sleep]:
            appeal = determine_appeal_category(product)
            checks = purchase_checkpoints(product, appeal)
            self.assertNotIn("履き心地", checks)
            self.assertNotIn("洗う手間", checks)
            self.assertNotIn("購入単位", checks)

    def test_post_text_does_not_use_truncated_product_name_anchor(self) -> None:
        scored = score_product(
            Product(
                category="知育玩具",
                name="積み木 音が鳴る 木製 知育玩具 赤ちゃん 安全 大きめサイズ 名入れ無料 かわいい プレゼント",
                url="https://example.com/long-blocks",
                price=3980,
                review_count=500,
                review_average=4.6,
                caption="1歳 積み木 木のおもちゃ 音が鳴る",
                catchcopy="誕生日プレゼント",
                shop_name="楽天ショップ",
                image_url="https://example.com/image.jpg",
            ),
            date(2026, 6, 9),
        )

        post_text = build_post_text(scored)
        body = post_text.split("投稿文：")[1]

        self.assertNotRegex(body, r"\.\.\.は")
        self.assertIn("音が鳴る木製つみき", body)

    def test_post_openings_are_not_repeated_three_times_for_same_category(self) -> None:
        products = [
            Product(
                category="知育玩具",
                name="積み木 木製 知育玩具",
                url="https://example.com/blocks",
                price=3980,
                review_count=500,
                review_average=4.6,
                caption="1歳 積み木 木のおもちゃ",
                catchcopy="誕生日プレゼント",
                shop_name="楽天ショップ",
                image_url="https://example.com/image.jpg",
            ),
            Product(
                category="知育玩具",
                name="アクティビティキューブ 木のおもちゃ",
                url="https://example.com/activity",
                price=5980,
                review_count=500,
                review_average=4.6,
                caption="1歳 型はめ ルーピング 知育玩具",
                catchcopy="名入れ 誕生日",
                shop_name="楽天ショップ",
                image_url="https://example.com/image.jpg",
            ),
            Product(
                category="知育玩具",
                name="リングテン 紐通し 木製玩具",
                url="https://example.com/ring",
                price=4980,
                review_count=500,
                review_average=4.6,
                caption="リング 紐通し 手先 知育",
                catchcopy="長く遊べる",
                shop_name="楽天ショップ",
                image_url="https://example.com/image.jpg",
            ),
        ]

        openings = [
            build_post_text(score_product(product, date(2026, 6, 9))).split("投稿文：")[1].strip().split("。")[0]
            for product in products
        ]

        self.assertGreater(len(set(openings)), 1)

    def test_review_count_and_rating_are_not_purchase_reasons_in_post(self) -> None:
        scored = score_product(
            Product(
                category="キッズ用品",
                name="キッズカメラ レビュー8000件 評価4.8 ゲームなし",
                url="https://example.com/review-camera",
                price=4980,
                review_count=8000,
                review_average=4.8,
                caption="キッズカメラ 写真 撮影 スマホ転送 SDカード",
                catchcopy="誕生日プレゼント",
                shop_name="楽天ショップ",
                image_url="https://example.com/image.jpg",
            ),
            date(2026, 6, 9),
        )

        post_text = build_post_text(scored)

        self.assertNotIn("レビュー", post_text)
        self.assertNotIn("評価", post_text)

    def test_post_body_is_compact_and_avoids_repeated_claims(self) -> None:
        products = [
            Product(
                category="知育玩具",
                name="積み木 音が鳴る 木製 知育玩具 赤ちゃん 安全 大きめサイズ 名入れ",
                url="https://example.com/blocks",
                price=3980,
                review_count=800,
                review_average=4.6,
                caption="1歳 積み木 木のおもちゃ 音が鳴る パーツ 大きめ 名入れ",
                catchcopy="誕生日 プレゼント",
                shop_name="楽天ショップ",
                image_url="https://example.com/image.jpg",
            ),
            Product(
                category="知育玩具",
                name="木のおもちゃ アクティビティキューブ 1歳 名入れ無料 1年保証",
                url="https://example.com/activity",
                price=5980,
                review_count=600,
                review_average=4.7,
                caption="知育玩具 1歳 木のおもちゃ アクティビティキューブ 型はめ ルーピング",
                catchcopy="誕生日プレゼント",
                shop_name="楽天ショップ",
                image_url="https://example.com/image.jpg",
            ),
            Product(
                category="知育玩具",
                name="リングテン 紐通し 木製玩具",
                url="https://example.com/ring",
                price=4980,
                review_count=500,
                review_average=4.6,
                caption="リング 紐通し 手先 知育",
                catchcopy="長く遊べる",
                shop_name="楽天ショップ",
                image_url="https://example.com/image.jpg",
            ),
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
        ]

        posts = [build_post_text(score_product(product, date(2026, 6, 12))) for product in products]
        bodies = [post_body(text) for text in posts]
        titles = [post_title(text) for text in posts]

        for body in bodies:
            self.assertLessEqual(body.count("。"), 5)
            self.assertGreaterEqual(len(body), 160, body)
            self.assertLessEqual(len(body), 220)
            self.assertNotIn("ところを見たいです", body)
            self.assertNotIn("判断したいです", body)
            self.assertNotIn("親子で遊び方を広げやすい", body)
            self.assertNotRegex(body, r"(.{2,12})は、\1を")

        self.assertTrue(all(count < 3 for count in Counter(titles).values()))
        self.assertNotIn("これなら親子で長く遊べる", titles)
        self.assertNotIn("遊びながら成長を感じたい日", titles)
        self.assertNotIn("おしりふきは、おしりふき", bodies[-1])

    def test_purchase_checkpoints_appear_only_in_final_sentence(self) -> None:
        products_and_terms = [
            (
                Product(
                    category="知育玩具",
                    name="積み木 音が鳴る 木製 知育玩具 赤ちゃん 安全 大きめサイズ 名入れ",
                    url="https://example.com/blocks",
                    price=3980,
                    review_count=800,
                    review_average=4.6,
                    caption="1歳 積み木 木のおもちゃ 音が鳴る パーツ 大きめ 名入れ",
                    catchcopy="誕生日 プレゼント",
                    shop_name="楽天ショップ",
                    image_url="https://example.com/image.jpg",
                ),
                ["対象年齢", "パーツ"],
            ),
            (
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
                ["容量", "価格", "置き場所"],
            ),
        ]

        for product, terms in products_and_terms:
            body = post_body(build_post_text(score_product(product, date(2026, 6, 12))))
            sentences = [sentence for sentence in body.split("。") if sentence]
            final_sentence = sentences[-1]
            previous_text = "。".join(sentences[:-1])
            for term in terms:
                self.assertEqual(body.count(term), 1)
                self.assertNotIn(term, previous_text)
                self.assertIn(term, final_sentence)


if __name__ == "__main__":
    unittest.main()
