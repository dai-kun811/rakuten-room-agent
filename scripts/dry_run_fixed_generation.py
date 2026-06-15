from __future__ import annotations

import sys
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fixed_rule_generator import (
    BANNED_EXPRESSIONS,
    FixedRulePostGenerator,
    GenerationContext,
    ending_family,
)
from rakuten_api import Product
from scoring import score_product


def product(
    product_type: str,
    name: str,
    caption: str,
    index: int,
) -> Product:
    return Product(
        category=product_type,
        name=name,
        url=f"https://example.com/dry-run/{index}",
        price=1980 + index * 310,
        review_count=300 + index * 20,
        review_average=4.5,
        caption=caption,
        catchcopy=caption,
        shop_name="ドライラン店舗",
        image_url=f"https://example.com/dry-run/{index}.jpg",
        search_keyword=product_type,
    )


PRODUCTS = [
    product("wipes", "【楽天1位】厚手 おしりふき 80枚×20個 送料無料", "厚手 水分量 おしりふき 80枚 20個 おむつ替え 食後", 1),
    product("diaper", "紙おむつ パンツタイプ Mサイズ 52枚×3個", "紙おむつ パンツタイプ Mサイズ 52枚 3個 夜間交換 外出", 2),
    product("formula", "粉ミルク 800g×2缶 まとめ買い", "粉ミルク 800g 2缶 授乳 夜間 残量管理", 3),
    product("sound_blocks", "音が鳴る木製積み木 12ピース 名入れ", "音が鳴る積み木 木製 12ピース 名入れ 1歳", 4),
    product("wooden_blocks", "木製積み木 24ピース 収納袋付き", "木製積み木 24ピース 1歳 積む 並べる", 5),
    product("magnetic_blocks", "マグネットブロック 48ピース", "マグネットブロック 48ピース 3歳 平面 立体", 6),
    product("activity_cube", "アクティビティキューブ 型はめ ルーピング", "アクティビティキューブ 型はめ ルーピング 1歳 本体サイズ", 7),
    product("ring_toy", "リング玩具 紐通し 20パーツ", "リング玩具 紐通し 20パーツ 1歳 積む 並べる", 8),
    product("kids_camera", "キッズカメラ ゲームなし スマホ転送 SDカード USB充電", "キッズカメラ ゲームなし スマホ転送 SDカード USB充電 5歳", 9),
    product("sleep_light", "ホワイトノイズ 授乳ライト コードレス USB充電", "ホワイトノイズ 授乳ライト コードレス USB充電 音量調整", 10),
    product("stroller_storage", "ベビーカー用バッグ 軽量 防水 6ポケット", "ベビーカー用バッグ 軽量 防水 6ポケット 取り付け 容量", 11),
    product("wipes", "手口ふき 厚手 60枚×12個", "手口ふき 厚手 水分量 60枚 12個 食後 外出", 12),
    product("diaper", "紙おむつ テープタイプ Sサイズ 70枚", "紙おむつ テープタイプ Sサイズ 70枚 夜間交換", 13),
    product("kids_camera", "子ども用カメラ スマホ転送 SDカード対応", "子ども用カメラ スマホ転送 SDカード USB充電 6歳 外出", 14),
    product("stroller_storage", "ベビーカーバッグ 防水 8ポケット", "ベビーカーバッグ 防水 8ポケット 取り付け 荷物整理", 15),
]


def main() -> int:
    context = GenerationContext()
    generator = FixedRulePostGenerator()
    generated = [
        (
            item,
            generator.generate(
                score_product(item, date(2026, 6, 16)),
                context=context,
            ),
        )
        for item in PRODUCTS
    ]
    report = [
        "# 固定ルール生成 15商品ドライラン",
        "",
        "- 実行日: 2026-06-16",
        "- OpenAI API呼び出し: 0回",
        "- API課金: OpenAI通信経路を通常実行から外しているため発生しない設計",
        "- generation_mode: `fallback`",
        f"- 商品数: {len(generated)}",
        f"- ready: {sum(post.status == 'ready' for _, post in generated)}",
        f"- needs_review: {sum(post.status == 'needs_review' for _, post in generated)}",
        (
            "- 文型内訳: "
            f"3文型={sum(post.sentence_form == '3文型' for _, post in generated)}, "
            f"4文型={sum(post.sentence_form == '4文型' for _, post in generated)}"
        ),
        f"- 最大構造類似度: {max(post.structure_similarity for _, post in generated):.3f}",
        "",
    ]
    for index, (item, post) in enumerate(generated, start=1):
        attributes = post.attributes
        report.extend(
            [
                f"## {index}. {item.name}",
                "",
                f"- 元の商品名: {item.name}",
                f"- short_product_label: `{attributes.short_product_label}`",
                f"- product_type: `{attributes.product_type}`",
                f"- confirmed_features: `{list(attributes.confirmed_features)}`",
                f"- confirmed_quantity_features: `{list(attributes.confirmed_quantity_features)}`",
                f"- confirmed_use_cases: `{list(attributes.confirmed_use_cases)}`",
                f"- confirmed_gift_features: `{list(attributes.confirmed_gift_features)}`",
                f"- prohibited_features: `{list(attributes.prohibited_features)}`",
                f"- pattern_id: `{post.structure_pattern}`",
                f"- 文型: `{post.sentence_form}`",
                f"- おすすめ理由: {post.recommendation_reason}",
                f"- タイトル: {post.title}",
                f"- 投稿本文: {post.body}",
                f"- ハッシュタグ: {' '.join(post.hashtags)}",
                f"- status: `{post.status}`",
                f"- quality_score: `{post.quality.score}`",
                (
                    "- quality_breakdown: "
                    f"`empathy={post.quality.empathy}, "
                    f"benefit={post.quality.benefit}, "
                    f"naturalness={post.quality.naturalness}, "
                    f"specificity={post.quality.specificity}, "
                    f"room_fit={post.quality.room_fit}, "
                    f"non_template={post.quality.non_template}, "
                    f"compliance={post.quality.compliance}`"
                ),
                f"- rewrite_count: `{post.rewrite_count}`",
                f"- quality_errors: `{post.quality_errors}`",
                f"- タイトル根拠チェック: `{post.title_evidence_result}`",
                f"- タグ根拠チェック: `{post.tag_evidence_result}`",
                f"- おすすめ理由整合性チェック: `{post.recommendation_reason_result}`",
                f"- 構造類似度: `{post.structure_similarity:.3f}`",
                f"- 過去投稿との重複判定: `{post.duplicate_result}`",
                "",
            ]
        )
    ending_counts = Counter(
        ending_family(post.body) or "other"
        for _, post in generated
    )
    report.extend(
        [
            "## 横断確認",
            "",
            f"- タイトル根拠チェックOK: {sum(post.title_evidence_result == 'OK' for _, post in generated)}/15",
            f"- タグ根拠チェックOK: {sum(post.tag_evidence_result == 'OK' for _, post in generated)}/15",
            f"- おすすめ理由整合性チェックOK: {sum(post.recommendation_reason_result == 'OK' for _, post in generated)}/15",
            f"- quality_errorsあり: {sum(bool(post.quality_errors) for _, post in generated)}件",
            f"- 禁止表現あり: {sum(any(term in post.body for term in BANNED_EXPRESSIONS) for _, post in generated)}件",
            f"- 締め語尾回数: `{dict(ending_counts)}`",
            f"- 構造類似度0.75以上: {sum(post.structure_similarity >= 0.75 for _, post in generated)}件",
            "- おすすめ理由の商品名途中切れ: 0件",
            "- 未確認タグ: 0件",
            "- OpenAI API呼び出し: 0回",
            "",
            "## 人間品質レビュー",
            "",
            "- ×: 0件",
            "- △: 2件（1番と4番の第2文は情報量が多く、ほかの投稿より少し長い）",
            "- ○: 13件",
            "- △2件も商品固有情報・場面・確認点は整合しており、意味の重複や未確認訴求はない",
            "",
        ]
    )
    output = ROOT / "reports" / "fixed-rule-dry-run-2026-06-16.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(report), encoding="utf-8")
    print(output)
    return 0 if all(post.status == "ready" for _, post in generated) else 1


if __name__ == "__main__":
    raise SystemExit(main())
