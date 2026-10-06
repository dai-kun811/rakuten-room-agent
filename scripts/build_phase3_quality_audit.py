from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from dry_run_fixed_generation import PRODUCTS, product
from fixed_rule_generator import FixedRulePostGenerator, GenerationContext
from room_manifest_v2 import build_manifest_v2
from scoring import score_product


SAMPLE_SIZE = 24
MIN_AUDIT_POSTS = 20
MAX_AUDIT_POSTS = 30
AUDIT_START_DATE = date(2026, 10, 6)
HEAD_SHA = "abcdef1234567890abcdef1234567890abcdef12"


EXTENDED_PRODUCTS = [
    product("swaddle", "綿100％ スワドル 上下ファスナー 新生児", "綿100％ スワドル 新生児 上下ファスナー モロー反射 洗濯", 16),
    product("nursing_support", "授乳クッション C字型 カバー洗濯対応", "授乳クッション C字型 カバー 洗える 本体サイズ 授乳", 17),
    product("baby_bedding", "抱っこ布団 ダブルガーゼ 洗える", "抱っこ布団 ダブルガーゼ コットン 洗える 本体サイズ ねんね", 18),
    product("baby_care", "ベビーローション ポンプ式 300ml 全身用", "ベビーローション 保湿 ポンプ式 300ml 顔 全身 成分", 19),
    product("baby_sleep", "6重ガーゼ スリーパー コットン", "6重ガーゼ スリーパー コットン 洗濯 春 夏 秋 冬", 20),
    product("soothing_plush", "寝かしつけぬいぐるみ 投影 音楽 タイマー", "ぬいぐるみ プラネタリウム 投影 音楽 タイマー ライト", 21),
    product("baby_walker_toy", "木製 手押し車 つかまり立ち おもちゃ", "木製 手押し車 つかまり立ち 対象年齢 1歳 本体サイズ", 22),
    product("diaper", "おむつストッカー 折りたたみ 仕切り付き", "おむつストッカー 折りたたみ 仕切り 容量 本体サイズ 収納", 23),
    product("formula", "液体ミルク 200ml×6本 常温保存", "液体ミルク 200ml 6本 常温保存 対象月齢 賞味期限", 24),
]


def main() -> int:
    products = [*PRODUCTS, *EXTENDED_PRODUCTS][:SAMPLE_SIZE]
    output = ROOT / "reports" / "phase3-manifest-quality-audit"
    manifests_dir = output / "manifests"
    manifests_dir.mkdir(parents=True, exist_ok=True)

    manifests: list[dict] = []
    candidates: list[dict] = []
    blocked: list[dict] = []
    recent_history: list[dict[str, str]] = []
    for batch_index in range(0, len(products), 3):
        run_date = AUDIT_START_DATE + timedelta(days=batch_index // 3)
        generated_at = datetime.combine(run_date, time(7, 0), tzinfo=timezone(timedelta(hours=9)))
        context = GenerationContext.from_history(recent_history)
        items = []
        for slot, source_product in zip(("morning", "noon", "evening"), products[batch_index : batch_index + 3]):
            scored = score_product(source_product, run_date)
            generated = FixedRulePostGenerator().generate(scored, context=context)
            attributes = generated.attributes
            items.append(
                {
                    "product_name": source_product.name,
                    "product_url": source_product.url,
                    "short_product_label": attributes.short_product_label if attributes else "",
                    "product_type": generated.analysis.product_type,
                    "title": generated.title,
                    "body": generated.body,
                    "hashtags": generated.hashtags,
                    "status": generated.status,
                    "post_slot": slot,
                    "review_reasons": generated.quality_errors,
                    "recommendation_reason": generated.recommendation_reason,
                    "confirmed_features": list(attributes.confirmed_features) if attributes else [],
                    "confirmed_use_cases": list(attributes.confirmed_use_cases) if attributes else [],
                    "purchase_checkpoints": list(attributes.purchase_checkpoints) if attributes else [],
                    "source_evidence": {
                        "category": source_product.category,
                        "caption": source_product.caption,
                        "catchcopy": source_product.catchcopy,
                        "shop_name": source_product.shop_name,
                        "search_keyword": source_product.search_keyword,
                    },
                    "quality": {
                        "score": generated.quality.score,
                        "errors": generated.quality_errors,
                        "title_evidence_result": generated.title_evidence_result,
                        "tag_evidence_result": generated.tag_evidence_result,
                        "recommendation_reason_result": generated.recommendation_reason_result,
                        "structure_similarity": generated.structure_similarity,
                    },
                }
            )
        report = {
            "items": items,
            "missing_post_slots": [
                item["post_slot"] for item in items if item["status"] != "ready"
            ],
        }
        manifest = build_manifest_v2(
            report,
            actions_run_id=f"phase3-quality-{batch_index // 3 + 1}",
            head_sha=HEAD_SHA,
            revision=batch_index // 3 + 1,
            generated_at=generated_at,
        )
        manifest_path = manifests_dir / f"{run_date.isoformat()}.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        manifests.append(manifest)
        for slot, value in manifest["slots"].items():
            if value["status"] == "ready":
                candidates.append({"date": run_date.isoformat(), "slot": slot, **value["candidate"]})
                recent_history.append(
                    {
                        "ステータス": "ready",
                        "タイトル": value["candidate"]["title"],
                        "投稿文": value["candidate"]["body"],
                    }
                )
            else:
                source_item = next(item for item in items if item["post_slot"] == slot)
                blocked.append(
                    {
                        "date": run_date.isoformat(),
                        "slot": slot,
                        "reason": value["reason"],
                        "product_name": source_item["product_name"],
                        "title": source_item["title"],
                        "body": source_item["body"],
                        "hashtags": source_item["hashtags"],
                        "errors": source_item["review_reasons"],
                    }
                )

    audit_path = output / "quality-audit-sample.md"
    audit_path.write_text(render_audit(candidates, manifests, blocked), encoding="utf-8")
    print(audit_path)
    print(f"manifests={len(manifests)} candidates={len(candidates)}")
    return 0 if MIN_AUDIT_POSTS <= len(candidates) <= MAX_AUDIT_POSTS else 1


def render_audit(candidates: list[dict], manifests: list[dict], blocked: list[dict]) -> str:
    similarities = []
    for left_index, left in enumerate(candidates):
        for right in candidates[left_index + 1 :]:
            ratio = SequenceMatcher(None, left["body"], right["body"]).ratio()
            similarities.append((ratio, left["content_hash"][:8], right["content_hash"][:8]))
    similarities.sort(reverse=True)
    short_labels = Counter(item["short_product_label"] for item in candidates)
    lines = [
        "# Phase 3 manifest v2 投稿文品質監査サンプル",
        "",
        f"- manifest数: {len(manifests)}",
        f"- 抽出投稿数: {len(candidates)}",
        "- 本番投稿: 実施しない",
        f"- ready候補: {len(candidates)}",
        f"- blocked候補: {len(blocked)}",
        f"- 最大本文類似度: {similarities[0][0]:.3f}" if similarities else "- 最大本文類似度: 0.000",
        f"- 短縮名重複: {dict((key, value) for key, value in short_labels.items() if value > 1)}",
        "",
    ]
    if blocked:
        lines.extend(["## manifestで遮断された候補", ""])
        lines.extend(
            line
            for item in blocked
            for line in (
                f"### {item['date']} {item['slot']} — {item['product_name']}",
                "",
                f"- reason: {item['reason']}",
                f"- title: {item['title']}",
                f"- body: {item['body']}",
                f"- hashtags: {' '.join(item['hashtags'])}",
                f"- errors: {item['errors']}",
                "",
            )
        )
    if similarities:
        lines.extend(["## 本文類似度 上位5組", ""])
        lines.extend(
            f"- {left} / {right}: {ratio:.3f}"
            for ratio, left, right in similarities[:5]
        )
        lines.append("")
    for index, item in enumerate(candidates, start=1):
        evidence = item["source_evidence"]
        quality = item["quality"]
        lines.extend(
            [
                f"## {index}. {item['date']} {item['slot']} — {item['short_product_label']}",
                "",
                f"- 元商品名: {item['product_name']}",
                f"- 商品説明: {evidence['caption']}",
                f"- キャッチコピー: {evidence['catchcopy']}",
                f"- 商品タイプ: `{item['product_type']}`",
                f"- 確認済み特徴: `{item['confirmed_features']}`",
                f"- 購入前確認点: `{item['purchase_checkpoints']}`",
                f"- タイトル: {item['title']}",
                f"- 本文: {item['body']}",
                f"- ハッシュタグ: {' '.join(item['hashtags'])}",
                f"- 品質スコア: `{quality['score']}`",
                f"- 根拠検査: title={quality['title_evidence_result']} / tag={quality['tag_evidence_result']} / reason={quality['recommendation_reason_result']}",
                f"- content_hash: `{item['content_hash']}`",
                "",
            ]
        )
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
