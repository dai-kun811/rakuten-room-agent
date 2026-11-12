"""Fail-closed, offline planning for ROOM-linked X content.

This module plans content and validates release evidence.  It never connects to
X, never clicks the X UI, and never publishes a post.  Production delivery must
use a separately approved official X connection.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Mapping
from urllib.parse import urlparse


SLOTS = ("morning", "noon", "evening")
SLOT_ORDER = {slot: index for index, slot in enumerate(SLOTS)}
PROMO_WEEKDAYS = {1, 3, 5}  # Tuesday, Thursday, Saturday
HELPFUL_PATTERNS = (
    "micro_hack_v1",
    "saveable_checklist_v1",
    "before_after_setup_v1",
    "conversation_prompt_v1",
)
HELPFUL_PATTERN_BY_WEEKDAY = {
    0: "micro_hack_v1",            # Monday
    2: "saveable_checklist_v1",    # Wednesday
    4: "before_after_setup_v1",    # Friday
    6: "conversation_prompt_v1",   # Sunday
}
MIN_PROMO_QUALITY = 85
MAX_X_TEXT_LENGTH = 280
FORBIDDEN_CLAIMS = (
    "絶対",
    "必ず",
    "最強",
    "治る",
    "完治",
    "確実に",
    "効果絶大",
    "発達に効く",
    "発達が早くなる",
    "頭が良くなる",
    "医師不要",
    "薬不要",
    "100%",
)
URL_PATTERN = re.compile(r"https?://[^\s]+")
OWN_ROOM_DETAIL_PATTERN = re.compile(r"^/tora_papa/\d+/?$")


class XContentPlanError(ValueError):
    """A fail-closed validation error."""


@dataclass(frozen=True)
class RoomCandidate:
    slot: str
    product_id: str
    product_url: str
    title: str
    category: str
    quality_score: int
    ready: bool
    review_reasons: tuple[str, ...]
    promo_copy: str


@dataclass(frozen=True)
class HelpfulCard:
    card_id: str
    pattern_id: str
    hook: str
    points: tuple[str, ...]
    caution: str
    question: str
    source_urls: tuple[str, ...]
    claims_reviewed: bool
    original_visual_ready: bool


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise XContentPlanError(f"{field} is required")
    return text


def _https_url(value: Any, field: str) -> str:
    url = _text(value, field)
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise XContentPlanError(f"{field} must be a public HTTPS URL")
    return url


def _candidate(value: Mapping[str, Any]) -> RoomCandidate:
    if not isinstance(value, Mapping):
        raise XContentPlanError("each ROOM candidate must be an object")
    slot = str(value.get("slot", ""))
    if slot not in SLOT_ORDER:
        raise XContentPlanError("candidate slot is invalid")
    quality = value.get("quality_score")
    if isinstance(quality, bool) or not isinstance(quality, int) or not 0 <= quality <= 100:
        raise XContentPlanError("quality_score must be an integer from 0 to 100")
    ready = value.get("ready")
    if not isinstance(ready, bool):
        raise XContentPlanError("ready must be boolean")
    reasons = value.get("review_reasons", [])
    if not isinstance(reasons, list) or any(not str(item).strip() for item in reasons):
        raise XContentPlanError("review_reasons must be a list of non-empty values")
    promo_copy = _text(value.get("promo_copy"), "promo_copy")
    if "#PR" not in promo_copy:
        raise XContentPlanError("promo_copy must include #PR")
    if any(token in promo_copy for token in FORBIDDEN_CLAIMS):
        raise XContentPlanError("promo_copy contains a forbidden claim")
    if any(token in promo_copy.lower() for token in ("http://", "https://", "www.", "t.co/", "room.rakuten.co.jp")):
        raise XContentPlanError("promo_copy must not embed a URL before ROOM publication")
    if x_weighted_length(promo_copy) + 1 + 23 > MAX_X_TEXT_LENGTH:
        raise XContentPlanError("promo_copy leaves no room for the ROOM URL")
    return RoomCandidate(
        slot=slot,
        product_id=_text(value.get("product_id"), "product_id"),
        product_url=_https_url(value.get("product_url"), "product_url"),
        title=_text(value.get("title"), "title"),
        category=_text(value.get("category"), "category"),
        quality_score=quality,
        ready=ready,
        review_reasons=tuple(str(item).strip() for item in reasons),
        promo_copy=promo_copy,
    )


def _helpful_card(value: Mapping[str, Any]) -> HelpfulCard:
    if not isinstance(value, Mapping):
        raise XContentPlanError("each helpful card must be an object")
    pattern_id = str(value.get("pattern_id", ""))
    if pattern_id not in HELPFUL_PATTERNS:
        raise XContentPlanError("helpful pattern is invalid")
    points = value.get("points")
    if not isinstance(points, list) or not 2 <= len(points) <= 3:
        raise XContentPlanError("helpful points must contain two or three items")
    cleaned_points = tuple(_text(item, "helpful point") for item in points)
    source_urls = value.get("source_urls")
    if not isinstance(source_urls, list) or not source_urls:
        raise XContentPlanError("helpful source_urls must not be empty")
    sources = tuple(_https_url(item, "helpful source URL") for item in source_urls)
    card = HelpfulCard(
        card_id=_text(value.get("card_id"), "card_id"),
        pattern_id=pattern_id,
        hook=_text(value.get("hook"), "hook"),
        points=cleaned_points,
        caution=_text(value.get("caution"), "caution"),
        question=_text(value.get("question"), "question"),
        source_urls=sources,
        claims_reviewed=value.get("claims_reviewed") is True,
        original_visual_ready=value.get("original_visual_ready") is True,
    )
    if not card.claims_reviewed:
        raise XContentPlanError("helpful claims must be reviewed")
    if not card.original_visual_ready:
        raise XContentPlanError("an original helpful visual is required")
    combined = "\n".join((card.hook, *card.points, card.caution, card.question))
    if any(token in combined for token in FORBIDDEN_CLAIMS):
        raise XContentPlanError("helpful card contains a forbidden claim")
    if "#PR" in combined or "http://" in combined or "https://" in combined:
        raise XContentPlanError("helpful copy must not contain promotion or links")
    if not card.question.endswith(("？", "?")):
        raise XContentPlanError("helpful card must end with one clear question")
    if sum(combined.count(mark) for mark in ("？", "?")) != 1:
        raise XContentPlanError("helpful card must contain exactly one question")
    return card


def render_helpful_copy(card: HelpfulCard) -> str:
    numbered = "\n".join(f"{index}. {point}" for index, point in enumerate(card.points, 1))
    text = f"{card.hook}\n\n{numbered}\n\n{card.caution}\n\n{card.question}"
    if x_weighted_length(text) > MAX_X_TEXT_LENGTH:
        raise XContentPlanError("helpful copy exceeds the X text limit")
    return text


def _plan_id(payload: Mapping[str, Any]) -> str:
    return _sha256(_canonical_bytes(payload))[:16]


def _require_plan_integrity(plan: Mapping[str, Any]) -> None:
    claimed_plan_id = str(plan.get("plan_id", ""))
    payload = dict(plan)
    payload.pop("plan_id", None)
    if not claimed_plan_id or _plan_id(payload) != claimed_plan_id:
        raise XContentPlanError("plan integrity check failed")


def _helpful_content_hash(card: HelpfulCard) -> str:
    return _sha256(
        _canonical_bytes(
            {
                "pattern_id": card.pattern_id,
                "hook": card.hook,
                "points": card.points,
                "caution": card.caution,
                "question": card.question,
            }
        )
    )


def x_weighted_length(text: str) -> int:
    """Return a conservative X-compatible weighted length.

    URLs count as 23 characters. ASCII and X's common single-weight Unicode
    ranges count as one; other code points, including Japanese and emoji, count
    as two. Uncommon characters therefore fail closed rather than overflowing.
    """

    def weighted_segment(segment: str) -> int:
        return sum(
            1
            if ord(character) <= 0x10FF
            or 0x2000 <= ord(character) <= 0x200D
            or 0x2010 <= ord(character) <= 0x201F
            or 0x2032 <= ord(character) <= 0x2037
            else 2
            for character in segment
        )

    length = 0
    position = 0
    for match in URL_PATTERN.finditer(text):
        length += weighted_segment(text[position : match.start()])
        length += 23
        position = match.end()
    return length + weighted_segment(text[position:])


def plan_x_content(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Create an immutable offline daily plan from a validated snapshot."""

    if not isinstance(snapshot, Mapping):
        raise XContentPlanError("snapshot must be an object")
    if snapshot.get("version") != 1:
        raise XContentPlanError("snapshot version must be 1")
    try:
        plan_date = date.fromisoformat(str(snapshot.get("plan_date")))
    except ValueError as exc:
        raise XContentPlanError("plan_date must be YYYY-MM-DD") from exc

    recent_ids = snapshot.get("recent_promoted_product_ids", [])
    if not isinstance(recent_ids, list):
        raise XContentPlanError("recent_promoted_product_ids must be a list")
    recent = {str(item) for item in recent_ids}

    if plan_date.weekday() in PROMO_WEEKDAYS:
        raw_candidates = snapshot.get("room_candidates")
        if not isinstance(raw_candidates, list):
            raise XContentPlanError("room_candidates must be a list")
        candidates = [_candidate(item) for item in raw_candidates]
        product_ids = [item.product_id for item in candidates]
        if len(product_ids) != len(set(product_ids)):
            raise XContentPlanError("room candidates must use unique product IDs")
        eligible = [
            item
            for item in candidates
            if item.ready
            and not item.review_reasons
            and item.quality_score >= MIN_PROMO_QUALITY
            and item.product_id not in recent
        ]
        eligible.sort(key=lambda item: (SLOT_ORDER[item.slot], -item.quality_score, item.product_id))
        if not eligible:
            raise XContentPlanError("no eligible ROOM candidate for the promotional post")
        binding = next(
            {
                "slot": item.slot,
                "product_id": item.product_id,
                "product_url": item.product_url,
                "title": item.title,
                "category": item.category,
                "quality_score": item.quality_score,
                "promo_copy": item.promo_copy,
            }
            for item in eligible
        )
        plan: dict[str, Any] = {
            "version": 1,
            "plan_date": plan_date.isoformat(),
            "content_kind": "promotion",
            "pattern_id": "existing_promotion_winning_pattern",
            "prepared_before_room_publication": True,
            "binding": binding,
            "release_gate": "matching_room_ledger_posted",
            "target_release_delay_minutes": 10,
            "requires_pr_hashtag": True,
            "requires_paid_partnership_label": True,
            "delivery": {
                "automatic_publish": False,
                "status": "official_x_connection_required",
                "required_controls": ["paid_partnership_label"],
            },
        }
    else:
        raw_cards = snapshot.get("helpful_cards")
        if not isinstance(raw_cards, list) or not raw_cards:
            raise XContentPlanError("helpful_cards must not be empty")
        cards = [_helpful_card(item) for item in raw_cards]
        card_ids = [item.card_id for item in cards]
        if len(card_ids) != len(set(card_ids)):
            raise XContentPlanError("helpful cards must use unique card IDs")
        raw_recent_hashes = snapshot.get("recent_helpful_content_hashes")
        if not isinstance(raw_recent_hashes, list):
            raise XContentPlanError("recent_helpful_content_hashes must be a list")
        recent_helpful_hashes = {str(item) for item in raw_recent_hashes}
        expected_pattern = HELPFUL_PATTERN_BY_WEEKDAY[plan_date.weekday()]
        matching = sorted(
            (
                item
                for item in cards
                if item.pattern_id == expected_pattern
                and _helpful_content_hash(item) not in recent_helpful_hashes
            ),
            key=lambda item: item.card_id,
        )
        if not matching:
            raise XContentPlanError("no non-duplicate reviewed helpful card for the scheduled pattern")
        selected = matching[0]
        helpful_content_hash = _helpful_content_hash(selected)
        plan = {
            "version": 1,
            "plan_date": plan_date.isoformat(),
            "content_kind": "helpful",
            "pattern_id": selected.pattern_id,
            "card": asdict(selected),
            "copy": render_helpful_copy(selected),
            "helpful_content_hash": helpful_content_hash,
            "release_gate": "reviewed_card_and_original_visual_ready",
            "requires_pr_hashtag": False,
            "requires_paid_partnership_label": False,
            "contains_room_link": False,
            "delivery": {
                "automatic_publish": False,
                "status": "official_x_connection_required",
            },
        }

    plan["idempotency_key"] = f"x-daily:{plan_date.isoformat()}"
    plan["plan_id"] = _plan_id(plan)
    return plan


def release_promotion(
    plan: Mapping[str, Any],
    evidence: Mapping[str, Any],
    *,
    durable_claim: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind a prepared promotional copy to exact posted ROOM evidence."""

    if not isinstance(plan, Mapping) or not isinstance(evidence, Mapping):
        raise XContentPlanError("plan and evidence must be objects")
    if not isinstance(durable_claim, Mapping):
        raise XContentPlanError("durable claim must be an object")
    _require_plan_integrity(plan)
    if plan.get("content_kind") != "promotion":
        raise XContentPlanError("release evidence is only valid for a promotional plan")
    claim_id = _text(durable_claim.get("claim_id"), "claim_id")
    if (
        durable_claim.get("state") != "claimed"
        or durable_claim.get("idempotency_key") != plan.get("idempotency_key")
        or durable_claim.get("plan_id") != plan.get("plan_id")
    ):
        raise XContentPlanError("an atomic durable claim for this plan is required")
    if evidence.get("status") != "posted":
        raise XContentPlanError("ROOM evidence must be posted")
    evidence_slot = str(evidence.get("slot", ""))
    evidence_product_id = str(evidence.get("product_id", ""))
    evidence_product_url = _https_url(evidence.get("product_url"), "evidence product_url")
    room_detail_product_url = _https_url(
        evidence.get("room_detail_product_url"),
        "room_detail_product_url",
    )
    posted_evidence_id = _text(evidence.get("posted_evidence_id"), "posted_evidence_id")
    room_detail_url = _https_url(evidence.get("room_detail_url"), "room_detail_url")
    parsed_room = urlparse(room_detail_url)
    if (
        parsed_room.netloc.lower() != "room.rakuten.co.jp"
        or parsed_room.params
        or parsed_room.query
        or parsed_room.fragment
        or not OWN_ROOM_DETAIL_PATTERN.fullmatch(parsed_room.path)
    ):
        raise XContentPlanError("room_detail_url must be the owned ROOM detail URL")

    binding = plan.get("binding")
    if not isinstance(binding, Mapping) or not (
        binding.get("slot") == evidence_slot
        and binding.get("product_id") == evidence_product_id
        and binding.get("product_url") == evidence_product_url
    ):
        raise XContentPlanError("ROOM evidence does not match a prepared binding")
    if room_detail_product_url != binding.get("product_url"):
        raise XContentPlanError("ROOM detail evidence does not match the prepared product")
    copy = f"{str(binding['promo_copy']).strip()}\n{room_detail_url}"
    if x_weighted_length(copy) > MAX_X_TEXT_LENGTH:
        raise XContentPlanError("released promotional copy exceeds the X text limit")
    release = {
        "version": 1,
        "plan_id": plan.get("plan_id"),
        "plan_date": plan.get("plan_date"),
        "content_kind": "promotion",
        "binding": binding,
        "copy": copy,
        "room_detail_url": room_detail_url,
        "room_detail_product_url": room_detail_product_url,
        "posted_evidence_id": posted_evidence_id,
        "idempotency_key": plan.get("idempotency_key"),
        "durable_claim_id": claim_id,
        "release_gate": "passed",
        "requires_paid_partnership_label": True,
        "delivery": {
            "automatic_publish": False,
            "status": "official_x_connection_required",
            "required_controls": ["paid_partnership_label"],
        },
    }
    release["release_id"] = _plan_id(release)
    return release
