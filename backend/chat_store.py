"""Persistent chat session storage as JSON files."""

from __future__ import annotations

import json
import os
import uuid
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class ChatStore:
    """Reads/writes chat sessions as individual JSON files."""

    def __init__(self, store_dir: str) -> None:
        self.store_dir = store_dir
        os.makedirs(store_dir, exist_ok=True)

    def _session_path(self, session_id: str) -> str:
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

    def get_session(self, session_id: str) -> dict | None:
        """Return full session with messages, or None if not found."""
        return self._read(session_id)

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

    def get_messages_for_api(self, session_id: str) -> list[dict]:
        """Return messages formatted for the Claude API (role + content only)."""
        session = self._read(session_id)
        if session is None:
            return []
        return [
            {"role": m["role"], "content": m["content"]}
            for m in session.get("messages", [])
        ]
