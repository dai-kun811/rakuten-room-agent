from __future__ import annotations

import argparse
from pathlib import Path

from room_poster import (
    COMMENT_SELECTORS,
    ROOM_TRIGGER_SELECTORS,
    SUBMIT_SELECTORS,
    RoomPostError,
    RoomPoster,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify the local Rakuten ROOM session without posting."
    )
    parser.add_argument("product_url")
    parser.add_argument(
        "--profile-dir",
        default=str(Path.home() / ".rakuten-room" / "chrome-profile"),
    )
    args = parser.parse_args()

    from playwright.sync_api import sync_playwright

    profile_dir = Path(args.profile_dir).expanduser().resolve()
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(profile_dir),
            channel="chrome",
            headless=True,
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.set_default_timeout(30_000)
        try:
            page.goto(args.product_url, wait_until="domcontentloaded")
            RoomPoster._assert_authenticated(page)
            trigger = RoomPoster._first_visible(page, ROOM_TRIGGER_SELECTORS)
            if trigger is None:
                raise RoomPostError("ROOM share button was not found.")
            trigger_href = trigger.get_attribute("href")
            if trigger_href:
                page.goto(trigger_href, wait_until="domcontentloaded")
                target = page
            else:
                pages_before = len(context.pages)
                trigger.click(force=True)
                page.wait_for_timeout(1_000)
                target = (
                    context.pages[-1]
                    if len(context.pages) > pages_before
                    else page
                )
            target.set_default_timeout(30_000)
            target.wait_for_load_state("domcontentloaded")
            target.wait_for_timeout(3_000)
            RoomPoster._assert_authenticated(target)

            if RoomPoster._first_visible(target, COMMENT_SELECTORS) is None:
                raise RoomPostError("ROOM comment field was not found.")
            if RoomPoster._first_visible(target, SUBMIT_SELECTORS) is None:
                raise RoomPostError("ROOM submit button was not found.")
            print(f"AUTH_PROBE_OK url={target.url}")
            return 0
        finally:
            context.close()


if __name__ == "__main__":
    raise SystemExit(main())
