from __future__ import annotations

import io
import json
import logging
import os
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from room_poster import RoomPostError, RoomPoster, build_room_comment
from sheets import normalize_product_url


JST_OFFSET = "+09:00"
REPO_API = "https://api.github.com/repos/dai-kun811/rakuten-room-agent"
DEFAULT_GIT = Path(
    r"C:\Users\daiku\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe"
)
STATE_DIR = Path.home() / ".rakuten-room"
AUTH_STATE_PATH = STATE_DIR / "storage-state.json"
LEDGER_PATH = STATE_DIR / "post-ledger.jsonl"
LOG_PATH = STATE_DIR / "worker.log"


def github_token() -> str:
    git = str(DEFAULT_GIT) if DEFAULT_GIT.exists() else "git"
    completed = subprocess.run(
        [git, "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        text=True,
        capture_output=True,
        check=True,
    )
    fields = dict(
        line.split("=", 1)
        for line in completed.stdout.splitlines()
        if "=" in line
    )
    token = fields.get("password", "")
    if not token:
        raise RuntimeError("GitHub credential is unavailable.")
    return token


def github_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Rakuten-ROOM-local-poster",
    }


def fetch_latest_generation_report(
    session: Any,
    *,
    headers: dict[str, str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    runs_response = session.get(
        f"{REPO_API}/actions/workflows/daily.yml/runs",
        headers=headers,
        params={"status": "success", "per_page": 10},
        timeout=30,
    )
    runs_response.raise_for_status()
    runs = [
        run
        for run in runs_response.json().get("workflow_runs", [])
        if run.get("conclusion") == "success"
    ]
    if not runs:
        raise RuntimeError("Successful daily workflow run was not found.")

    for run in runs:
        artifacts_response = session.get(
            f"{REPO_API}/actions/runs/{run['id']}/artifacts",
            headers=headers,
            params={"per_page": 100},
            timeout=30,
        )
        artifacts_response.raise_for_status()
        artifact = next(
            (
                item
                for item in artifacts_response.json().get("artifacts", [])
                if item.get("name") == "room-generation-report" and not item.get("expired")
            ),
            None,
        )
        if artifact is None:
            continue
        archive_response = session.get(
            artifact["archive_download_url"],
            headers=headers,
            timeout=60,
        )
        archive_response.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(archive_response.content)) as archive:
            report_name = next(
                name
                for name in archive.namelist()
                if name.endswith("room_generation_report.json")
            )
            report = json.loads(archive.read(report_name).decode("utf-8"))
        return run, report
    raise RuntimeError("room-generation-report artifact was not found.")


def ready_items(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item
        for item in report.get("items", [])
        if item.get("status") == "ready" and item.get("product_url") and item.get("body")
    ]


def load_reserved_urls(path: Path = LEDGER_PATH) -> set[str]:
    if not path.exists():
        return set()
    reserved: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        url = normalize_product_url(str(event.get("normalized_url", "")))
        if url:
            reserved.add(url)
    return reserved


def append_ledger_event(event: dict[str, Any], path: Path = LEDGER_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def configure_logging() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler()],
    )


def main() -> int:
    configure_logging()
    logger = logging.getLogger("local-room-worker")
    if not AUTH_STATE_PATH.exists():
        logger.error("ROOM authentication state is missing.")
        return 1

    try:
        storage_state = json.loads(AUTH_STATE_PATH.read_text(encoding="utf-8"))
        token = github_token()
        import requests

        with requests.Session() as session:
            run, report = fetch_latest_generation_report(
                session,
                headers=github_headers(token),
            )
        items = ready_items(report)
        reserved_urls = load_reserved_urls()
        candidates = [
            item
            for item in items
            if normalize_product_url(item["product_url"]) not in reserved_urls
        ]
        logger.info(
            "Latest run=%s report_run_id=%s ready=%s new=%s",
            run["id"],
            report.get("run_id", ""),
            len(items),
            len(candidates),
        )
        if not candidates:
            return 0

        poster = RoomPoster(storage_state, headless=True)
        failures = 0
        for item in candidates:
            normalized_url = normalize_product_url(item["product_url"])
            base_event = {
                "timestamp": datetime.now().astimezone().isoformat(),
                "actions_run_id": run["id"],
                "report_run_id": report.get("run_id", ""),
                "normalized_url": normalized_url,
                "product_name": str(item.get("product_name", ""))[:200],
            }
            append_ledger_event({**base_event, "status": "reserved"})
            try:
                comment = build_room_comment(item["body"], item.get("hashtags", []))
                poster.post(item["product_url"], comment)
                append_ledger_event({**base_event, "status": "posted"})
                logger.info("ROOM post completed url=%s", normalized_url)
            except Exception as exc:
                detail = str(exc) if isinstance(exc, RoomPostError) else type(exc).__name__
                append_ledger_event({**base_event, "status": "failed", "detail": detail})
                logger.error("ROOM post failed url=%s error=%s", normalized_url, detail)
                failures += 1
        return 1 if failures else 0
    except Exception as exc:
        logger.error("Local ROOM worker failed error=%s", type(exc).__name__)
        return 1


if __name__ == "__main__":
    sys.exit(main())
