"""Persistent chat session storage as JSON files."""

from __future__ import annotations

import json
import os
import re
import uuid
import logging
from datetime import datetime, timezone

# Valid session IDs: UUID format or tg-<digits>/wa-<hex> prefix (Phase 3)
_SESSION_ID_RE = re.compile(r"^(tg-[0-9]+|wa-[a-f0-9]+|[a-f0-9-]+)$")

logger = logging.getLogger(__name__)


class ChatStore:
    """Reads/writes chat sessions as individual JSON files."""

    def __init__(self, store_dir: str) -> None:
        self.store_dir = store_dir
        os.makedirs(store_dir, exist_ok=True)

    def _session_path(self, session_id: str) -> str:
        if not _SESSION_ID_RE.match(session_id):
            raise ValueError(f"Invalid session ID format: {session_id}")
        return os.path.join(self.store_dir, f"{session_id}.json")

    def _read(self, session_id: str) -> dict | None:
        path = self._session_path(session_id)
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write(self, session: dict) -> None:
        path = self._session_path(session["id"])
        with open(path, "w", encoding="utf-8") as f:
            json.dump(session, f, indent=2, ensure_ascii=False)

    def list_sessions(self) -> list[dict]:
        """Return summary list sorted by updated descending."""
        sessions = []
        for filename in os.listdir(self.store_dir):
            if not filename.endswith(".json"):
                continue
            try:
                with open(os.path.join(self.store_dir, filename), "r", encoding="utf-8") as f:
                    data = json.load(f)
                sessions.append({
                    "id": data["id"],
                    "title": data.get("title", "Untitled"),
                    "created": data["created"],
                    "updated": data["updated"],
                    "message_count": len(data.get("messages", [])),
                })
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning(f"Skipping corrupt session file {filename}: {e}")
        sessions.sort(key=lambda s: s["updated"], reverse=True)
        return sessions

    def create_session(self) -> dict:
        """Create a new empty session. Returns the full session object."""
        now = datetime.now(timezone.utc).isoformat()
        session = {
            "id": str(uuid.uuid4()),
            "title": "New Session",
            "created": now,
            "updated": now,
            "messages": [],
        }
        self._write(session)
        return session

    def get_or_create_session(self, session_id: str) -> dict:
        """Get existing session or create one with the given ID (for tg-*/wa-* sessions)."""
        session = self._read(session_id)
        if session is not None:
            return session
        now = datetime.now(timezone.utc).isoformat()
        session = {
            "id": session_id,
            "title": "New Session",
            "created": now,
            "updated": now,
            "messages": [],
        }
        self._write(session)
        return session

    def get_session(self, session_id: str) -> dict | None:
        """Return full session with messages, or None if not found."""
        return self._read(session_id)

    def message_count(self, session_id: str) -> int:
        """Return the number of messages in a session."""
        session = self._read(session_id)
        if session is None:
            return 0
        return len(session.get("messages", []))

    def append_message(self, session_id: str, message: dict) -> bool:
        """Append a message to a session. Returns False if session not found."""
        session = self._read(session_id)
        if session is None:
            return False
        message.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
        session["messages"].append(message)
        session["updated"] = datetime.now(timezone.utc).isoformat()

        # Auto-title from first user message
        if session["title"] == "New Session" and message.get("role") == "user":
            title = message["content"].strip()
            session["title"] = title[:60] + ("..." if len(title) > 60 else "")

        self._write(session)
        return True

    def rename_session(self, session_id: str, title: str) -> bool:
        """Rename a session. Returns False if not found."""
        session = self._read(session_id)
        if session is None:
            return False
        session["title"] = title
        self._write(session)
        return True

    def delete_session(self, session_id: str) -> bool:
        """Delete a session file. Returns False if not found."""
        path = self._session_path(session_id)
        if not os.path.exists(path):
            return False
        os.remove(path)
        return True

    def dismiss_update(self, session_id: str, update_key: str) -> bool:
        """Mark a graph update as dismissed. Returns False if session not found."""
        session = self._read(session_id)
        if session is None:
            return False
        dismissed = session.setdefault("dismissed_updates", [])
        if update_key not in dismissed:
            dismissed.append(update_key)
        self._write(session)
        return True

    def undismiss_update(self, session_id: str, update_key: str) -> bool:
        """Remove a node from the dismissed list (e.g. when confirmed via different path)."""
        session = self._read(session_id)
        if session is None:
            return False
        dismissed = session.get("dismissed_updates", [])
        if update_key in dismissed:
            dismissed.remove(update_key)
            session["dismissed_updates"] = dismissed
            self._write(session)
        return True

    def get_messages_for_api(self, session_id: str) -> list[dict]:
        """Return messages formatted for the Claude API (role + content only)."""
        session = self._read(session_id)
        if session is None:
            return []
        return [
            {"role": m["role"], "content": m["content"]}
            for m in session.get("messages", [])
        ]

    def set_pending_updates(self, session_id: str, updates: list[dict]) -> bool:
        """Store pending graph updates on a session (for Telegram confirm flow)."""
        session = self._read(session_id)
        if session is None:
            return False
        session["pending_updates"] = updates
        self._write(session)
        return True

    def pop_pending_update(self, session_id: str) -> dict | None:
        """Pop the first pending graph update. Returns None if empty or session missing."""
        session = self._read(session_id)
        if session is None:
            return None
        pending = session.get("pending_updates", [])
        if not pending:
            return None
        update = pending.pop(0)
        session["pending_updates"] = pending
        self._write(session)
        return update

    def get_pending_updates(self, session_id: str) -> list[dict]:
        """Return all pending graph updates for a session."""
        session = self._read(session_id)
        if session is None:
            return []
        return session.get("pending_updates", [])

    def get_session_node_ids(self, session_id: str) -> set[str]:
        """Return set of all node IDs from graph_updates in this session."""
        session = self._read(session_id)
        if session is None:
            return set()
        node_ids: set[str] = set()
        for m in session.get("messages", []):
            for u in m.get("graph_updates", []):
                nid = u.get("node_id")
                if nid:
                    node_ids.add(nid)
                src = u.get("source")
                tgt = u.get("target")
                if src:
                    node_ids.add(src)
                if tgt:
                    node_ids.add(tgt)
        return node_ids
