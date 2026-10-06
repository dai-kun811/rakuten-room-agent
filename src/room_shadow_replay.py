from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Iterable, Mapping

from fixed_rule_generator import FixedRulePostGenerator, GenerationContext
from generation_report import GenerationReportItem, write_generation_reports
from main import POST_SLOTS, diversify_products, generate_until_ready
from rakuten_api import Product
from room_operation_contract import JST
from scoring import score_all_products
from sheets import scored_product_to_row


def products_from_generation_report(report: Mapping[str, Any]) -> list[Product]:
    """Rebuild real product snapshots without reusing the old generated copy.

    Historical reports do not contain Rakuten credentials or every API field.
    Only product-owned fields retained in the artifact are used; generated
    benefits and old post copy are deliberately excluded from source text.
    """
    products: list[Product] = []
    seen_urls: set[str] = set()
    for item in report.get("items", []):
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("product_name", "")).strip()
        url = str(item.get("product_url", "")).strip().split("?", 1)[0].rstrip("/")
        if not name or not url or url in seen_urls:
            continue
        row = item.get("sheet_row")
        row = row if isinstance(row, Mapping) else {}
        products.append(
            Product(
                category=str(row.get("カテゴリ", "")).strip(),
                name=name,
                url=url,
                price=_integer(row.get("価格")),
                review_count=_integer(row.get("レビュー件数")),
                review_average=_float(row.get("評価")),
                caption="",
                catchcopy="",
                shop_name=str(row.get("ショップ名", "")).strip(),
                image_url=str(row.get("画像URL", "")).strip(),
                search_keyword=str(row.get("検索キーワード", "")).strip(),
            )
        )
        seen_urls.add(url)
    return products


def replay_generation_report(
    source_report: Mapping[str, Any],
    *,
    routine_date: date,
    source_actions_run_id: str | int,
    output_dir: Path,
    head_sha: str = "0000000000000000000000000000000000000000",
    prior_history: Iterable[dict[str, str]] = (),
) -> Mapping[str, Any]:
    history = list(prior_history)
    products = products_from_generation_report(source_report)
    scored = score_all_products(products, routine_date)
    candidates = diversify_products(scored, history, limit=len(scored), today=routine_date)
    results = generate_until_ready(
        candidates,
        generator=FixedRulePostGenerator(),
        context=GenerationContext.from_history(history),
        target_ready=len(POST_SLOTS),
    )
    ready_index = 0
    report_items: list[GenerationReportItem] = []
    run_id = f"shadow-replay-{source_actions_run_id}"
    for scored_product, generated in results:
        post_slot = ""
        if generated.status == "ready" and ready_index < len(POST_SLOTS):
            post_slot = POST_SLOTS[ready_index]
            ready_index += 1
        row = scored_product_to_row(
            scored_product,
            generated,
            today=routine_date,
            run_id=run_id,
        )
        report_items.append(
            GenerationReportItem(
                scored=scored_product,
                generated=generated,
                row=row,
                write_sheet="",
                duplicate_result=generated.duplicate_result,
                post_slot=post_slot,
            )
        )

    executed_at = datetime.combine(routine_date, time(7, 30), tzinfo=JST)
    paths = write_generation_reports(
        output_dir,
        run_id=run_id,
        executed_at=executed_at,
        generation_mode="fallback-shadow-replay",
        output_sheet_name="SHADOW_ONLY_NO_WRITE",
        review_sheet_name="SHADOW_ONLY_NO_WRITE",
        fetch_report=None,
        items=report_items,
        required_post_slots=POST_SLOTS,
        manifest_actions_run_id=f"shadow-replay-{source_actions_run_id}",
        manifest_head_sha=head_sha,
    )
    json_path = next(path for path in paths if path.name == "room_generation_report.json")
    import json

    return json.loads(json_path.read_text(encoding="utf-8"))


def ready_history_records(report: Mapping[str, Any]) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    executed_at = str(report.get("executed_at", ""))[:10]
    for item in report.get("items", []):
        if not isinstance(item, Mapping) or item.get("status") != "ready":
            continue
        records.append(
            {
                "日付": executed_at,
                "ステータス": "ready",
                "商品タイプ": str(item.get("product_type", "")),
                "タイトル": str(item.get("title", "")),
                "投稿文": str(item.get("body", "")),
                "商品URL": str(item.get("product_url", "")),
                "正規化URL": str(item.get("product_url", "")),
            }
        )
    return records


def _integer(value: Any) -> int:
    try:
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0
