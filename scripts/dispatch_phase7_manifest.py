from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import requests

from local_room_worker import REPO_API, github_headers, github_token


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Dispatch one isolated manifest run and restore disabled workflow state."
    )
    parser.add_argument("--ref", required=True)
    args = parser.parse_args()
    headers = github_headers(github_token())
    workflow_url = f"{REPO_API}/actions/workflows/daily.yml"
    dispatch_url = f"{workflow_url}/dispatches"
    with requests.Session() as session:
        state_response = session.get(workflow_url, headers=headers, timeout=30)
        state_response.raise_for_status()
        original_state = str(state_response.json().get("state", ""))
        if original_state not in {"active", "disabled_manually"}:
            raise RuntimeError(f"unsupported workflow state: {original_state}")
        enabled_temporarily = original_state == "disabled_manually"
        try:
            if enabled_temporarily:
                response = session.put(f"{workflow_url}/enable", headers=headers, timeout=30)
                response.raise_for_status()
            response = session.post(
                dispatch_url,
                headers=headers,
                json={"ref": args.ref},
                timeout=30,
            )
            response.raise_for_status()
            print("dispatch=accepted")
        finally:
            if enabled_temporarily:
                response = session.put(f"{workflow_url}/disable", headers=headers, timeout=30)
                response.raise_for_status()
        final = session.get(workflow_url, headers=headers, timeout=30)
        final.raise_for_status()
        final_state = str(final.json().get("state", ""))
        print(f"final_state={final_state}")
        if final_state != original_state:
            raise RuntimeError("workflow state was not restored")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
