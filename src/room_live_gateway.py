from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from room_orchestrator import (
    AccountRestricted,
    AuthenticationRequired,
    CaptchaRequired,
    Confirmation,
)
from room_poster import RoomPostError, RoomPoster, build_room_comment


OWN_ROOM_URL = "https://room.rakuten.co.jp/tora_papa/items"
CAPTCHA_PATTERN = re.compile(r"captcha|ロボットではありません|画像認証", re.IGNORECASE)
AUTH_PATTERN = re.compile(r"ログイン状態|login|sign[ -]?in", re.IGNORECASE)
RESTRICTED_PATTERN = re.compile(r"利用制限|投稿できません|アカウント.*制限", re.IGNORECASE)


class Poster(Protocol):
    def post(
        self,
        product_url: str,
        comment: str,
        *,
        before_submit: Callable[[], None] | None = None,
        after_submit: Callable[[], None] | None = None,
    ) -> Any: ...


class LiveVerifier(Protocol):
    def __call__(self, body: str) -> bool: ...


class RoomLiveGateway:
    """Production adapter. Only authenticated ROOM display can confirm POSTED."""

    def __init__(self, poster: Poster, verifier: LiveVerifier) -> None:
        self.poster = poster
        self.verifier = verifier

    def submit(
        self,
        candidate: Mapping[str, Any],
        *,
        before_submit: Callable[[], None],
        after_submit: Callable[[], None],
    ) -> Confirmation:
        body = str(candidate.get("body", "")).strip()
        product_url = str(candidate.get("normalized_url", "")).strip()
        hashtags = candidate.get("hashtags", [])
        if not body or not product_url or not isinstance(hashtags, list):
            raise RoomPostError("manifest候補の投稿必須項目が不足しています。")
        comment = build_room_comment(body, [str(tag) for tag in hashtags])
        try:
            self.poster.post(
                product_url,
                comment,
                before_submit=before_submit,
                after_submit=after_submit,
            )
        except RoomPostError as exc:
            raise classify_room_error(exc) from exc
        # Verify the exact submitted comment, including hashtags.  A body-only
        # match can collide with an older post whose tags or source item differ.
        return Confirmation.PRESENT if self.verifier(comment) else Confirmation.UNKNOWN


class AuthenticatedRoomVerifier:
    """Bounded read-only verification against the account's latest ROOM items."""

    def __init__(
        self,
        *,
        user_data_dir: Path | str,
        headless: bool = True,
        attempts: int = 3,
        timeout_ms: int = 30_000,
    ) -> None:
        if attempts < 1 or attempts > 3:
            raise ValueError("verification attempts must be between 1 and 3")
        self.user_data_dir = Path(user_data_dir).expanduser().resolve()
        self.headless = headless
        self.attempts = attempts
        self.timeout_ms = timeout_ms

    def __call__(self, comment: str) -> bool:
        from playwright.sync_api import sync_playwright

        expected = normalize_visible_text(comment)
        if len(expected) < 20:
            raise RoomPostError("実ROOM確認に使う本文が短すぎます。")
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                str(self.user_data_dir), channel="chrome", headless=self.headless
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.set_default_timeout(self.timeout_ms)
                for attempt in range(self.attempts):
                    page.goto(
                        OWN_ROOM_URL,
                        wait_until="commit",
                        timeout=max(60_000, self.timeout_ms * 2),
                    )
                    page.wait_for_timeout(3_000 + attempt * 2_000)
                    RoomPoster._assert_authenticated(page)
                    visible = normalize_visible_text(page.locator("body").inner_text())
                    if CAPTCHA_PATTERN.search(visible):
                        raise CaptchaRequired("ROOM表示確認でCAPTCHAを検出しました。")
                    if expected in visible:
                        return True
                return False
            finally:
                context.close()


def classify_room_error(error: RoomPostError) -> Exception:
    message = str(error)
    if CAPTCHA_PATTERN.search(message):
        return CaptchaRequired(message)
    if AUTH_PATTERN.search(message):
        return AuthenticationRequired(message)
    if RESTRICTED_PATTERN.search(message):
        return AccountRestricted(message)
    return error


def normalize_visible_text(value: str) -> str:
    return re.sub(r"\s+", "", value or "")
