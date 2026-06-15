from __future__ import annotations

import logging
import os
import re
import sys
import uuid
from collections import Counter
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from zoneinfo import ZoneInfo

from fixed_rule_generator import (
    GENERATION_MODE,
    FixedRulePostGenerator,
    GenerationContext,
    classify_product_type,
)
from generation_report import GenerationReportItem, write_generation_reports
from rakuten_api import Product, RakutenApiClient, rotating_categories
from scoring import (
    ScoredProduct,
    build_selection_tiers_from_env,
    count_filter_results,
    score_all_products,
    select_products,
)
from sheets import (
    DEFAULT_REVIEW_SHEET_NAME,
    SheetsClient,
    normalize_product_url,
    scored_product_to_row,
    target_sheet_for_status,
)

JST = ZoneInfo("Asia/Tokyo")
LOGGER = logging.getLogger("rakuten-room-agent")


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    run_id = uuid.uuid4().hex[:12]

    try:
        application_id = get_required_env("RAKUTEN_APPLICATION_ID")
        access_key = os.getenv("RAKUTEN_ACCESS_KEY")
        referer = os.getenv("RAKUTEN_REFERER")
        service_account_json = get_required_env("GOOGLE_SERVICE_ACCOUNT_JSON")
        spreadsheet_id = get_required_env("SPREADSHEET_ID")
        source_sheet_name = os.getenv("SHEET_NAME", "Sheet1")
        output_sheet_name = os.getenv("OUTPUT_SHEET_NAME", "ROOM_Posts_v2")
        review_sheet_name = os.getenv("REVIEW_SHEET_NAME", DEFAULT_REVIEW_SHEET_NAME)

        now = datetime.now(JST)
        today = now.date()
        categories = [
            category for category, _ in rotating_categories(today, category_limit=5)
        ]
        LOGGER.info(
            "楽天ROOM商品選定を開始 run_id=%s datetime=%s output_sheet=%s categories=%s",
            run_id,
            now.isoformat(),
            output_sheet_name,
            categories,
        )
        LOGGER.info(
            "外部サービス設定 access_key_configured=%s referer_configured=%s generation_mode=%s openai_api_calls=0",
            bool(access_key),
            bool(referer and referer.strip()),
            GENERATION_MODE,
        )
        LOGGER.info("OpenAI API未使用: 固定ルール生成のみで実行します。")

        sheets_client = SheetsClient(spreadsheet_id, service_account_json)
        sheets_client.ensure_headers(output_sheet_name)
        sheets_client.ensure_headers(review_sheet_name)

        rakuten_client = RakutenApiClient(
            application_id,
            access_key=access_key,
            referer=referer,
        )
        products, fetch_report = rakuten_client.fetch_products(target_date=today)
        LOGGER.info(
            "楽天API取得完了 run_id=%s unique_products=%s total_api_items=%s attempts=%s failures=%s",
            run_id,
            len(products),
            fetch_report.total_items,
            len(fetch_report.attempts),
            len(fetch_report.failed_attempts),
        )

        if not products:
            reason = fetch_report.failure_summary()
            LOGGER.error("楽天APIから商品を取得できませんでした run_id=%s reason=%s", run_id, reason)
            sheets_client.append_error(
                review_sheet_name,
                today=today,
                run_id=run_id,
                reason=reason,
            )
            return 0

        existing_urls = sheets_client.read_existing_urls(output_sheet_name)
        existing_urls.update(sheets_client.read_existing_urls(review_sheet_name))
        if output_sheet_name != source_sheet_name:
            existing_urls.update(sheets_client.read_existing_urls(source_sheet_name))
        recent_history = sheets_client.read_recent_history(
            output_sheet_name,
            today=today,
            days=30,
        )
        recent_history.extend(
            sheets_client.read_recent_history(
                review_sheet_name,
                today=today,
                days=30,
            )
        )
        deduped_products, duplicate_count = deduplicate_products(
            products,
            existing_urls=existing_urls,
        )
        LOGGER.info(
            "重複除外完了 run_id=%s existing_urls=%s removed=%s remaining=%s",
            run_id,
            len(existing_urls),
            duplicate_count,
            len(deduped_products),
        )

        if not deduped_products:
            reason = "楽天APIから商品は取得できましたが、既存URLまたは類似商品とすべて重複しました。"
            LOGGER.error("%s run_id=%s", reason, run_id)
            sheets_client.append_error(
                review_sheet_name,
                today=today,
                run_id=run_id,
                reason=reason,
            )
            return 0

        tiers = build_selection_tiers_from_env()
        filter_counts = count_filter_results(deduped_products, tiers)
        LOGGER.info("選定条件別の候補数 run_id=%s counts=%s", run_id, filter_counts)
        candidates = select_products(deduped_products, today, tiers, limit=15)
        selected_products = diversify_products(candidates, recent_history, limit=5)
        if not selected_products:
            top_products = score_all_products(deduped_products, today)[:3]
            details = [
                {
                    "product": item.product.name[:40],
                    "score": item.total_score,
                    "type": classify_product_type(item.product),
                }
                for item in top_products
            ]
            reason = (
                "段階的な緩和条件でも選定対象が0件でした。"
                f"条件別候補数={filter_counts} 上位候補={details}"
            )
            LOGGER.error("%s run_id=%s", reason, run_id)
            sheets_client.append_error(
                review_sheet_name,
                today=today,
                run_id=run_id,
                reason=reason,
            )
            return 0

        generator = FixedRulePostGenerator()
        context = GenerationContext.from_history(recent_history)
        ready_rows: list[list[object]] = []
        review_rows: list[list[object]] = []
        report_items: list[GenerationReportItem] = []
        for item in selected_products:
            generated = generator.generate(
                item,
                context=context,
                season="",
            )
            LOGGER.info(
                "投稿生成結果 run_id=%s product=%s score=%s demand=%s type=%s axis=%s pattern=%s quality=%s rewrites=%s status=%s generation_mode=%s quality_errors=%s",
                run_id,
                item.product.name[:50],
                item.total_score,
                item.demand_score,
                generated.analysis.product_type,
                generated.analysis.appeal_axis,
                generated.structure_pattern,
                generated.quality.score,
                generated.rewrite_count,
                generated.status,
                generated.generation_mode,
                generated.quality_errors,
            )
            row = scored_product_to_row(
                item,
                generated,
                today=today,
                run_id=run_id,
            )
            write_sheet = target_sheet_for_status(
                generated.status,
                output_sheet_name=output_sheet_name,
                review_sheet_name=review_sheet_name,
            )
            if generated.status == "ready":
                ready_rows.append(row)
            else:
                review_rows.append(row)
            report_items.append(
                GenerationReportItem(
                    scored=item,
                    generated=generated,
                    row=row,
                    write_sheet=write_sheet,
                    duplicate_result=generated.duplicate_result,
                )
            )

        write_generation_reports(
            Path("reports"),
            run_id=run_id,
            executed_at=now,
            generation_mode=GENERATION_MODE,
            output_sheet_name=output_sheet_name,
            review_sheet_name=review_sheet_name,
            fetch_report=fetch_report,
            items=report_items,
        )
        sheets_client.append_products(output_sheet_name, ready_rows)
        sheets_client.append_products(review_sheet_name, review_rows)
        LOGGER.info(
            "Googleスプレッドシート追記完了 run_id=%s ready_sheet=%s ready_rows=%s review_sheet=%s review_rows=%s",
            run_id,
            output_sheet_name,
            len(ready_rows),
            review_sheet_name,
            len(review_rows),
        )
        return 0
    except Exception:
        LOGGER.exception("処理中にエラーが発生しました run_id=%s", run_id)
        return 1


def deduplicate_products(
    products: list[Product],
    *,
    existing_urls: set[str],
) -> tuple[list[Product], int]:
    kept: list[Product] = []
    seen_urls = set(existing_urls)
    removed = 0
    for product in products:
        normalized_url = normalize_product_url(product.url)
        if not normalized_url or normalized_url in seen_urls:
            removed += 1
            continue
        if any(is_near_duplicate(product, other) for other in kept):
            removed += 1
            continue
        kept.append(product)
        seen_urls.add(normalized_url)
    return kept, removed


def is_near_duplicate(left: Product, right: Product) -> bool:
    left_name = normalize_product_name(left.name)
    right_name = normalize_product_name(right.name)
    similarity = SequenceMatcher(None, left_name, right_name).ratio()
    same_shop = bool(left.shop_name and left.shop_name == right.shop_name)
    same_price = left.price > 0 and left.price == right.price
    return similarity >= 0.9 or (similarity >= 0.78 and same_shop and same_price)


def diversify_products(
    candidates: list[ScoredProduct],
    recent_history: list[dict[str, str]],
    *,
    limit: int,
) -> list[ScoredProduct]:
    recent_types = Counter(
        record.get("商品タイプ", "")
        for record in recent_history
        if record.get("商品タイプ")
    )
    ranked = sorted(
        candidates,
        key=lambda item: (
            recent_types[classify_product_type(item.product)],
            -item.total_score,
        ),
    )
    selected: list[ScoredProduct] = []
    selected_types: Counter[str] = Counter()
    for item in ranked:
        product_type = classify_product_type(item.product)
        if selected_types[product_type] >= 2:
            continue
        selected.append(item)
        selected_types[product_type] += 1
        if len(selected) >= limit:
            return selected
    for item in ranked:
        if item not in selected:
            selected.append(item)
        if len(selected) >= limit:
            break
    return selected


def normalize_product_name(name: str) -> str:
    normalized = re.sub(r"[\[\]【】()（）★☆◆◇■□●○◎※♪]", " ", name.lower())
    normalized = re.sub(
        r"(送料無料|ポイント\d+倍|楽天\d+位|ランキング\d+位|セール|限定|大人気|人気)",
        " ",
        normalized,
    )
    return re.sub(r"[^0-9a-zぁ-んァ-ヶ一-龠]+", "", normalized)


def get_required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"必須の環境変数が設定されていません: {name}")
    return value


if __name__ == "__main__":
    sys.exit(main())
