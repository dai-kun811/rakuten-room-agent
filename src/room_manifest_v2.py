from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from room_operation_contract import (
    POST_SLOTS,
    normalize_product_url,
    routine_date_jst,
    validate_manifest_v2,
)


MANIFEST_BASENAME = "room_operational_manifest_v2"


def build_manifest_v2(
    generation_report: Mapping[str, Any],
    *,
    actions_run_id: str | int,
    head_sha: str,
    revision: int,
    generated_at: datetime,
    supersedes_revision: int | None = None,
    recovery_id: str | None = None,
    generation_channel: str | None = None,
) -> dict[str, Any]:
    """Convert the existing generation report into the immutable v2 contract.

    Every posting slot is evaluated independently. A weak or missing candidate
    blocks only its own slot and never suppresses ready candidates in other
    slots.
    """
    items = generation_report.get("items", [])
    if not isinstance(items, list):
        raise ValueError("generation report items must be a list")

    by_slot: dict[str, list[Mapping[str, Any]]] = {slot: [] for slot in POST_SLOTS}
    for item in items:
        if not isinstance(item, Mapping):
            continue
        slot = str(item.get("post_slot", "")).strip()
        if slot in by_slot:
            by_slot[slot].append(item)

    slots: dict[str, dict[str, Any]] = {}
    for slot in POST_SLOTS:
        candidates = by_slot[slot]
        ready = [item for item in candidates if _is_manifest_ready(item)]
        if len(ready) == 1:
            slots[slot] = {"status": "ready", "candidate": _candidate_payload(ready[0])}
        elif len(ready) > 1:
            slots[slot] = {
                "status": "blocked",
                "reason": "multiple_ready_candidates_for_slot",
            }
        else:
            slots[slot] = {
                "status": "blocked",
                "reason": _blocked_reason(slot, candidates, generation_report),
            }

    payload: dict[str, Any] = {
        "schema_version": 2,
        "routine_date_jst": routine_date_jst(generated_at).isoformat(),
        "revision": revision,
        "supersedes_revision": supersedes_revision,
        "actions_run_id": str(actions_run_id),
        "head_sha": head_sha,
        "generated_at": generated_at.isoformat(),
        "slots": slots,
    }
    if recovery_id is not None:
        payload["recovery_id"] = recovery_id
    if generation_channel is not None:
        payload["generation_channel"] = generation_channel
    validate_manifest_v2(payload)
    return payload


def write_manifest_v2(
    report_dir: Path,
    generation_report: Mapping[str, Any],
    *,
    actions_run_id: str | int,
    head_sha: str,
    revision: int,
    generated_at: datetime,
    supersedes_revision: int | None = None,
    recovery_id: str | None = None,
    generation_channel: str | None = None,
) -> Path:
    payload = build_manifest_v2(
        generation_report,
        actions_run_id=actions_run_id,
        head_sha=head_sha,
        revision=revision,
        generated_at=generated_at,
        supersedes_revision=supersedes_revision,
        recovery_id=recovery_id,
        generation_channel=generation_channel,
    )
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"{MANIFEST_BASENAME}.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return path


def candidate_content_hash(candidate: Mapping[str, Any]) -> str:
    canonical = {
        "body": str(candidate.get("body", "")),
        "hashtags": [str(tag) for tag in candidate.get("hashtags", [])],
        "normalized_url": normalize_product_url(str(candidate.get("product_url", ""))),
        "title": str(candidate.get("title", "")),
    }
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _is_manifest_ready(item: Mapping[str, Any]) -> bool:
    quality = item.get("quality")
    quality_errors = quality.get("errors", []) if isinstance(quality, Mapping) else ["missing"]
    return (
        item.get("status") == "ready"
        and not item.get("review_reasons")
        and isinstance(quality, Mapping)
        and not quality_errors
        and bool(str(item.get("product_url", "")).strip())
        and bool(str(item.get("body", "")).strip())
    )


def _candidate_payload(item: Mapping[str, Any]) -> dict[str, Any]:
    product_url = str(item["product_url"]).strip()
    quality = item.get("quality", {})
    candidate = {
        "product_url": product_url,
        "normalized_url": normalize_product_url(product_url),
        "product_name": str(item.get("product_name", "")).strip(),
        "short_product_label": str(item.get("short_product_label", "")).strip(),
        "product_type": str(item.get("product_type", "")).strip(),
        "title": str(item.get("title", "")).strip(),
        "body": str(item.get("body", "")).strip(),
        "hashtags": [str(tag) for tag in item.get("hashtags", [])],
        "recommendation_reason": str(item.get("recommendation_reason", "")).strip(),
        "confirmed_features": [str(value) for value in item.get("confirmed_features", [])],
        "confirmed_use_cases": [str(value) for value in item.get("confirmed_use_cases", [])],
        "purchase_checkpoints": [str(value) for value in item.get("purchase_checkpoints", [])],
        "source_evidence": _safe_source_evidence(item.get("source_evidence")),
        "quality": {
            "status": "passed",
            "score": quality.get("score"),
            "errors": [],
            "title_evidence_result": quality.get("title_evidence_result", ""),
            "tag_evidence_result": quality.get("tag_evidence_result", ""),
            "recommendation_reason_result": quality.get("recommendation_reason_result", ""),
            "structure_similarity": quality.get("structure_similarity", 0.0),
        },
    }
    candidate["content_hash"] = candidate_content_hash(candidate)
    return candidate


def _safe_source_evidence(value: Any) -> dict[str, str]:
    allowed = ("category", "caption", "catchcopy", "shop_name", "search_keyword")
    if not isinstance(value, Mapping):
        return {key: "" for key in allowed}
    return {key: str(value.get(key, "")).strip() for key in allowed}


def _blocked_reason(
    slot: str,
    candidates: list[Mapping[str, Any]],
    report: Mapping[str, Any],
) -> str:
    reasons: list[str] = []
    for item in candidates:
        for reason in item.get("review_reasons", []):
            text = str(reason).strip()
            if text and text not in reasons:
                reasons.append(text)
    if reasons:
        return "quality_rejected: " + " | ".join(reasons[:3])
    missing = report.get("missing_post_slots", [])
    if isinstance(missing, list) and slot in missing:
        return "missing_quality_safe_candidate"
    return "candidate_not_ready"
