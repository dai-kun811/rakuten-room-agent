from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import datetime, time
from difflib import SequenceMatcher
from enum import Enum
from typing import Any, Iterable, Mapping

from room_operation_contract import (
    JST,
    POST_SLOTS,
    ContractError,
    normalize_product_url,
    routine_date_jst,
    validate_manifest_v2,
)


SLOT_DUE_TIMES = {
    "morning": time(8, 0),
    "noon": time(12, 0),
    "evening": time(19, 0),
}
BRAND_TAG = "#とらパパ厳選"
HIGH_RISK_PHRASES = (
    "必ず寝る",
    "泣き止む",
    "安全な睡眠場所",
    "絶対",
    "間違いなし",
)
HUMAN_REVIEW_PRODUCT_TERMS = (
    "自分で飲む",
    "セルフミルク",
    "ママ代行",
    "ハンズフリー授乳",
)


class ShadowAction(str, Enum):
    WOULD_CLAIM = "would_claim"
    WAIT = "wait"
    BLOCKED = "blocked"
    ALREADY_POSTED = "already_posted"
    HUMAN_HOLD = "human_hold"
    EXPIRED_UNPOSTED = "expired_unposted"


@dataclass(frozen=True)
class ShadowDecision:
    routine_date: str
    slot: str
    action: ShadowAction
    reason: str
    manifest_revision: int
    normalized_url: str = ""
    content_hash: str = ""
    product_type: str = ""

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["action"] = self.action.value
        return value


@dataclass(frozen=True)
class ShadowQualityResult:
    candidate_count: int
    blocked_slot_count: int
    duplicate_url_count: int
    maximum_similarity: float
    issues: tuple[str, ...]
    product_type_counts_7d: Mapping[str, int]
    product_type_counts_30d: Mapping[str, int]

    @property
    def passed(self) -> bool:
        return not self.issues

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["passed"] = self.passed
        return value


def evaluate_shadow_manifest(
    manifest: Mapping[str, Any],
    *,
    now: datetime,
    legacy_events: Iterable[Mapping[str, Any]] = (),
    posted_urls: Iterable[str] = (),
) -> list[ShadowDecision]:
    """Evaluate production intent without reserving or submitting anything."""
    validate_manifest_v2(manifest)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ContractError("shadow evaluation requires a timezone-aware datetime")

    day = str(manifest["routine_date_jst"])
    current_day = routine_date_jst(now).isoformat()
    local_now = now.astimezone(JST)
    revision = int(manifest["revision"])
    latest_legacy = _latest_legacy_events(legacy_events, day)
    normalized_posted = {
        normalize_product_url(value) for value in posted_urls if str(value).strip()
    }
    for event in latest_legacy.values():
        if str(event.get("status", "")) == "posted" and event.get("normalized_url"):
            normalized_posted.add(normalize_product_url(str(event["normalized_url"])))

    decisions: list[ShadowDecision] = []
    for slot in POST_SLOTS:
        value = manifest["slots"][slot]
        legacy = latest_legacy.get(slot)
        if legacy is not None:
            legacy_status = str(legacy.get("status", ""))
            if legacy_status == "posted":
                decisions.append(
                    ShadowDecision(day, slot, ShadowAction.ALREADY_POSTED, "legacy_ledger_posted", revision)
                )
                continue
            if legacy_status == "reserved":
                decisions.append(
                    ShadowDecision(day, slot, ShadowAction.HUMAN_HOLD, "legacy_reservation_is_uncertain", revision)
                )
                continue
            if legacy_status == "failed" and not _definitive_pre_submit_failure(legacy):
                decisions.append(
                    ShadowDecision(day, slot, ShadowAction.HUMAN_HOLD, "legacy_failure_not_proven_pre_submit", revision)
                )
                continue

        if value["status"] == "blocked":
            decisions.append(
                ShadowDecision(day, slot, ShadowAction.BLOCKED, str(value["reason"]), revision)
            )
            continue

        candidate = value["candidate"]
        normalized_url = normalize_product_url(str(candidate["normalized_url"]))
        common = {
            "manifest_revision": revision,
            "normalized_url": normalized_url,
            "content_hash": str(candidate["content_hash"]),
            "product_type": str(candidate["product_type"]),
        }
        candidate_issues = _candidate_quality_issues(day, slot, candidate)
        if candidate_issues:
            decisions.append(
                ShadowDecision(
                    day,
                    slot,
                    ShadowAction.BLOCKED,
                    "shadow_quality_gate:" + "|".join(candidate_issues[:3]),
                    **common,
                )
            )
        elif normalized_url in normalized_posted:
            decisions.append(
                ShadowDecision(day, slot, ShadowAction.HUMAN_HOLD, "product_url_already_posted", **common)
            )
        elif day < current_day:
            decisions.append(
                ShadowDecision(day, slot, ShadowAction.EXPIRED_UNPOSTED, "routine_day_has_ended", **common)
            )
        elif day > current_day or local_now.time() < SLOT_DUE_TIMES[slot]:
            decisions.append(
                ShadowDecision(day, slot, ShadowAction.WAIT, "slot_not_due", **common)
            )
        else:
            decisions.append(
                ShadowDecision(day, slot, ShadowAction.WOULD_CLAIM, "quality_passed_and_slot_due", **common)
            )
    return decisions


def audit_shadow_manifests(
    manifests: Iterable[Mapping[str, Any]],
    *,
    similarity_threshold: float = 0.75,
) -> ShadowQualityResult:
    ordered = sorted(manifests, key=lambda value: str(value.get("routine_date_jst", "")))
    if not ordered:
        return ShadowQualityResult(0, 0, 0, 0.0, ("no_manifests",), {}, {})

    candidates: list[tuple[str, str, Mapping[str, Any]]] = []
    issues: list[str] = []
    blocked = 0
    for manifest in ordered:
        try:
            validate_manifest_v2(manifest)
        except ContractError as exc:
            issues.append(f"manifest_invalid:{exc}")
            continue
        day = str(manifest["routine_date_jst"])
        for slot in POST_SLOTS:
            value = manifest["slots"][slot]
            if value["status"] == "blocked":
                blocked += 1
                issues.append(f"{day}:{slot}:blocked:{value['reason']}")
                continue
            candidate = value["candidate"]
            candidates.append((day, slot, candidate))
            issues.extend(_candidate_quality_issues(day, slot, candidate))

    seen_urls: dict[str, tuple[str, str]] = {}
    duplicate_urls = 0
    for day, slot, candidate in candidates:
        url = normalize_product_url(str(candidate["normalized_url"]))
        if url in seen_urls:
            duplicate_urls += 1
            previous_day, previous_slot = seen_urls[url]
            issues.append(f"{day}:{slot}:duplicate_url:{previous_day}:{previous_slot}")
        else:
            seen_urls[url] = (day, slot)

    maximum_similarity = 0.0
    for index, (left_day, left_slot, left) in enumerate(candidates):
        for right_day, right_slot, right in candidates[index + 1 :]:
            ratio = _body_similarity(str(left["body"]), str(right["body"]))
            maximum_similarity = max(maximum_similarity, ratio)
            if ratio >= similarity_threshold:
                issues.append(
                    f"{left_day}:{left_slot}/{right_day}:{right_slot}:body_similarity:{ratio:.3f}"
                )

    newest = max(str(value["routine_date_jst"]) for value in ordered)
    newest_date = datetime.fromisoformat(newest).date()
    counts_7d: dict[str, int] = {}
    counts_30d: dict[str, int] = {}
    for day, _slot, candidate in candidates:
        age = (newest_date - datetime.fromisoformat(day).date()).days
        product_type = str(candidate["product_type"])
        if age <= 6:
            counts_7d[product_type] = counts_7d.get(product_type, 0) + 1
        if age <= 29:
            counts_30d[product_type] = counts_30d.get(product_type, 0) + 1

    return ShadowQualityResult(
        candidate_count=len(candidates),
        blocked_slot_count=blocked,
        duplicate_url_count=duplicate_urls,
        maximum_similarity=round(maximum_similarity, 3),
        issues=tuple(dict.fromkeys(issues)),
        product_type_counts_7d=counts_7d,
        product_type_counts_30d=counts_30d,
    )


def _candidate_quality_issues(
    day: str,
    slot: str,
    candidate: Mapping[str, Any],
) -> list[str]:
    prefix = f"{day}:{slot}"
    title = str(candidate.get("title", ""))
    body = str(candidate.get("body", ""))
    product_name = str(candidate.get("product_name", ""))
    recommendation_reason = str(candidate.get("recommendation_reason", "")).strip()
    purchase_checkpoints = candidate.get("purchase_checkpoints", [])
    source_evidence = candidate.get("source_evidence", {})
    tags = candidate.get("hashtags", [])
    issues: list[str] = []
    if not title:
        issues.append(f"{prefix}:empty_title")
    if not 150 <= len(body) <= 260:
        issues.append(f"{prefix}:body_length:{len(body)}")
    sentence_count = len(re.findall(r"[^。！？]+[。！？]", body))
    if sentence_count not in {3, 4}:
        issues.append(f"{prefix}:sentence_count:{sentence_count}")
    if not isinstance(tags, list) or len(tags) != 5 or tags[-1:] != [BRAND_TAG]:
        issues.append(f"{prefix}:hashtags_invalid")
    if not recommendation_reason:
        issues.append(f"{prefix}:recommendation_reason_missing")
    if not isinstance(purchase_checkpoints, list) or not purchase_checkpoints:
        issues.append(f"{prefix}:purchase_checkpoints_missing")
    if not isinstance(source_evidence, Mapping) or not any(
        str(source_evidence.get(key, "")).strip()
        for key in ("category", "caption", "catchcopy", "search_keyword")
    ):
        issues.append(f"{prefix}:source_evidence_missing")
    for phrase in HIGH_RISK_PHRASES:
        if phrase in f"{title}{body}":
            issues.append(f"{prefix}:high_risk_phrase:{phrase}")
    if "SDカード付き" in f"{title}{body}" and not any(
        marker in product_name for marker in ("SDカード付き", "SDカード付属", "SDカード同梱", "SDカード付")
    ):
        issues.append(f"{prefix}:sd_card_inclusion_unverified")
    product_text = f"{product_name}{title}{body}{' '.join(str(tag) for tag in tags)}"
    if str(candidate.get("product_type", "")) == "nursing_support":
        risky = next((term for term in HUMAN_REVIEW_PRODUCT_TERMS if term in product_text), "")
        if risky:
            issues.append(f"{prefix}:human_review_required_feeding_support:{risky}")
    return issues


def _latest_legacy_events(
    events: Iterable[Mapping[str, Any]],
    day: str,
) -> dict[str, Mapping[str, Any]]:
    latest: dict[str, Mapping[str, Any]] = {}
    for event in events:
        raw_slot = str(event.get("post_slot", ""))
        event_day, separator, slot = raw_slot.partition(":")
        if separator and event_day == day and slot in POST_SLOTS:
            latest[slot] = event
    return latest


def _definitive_pre_submit_failure(event: Mapping[str, Any]) -> bool:
    return str(event.get("detail", "")) == "楽天商品ページにROOM投稿ボタンが見つかりません。"


def _body_similarity(left: str, right: str) -> float:
    normalize = lambda value: re.sub(r"[\s、。！？,.#]", "", value).lower()
    return SequenceMatcher(None, normalize(left), normalize(right)).ratio()
