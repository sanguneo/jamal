"""Persistent history of notifications actually observed by this adapter."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jamal.codec import Message, Notification


class History:
    def __init__(self, path: Path, account: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS account (id TEXT PRIMARY KEY)")
        existing = self.db.execute("SELECT id FROM account").fetchone()
        if existing and existing[0] != account:
            self.db.close()
            raise ValueError("State belongs to a different account")
        self.db.execute("INSERT OR IGNORE INTO account VALUES (?)", (account,))
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS messages "
            "(seq INTEGER PRIMARY KEY, id TEXT UNIQUE, room TEXT, recipient TEXT, body TEXT)"
        )
        self.db.commit()

    def ingest(self, note: Notification) -> bool:
        changed = False
        for index, message in enumerate(note.messages):
            outgoing = (message.direction or "").lower() in ("out", "outgoing")
            peers = message.to if outgoing else ((message.from_ or ""),)
            recipient = peers[0] if len(peers) == 1 else ""
            room = message.conversation_id or recipient
            if not room:
                continue
            # Notification index distinguishes identical messages; attributes give a
            # stable fallback when the backend did not include that index.
            identity = json.dumps(
                [note.nms_index, note.push_epoch, index, dict(message.raw_attributes)],
                sort_keys=True,
            )
            message_id = hashlib.sha256(identity.encode()).hexdigest()
            body = wire_message(message, message_id, room, outgoing)
            if (
                len(peers) != 1 or (not outgoing and len(message.to) > 1)
                or re.fullmatch(r"\+?[0-9]{7,15}", recipient) is None
            ):
                recipient = ""
            cursor = self.db.execute(
                "INSERT OR IGNORE INTO messages(id, room, recipient, body) VALUES (?, ?, ?, ?)",
                (message_id, room, recipient, json.dumps(body)),
            )
            changed |= cursor.rowcount > 0
        self.db.commit()
        return changed

    def rooms(self) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT room, recipient, body FROM messages WHERE seq IN "
            "(SELECT MAX(seq) FROM messages GROUP BY room) ORDER BY seq DESC"
        ).fetchall()
        return [
            {
                "id": room, "title": recipient or room,
                "kind": "direct" if recipient else "group", "unread": 0,
                "lastActivityAt": (body := json.loads(raw))["sentAt"],
                "lastMessage": {"text": body["text"][:200], "fromMe": body["fromMe"]},
                **({"features": {"messages.send": False, "attachments.send": False}}
                   if not recipient else {}),
            }
            for room, recipient, raw in rows
        ]

    def recipient(self, room: str) -> str:
        row = self.db.execute(
            "SELECT recipient FROM messages WHERE room=? ORDER BY seq DESC LIMIT 1", (room,)
        ).fetchone()
        if row is None:
            raise KeyError("No such observed room")
        return str(row[0])

    def position(self, room: str, message_id: str) -> int:
        row = self.db.execute(
            "SELECT seq FROM messages WHERE room=? AND id=?", (room, message_id)
        ).fetchone()
        if row is None:
            raise ValueError("Unknown message cursor")
        return int(row[0])

    def messages(self, room: str, limit: int, before: str | None) -> dict[str, Any]:
        self.recipient(room)
        boundary = self.position(room, before) if before else self.latest() + 1
        rows = self.db.execute(
            "SELECT body FROM messages WHERE room=? AND seq<? ORDER BY seq DESC LIMIT ?",
            (room, boundary, limit + 1),
        ).fetchall()
        return {
            "ok": True, "messages": [json.loads(row[0]) for row in reversed(rows[:limit])],
            "hasEarlier": len(rows) > limit,
        }

    def latest(self) -> int:
        return int(self.db.execute("SELECT COALESCE(MAX(seq), 0) FROM messages").fetchone()[0])

    def after(self, positions: dict[str, int], baseline: int) -> list[tuple[int, dict[str, Any]]]:
        floor = min([baseline, *positions.values()])
        rows = self.db.execute(
            "SELECT seq, room, body FROM messages WHERE seq>? ORDER BY seq", (floor,)
        ).fetchall()
        return [
            (seq, json.loads(body)) for seq, room, body in rows
            if seq > positions.get(room, baseline)
        ]

    def cursors(self) -> dict[str, str]:
        return {
            str(room): str(message_id) for room, message_id in self.db.execute(
                "SELECT room, id FROM messages WHERE seq IN "
                "(SELECT MAX(seq) FROM messages GROUP BY room)"
            )
        }

    def close(self) -> None:
        self.db.close()


def wire_message(message: Message, message_id: str, room: str, outgoing: bool) -> dict[str, Any]:
    try:
        sent_at = datetime.fromisoformat(message.date or "")
        if sent_at.tzinfo is None:
            sent_at = sent_at.replace(tzinfo=UTC)
    except ValueError:
        sent_at = datetime.now(UTC)
    return {
        "id": message_id, "roomId": room, "sender": message.from_ or "",
        "text": message.text or "", "fromMe": outgoing, "sentAt": sent_at.isoformat(),
        "kind": "text" if message.content_type in (None, "text/plain") else "media",
    }
