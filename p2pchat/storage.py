from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path


class Storage:
    def __init__(self, home: Path):
        self.db_path = home / "hermes.db"
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    ts REAL,
                    from_id TEXT,
                    from_name TEXT,
                    target TEXT,
                    body TEXT,
                    type TEXT,
                    enc TEXT,
                    state TEXT,
                    raw_json TEXT
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_target ON messages(target)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_ts ON messages(ts)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS peers (
                    peer_id TEXT PRIMARY KEY,
                    username TEXT,
                    last_seen REAL,
                    online INTEGER,
                    status_text TEXT,
                    presence_json TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS file_transfers (
                    id TEXT PRIMARY KEY,
                    file_name TEXT,
                    file_path TEXT,
                    size INTEGER,
                    direction TEXT,
                    peer_id TEXT,
                    status TEXT,
                    ts REAL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS outbox (
                    id TEXT PRIMARY KEY,
                    ts REAL,
                    target TEXT,
                    body TEXT,
                    enc TEXT,
                    tries INTEGER,
                    last_error TEXT,
                    raw_json TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_outbox_ts ON outbox(ts)")

    def save_message(self, msg: dict):
        target = msg.get("channel") or msg.get("to") or "@broadcast"
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO messages (id, ts, from_id, from_name, target, body, type, enc, state, raw_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    msg.get("id"),
                    msg.get("ts", time.time()),
                    msg.get("from_id"),
                    msg.get("from_name"),
                    target,
                    msg.get("body"),
                    msg.get("type", "msg"),
                    msg.get("enc", "none"),
                    msg.get("state", "sent"),
                    json.dumps(msg),
                ),
            )

    def get_messages(self, target: str, limit: int = 100) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT raw_json FROM messages WHERE target = ? ORDER BY ts DESC LIMIT ?",
                (target, limit),
            ).fetchall()
            return [json.loads(row["raw_json"]) for row in reversed(rows)]

    def search_messages(self, query: str) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT raw_json FROM messages WHERE body LIKE ? ORDER BY ts DESC",
                (f"%{query}%",),
            ).fetchall()
            return [json.loads(row["raw_json"]) for row in rows]

    def save_peer(self, peer: dict):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO peers (peer_id, username, last_seen, online, status_text, presence_json) VALUES (?,?,?,?,?,?)",
                (
                    peer.get("id"),
                    peer.get("name"),
                    time.time(),
                    1 if peer.get("online") else 0,
                    peer.get("status", ""),
                    json.dumps(peer),
                ),
            )

    def get_peers(self) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT presence_json FROM peers WHERE online = 1"
            ).fetchall()
            return [json.loads(row["presence_json"]) for row in rows]

    def update_message_state(self, msg_id: str, state: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE messages SET state = ? WHERE id = ?", (state, msg_id))

    def enqueue_outbox(self, msg: dict, last_error: str | None = None):
        target = msg.get("channel") or msg.get("to") or "@broadcast"
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO outbox (id, ts, target, body, enc, tries, last_error, raw_json) VALUES (?,?,?,?,?,?,?,?)",
                (
                    msg.get("id"),
                    msg.get("ts", time.time()),
                    target,
                    msg.get("body"),
                    msg.get("enc", "none"),
                    int(msg.get("tries", 0)),
                    last_error,
                    json.dumps(msg),
                ),
            )

    def list_outbox(self, limit: int = 50) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT raw_json, tries, last_error FROM outbox ORDER BY ts ASC LIMIT ?",
                (limit,),
            ).fetchall()
            items = []
            for row in rows:
                payload = json.loads(row["raw_json"])
                payload["tries"] = row["tries"]
                if row["last_error"]:
                    payload["last_error"] = row["last_error"]
                items.append(payload)
            return items

    def update_outbox_attempt(
        self, msg_id: str, tries: int, last_error: str | None = None
    ):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE outbox SET tries = ?, last_error = ? WHERE id = ?",
                (int(tries), last_error, msg_id),
            )

    def remove_outbox(self, msg_id: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM outbox WHERE id = ?", (msg_id,))

    def create_file_transfer(
        self,
        tid: str,
        fname: str,
        fpath: str,
        fsize: int = 0,
        fdir: str = "send",
        fpeer: str = "",
    ):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO file_transfers (id, file_name, file_path, size, direction, peer_id, status, ts) VALUES (?,?,?,?,?,?,?,?)",
                (tid, fname, fpath, fsize, fdir, fpeer, "pending", time.time()),
            )
