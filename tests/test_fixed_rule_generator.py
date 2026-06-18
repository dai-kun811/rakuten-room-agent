from __future__ import annotations

import os
import json
import sys
import unittest
from dataclasses import replace
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fixed_rule_generator import (
    BANNED_INTENTION_PHRASES,
    FixedRulePostGenerator,
    GenerationContext,
    HASHTAGS,
    PATTERNS,
    ProductAttributes,
    classify_product_type,
    confirmation_repeat_count,
    ending_family,
    extract_attributes,
    stable_index,
    split_sentences,
    syntax_similarity,
    validate_post,
)
from llm_post_generator import OpenAIPostGenerator
from rakuten_api import Product
from scoring import score_product


def product_for(product_type: str, *, suffix: str = "") -> Product:
    data = {
        "wipes": (
            "厚手 おしりふき 80枚 20個",
            "厚手 水分量 おしりふき 80枚 20個 おむつ替え 食後",
        ),
        "swaddle": (
            "モロー反射 おくるみ スワドル 新生児",
            "モロー反射 おくるみ スワドル 新生児 夜 洗える コットン",
        ),
        "nursing_support": (
            "ハンズフリー授乳 ママ代行ミルク屋さん",
            "ハンズフリー授乳 ミルクサポート 哺乳瓶ホルダー 授乳準備",
        ),
        "baby_bedding": (
            "抱っこ布団 日本製 ダブルガーゼ",
            "抱っこ布団 ねんねクッション ダブルガーゼ 綿100 洗える 背中スイッチ対策",
        ),
        "baby_care": (
            "ベビー保湿剤 ベビーローション 新生児",
            "ベビー保湿剤 ベビーローション 保湿 新生児 全身 お風呂上がり",
        ),
        "baby_sleep": (
            "ベビー スリーパー ガーゼ 寝冷え対策",
            "ベビー スリーパー ガーゼ 綿 夜 寝冷え 洗える 新生児",
        ),
        "diaper": (
            "紙おむつ パンツタイプ Mサイズ 52枚",
            "紙おむつ パンツタイプ Mサイズ 52枚 夜間交換 外出",
        ),
        "formula": (
            "粉ミルク 800g 2缶",
            "粉ミルク 800g 2缶 授乳 夜間 残量管理",
        ),
        "sound_blocks": (
            "音が鳴る木製積み木 12ピース 名入れ",
            "音が鳴る積み木 木製 12ピース 名入れ 1歳",
        ),
        "wooden_blocks": (
            "木製積み木 24ピース",
            "木製積み木 24ピース 1歳 積む 並べる",
        ),
        "magnetic_blocks": (
            "マグネットブロック 48ピース",
            "マグネットブロック 48ピース 3歳 平面 立体",
        ),
        "baby_walker_toy": (
            "木製 手押し車 ファーストウォーカー",
            "木製 手押し車 ファーストウォーカー つかまり立ち リビング",
        ),
        "activity_cube": (
            "アクティビティキューブ 型はめ ルーピング",
            "アクティビティキューブ 型はめ ルーピング 1歳",
        ),
        "ring_toy": (
            "リング玩具 紐通し 20パーツ",
            "リング玩具 紐通し 20パーツ 1歳 積む 並べる",
        ),
        "kids_camera": (
            "キッズカメラ ゲームなし スマホ転送 SDカード USB充電",
            "キッズカメラ ゲームなし スマホ転送 SDカード USB充電 5歳",
        ),
        "sleep_light": (
            "ホワイトノイズ 授乳ライト コードレス USB充電",
            "ホワイトノイズ 授乳ライト コードレス USB充電 音量",
        ),
        "soothing_plush": (
            "寝かしつけぬいぐるみ プラネタリウム メロディー",
            "寝かしつけぬいぐるみ プラネタリウム メロディー オルゴール 投影 音楽",
        ),
        "stroller_storage": (
            "ベビーカー用バッグ 軽量 防水 6ポケット",
            "ベビーカー用バッグ 軽量 防水 6ポケット 取り付け",
        ),
    }[product_type]
    return Product(
        category=product_type,
        name=f"{data[0]} {suffix}".strip(),
        url=f"https://example.com/{product_type}/{suffix or 'base'}",
        price=3980,
        review_count=500,
        review_average=4.5,
        caption=data[1],
        catchcopy=data[1],
        shop_name="テストショップ",
        image_url="https://example.com/image.jpg",
        search_keyword=product_type,
    )


def generate(product_type: str, *, context: GenerationContext | None = None):
    product = product_for(product_type)
    return FixedRulePostGenerator().generate(
        score_product(product, date(2026, 6, 16)),
        context=context or GenerationContext(),
    )


class FixedRuleGeneratorTest(unittest.TestCase):
    def test_supported_types_are_classified(self) -> None:
        for product_type in HASHTAGS:
            self.assertEqual(
                classify_product_type(product_for(product_type)),
                product_type,
            )

    def test_requested_new_product_types_are_classified(self) -> None:
        cases = [
            ("【楽天3冠】モロー反射 おくるみ スワドル 新生児", "swaddle"),
            ("スワドル 手が出せる おくるみ 新生児", "swaddle"),
            ("赤ちゃん用ねくるみ モロー反射対策", "swaddle"),
            ("ハンズフリー授乳 ママ代行ミルク屋さん", "nursing_support"),
            ("哺乳瓶ホルダー 授乳サポート", "nursing_support"),
            ("授乳クッション 新生児", "nursing_support"),
            ("抱っこ布団 日本製 ダブルガーゼ", "baby_bedding"),
            ("背中スイッチ対策 ねんねクッション", "baby_bedding"),
            ("ベビー布団 新生児用", "baby_bedding"),
            ("ベビー保湿剤 ベビーローション 新生児", "baby_care"),
            ("赤ちゃん 爪切り ケア用品", "baby_care"),
            ("鼻吸い器 鼻水吸引 ベビーケア", "baby_care"),
            ("ベビー スリーパー ガーゼ 寝冷え対策", "baby_sleep"),
            ("ナイトライト 夜のお世話 ベビー", "baby_sleep"),
            ("紙おむつ 新生児 テープタイプ", "diaper"),
            ("ベビー用おむつ パンツタイプ", "diaper"),
            ("寝かしつけぬいぐるみ プラネタリウム メロディー", "soothing_plush"),
            ("プラネタリウム付きぬいぐるみ オルゴール", "soothing_plush"),
            ("投影機能付きベビートイ 音楽 ライト", "soothing_plush"),
            ("木製 手押し車", "baby_walker_toy"),
            ("ファーストウォーカー", "baby_walker_toy"),
            ("ベビーウォーカー", "baby_walker_toy"),
            ("カタカタ 押し車", "baby_walker_toy"),
            ("つかまり立ち おもちゃ", "baby_walker_toy"),
            ("木製積み木", "wooden_blocks"),
            ("つみき", "wooden_blocks"),
            ("ウッドブロック", "wooden_blocks"),
            ("スタッキングブロック", "wooden_blocks"),
            ("マグネットブロック", "magnetic_blocks"),
            ("磁石ブロック", "magnetic_blocks"),
            ("マグビルド 磁気ブロック", "magnetic_blocks"),
        ]
        for name, expected in cases:
            product = replace(product_for("wipes"), name=name, caption=name, catchcopy=name)
            self.assertEqual(classify_product_type(product), expected, name)

    def test_requested_non_diaper_products_do_not_become_diaper(self) -> None:
        cases = [
            "おくるみ スワドル 新生児",
            "授乳クッション",
            "抱っこ布団",
            "ベビー保湿剤 ベビーローション",
            "ベビー スリーパー ガーゼ",
            "木製手押し車",
            "木製ままごと",
            "木製パズル",
            "木製楽器",
        ]
        for name in cases:
            product = replace(product_for("wipes"), name=name, caption=name, catchcopy=name)
            self.assertNotEqual(classify_product_type(product), "diaper", name)

    def test_wooden_toy_non_blocks_do_not_become_wooden_blocks(self) -> None:
        cases = [
            ("木製手押し車", "baby_walker_toy"),
            ("木製ままごと", "unknown"),
            ("木製パズル", "unknown"),
            ("木製楽器", "unknown"),
        ]
        for name, expected in cases:
            product = replace(product_for("wipes"), name=name, caption=name, catchcopy=name)
            self.assertEqual(classify_product_type(product), expected, name)

    def test_baby_walker_toy_posts_are_not_blocks_or_walking_claims(self) -> None:
        cases = [
            "木製 手押し車",
            "ファーストウォーカー",
            "ベビーウォーカー",
            "カタカタ 押し車",
            "つかまり立ち おもちゃ",
        ]
        forbidden = ["積み木", "歩けるようになる", "成長が早まる", "必ず", "絶対", "万能", "完璧"]
        for name in cases:
            product = replace(
                product_for("baby_walker_toy"),
                name=name,
                caption=f"{name} 木製 リビング つかまり立ち",
                catchcopy=f"{name} 木製 リビング つかまり立ち",
                url=f"https://example.com/baby-walker/{name}",
            )
            generated = FixedRulePostGenerator().generate(
                score_product(product, date(2026, 6, 16)),
                context=GenerationContext(),
            )
            self.assertEqual(generated.attributes.product_type, "baby_walker_toy")
            self.assertEqual(generated.status, "ready", generated.quality_errors)
            self.assertGreaterEqual(len(generated.body), 160, generated.body)
            self.assertLessEqual(len(generated.body), 220, generated.body)
            self.assertLessEqual(len(split_sentences(generated.body)), 3, generated.body)
            self.assertFalse(any(term in generated.body for term in forbidden), generated.body)

    def test_block_posts_do_not_end_with_weak_room_copy(self) -> None:
        weak_phrases = [
            "確認したい",
            "確かめておきたい",
            "チェックしたい",
            "見ておきたい",
            "比較したい",
            "検討したい",
            "時間を作れ",
            "動きを試しやすく",
            "使い分けられます",
            "変えられます",
            "続けやすいです",
            "同じ道具でも",
        ]
        for product_type in ["wooden_blocks", "magnetic_blocks", "baby_walker_toy"]:
            generated = generate(product_type)
            self.assertEqual(generated.status, "ready", generated.quality_errors)
            self.assertFalse(any(phrase in generated.body for phrase in weak_phrases), generated.body)

    def test_weak_room_copy_is_rejected(self) -> None:
        generated = generate("magnetic_blocks")
        changed = replace(generated, body=generated.body + "対象年齢を確認したいです。")
        self.assertTrue(
            any("marketing_weak_cta" in error for error in validate_post(changed, changed.attributes)),
        )

    def test_magnetic_blocks_do_not_mix_conflicting_quantities(self) -> None:
        generated = generate("magnetic_blocks")

        self.assertEqual(generated.status, "ready", generated.quality_errors)
        self.assertIn("48ピース", generated.body)
        self.assertNotIn("50個", generated.body)

    def test_unconfirmed_quantity_in_body_is_rejected(self) -> None:
        generated = generate("magnetic_blocks")
        changed = replace(generated, body=generated.body + "50個でも遊べます。")

        self.assertTrue(
            any("unsupported_quantity_claim" in error for error in validate_post(changed, changed.attributes)),
        )

    def test_conflicting_block_counts_are_rejected(self) -> None:
        generated = generate("magnetic_blocks")
        attributes = replace(
            generated.attributes,
            confirmed_quantity_features=("48ピース", "50個"),
        )
        changed = replace(
            generated,
            body=generated.body + "50個のパーツとしても扱えます。",
            attributes=attributes,
        )

        self.assertTrue(
            any("quantity_conflict" in error for error in validate_post(changed, attributes)),
        )

    def test_kids_camera_copy_does_not_repeat_return_home_review_benefit(self) -> None:
        generated = generate("kids_camera")

        self.assertEqual(generated.status, "ready", generated.quality_errors)
        self.assertLessEqual(generated.body.count("帰宅後"), 1, generated.body)
        self.assertLessEqual(generated.body.count("見返"), 1, generated.body)

    def test_duplicate_benefit_repetition_is_rejected(self) -> None:
        generated = generate("kids_camera")
        changed = replace(
            generated,
            body=(
                "外出先で子どもが写真を撮れるキッズカメラです。"
                "撮った写真を帰宅後に親子で見返せます。"
                "帰宅後に写真を選ぶことで、外出の出来事を親子で振り返れます。"
            ),
        )

        self.assertTrue(
            any("duplicate_phrase" in error for error in validate_post(changed, changed.attributes)),
        )

    def test_normal_run_never_calls_openai(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "secret-value"}):
            with patch("urllib.request.urlopen") as urlopen:
                generated = generate("wipes")
        urlopen.assert_not_called()
        self.assertEqual(generated.generation_mode, "fallback")
        self.assertEqual(generated.source, "固定ルール")

    def test_openai_key_does_not_change_generation_mode(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "secret-value"}):
            generated = generate("kids_camera")
        self.assertEqual(generated.generation_mode, "fallback")
        self.assertNotIn("OpenAI", generated.source)

    def test_legacy_openai_generator_requires_explicit_double_opt_in(self) -> None:
        with patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "secret-value",
                "USE_OPENAI": "false",
                "GENERATION_MODE": "fallback",
            },
        ):
            generator = OpenAIPostGenerator(
                api_key="secret-value",
                session=object(),
            )
        self.assertFalse(generator.enabled)

    def test_workflow_does_not_receive_openai_settings(self) -> None:
        workflow = (
            Path(__file__).resolve().parents[1] / ".github" / "workflows" / "daily.yml"
        ).read_text(encoding="utf-8")
        self.assertNotIn("OPENAI_API_KEY", workflow)
        self.assertNotIn("OPENAI_MODEL", workflow)
        self.assertIn("GENERATION_MODE: fallback", workflow)

    def test_ring_toy_rejects_magnetic_block_copy(self) -> None:
        generated = generate("ring_toy")
        generated.body += "マグネットブロックでも遊べます。"
        self.assertIn(
            "別商品または商品タイプ違いの特徴が混入",
            validate_post(generated, generated.attributes),
        )

    def test_activity_cube_rejects_wooden_block_copy(self) -> None:
        generated = generate("activity_cube")
        generated.body += "木製つみきとして並べられます。"
        self.assertIn(
            "別商品または商品タイプ違いの特徴が混入",
            validate_post(generated, generated.attributes),
        )

    def test_consumable_context_mismatches_are_rejected(self) -> None:
        cases = [
            ("wipes", "お腹を空かせた"),
            ("diaper", "授乳"),
            ("formula", "おむつ替え"),
        ]
        for product_type, phrase in cases:
            generated = generate(product_type)
            generated.body += f"{phrase}場面にも使います。"
            self.assertIn(
                "別商品または商品タイプ違いの特徴が混入",
                validate_post(generated, generated.attributes),
            )

    def test_product_type_keyword_conflict_prevents_ready(self) -> None:
        generated = generate("diaper")
        conflicted_attributes = replace(
            generated.attributes,
            source_product_text="おくるみ スワドル 新生児",
        )

        errors = validate_post(generated, conflicted_attributes)

        self.assertTrue(any("product_type_keyword_conflict" in error for error in errors), errors)

    def test_new_product_types_generate_ready_without_diaper_context(self) -> None:
        for product_type in ["swaddle", "nursing_support", "baby_bedding", "baby_care", "baby_sleep", "soothing_plush"]:
            generated = generate(product_type)
            self.assertEqual(generated.status, "ready", generated.quality_errors)
            self.assertEqual(generated.analysis.product_type, product_type)
            self.assertNotIn("#紙おむつ", generated.hashtags)
            self.assertNotIn("紙おむつ", generated.body)

    def test_required_quality_cases_classify_and_generate_safely(self) -> None:
        cases = [
            ("おくるみ モロー反射 新生児 コットン", "swaddle"),
            ("スワドル 手が出せる おくるみ 新生児", "swaddle"),
            ("ガーゼケット ベビー 綿 洗える", "baby_sleep"),
            ("紙おむつ 新生児 テープタイプ 52枚", "diaper"),
            ("おむつ替えシート 防水 持ち運び", "diaper"),
            ("授乳クッション Cカーブ 新生児 カバー洗える", "nursing_support"),
            ("Cカーブクッション 授乳用品 新生児", "nursing_support"),
            ("ベビー保湿剤 ベビーローション 新生児 全身", "baby_care"),
            ("ベビー スリーパー ガーゼ 寝冷え対策", "baby_sleep"),
        ]
        for name, expected_type in cases:
            product = replace(
                product_for("wipes"),
                name=name,
                caption=name,
                catchcopy=name,
                url=f"https://example.com/quality/{expected_type}/{stable_index(name, 100000)}",
            )
            generated = FixedRulePostGenerator().generate(
                score_product(product, date(2026, 6, 16)),
                context=GenerationContext(),
            )
            self.assertEqual(generated.attributes.product_type, expected_type, name)
            self.assertEqual(generated.status, "ready", (name, generated.body, generated.quality_errors))
            external = f"{generated.title}{generated.body}{generated.recommendation_reason}"
            for banned in ["万能", "絶対", "確実に", "治る", "防げる", "誰でも", "これ一つで完璧"]:
                self.assertNotIn(banned, external)
            if expected_type == "swaddle":
                self.assertNotIn("おむつ替え", external)
            if expected_type == "nursing_support":
                self.assertIn("授乳", external)
            if expected_type == "baby_care":
                self.assertIn("ケア", external)
            if expected_type == "baby_sleep":
                self.assertTrue(any(term in external for term in ["夜", "寝冷え", "布もの", "灯り"]))

    def test_ready_posts_do_not_use_intention_phrases(self) -> None:
        for product_type in HASHTAGS:
            generated = generate(product_type)
            if generated.status != "ready":
                continue
            combined = f"{generated.title}{generated.body}"
            self.assertFalse(
                any(phrase in combined for phrase in BANNED_INTENTION_PHRASES),
                (product_type, generated.body),
            )
    def test_previous_action_run_fixture_generates_at_least_three_ready_posts(self) -> None:
        fixture_path = Path(__file__).parent / "fixtures" / "room_run_27583525520_products.json"
        items = json.loads(fixture_path.read_text(encoding="utf-8"))
        context = GenerationContext()
        posts = []
        for item in items:
            product = Product(**item)
            posts.append(
                FixedRulePostGenerator().generate(
                    score_product(product, date(2026, 6, 16)),
                    context=context,
                )
            )

        ready_posts = [post for post in posts if post.status == "ready"]
        self.assertGreaterEqual(len(ready_posts), 3, [post.quality_errors for post in posts])
        for post in posts:
            self.assertEqual(len(post.hashtags), 5, (post.title, post.hashtags, post.quality_errors))
            self.assertNotIn("授乳クッションの授乳クッション", post.body)
            self.assertNotIn("授乳クッションの授乳クッション", post.recommendation_reason)
            self.assertFalse(
                any(term in post.body for term in ["必ず寝る", "泣き止む", "安眠できる", "背中スイッチを防ぐ"]),
                post.body,
            )
        self.assertEqual(posts[0].attributes.product_type, "nursing_support")
        self.assertEqual(posts[2].attributes.product_type, "soothing_plush")
        self.assertEqual(posts[4].attributes.product_type, "soothing_plush")
        external_banned = [
            "赤ちゃんを置く",
            "寝かせる場所",
            "枕元",
            "添い寝",
            "ベッド内",
            "背中スイッチ",
            "安眠",
            "夜泣き改善",
        ]
        for post in posts:
            external = f"{post.title}{post.body}{post.recommendation_reason}{' '.join(post.hashtags)}"
            self.assertFalse(any(term in external for term in external_banned), external)
            self.assertLess(confirmation_repeat_count(post.body), 3, post.body)

    def test_soothing_plush_does_not_use_planetarium_tag_without_feature(self) -> None:
        product = replace(
            product_for("soothing_plush"),
            name="寝かしつけぬいぐるみ メロディー 音楽",
            caption="寝かしつけぬいぐるみ メロディー 音楽",
            catchcopy="寝かしつけぬいぐるみ メロディー 音楽",
            url="https://example.com/soothing/no-projector",
        )
        generated = FixedRulePostGenerator().generate(
            score_product(product, date(2026, 6, 16)),
            context=GenerationContext(),
        )

        self.assertEqual(generated.status, "ready", generated.quality_errors)
        self.assertNotIn("#プラネタリウム", generated.hashtags)

    def test_specific_review_reason_codes_are_exposed(self) -> None:
        generated = generate("soothing_plush")
        changed = replace(generated, hashtags=generated.hashtags[:3])
        errors = validate_post(changed, generated.attributes)
        self.assertTrue(any(error.startswith("hashtag_count_below_5") for error in errors), errors)

        repeated_label = f"{generated.attributes.short_product_label}の{generated.attributes.short_product_label}"
        changed = replace(generated, body=generated.body + f"{repeated_label}です。")
        errors = validate_post(changed, generated.attributes)
        self.assertTrue(any(error.startswith("duplicate_short_label") for error in errors), errors)

    def test_sleep_safety_phrases_are_hard_errors(self) -> None:
        nursing = generate("nursing_support")
        changed = replace(nursing, body=nursing.body + "赤ちゃんを一時的に寝かせる場所として使えます。")
        errors = validate_post(changed, nursing.attributes)
        self.assertTrue(any("睡眠場所" in error for error in errors), errors)

        changed = replace(nursing, body=nursing.body + "背中スイッチ対策にも触れています。")
        errors = validate_post(changed, nursing.attributes)
        self.assertTrue(any("back_switch" in error for error in errors), errors)

        plush = generate("soothing_plush")
        changed = replace(plush, title="枕元に置きたいぬいぐるみ")
        errors = validate_post(changed, plush.attributes)
        self.assertTrue(any("枕元" in error for error in errors), errors)

    def test_repeated_confirmation_and_semantic_benefits_are_rejected(self) -> None:
        generated = generate("soothing_plush")
        changed = replace(
            generated,
            body="対象年齢を確認したいです。音量を確認したいです。電源方式を見ておきたいです。",
        )
        errors = validate_post(changed, generated.attributes)
        self.assertTrue(any("確認系表現" in error for error in errors), errors)

        changed = replace(
            generated,
            body="光や音を整えたいです。光や音を寝室に合わせたいです。対象年齢を手がかりに選びたいです。",
        )
        errors = validate_post(changed, generated.attributes)
        self.assertTrue(any("同一ベネフィット" in error for error in errors), errors)

    def test_title_and_body_type_mismatch_is_rejected(self) -> None:
        generated = generate("ring_toy")
        generated.structure_pattern = "activity_cube_01"
        errors = validate_post(generated, generated.attributes)
        self.assertIn("pattern_idが商品タイプと不一致", errors)

    def test_duplicate_title_regenerates(self) -> None:
        product = product_for("wipes")
        patterns = PATTERNS["wipes"]
        start = stable_index(product.url, len(patterns))
        context = GenerationContext(used_titles={patterns[start].title})
        generated = FixedRulePostGenerator().generate(
            score_product(product, date(2026, 6, 16)),
            context=context,
        )
        self.assertGreaterEqual(generated.rewrite_count, 1)
        self.assertEqual(generated.status, "ready")

    def test_exact_body_and_opening_duplicates_regenerate(self) -> None:
        first = generate("formula")
        context = GenerationContext(
            historical_bodies=[first.body],
            historical_titles={first.title},
        )
        second = FixedRulePostGenerator().generate(
            score_product(product_for("formula"), date(2026, 6, 16)),
            context=context,
        )
        self.assertGreaterEqual(second.rewrite_count, 1)
        self.assertNotEqual(first.body, second.body)

    def test_unconfirmed_sound_and_name_option_are_rejected(self) -> None:
        generated = generate("wooden_blocks")
        for phrase, feature in [("音が鳴る", "sound"), ("名入れ対応", "name_option")]:
            changed = replace(generated, body=generated.body + f"{phrase}商品です。")
            errors = validate_post(changed, changed.attributes)
            self.assertTrue(
                any(feature in error for error in errors),
                errors,
            )

    def test_product_name_noise_is_removed_and_residual_is_rejected(self) -> None:
        noisy = product_for("wipes")
        noisy = replace(
            noisy,
            name="【楽天1位】★ おしりふき 80枚 20個 送料無料 テストショップ",
        )
        attributes = extract_attributes(noisy)
        self.assertNotIn("楽天1位", attributes.normalized_product_name)
        self.assertNotIn("送料無料", attributes.normalized_product_name)
        generated = generate("wipes")
        generated.body += "楽天1位の商品です。"
        self.assertIn("禁止表現を使用", validate_post(generated, generated.attributes))

    def test_invalid_sentence_start_is_rejected(self) -> None:
        generated = generate("wipes")
        for prefix in ["、", "!", "＜"]:
            changed = replace(generated, body=prefix + generated.body)
            self.assertIn(
                "文頭が読点または記号",
                validate_post(changed, changed.attributes),
            )

    def test_maximum_regeneration_marks_needs_review(self) -> None:
        with patch(
            "fixed_rule_generator.validate_post",
            return_value=["同一タイトル"],
        ):
            generated = generate("wipes")
        self.assertEqual(generated.status, "needs_review")
        self.assertEqual(generated.rewrite_count, 4)
        self.assertIn("最大5回の再生成で品質条件を満たせない", generated.quality_errors)

    def test_mismatched_hashtags_are_rejected(self) -> None:
        generated = generate("ring_toy")
        generated.hashtags = HASHTAGS["kids_camera"]
        errors = validate_post(generated, generated.attributes)
        self.assertTrue(
            any(
                "ハッシュタグの根拠がない" in error
                or "確認済み属性から生成したハッシュタグと不一致" in error
                for error in errors
            ),
            errors,
        )

    def test_more_than_three_checkpoints_are_rejected(self) -> None:
        generated = generate("wipes")
        attributes = replace(
            generated.attributes,
            purchase_checkpoints=("枚数", "個数", "価格", "収納場所"),
        )
        self.assertIn(
            "購入前確認点が4つ以上",
            validate_post(generated, attributes),
        )

    def test_all_ready_posts_have_five_grounded_hashtags(self) -> None:
        for product_type in HASHTAGS:
            generated = generate(product_type)
            self.assertEqual(generated.status, "ready", generated.quality_errors)
            self.assertEqual(len(generated.hashtags), 5)
            self.assertEqual(generated.hashtags[-1], "#とらパパ厳選")
            self.assertEqual(generated.tag_evidence_result, "OK")
            self.assertLessEqual(generated.quality.score, 95)

    def test_hand_wipes_keep_product_specific_label_and_hashtags(self) -> None:
        product = replace(
            product_for("wipes"),
            name="手口ふき 厚手 60枚 12個",
            caption="手口ふき 厚手 水分量 60枚 12個 食後 外出",
            catchcopy="手口ふき 厚手 水分量 60枚 12個 食後 外出",
            url="https://example.com/wipes/hand",
        )
        generated = FixedRulePostGenerator().generate(
            score_product(product, date(2026, 6, 16)),
            context=GenerationContext(),
        )

        self.assertEqual(generated.status, "ready", generated.quality_errors)
        self.assertEqual(generated.attributes.short_product_label, "手口ふき")
        self.assertIn("手口ふき", generated.body)
        self.assertNotIn("おしりふき", generated.body)
        self.assertEqual(generated.hashtags[0], "#手口ふき")

    def test_unsupported_title_scene_is_rejected(self) -> None:
        generated = generate("activity_cube")
        changed = replace(
            generated,
            title="雨の日に遊びを切り替える",
            body=generated.body.replace("雨の日", "家で"),
        )
        self.assertIn(
            "タイトルの使用場面「雨の日」が本文にない",
            validate_post(changed, changed.attributes),
        )

    def test_unsupported_birthday_title_and_tag_are_rejected(self) -> None:
        generated = generate("kids_camera")
        changed = replace(
            generated,
            title="誕生日に選ぶキッズカメラ",
            hashtags=[*generated.hashtags[:-2], "#誕生日プレゼント", generated.hashtags[-1]],
        )
        errors = validate_post(changed, changed.attributes)
        self.assertIn("タイトルの誕生日訴求に商品情報の根拠がない", errors)
        self.assertIn("ハッシュタグの根拠がない: #誕生日プレゼント", errors)

    def test_unconfirmed_wood_and_night_hashtags_are_rejected(self) -> None:
        ring = generate("ring_toy")
        ring_changed = replace(
            ring,
            hashtags=[*ring.hashtags[:-2], "#木のおもちゃ", ring.hashtags[-1]],
        )
        self.assertIn(
            "ハッシュタグの根拠がない: #木のおもちゃ",
            validate_post(ring_changed, ring_changed.attributes),
        )

        diaper = generate("diaper")
        diaper_changed = replace(
            diaper,
            title="紙おむつの残量を整えたい",
            body=diaper.body.replace("夜", "毎日"),
            hashtags=[*diaper.hashtags[:-2], "#夜のおむつ替え", diaper.hashtags[-1]],
        )
        self.assertIn(
            "ハッシュタグの根拠がない: #夜のおむつ替え",
            validate_post(diaper_changed, diaper_changed.attributes),
        )

    def test_recommendation_reason_type_mismatch_and_truncation_are_rejected(self) -> None:
        wipes = generate("wipes")
        wrong_type = replace(
            wipes,
            recommendation_reason="外出時の荷物整理に合い、ベビーカーバッグへ収納できる一方、取り付け方法は確認したい。",
        )
        self.assertIn(
            "おすすめ理由の商品タイプが本文と一致しない",
            validate_post(wrong_type, wrong_type.attributes),
        )

        diaper = generate("diaper")
        truncated = replace(
            diaper,
            recommendation_reason="紙おむつサイズ5を管理しやすい一方、枚数は確認したい。",
        )
        self.assertIn(
            "おすすめ理由の商品名が途中で切れている",
            validate_post(truncated, truncated.attributes),
        )

    def test_product_name_only_title_is_rejected(self) -> None:
        generated = generate("wooden_blocks")
        changed = replace(generated, title=generated.attributes.short_product_label)
        self.assertIn(
            "タイトルが商品名の言い換えだけ",
            validate_post(changed, changed.attributes),
        )

    def test_structure_similarity_detects_same_sentence_skeleton(self) -> None:
        left = (
            "家遊びの内容が同じになりがちです。"
            "積み木なら、音を楽しめますし、配置すると遊び方を変えられます。"
            "親子で形の違いを見つける時間にもつながります。"
            "対象年齢とサイズを確認して選びたいです。"
        )
        right = (
            "外出写真の内容が同じになりがちです。"
            "カメラなら、撮影を楽しめますし、転送すると見返し方を変えられます。"
            "親子で写真の違いを見つける時間にもつながります。"
            "対象年齢と充電方式を確認して選びたいです。"
        )
        self.assertGreaterEqual(syntax_similarity(left, right), 0.75)

    def test_fourth_identical_ending_is_rejected(self) -> None:
        generated = generate("formula")
        body = "".join(split_sentences(generated.body)[:-1]) + "容量を確認して選びたいです。"
        generated = replace(generated, body=body)
        ending = ending_family(body)
        self.assertTrue(ending)
        context = GenerationContext(ending_counts={ending: 3})
        self.assertTrue(
            any("締め語尾" in error for error in validate_post(generated, generated.attributes, context)),
        )

    def test_same_run_mixes_three_and_four_sentence_posts(self) -> None:
        product_types = [
            "wipes",
            "diaper",
            "formula",
            "sound_blocks",
            "wooden_blocks",
            "magnetic_blocks",
            "activity_cube",
            "ring_toy",
            "kids_camera",
            "sleep_light",
            "stroller_storage",
            "wipes",
            "diaper",
            "kids_camera",
            "stroller_storage",
        ]
        context = GenerationContext()
        posts = []
        for index, product_type in enumerate(product_types):
            item = replace(
                product_for(product_type, suffix=str(index)),
                url=f"https://example.com/mixed/{index}",
            )
            posts.append(
                FixedRulePostGenerator().generate(
                    score_product(item, date(2026, 6, 16)),
                    context=context,
                )
            )

        three_count = sum(post.sentence_form == "3文型" for post in posts)
        four_count = sum(post.sentence_form == "4文型" for post in posts)
        self.assertTrue(all(post.status == "ready" for post in posts), [post.quality_errors for post in posts])
        self.assertGreaterEqual(three_count, 6)
        self.assertGreaterEqual(four_count, 6)
        self.assertLessEqual(three_count, 9)
        self.assertLessEqual(four_count, 9)
        self.assertTrue(all(post.structure_similarity < 0.75 for post in posts))

    def test_generated_posts_do_not_use_requested_awkward_phrases(self) -> None:
        awkward_phrases = [
            "水分量の記載がある",
            "声をかけやすい内容",
            "場面へ遊びを広げる",
            "毎晩の準備",
            "コードレスの機器",
            "一度見せ合う時間",
            "子どもが扱える大きさか考えたい",
        ]
        for product_type in HASHTAGS:
            generated = generate(product_type)
            self.assertFalse(
                any(phrase in generated.body for phrase in awkward_phrases),
                generated.body,
            )

    def test_each_type_has_at_least_eight_coherent_patterns(self) -> None:
        for product_type, patterns in PATTERNS.items():
            self.assertGreaterEqual(len(patterns), 8, product_type)
            self.assertEqual(
                len({pattern.pattern_id for pattern in patterns}),
                len(patterns),
            )


if __name__ == "__main__":
    unittest.main()
