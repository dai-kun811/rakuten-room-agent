from __future__ import annotations

import base64
import binascii
import json
import logging
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from sheets import normalize_product_url


LOGGER = logging.getLogger("rakuten-room-agent")


class RoomPostError(RuntimeError):
    pass


@dataclass(frozen=True)
class RoomPostResult:
    product_url: str
    status: str
    detail: str = ""


ROOM_TRIGGER_SELECTORS = (
    '[irc="RoomShareButton"]',
    'a[href*="room.rakuten.co.jp"][href*="post"]',
    'a[href*="room.rakuten.co.jp"][href*="collect"]',
    '[aria-label*="ROOM"]',
    'button:has-text("ROOM")',
)
COMMENT_SELECTORS = (
    'textarea[name*="comment"]',
    'textarea[placeholder*="コメント"]',
    'textarea',
)
SUBMIT_SELECTORS = (
    'button:has-text("投稿する")',
    'input[type="submit"][value*="投稿"]',
    'button[type="submit"]:has-text("投稿")',
)
SUCCESS_PATTERN = re.compile(r"投稿しました|投稿が完了|投稿完了")


def decode_storage_state(encoded: str) -> dict[str, Any]:
    if not encoded or not encoded.strip():
        raise RoomPostError("ROOM_AUTH_STATE_B64 が未設定です。")
    try:
        raw = base64.b64decode(encoded.strip(), validate=True)
        state = json.loads(raw.decode("utf-8"))
    except (ValueError, binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RoomPostError("ROOM_AUTH_STATE_B64 の形式が不正です。") from exc
    if not isinstance(state, dict) or not isinstance(state.get("cookies"), list):
        raise RoomPostError("ROOM_AUTH_STATE_B64 に有効なブラウザ認証状態がありません。")
    return state


def build_room_comment(body: str, hashtags: Iterable[str]) -> str:
    clean_body = body.strip()
    clean_tags = " ".join(tag.strip() for tag in hashtags if tag.strip())
    if not clean_body:
        raise RoomPostError("投稿本文が空です。")
    return f"{clean_body}\n\n{clean_tags}" if clean_tags else clean_body


def post_ready_items(
    report_items: list[Any],
    *,
    sheets_client: Any,
    run_id: str,
    executed_at: datetime,
    log_sheet_name: str,
    auth_state_b64: str,
    headless: bool,
) -> list[RoomPostResult]:
    ready_items = [item for item in report_items if item.generated.status == "ready"]
    if not ready_items:
        LOGGER.info("ROOM自動投稿対象なし run_id=%s", run_id)
        return []

    sheets_client.ensure_room_post_log(log_sheet_name)
    reserved_urls = sheets_client.read_reserved_room_urls(log_sheet_name)
    poster = RoomPoster(decode_storage_state(auth_state_b64), headless=headless)
    results: list[RoomPostResult] = []
    timestamp = executed_at.isoformat()

    for item in ready_items:
        product = item.scored.product
        normalized_url = normalize_product_url(product.url)
        if normalized_url in reserved_urls:
            LOGGER.info("ROOM自動投稿スキップ run_id=%s url=%s reason=reserved", run_id, normalized_url)
            results.append(
                RoomPostResult(
                    product_url=normalized_url,
                    status="skipped",
                    detail="投稿ログに予約済みURLがあります。",
                )
            )
            continue

        sheets_client.append_room_post_event(
            log_sheet_name,
            executed_at=timestamp,
            run_id=run_id,
            normalized_url=normalized_url,
            status="reserved",
            detail="投稿前予約",
            product_name=product.name,
        )
        reserved_urls.add(normalized_url)
        try:
            comment = build_room_comment(item.generated.body, item.generated.hashtags)
            poster.post(product.url, comment)
            sheets_client.append_room_post_event(
                log_sheet_name,
                executed_at=timestamp,
                run_id=run_id,
                normalized_url=normalized_url,
                status="posted",
                detail="ROOM投稿完了",
                product_name=product.name,
            )
            LOGGER.info("ROOM自動投稿完了 run_id=%s url=%s", run_id, normalized_url)
            results.append(RoomPostResult(product_url=normalized_url, status="posted"))
        except Exception as exc:
            detail = str(exc) if isinstance(exc, RoomPostError) else type(exc).__name__
            try:
                sheets_client.append_room_post_event(
                    log_sheet_name,
                    executed_at=timestamp,
                    run_id=run_id,
                    normalized_url=normalized_url,
                    status="failed",
                    detail=detail,
                    product_name=product.name,
                )
            except Exception:
                LOGGER.error("ROOM投稿失敗ログの追記にも失敗しました run_id=%s url=%s", run_id, normalized_url)
            LOGGER.error(
                "ROOM自動投稿失敗 run_id=%s url=%s error=%s",
                run_id,
                normalized_url,
                detail,
            )
            results.append(
                RoomPostResult(
                    product_url=normalized_url,
                    status="failed",
                    detail=detail,
                )
            )
    return results


class RoomPoster:
    def __init__(
        self,
        storage_state: dict[str, Any],
        *,
        headless: bool = True,
        timeout_ms: int = 30_000,
    ) -> None:
        self.storage_state = storage_state
        self.headless = headless
        self.timeout_ms = timeout_ms

    def post(self, product_url: str, comment: str) -> RoomPostResult:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=self.headless)
            context = browser.new_context(storage_state=self.storage_state)
            page = context.new_page()
            page.set_default_timeout(self.timeout_ms)
            try:
                page.goto(product_url, wait_until="domcontentloaded")
                self._assert_authenticated(page)
                trigger = self._first_visible(page, ROOM_TRIGGER_SELECTORS)
                if trigger is None:
                    raise RoomPostError("楽天商品ページにROOM投稿ボタンが見つかりません。")

                pages_before = len(context.pages)
                trigger.click()
                page.wait_for_timeout(1_000)
                target = context.pages[-1] if len(context.pages) > pages_before else page
                target.set_default_timeout(self.timeout_ms)
                target.wait_for_load_state("domcontentloaded")
                self._assert_authenticated(target)

                textarea = self._first_visible(target, COMMENT_SELECTORS)
                if textarea is None:
                    raise RoomPostError("ROOM投稿画面にコメント入力欄が見つかりません。")
                textarea.fill(comment)

                submit = self._first_visible(target, SUBMIT_SELECTORS)
                if submit is None:
                    raise RoomPostError("ROOM投稿画面に投稿ボタンが見つかりません。")
                form_url = target.url
                submit.click()

                try:
                    target.get_by_text(SUCCESS_PATTERN).first.wait_for(state="visible")
                except PlaywrightTimeoutError:
                    if target.is_closed():
                        return RoomPostResult(product_url=product_url, status="posted")
                    textarea_still_visible = self._first_visible(target, COMMENT_SELECTORS) is not None
                    if target.url == form_url or textarea_still_visible:
                        raise RoomPostError("投稿後の完了表示を確認できませんでした。")
                return RoomPostResult(product_url=product_url, status="posted")
            finally:
                context.close()
                browser.close()

    @staticmethod
    def _first_visible(page: Any, selectors: Iterable[str]) -> Any | None:
        for selector in selectors:
            locator = page.locator(selector).first
            try:
                locator.wait_for(state="visible", timeout=5_000)
                return locator
            except Exception:
                continue
        return None

    @staticmethod
    def _assert_authenticated(page: Any) -> None:
        url = page.url.lower()
        if "login" in url or "signin" in url:
            raise RoomPostError("楽天ROOMのログイン状態が期限切れです。")
        password = page.locator('input[type="password"]').first
        try:
            if password.is_visible():
                raise RoomPostError("楽天ROOMのログイン状態が期限切れです。")
        except RoomPostError:
            raise
        except Exception:
            return


def write_room_post_report(
    output_dir: Path,
    *,
    run_id: str,
    executed_at: datetime,
    results: list[RoomPostResult],
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "room_post_report.json"
    payload = {
        "run_id": run_id,
        "executed_at": executed_at.isoformat(),
        "posted": sum(result.status == "posted" for result in results),
        "failed": sum(result.status == "failed" for result in results),
        "skipped": sum(result.status == "skipped" for result in results),
        "results": [asdict(result) for result in results],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
