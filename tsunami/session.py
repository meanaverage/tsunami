"""Session persistence — save and restore agent state.

Memory is external. The file system is long-term memory.
Sessions are saved as JSONL in .history/ — one line per message.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .state import AgentState, Message, Plan


def save_session(state: AgentState, session_dir: Path, session_id: str = "latest"):
    """Save the full conversation to a JSONL file."""
    session_dir.mkdir(parents=True, exist_ok=True)
    path = session_dir / f"{session_id}.jsonl"

    with open(path, "w") as f:
        # Header with metadata
        meta = {
            "_meta": True,
            "iteration": state.iteration,
            "task_complete": state.task_complete,
            "error_counts": state.error_counts,
            "plan": state.plan.to_dict() if state.plan else None,
            "timestamp": time.time(),
        }
        f.write(json.dumps(meta) + "\n")

        # Messages
        for m in state.conversation:
            record = {
                "role": m.role,
                "content": m.content,
                "tool_call": m.tool_call,
                "timestamp": m.timestamp,
            }
            f.write(json.dumps(record) + "\n")

    return path


def load_session(session_dir: Path, session_id: str = "latest") -> AgentState | None:
    """Load a conversation from a JSONL file."""
    path = session_dir / f"{session_id}.jsonl"
    if not path.exists():
        return None

    state = AgentState()
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)

            if record.get("_meta"):
                state.iteration = record.get("iteration", 0)
                state.task_complete = record.get("task_complete", False)
                state.error_counts = record.get("error_counts", {})
                plan_data = record.get("plan")
                if plan_data:
                    state.plan = Plan.from_dict(plan_data)
                continue

            state.conversation.append(Message(
                role=record["role"],
                content=record["content"],
                tool_call=record.get("tool_call"),
                timestamp=record.get("timestamp", 0),
            ))

    return state


def list_sessions(session_dir: Path) -> list[dict]:
    """List all saved sessions with metadata."""
    sessions = []
    if not session_dir.exists():
        return sessions

    for path in sorted(session_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            with open(path) as f:
                first_line = f.readline()
                meta = json.loads(first_line)
                if meta.get("_meta"):
                    sessions.append({
                        "id": path.stem,
                        "path": str(path),
                        "iteration": meta.get("iteration", 0),
                        "complete": meta.get("task_complete", False),
                        "timestamp": meta.get("timestamp", 0),
                    })
        except (json.JSONDecodeError, KeyError):
            continue

    return sessions
