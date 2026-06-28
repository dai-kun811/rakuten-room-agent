from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable


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


def build_room_comment(body: str, hashtags: Iterable[str]) -> str:
    clean_body = body.strip()
    clean_tags = " ".join(tag.strip() for tag in hashtags if tag.strip())
    if not clean_body:
        raise RoomPostError("投稿本文が空です。")
    return f"{clean_body}\n\n{clean_tags}" if clean_tags else clean_body


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
