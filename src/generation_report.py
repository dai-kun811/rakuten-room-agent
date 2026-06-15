from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from fixed_rule_generator import GeneratedPost
from rakuten_api import FetchReport
from scoring import ScoredProduct
from sheets import SHEET_HEADERS, normalize_product_url


REPORT_BASENAME = "room_generation_report"
SECRET_FIELD_NAMES = {
    "api_key",
    "authorization",
    "cookie",
    "spreadsheet_id",
    "google_service_account_json",
    "rakuten_application_id",
    "rakuten_access_key",
    "rakuten_referer",
}


@dataclass(frozen=True)
class GenerationReportItem:
    scored: ScoredProduct
    generated: GeneratedPost
    row: list[object]
    write_sheet: str
    duplicate_result: str


def write_generation_reports(
    report_dir: Path,
    *,
    run_id: str,
    executed_at: datetime,
    generation_mode: str,
    output_sheet_name: str,
    review_sheet_name: str,
    fetch_report: FetchReport | None,
    items: list[GenerationReportItem],
) -> list[Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    payload = build_report_payload(
        run_id=run_id,
        executed_at=executed_at,
        generation_mode=generation_mode,
        output_sheet_name=output_sheet_name,
        review_sheet_name=review_sheet_name,
        fetch_report=fetch_report,
        items=items,
    )
    ensure_no_secret_fields(payload)
    json_path = report_dir / f"{REPORT_BASENAME}.json"
    csv_path = report_dir / f"{REPORT_BASENAME}.csv"
    md_path = report_dir / f"{REPORT_BASENAME}.md"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_csv_report(csv_path, payload["items"])
    write_markdown_report(md_path, payload)
    return [json_path, csv_path, md_path]


def build_report_payload(
    *,
    run_id: str,
    executed_at: datetime,
    generation_mode: str,
    output_sheet_name: str,
    review_sheet_name: str,
    fetch_report: FetchReport | None,
    items: list[GenerationReportItem],
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "executed_at": executed_at.isoformat(),
        "generation_mode": generation_mode,
        "openai_api_calls": 0,
        "output_sheet_name": output_sheet_name,
        "review_sheet_name": review_sheet_name,
        "sheet_columns": SHEET_HEADERS,
        "sheet_column_count": len(SHEET_HEADERS),
        "rakuten_fetch": fetch_summary(fetch_report),
        "items": [report_item(item) for item in items],
    }


def fetch_summary(fetch_report: FetchReport | None) -> dict[str, Any]:
    if fetch_report is None:
        return {"total_api_items": 0, "attempts": 0, "failures": 0}
    return {
        "total_api_items": fetch_report.total_items,
        "attempts": len(fetch_report.attempts),
        "failures": len(fetch_report.failed_attempts),
        "successful_attempts": len(fetch_report.successful_attempts),
    }


def report_item(item: GenerationReportItem) -> dict[str, Any]:
    product = item.scored.product
    generated = item.generated
    attributes = generated.attributes
    row_map = {
        header: stringify_cell(item.row[index]) if index < len(item.row) else ""
        for index, header in enumerate(SHEET_HEADERS)
    }
    return {
        "product_name": product.name,
        "product_url": normalize_product_url(product.url),
        "short_product_label": attributes.short_product_label if attributes else "",
        "product_type": generated.analysis.product_type,
        "classification_keywords": list(attributes.classification_keywords) if attributes else [],
        "title": generated.title,
        "body": generated.body,
        "hashtags": generated.hashtags,
        "status": generated.status,
        "review_reasons": generated.quality_errors,
        "generation_mode": generated.generation_mode,
        "quality": {
            "score": generated.quality.score,
            "errors": generated.quality_errors,
            "title_evidence_result": generated.title_evidence_result,
            "tag_evidence_result": generated.tag_evidence_result,
            "recommendation_reason_result": generated.recommendation_reason_result,
            "structure_similarity": generated.structure_similarity,
        },
        "write_sheet": item.write_sheet,
        "duplicate_result": item.duplicate_result,
        "confirmed_features": list(attributes.confirmed_features) if attributes else [],
        "feature_sources": feature_sources(product, attributes.confirmed_features if attributes else ()),
        "sheet_row": row_map,
        "sheet_row_column_count": len(item.row),
    }


def feature_sources(product: Any, features: tuple[str, ...]) -> dict[str, list[str]]:
    source_fields = {
        "category": product.category,
        "name": product.name,
        "caption": product.caption,
        "catchcopy": product.catchcopy,
        "shop_name": product.shop_name,
    }
    result: dict[str, list[str]] = {}
    for feature in features:
        matches = [
            field
            for field, value in source_fields.items()
            if feature_marker_hint(feature) and feature_marker_hint(feature) in value.lower()
        ]
        result[feature] = matches or ["product_text"]
    return result


def feature_marker_hint(feature: str) -> str:
    return {
        "thick": "厚手",
        "pants": "パンツ",
        "tape": "テープ",
        "swaddle": "スワドル",
        "moro_reflex": "モロー",
        "hands_free": "ハンズフリー",
        "nursing_cushion": "授乳クッション",
        "milk_support": "ミルク",
        "bottle_holder": "哺乳瓶",
        "hug_futon": "抱っこ布団",
        "sleep_cushion": "ねんね",
        "baby_futon": "ベビー布団",
        "back_switch": "背中スイッチ",
        "cotton": "コットン",
        "double_gauze": "ダブルガーゼ",
    }.get(feature, feature)


def write_csv_report(path: Path, items: list[dict[str, Any]]) -> None:
    fields = [
        "product_name",
        "short_product_label",
        "product_type",
        "classification_keywords",
        "title",
        "body",
        "hashtags",
        "status",
        "review_reasons",
        "generation_mode",
        "write_sheet",
        "duplicate_result",
        "sheet_row_column_count",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in items:
            writer.writerow(
                {
                    field: stringify_cell(item.get(field, ""))
                    for field in fields
                }
            )


def write_markdown_report(path: Path, payload: dict[str, Any]) -> None:
    lines = [
        "# ROOM Generation Report",
        "",
        f"- run_id: `{payload['run_id']}`",
        f"- executed_at: `{payload['executed_at']}`",
        f"- generation_mode: `{payload['generation_mode']}`",
        f"- openai_api_calls: `{payload['openai_api_calls']}`",
        f"- output_sheet_name: `{payload['output_sheet_name']}`",
        f"- review_sheet_name: `{payload['review_sheet_name']}`",
        f"- sheet_column_count: `{payload['sheet_column_count']}`",
        "",
    ]
    for index, item in enumerate(payload["items"], start=1):
        lines.extend(
            [
                f"## {index}. {item['product_name']}",
                "",
                f"- short_product_label: `{item['short_product_label']}`",
                f"- product_type: `{item['product_type']}`",
                f"- classification_keywords: `{', '.join(item['classification_keywords'])}`",
                f"- status: `{item['status']}`",
                f"- write_sheet: `{item['write_sheet']}`",
                f"- duplicate_result: `{item['duplicate_result']}`",
                f"- review_reasons: `{', '.join(item['review_reasons'])}`",
                f"- title: {item['title']}",
                f"- body: {item['body']}",
                f"- hashtags: {' '.join(item['hashtags'])}",
                f"- sheet_row_column_count: `{item['sheet_row_column_count']}`",
                "",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def stringify_cell(value: Any) -> str:
    if isinstance(value, (list, tuple, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def ensure_no_secret_fields(payload: Any) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key.lower() in SECRET_FIELD_NAMES:
                raise ValueError(f"secret-like field is not allowed in report: {key}")
            ensure_no_secret_fields(value)
    elif isinstance(payload, list):
        for value in payload:
            ensure_no_secret_fields(value)
