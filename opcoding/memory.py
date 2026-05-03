from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .utils import now_iso, project_op_dir


def db_path(root: Path) -> Path:
    path = project_op_dir(root) / "memory" / "opcoding.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def connect(root: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path(root))
    conn.execute(
        "create table if not exists events (id integer primary key, created_at text, kind text, title text, body text)"
    )
    conn.execute(
        "create table if not exists decisions (id integer primary key, created_at text, title text, body text, status text)"
    )
    conn.commit()
    return conn


def add_memory(root: Path, kind: str, title: str, body: str) -> dict[str, Any]:
    conn = connect(root)
    try:
        cur = conn.execute(
            "insert into events(created_at, kind, title, body) values (?, ?, ?, ?)",
            (now_iso(), kind, title, body),
        )
        conn.commit()
        return {"id": cur.lastrowid, "kind": kind, "title": title}
    finally:
        conn.close()


def add_decision(
    root: Path, title: str, body: str, status: str = "accepted"
) -> dict[str, Any]:
    conn = connect(root)
    try:
        cur = conn.execute(
            "insert into decisions(created_at, title, body, status) values (?, ?, ?, ?)",
            (now_iso(), title, body, status),
        )
        conn.commit()
        return {"id": cur.lastrowid, "title": title, "status": status}
    finally:
        conn.close()


def list_memory(root: Path, limit: int = 20) -> dict[str, Any]:
    conn = connect(root)
    try:
        events = conn.execute(
            "select created_at, kind, title, body from events order by id desc limit ?",
            (limit,),
        ).fetchall()
        decisions = conn.execute(
            "select created_at, title, body, status from decisions order by id desc limit ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    return {
        "events": [
            {"created_at": row[0], "kind": row[1], "title": row[2], "body": row[3]}
            for row in events
        ],
        "decisions": [
            {"created_at": row[0], "title": row[1], "body": row[2], "status": row[3]}
            for row in decisions
        ],
    }
