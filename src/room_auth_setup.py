from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path


ROOM_URL = "https://room.rakuten.co.jp/"


def find_chrome() -> Path:
    candidates = (
        Path(os.environ.get("PROGRAMFILES", ""))
        / "Google"
        / "Chrome"
        / "Application"
        / "chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", ""))
        / "Google"
        / "Chrome"
        / "Application"
        / "chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Google"
        / "Chrome"
        / "Application"
        / "chrome.exe",
    )
    chrome = next((candidate for candidate in candidates if candidate.is_file()), None)
    if chrome is None:
        raise RuntimeError("Google Chromeが見つかりません。")
    return chrome


def main() -> int:
    parser = argparse.ArgumentParser(
        description="通常のGoogle Chromeで楽天ROOMへ手動ログインします。"
    )
    parser.add_argument(
        "--profile-dir",
        default=str(Path.home() / ".rakuten-room" / "chrome-profile"),
    )
    args = parser.parse_args()
    profile_dir = Path(args.profile_dir).expanduser().resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)

    print("開いた通常のChromeで楽天ROOMへログインしてください。")
    print("my ROOMが表示されたら、この専用Chromeをすべて閉じてください。")
    subprocess.run(
        [
            str(find_chrome()),
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            ROOM_URL,
        ],
        check=True,
    )

    if not (profile_dir / "Default" / "Cookies").exists():
        raise RuntimeError("Chromeプロファイルを確認できません。")

    print(f"ブラウザプロファイルを保存しました: {profile_dir}")
    print("投稿なしの認証確認には room_auth_probe.py を実行してください。")
    print("このフォルダは機密情報です。リポジトリへ追加しないでください。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
