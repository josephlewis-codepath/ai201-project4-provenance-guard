"""SQLite storage: `submissions` holds current state, `audit_log` is append-only."""
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone

DB_PATH = os.environ.get("PROVENANCE_DB", os.path.join(os.path.dirname(__file__), "provenance.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS submissions (
    content_id       TEXT PRIMARY KEY,
    creator_id       TEXT NOT NULL,
    text             TEXT NOT NULL,
    attribution      TEXT NOT NULL,
    confidence       REAL NOT NULL,
    llm_score        REAL,
    llm_reasoning    TEXT,
    stylo_score      REAL,
    stylo_metrics    TEXT,
    flags            TEXT NOT NULL DEFAULT '[]',
    label_variant    TEXT NOT NULL,
    status           TEXT NOT NULL,
    appeal_id        TEXT,
    appeal_reasoning TEXT,
    appealed_at      TEXT,
    created_at       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp        TEXT NOT NULL,
    event            TEXT NOT NULL,
    content_id       TEXT NOT NULL,
    creator_id       TEXT NOT NULL,
    attribution      TEXT NOT NULL,
    confidence       REAL NOT NULL,
    llm_score        REAL,
    stylo_score      REAL,
    signals_used     TEXT NOT NULL,
    flags            TEXT NOT NULL,
    status           TEXT NOT NULL,
    appeal_id        TEXT,
    appeal_reasoning TEXT
);
"""

JSON_COLUMNS = ("stylo_metrics", "flags", "signals_used")


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _row_to_dict(row):
    d = dict(row)
    for col in JSON_COLUMNS:
        if col in d and d[col] is not None:
            d[col] = json.loads(d[col])
    return d


def init_db():
    with _connect() as conn:
        conn.executescript(SCHEMA)


def _log_event(conn, event, record):
    signals_used = [name for name, key in (("llm", "llm_score"), ("stylometry", "stylo_score"))
                    if record.get(key) is not None]
    conn.execute(
        """INSERT INTO audit_log (timestamp, event, content_id, creator_id, attribution, confidence,
                                  llm_score, stylo_score, signals_used, flags, status, appeal_id,
                                  appeal_reasoning)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (now_iso(), event, record["content_id"], record["creator_id"], record["attribution"],
         record["confidence"], record.get("llm_score"), record.get("stylo_score"),
         json.dumps(signals_used), json.dumps(record.get("flags", [])), record["status"],
         record.get("appeal_id"), record.get("appeal_reasoning")),
    )


def save_submission(record):
    """Insert a classified submission and write its `classified` audit event."""
    with _connect() as conn:
        conn.execute(
            """INSERT INTO submissions (content_id, creator_id, text, attribution, confidence, llm_score,
                                        llm_reasoning, stylo_score, stylo_metrics, flags, label_variant,
                                        status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (record["content_id"], record["creator_id"], record["text"], record["attribution"],
             record["confidence"], record.get("llm_score"), record.get("llm_reasoning"),
             record.get("stylo_score"), json.dumps(record.get("stylo_metrics")),
             json.dumps(record.get("flags", [])), record["label_variant"], record["status"],
             record["created_at"]),
        )
        _log_event(conn, "classified", record)


def get_submission(content_id):
    with _connect() as conn:
        row = conn.execute("SELECT * FROM submissions WHERE content_id = ?", (content_id,)).fetchone()
    return _row_to_dict(row) if row else None


def get_log(limit=20):
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [_row_to_dict(r) for r in rows]


def file_appeal(content_id, reasoning):
    """Move a classified submission to under_review and log the appeal with the original decision.

    Returns the updated submission, or None if it was not in `classified` status (already appealed).
    The conditional UPDATE makes this safe against two concurrent appeals.
    """
    with _connect() as conn:
        cur = conn.execute(
            """UPDATE submissions SET status = 'under_review', appeal_id = ?, appeal_reasoning = ?,
                                      appealed_at = ?
               WHERE content_id = ? AND status = 'classified'""",
            (str(uuid.uuid4()), reasoning, now_iso(), content_id),
        )
        if cur.rowcount == 0:
            return None
        row = _row_to_dict(conn.execute(
            "SELECT * FROM submissions WHERE content_id = ?", (content_id,)).fetchone())
        _log_event(conn, "appeal_filed", row)
    return row


def get_appeal_queue():
    """Submissions awaiting human review, oldest appeal first."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM submissions WHERE status = 'under_review' ORDER BY appealed_at ASC").fetchall()
    return [_row_to_dict(r) for r in rows]
