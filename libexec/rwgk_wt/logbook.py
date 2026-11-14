"""Best-effort session associations for requests created through wt."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def record_created_request(url, cwd):
    session_id = os.environ.get("AGENT_LOGBOOK_SESSION_ID") or os.environ.get(
        "CODEX_THREAD_ID"
    )
    if not session_id:
        return
    try:
        from agent_logbook import context, resources, store

        cwd = str(Path(cwd).resolve())
        links = [
            {**item, "relation": "workspace"}
            for item in context.workspace(cwd).get("keys", [])
        ]
        links.append(
            {
                **resources.normalize(url, cwd=cwd),
                "display": url,
                "relation": "created",
            }
        )
        store.append_event(
            {
                "agent": "codex",
                "session_id": session_id,
                "cwd": cwd,
                "links": links,
            }
        )
    except Exception as error:  # noqa: BLE001 -- logging must not interrupt publication
        detail = " ".join(str(error).split())
        print(
            f"wt: warning: could not record created request in agent-logbook: {detail}",
            file=sys.stderr,
        )
