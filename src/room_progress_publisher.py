from __future__ import annotations

import base64
import json
from datetime import datetime
from typing import Any

from local_room_worker import REPO_API, github_headers, github_token


STATE_BRANCH = "routine-state"
STATE_PATH = "automation-progress.json"


def build_public_payload(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "routine_date": str(summary.get("routine_date", "")),
        "followed": int(summary.get("followed", 0)),
        "liked": int(summary.get("liked", 0)),
        "goal": int(summary.get("goal", 50)),
        "attempted": int(summary.get("attempted", 0)),
        "failures": int(summary.get("failures", 0)),
        "completed": bool(summary.get("completed", False)),
        "updated_at": datetime.now().astimezone().isoformat(),
    }


def ensure_state_branch(session: Any, *, headers: dict[str, str]) -> None:
    ref_url = f"{REPO_API}/git/ref/heads/{STATE_BRANCH}"
    response = session.get(ref_url, headers=headers, timeout=30)
    if response.status_code == 200:
        return
    if response.status_code != 404:
        response.raise_for_status()

    main_response = session.get(
        f"{REPO_API}/branches/main",
        headers=headers,
        timeout=30,
    )
    main_response.raise_for_status()
    create_response = session.post(
        f"{REPO_API}/git/refs",
        headers=headers,
        json={
            "ref": f"refs/heads/{STATE_BRANCH}",
            "sha": main_response.json()["commit"]["sha"],
        },
        timeout=30,
    )
    if create_response.status_code not in {201, 422}:
        create_response.raise_for_status()


def publish_progress(
    summary: dict[str, Any],
    *,
    session: Any | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    owns_session = session is None
    if session is None:
        import requests

        session = requests.Session()
    resolved_headers = headers or github_headers(github_token())
    try:
        ensure_state_branch(session, headers=resolved_headers)
        content_url = f"{REPO_API}/contents/{STATE_PATH}"
        current = session.get(
            content_url,
            headers=resolved_headers,
            params={"ref": STATE_BRANCH},
            timeout=30,
        )
        current_sha = ""
        if current.status_code == 200:
            current_sha = str(current.json().get("sha", ""))
        elif current.status_code != 404:
            current.raise_for_status()

        payload = build_public_payload(summary)
        body: dict[str, Any] = {
            "message": f"Update ROOM routine progress for {payload['routine_date']}",
            "content": base64.b64encode(
                (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            ).decode("ascii"),
            "branch": STATE_BRANCH,
        }
        if current_sha:
            body["sha"] = current_sha
        update = session.put(
            content_url,
            headers=resolved_headers,
            json=body,
            timeout=30,
        )
        update.raise_for_status()
        return payload
    finally:
        if owns_session:
            session.close()
