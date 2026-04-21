"""SQLite persistence layer for the poly3 NBA injuries backend."""
from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "nba_poly.db"

_conn: sqlite3.Connection | None = None


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
    return _conn


# ---------------------------------------------------------------------------
# Schema creation & seeding
# ---------------------------------------------------------------------------

def init_db(csv_path: str | None = None) -> None:
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS players (
            player_name TEXT PRIMARY KEY COLLATE NOCASE,
            nba_team    TEXT NOT NULL,
            importance  INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS news_log (
            rowid INTEGER PRIMARY KEY,
            data  TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS bet_log (
            rowid INTEGER PRIMARY KEY,
            data  TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS transition_configs (
            key  TEXT PRIMARY KEY,
            data TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS betting_config (
            id                  INTEGER PRIMARY KEY CHECK (id = 1),
            auto_trade_enabled  INTEGER DEFAULT 0,
            threshold           REAL    DEFAULT 10.0
        );
    """)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS tweets (
            id TEXT PRIMARY KEY,
            tweet_id TEXT,
            created_at TEXT,
            received_at TEXT,
            raw_text TEXT,
            parsed_events TEXT,
            source TEXT DEFAULT 'UnderdogNBA',
            lag_seconds REAL
        );
    """)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS positions (
            id TEXT PRIMARY KEY,
            created_at TEXT,
            game_matchup TEXT,
            game_datetime TEXT,
            away_tricode TEXT,
            home_tricode TEXT,
            market_type TEXT,
            market_question TEXT,
            bet_team TEXT,
            token_id TEXT,
            outcome TEXT,
            condition_id TEXT,
            buy_price REAL,
            current_price REAL,
            shares REAL,
            amount_usd REAL,
            pnl REAL DEFAULT 0,
            status TEXT DEFAULT 'open',
            sell_price REAL,
            sold_at TEXT,
            order_id TEXT,
            sell_order_id TEXT,
            slug TEXT,
            batch_key TEXT
        );
    """)

    # Seed betting_config singleton row if missing
    conn.execute(
        "INSERT OR IGNORE INTO betting_config (id, auto_trade_enabled, threshold) VALUES (1, 0, 10.0)"
    )
    conn.commit()

    try:
        conn.execute("ALTER TABLE betting_config ADD COLUMN bet_amount REAL DEFAULT 10.0")
        conn.commit()
    except Exception:
        pass  # Column already exists

    for col, ddl in (
        ("block_hour_start", "ALTER TABLE betting_config ADD COLUMN block_hour_start INTEGER DEFAULT 16"),
        ("block_hour_end", "ALTER TABLE betting_config ADD COLUMN block_hour_end INTEGER DEFAULT 18"),
        ("block_minute_start", "ALTER TABLE betting_config ADD COLUMN block_minute_start INTEGER DEFAULT 0"),
        ("block_minute_end", "ALTER TABLE betting_config ADD COLUMN block_minute_end INTEGER DEFAULT 0"),
    ):
        try:
            conn.execute(ddl)
            conn.commit()
        except Exception:
            pass

    # Seed players from CSV if the table is empty
    if csv_path:
        count = conn.execute("SELECT COUNT(*) FROM players").fetchone()[0]
        if count == 0:
            p = Path(csv_path)
            if p.exists():
                with p.open("r", newline="", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        name = str(row.get("player_name", "")).strip()
                        team = str(row.get("nba_team", "")).strip()
                        try:
                            importance = int(row.get("importance", 0))
                        except (TypeError, ValueError):
                            importance = 0
                        if name and team:
                            conn.execute(
                                "INSERT OR IGNORE INTO players (player_name, nba_team, importance) VALUES (?, ?, ?)",
                                (name, team, importance),
                            )
                conn.commit()


# ---------------------------------------------------------------------------
# Players
# ---------------------------------------------------------------------------

def load_players() -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT player_name, nba_team, importance FROM players ORDER BY importance DESC, nba_team, player_name"
    ).fetchall()
    return [dict(r) for r in rows]


def upsert_player(name: str, team: str, importance: int) -> None:
    conn = _get_conn()
    conn.execute(
        "INSERT INTO players (player_name, nba_team, importance) VALUES (?, ?, ?) "
        "ON CONFLICT(player_name) DO UPDATE SET nba_team = excluded.nba_team, importance = excluded.importance",
        (name, team, importance),
    )
    conn.commit()


def delete_player(name: str) -> bool:
    conn = _get_conn()
    cur = conn.execute("DELETE FROM players WHERE player_name = ? COLLATE NOCASE", (name,))
    conn.commit()
    return cur.rowcount > 0


# ---------------------------------------------------------------------------
# News log  (JSON blob per row, newest first)
# ---------------------------------------------------------------------------

def load_news_log() -> list[dict]:
    conn = _get_conn()
    rows = conn.execute("SELECT data FROM news_log ORDER BY rowid DESC").fetchall()
    result = []
    for r in rows:
        try:
            result.append(json.loads(r["data"]))
        except (json.JSONDecodeError, TypeError):
            continue
    return result


def save_news_log(entries: list[dict]) -> None:
    conn = _get_conn()
    conn.execute("DELETE FROM news_log")
    # entries are newest-first; store so that the last inserted row has the highest rowid
    for entry in reversed(entries):
        conn.execute("INSERT INTO news_log (data) VALUES (?)", (json.dumps(entry, ensure_ascii=True),))
    conn.commit()


# ---------------------------------------------------------------------------
# Bet log  (JSON blob per row)
# ---------------------------------------------------------------------------

def load_bet_log() -> list[dict]:
    conn = _get_conn()
    rows = conn.execute("SELECT data FROM bet_log ORDER BY rowid DESC").fetchall()
    result = []
    for r in rows:
        try:
            result.append(json.loads(r["data"]))
        except (json.JSONDecodeError, TypeError):
            continue
    return result


def save_bet_log(entries: list[dict]) -> None:
    conn = _get_conn()
    conn.execute("DELETE FROM bet_log")
    for entry in reversed(entries):
        conn.execute("INSERT INTO bet_log (data) VALUES (?)", (json.dumps(entry, ensure_ascii=True),))
    conn.commit()


# ---------------------------------------------------------------------------
# Transition configs  (JSON blob keyed by composite key)
# ---------------------------------------------------------------------------

def load_transition_configs() -> list[dict]:
    conn = _get_conn()
    rows = conn.execute("SELECT key, data FROM transition_configs").fetchall()
    result = []
    for r in rows:
        try:
            result.append(json.loads(r["data"]))
        except (json.JSONDecodeError, TypeError):
            continue
    return result


def save_transition_configs(entries: list[dict]) -> None:
    conn = _get_conn()
    conn.execute("DELETE FROM transition_configs")
    for entry in entries:
        key = f"{entry.get('transition_type', '')}|{entry.get('from_state', '')}|{entry.get('to_state', '')}"
        conn.execute(
            "INSERT OR REPLACE INTO transition_configs (key, data) VALUES (?, ?)",
            (key, json.dumps(entry, ensure_ascii=True)),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Betting config  (single-row table)
# ---------------------------------------------------------------------------

def load_betting_config() -> dict:
    conn = _get_conn()
    row = conn.execute(
        "SELECT auto_trade_enabled, threshold, bet_amount, block_hour_start, block_hour_end FROM betting_config WHERE id = 1"
    ).fetchone()
    if row is None:
        return {"auto_trade_enabled": False, "threshold": 10.0, "bet_amount": 10.0, "block_hour_start": 16, "block_hour_end": 18}
    return {
        "auto_trade_enabled": bool(row["auto_trade_enabled"]),
        "threshold": float(row["threshold"]),
        "bet_amount": float(row["bet_amount"]) if row["bet_amount"] is not None else 10.0,
        "block_hour_start": int(row["block_hour_start"]) if row["block_hour_start"] is not None else 16,
        "block_hour_end": int(row["block_hour_end"]) if row["block_hour_end"] is not None else 18,
    }


def save_betting_config(enabled: bool, threshold: float, bet_amount: float = 10.0,
                        block_hour_start: int = 16, block_hour_end: int = 18) -> None:
    conn = _get_conn()
    conn.execute(
        "INSERT INTO betting_config (id, auto_trade_enabled, threshold, bet_amount, block_hour_start, block_hour_end) "
        "VALUES (1, ?, ?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET auto_trade_enabled = excluded.auto_trade_enabled, threshold = excluded.threshold, "
        "bet_amount = excluded.bet_amount, block_hour_start = excluded.block_hour_start, block_hour_end = excluded.block_hour_end",
        (int(enabled), threshold, bet_amount, int(block_hour_start), int(block_hour_end)),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Positions
# ---------------------------------------------------------------------------

def load_positions() -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM positions ORDER BY created_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def save_position(pos: dict) -> None:
    conn = _get_conn()
    conn.execute(
        """INSERT OR REPLACE INTO positions
        (id, created_at, game_matchup, game_datetime, away_tricode, home_tricode,
         market_type, market_question, bet_team, token_id, outcome, condition_id,
         buy_price, current_price, shares, amount_usd, pnl, status,
         sell_price, sold_at, order_id, sell_order_id, slug, batch_key)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (pos.get("id"), pos.get("created_at"), pos.get("game_matchup"), pos.get("game_datetime"),
         pos.get("away_tricode"), pos.get("home_tricode"), pos.get("market_type"),
         pos.get("market_question"), pos.get("bet_team"), pos.get("token_id"),
         pos.get("outcome"), pos.get("condition_id"), pos.get("buy_price"),
         pos.get("current_price"), pos.get("shares"), pos.get("amount_usd"),
         pos.get("pnl", 0), pos.get("status", "open"), pos.get("sell_price"),
         pos.get("sold_at"), pos.get("order_id"), pos.get("sell_order_id"),
         pos.get("slug"), pos.get("batch_key")),
    )
    conn.commit()


def update_position(position_id: str, updates: dict) -> bool:
    conn = _get_conn()
    sets = ", ".join(f"{k} = ?" for k in updates)
    vals = list(updates.values()) + [position_id]
    cur = conn.execute(f"UPDATE positions SET {sets} WHERE id = ?", vals)
    conn.commit()
    return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Tweets
# ---------------------------------------------------------------------------

def load_tweets(limit: int = 500) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM tweets ORDER BY received_at DESC LIMIT ?", (limit,)
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        # Parse the JSON events back
        try:
            d["parsed_events"] = json.loads(d["parsed_events"]) if d["parsed_events"] else []
        except (json.JSONDecodeError, TypeError):
            d["parsed_events"] = []
        result.append(d)
    return result


def save_tweet(tweet: dict) -> None:
    conn = _get_conn()
    events = tweet.get("parsed_events", [])
    events_json = json.dumps(events, ensure_ascii=True) if isinstance(events, list) else str(events)
    conn.execute(
        """INSERT OR IGNORE INTO tweets
        (id, tweet_id, created_at, received_at, raw_text, parsed_events, source, lag_seconds)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (tweet.get("id"), tweet.get("tweet_id"), tweet.get("created_at"),
         tweet.get("received_at"), tweet.get("raw_text"), events_json,
         tweet.get("source", "UnderdogNBA"), tweet.get("lag_seconds")),
    )
    conn.commit()


def clear_tweets() -> None:
    conn = _get_conn()
    conn.execute("DELETE FROM tweets")
    conn.commit()
