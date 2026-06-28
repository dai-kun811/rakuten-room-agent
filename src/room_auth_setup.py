from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="楽天ROOMへ手動ログインし、Playwright認証状態をローカル保存します。"
    )
    parser.add_argument(
        "--output",
        default=str(Path.home() / ".rakuten-room" / "storage-state.json"),
    )
    args = parser.parse_args()
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto("https://room.rakuten.co.jp/", wait_until="domcontentloaded")
        print("開いたブラウザで楽天ROOMへログインしてください。")
        input("my ROOMが表示されたら Enter を押してください: ")
        if "login" in page.url.lower() or "signin" in page.url.lower():
            raise RuntimeError("ログイン完了を確認できません。認証状態は保存しません。")
        state = context.storage_state()
        if not state.get("cookies"):
            raise RuntimeError("ログインCookieを確認できません。認証状態は保存しません。")
        output.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        context.close()
        browser.close()

    encoded_path = output.with_suffix(".b64")
    encoded_path.write_text(
        base64.b64encode(output.read_bytes()).decode("ascii"),
        encoding="ascii",
    )
    print(f"認証状態を保存しました: {output}")
    print(f"GitHub Secret登録用ファイル: {encoded_path}")
    print("両ファイルは機密情報です。リポジトリへ追加しないでください。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
