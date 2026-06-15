from __future__ import annotations

import os
import sys
import unittest
from dataclasses import replace
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fixed_rule_generator import (
    FixedRulePostGenerator,
    GenerationContext,
    HASHTAGS,
    PATTERNS,
    ProductAttributes,
    classify_product_type,
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
            ("紙おむつ 新生児 テープタイプ", "diaper"),
            ("ベビー用おむつ パンツタイプ", "diaper"),
        ]
        for name, expected in cases:
            product = replace(product_for("wipes"), name=name, caption=name, catchcopy=name)
            self.assertEqual(classify_product_type(product), expected, name)

    def test_requested_non_diaper_products_do_not_become_diaper(self) -> None:
        cases = [
            "おくるみ スワドル 新生児",
            "おむつポーチ 大容量",
            "おむつ替えシート 防水",
            "授乳クッション",
            "抱っこ布団",
        ]
        for name in cases:
            product = replace(product_for("wipes"), name=name, caption=name, catchcopy=name)
            self.assertNotEqual(classify_product_type(product), "diaper", name)

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
        for product_type in ["swaddle", "nursing_support", "baby_bedding"]:
            generated = generate(product_type)
            self.assertEqual(generated.status, "ready", generated.quality_errors)
            self.assertEqual(generated.analysis.product_type, product_type)
            self.assertNotIn("#紙おむつ", generated.hashtags)
            self.assertNotIn("紙おむつ", generated.body)

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
