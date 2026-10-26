from __future__ import annotations

import argparse
import inspect
import json
import re
import time
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

from fixed_rule_generator import FixedRulePostGenerator, GenerationContext, error_code, normalize_text
from main import POST_SLOTS, generate_until_ready
from rakuten_api import Product
from scoring import score_product


def _integer(value: object) -> int:
    digits = re.sub(r"[^0-9-]", "", str(value or ""))
    return int(digits) if digits not in {"", "-"} else 0


def _floating(value: object) -> float:
    match = re.search(r"-?\d+(?:\.\d+)?", str(value or ""))
    return float(match.group(0)) if match else 0.0


def _product(item: dict[str, object]) -> Product:
    source = dict(item.get("source_evidence") or {})
    row = dict(item.get("sheet_row") or {})
    return Product(
        category=str(source.get("category") or row.get("カテゴリ") or ""),
        name=str(item.get("product_name") or row.get("商品名") or ""),
        url=str(item.get("product_url") or row.get("商品URL") or ""),
        price=_integer(row.get("価格")),
        review_count=_integer(row.get("レビュー件数")),
        review_average=_floating(row.get("評価")),
        caption=str(source.get("caption") or ""),
        catchcopy=str(source.get("catchcopy") or ""),
        shop_name=str(source.get("shop_name") or row.get("ショップ名") or ""),
        image_url=str(row.get("画像URL") or ""),
        search_keyword=str(source.get("search_keyword") or row.get("検索キーワード") or ""),
    )


def _history_records(root: Path, target_day: date) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    earliest = target_day - timedelta(days=30)
    for path in sorted(root.glob("*/reports/room_operational_manifest_v2.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            routine_day = date.fromisoformat(str(payload["routine_date_jst"]))
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if not earliest <= routine_day < target_day:
            continue
        for slot in POST_SLOTS:
            value = dict(payload.get("slots", {}).get(slot) or {})
            if value.get("status") != "ready" or not isinstance(value.get("candidate"), dict):
                continue
            candidate = value["candidate"]
            records.append({
                "ステータス": "ready",
                "タイトル": str(candidate.get("title") or ""),
                "投稿文": str(candidate.get("body") or ""),
            })
    return records


def replay(report_path: Path, history_root: Path) -> dict[str, object]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    target_day = date.fromisoformat(str(report["executed_at"])[:10])
    candidates = [score_product(_product(item), target_day) for item in report["items"]]
    history = _history_records(history_root, target_day)

    sweep_context = GenerationContext.from_history(history)
    sweep_types: set[str] = set()
    sweep_status: Counter[str] = Counter()
    sweep_errors: dict[str, Counter[str]] = defaultdict(Counter)
    sweep_attempts: Counter[str] = Counter()
    sweep_started = time.perf_counter()
    for candidate in candidates:
        generated = FixedRulePostGenerator().generate(candidate, context=sweep_context, season="")
        product_type = str(generated.analysis.product_type)
        sweep_status[f"{product_type}:{generated.status}"] += 1
        sweep_attempts[product_type] += int(
            getattr(generated, "generation_attempts", 0) or (generated.rewrite_count + 1)
        )
        sweep_errors[product_type].update(error_code(value) for value in generated.quality_errors)
        if generated.status == "ready" and product_type not in sweep_types:
            # Patched generators defer context mutation until selection accepts
            # the candidate.  The baseline generator already remembers here;
            # avoid remembering it twice while preserving that old behavior.
            if generated.title not in sweep_context.used_titles:
                sweep_context.remember(generated)
            sweep_types.add(product_type)
    sweep_elapsed = time.perf_counter() - sweep_started

    context = GenerationContext.from_history(history)
    diagnostics: dict[str, object] = {}
    kwargs: dict[str, object] = {
        "generator": FixedRulePostGenerator(),
        "context": context,
        "target_ready": len(POST_SLOTS),
    }
    if "diagnostics" in inspect.signature(generate_until_ready).parameters:
        kwargs["diagnostics"] = diagnostics
    started = time.perf_counter()
    results = generate_until_ready(candidates, **kwargs)
    duration = time.perf_counter() - started

    ready = [(item, generated) for item, generated in results if generated.status == "ready"]
    reviewed = [(item, generated) for item, generated in results if generated.status != "ready"]
    attempts = 0
    errors: Counter[str] = Counter()
    type_summary: dict[str, dict[str, object]] = defaultdict(
        lambda: {"processed": 0, "ready": 0, "needs_review": 0, "attempts": 0, "errors": Counter()}
    )
    repeated_titles = 0
    repeated_bodies = 0
    repeated_body_types: Counter[str] = Counter()
    ready_repeated_bodies = 0
    for _item, generated in results:
        attempt_count = int(getattr(generated, "generation_attempts", 0) or (generated.rewrite_count + 1))
        attempts += attempt_count
        product_type = str(generated.analysis.product_type)
        summary = type_summary[product_type]
        summary["processed"] += 1
        summary["ready" if generated.status == "ready" else "needs_review"] += 1
        summary["attempts"] += attempt_count
        codes = [error_code(value) for value in generated.quality_errors]
        errors.update(codes)
        summary["errors"].update(codes)
        label = generated.attributes.short_product_label if generated.attributes else ""
        title_parts = generated.title.split("｜", 1)
        if len(title_parts) == 2 and label and normalize_text(label) in normalize_text(title_parts[1]):
            repeated_titles += 1
        if label and generated.body.count(label) >= 3:
            repeated_bodies += 1
            repeated_body_types[product_type] += 1
            if generated.status == "ready":
                ready_repeated_bodies += 1

    serializable_types = {
        product_type: {
            **{key: value for key, value in values.items() if key != "errors"},
            "errors": dict(values["errors"].most_common()),
        }
        for product_type, values in sorted(type_summary.items())
    }
    return {
        "source_report_items": len(report["items"]),
        "history_manifest_records": len(history),
        "listing_teaser_path": True,
        "quality_gate": "existing validate_post 150-260 and duplicate checks",
        "ready": len(ready),
        "needs_review": len(reviewed),
        "missing_slots": list(POST_SLOTS[len(ready):]),
        "attempts": attempts,
        "elapsed_seconds": round(duration, 6),
        "error_counts": dict(errors.most_common()),
        "product_types": serializable_types,
        "unnatural_title_label_repetition": repeated_titles,
        "body_label_repetition_3plus": repeated_bodies,
        "ready_body_label_repetition_3plus": ready_repeated_bodies,
        "body_label_repetition_types": dict(repeated_body_types.most_common()),
        "diagnostics": diagnostics,
        "full_saved_item_quality_sweep": {
            "status_counts": dict(sorted(sweep_status.items())),
            "attempts_by_type": dict(sorted(sweep_attempts.items())),
            "errors_by_type": {
                product_type: dict(values.most_common())
                for product_type, values in sorted(sweep_errors.items())
            },
            "elapsed_seconds": round(sweep_elapsed, 6),
        },
        "limitations": [
            "saved report contains 111 emitted items, not all 233 selected candidates",
            "candidate order is the emitted report order, not the unavailable original full selection order",
            "30-day history is reconstructed only from locally saved operational manifests",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline Phase 9 saved-artifact replay")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--history-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(replay(args.report, args.history_root), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
