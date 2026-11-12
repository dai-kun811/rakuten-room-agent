"""Fail-closed, exact-once publisher for reviewed ROOM-linked X documents."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol

from room_x_api import DpapiTokenStore, XApiClient, XApiError, XPostResultUncertain
from room_x_content_planner import XContentPlanError, _plan_id, _require_plan_integrity


class PublisherClient(Protocol):
    def upload_image(self, access_token: str, path: Path) -> str: ...

    def create_post(
        self,
        access_token: str,
        *,
        text: str,
        paid_partnership: bool,
        media_ids: tuple[str, ...] = (),
    ) -> Mapping[str, Any]: ...


class XPublishError(RuntimeError):
    """A local release gate or durable-state failure."""


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _document_key(document: Mapping[str, Any]) -> str:
    key = str(document.get("idempotency_key", "")).strip()
    if not key.startswith("x-daily:"):
        raise XPublishError("document idempotency key is invalid")
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


def _validate_document(
    document: Mapping[str, Any], *, today: date, media_path: Path | None
) -> tuple[str, str, bool]:
    try:
        plan_date = date.fromisoformat(str(document.get("plan_date", "")))
    except ValueError as exc:
        raise XPublishError("document plan date is invalid") from exc
    if plan_date != today:
        raise XPublishError("only today's scheduled X document can be published")
    kind = str(document.get("content_kind", ""))
    text = str(document.get("copy", "")).strip()
    if not text:
        raise XPublishError("document copy is empty")
    if kind == "promotion":
        claimed_release_id = str(document.get("release_id", ""))
        release_payload = dict(document)
        release_payload.pop("release_id", None)
        if not claimed_release_id or _plan_id(release_payload) != claimed_release_id:
            raise XPublishError("promotion release integrity check failed")
        if document.get("release_gate") != "passed":
            raise XPublishError("promotion release evidence has not passed")
        binding = document.get("binding")
        if not isinstance(binding, Mapping):
            raise XPublishError("promotion release binding is missing")
        if not str(document.get("durable_claim_id", "")).strip() or not str(
            document.get("posted_evidence_id", "")
        ).strip():
            raise XPublishError("promotion release lacks durable claim or posted evidence")
        if document.get("room_detail_product_url") != binding.get("product_url"):
            raise XPublishError("promotion release product evidence does not match")
        if document.get("requires_paid_partnership_label") is not True or "#PR" not in text:
            raise XPublishError("promotion requires #PR and Paid Partnership disclosure")
        if media_path is not None:
            raise XPublishError("promotion does not accept an unreviewed media attachment")
        return kind, text, True
    if kind == "helpful":
        try:
            _require_plan_integrity(document)
        except XContentPlanError as exc:
            raise XPublishError(str(exc)) from exc
        card = document.get("card")
        if not isinstance(card, Mapping) or not (
            card.get("claims_reviewed") is True and card.get("original_visual_ready") is True
        ):
            raise XPublishError("helpful content is not reviewed and visual-ready")
        if media_path is None:
            raise XPublishError("helpful content requires its reviewed original visual")
        if "#PR" in text or "http://" in text or "https://" in text:
            raise XPublishError("helpful content cannot contain promotion or links")
        return kind, text, False
    raise XPublishError("document content kind is unsupported")


def publish_document(
    document: Mapping[str, Any],
    *,
    state_dir: Path,
    client: PublisherClient,
    access_token: str,
    media_path: Path | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    current_day = today or date.today()
    kind, text, paid_partnership = _validate_document(
        document, today=current_day, media_path=media_path
    )
    key = _document_key(document)
    root = Path(state_dir)
    claim_path = root / "claims" / f"{key}.json"
    receipt_path = root / "receipts" / f"{key}.json"
    uncertain_path = root / "uncertain" / f"{key}.json"
    if receipt_path.is_file():
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        return {"status": "already_posted", "post_id": receipt.get("post_id", "")}
    if uncertain_path.is_file():
        raise XPublishError("a prior X Post result is uncertain; user verification is required")
    claim_path.parent.mkdir(parents=True, exist_ok=True)
    claim = {
        "version": 1,
        "state": "claimed",
        "plan_date": str(document.get("plan_date")),
        "idempotency_key": str(document.get("idempotency_key")),
        "document_id": str(document.get("release_id") or document.get("plan_id") or ""),
        "content_kind": kind,
        "claimed_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        descriptor = os.open(claim_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError as exc:
        raise XPublishError("an X publication claim already exists; automatic duplicate is stopped") from exc
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(claim, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")

    media_ids: tuple[str, ...] = ()
    try:
        if kind == "helpful":
            media_id = client.upload_image(access_token, Path(media_path))
            media_ids = (media_id,)
            claim["state"] = "media_uploaded"
            claim["media_id"] = media_id
            _write_json(claim_path, claim)
        claim["state"] = "submitting"
        claim["submit_started_at"] = datetime.now(timezone.utc).isoformat()
        _write_json(claim_path, claim)
        result = client.create_post(
            access_token,
            text=text,
            paid_partnership=paid_partnership,
            media_ids=media_ids,
        )
    except XPostResultUncertain as exc:
        claim["state"] = "uncertain"
        claim["error"] = str(exc)
        _write_json(uncertain_path, claim)
        _write_json(claim_path, claim)
        raise XPublishError(str(exc)) from exc
    except (XApiError, OSError) as exc:
        claim["state"] = "failed_pre_submit_or_rejected"
        claim["error"] = str(exc)
        _write_json(claim_path, claim)
        raise XPublishError(str(exc)) from exc

    post_id = str(result.get("id", "")).strip()
    if not post_id:
        claim["state"] = "uncertain"
        claim["error"] = "X result did not contain a Post ID"
        _write_json(uncertain_path, claim)
        _write_json(claim_path, claim)
        raise XPublishError("X result did not contain a Post ID; automatic retry is stopped")
    receipt = {
        "version": 1,
        "status": "posted",
        "post_id": post_id,
        "post_url": f"https://x.com/i/web/status/{post_id}",
        "plan_date": str(document.get("plan_date")),
        "idempotency_key": str(document.get("idempotency_key")),
        "document_id": claim["document_id"],
        "content_kind": kind,
        "paid_partnership": paid_partnership,
        "posted_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_json(receipt_path, receipt)
    claim["state"] = "posted"
    claim["post_id"] = post_id
    _write_json(claim_path, claim)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish one reviewed X document via the official API")
    parser.add_argument("--document", type=Path, required=True)
    parser.add_argument("--media", type=Path)
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=Path(".local/room-worker/x-publisher"),
    )
    parser.add_argument(
        "--token-store",
        type=Path,
        default=Path(".local/room-worker/x-publisher/oauth-token.dpapi"),
    )
    args = parser.parse_args()
    try:
        document = json.loads(args.document.read_text(encoding="utf-8"))
        store = DpapiTokenStore(args.token_store)
        bundle = store.load()
        client = XApiClient()
        refreshed = client.refresh_token(
            bundle,
            client_id=os.environ.get("X_OAUTH_CLIENT_ID", ""),
            client_secret=os.environ.get("X_OAUTH_CLIENT_SECRET", ""),
        )
        if refreshed != bundle:
            store.save(refreshed)
        result = publish_document(
            document,
            state_dir=args.state_dir,
            client=client,
            access_token=refreshed.access_token,
            media_path=args.media,
        )
    except (OSError, json.JSONDecodeError, XApiError, XPublishError) as exc:
        print(json.dumps({"status": "stopped", "reason": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
