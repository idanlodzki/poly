from __future__ import annotations

import copy
import csv
import json
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import httpx
from pydantic import BaseModel

ET = ZoneInfo("America/New_York")
STATIC_DIR = Path(__file__).parent / "static"
CSV_PATH = Path(__file__).resolve().parent.parent / "top_300_nba_players.csv"
TRANSITION_CONFIG_PATH = Path(__file__).resolve().parent / "transition_scores.json"
SCHEDULE_URL = "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json"
UPCOMING_GAMES_WINDOW_DAYS = 2
UPCOMING_GAMES_CACHE_SECONDS = 600
DEMO_REPORT_INTERVAL_MINUTES = 2
DEMO_TICK_SECONDS = DEMO_REPORT_INTERVAL_MINUTES * 60
STATUS_ORDER = ["Out", "Doubtful", "Questionable", "Probable", "Available"]
TRANSITION_TYPE_ORDER = ["added", "status_change", "removed"]
NOT_ON_REPORT_STATE = "Not On Report"
REMOVED_STATE = "Removed"
STATUS_RANK = {
    "Out": 0,
    "Doubtful": 1,
    "Questionable": 2,
    "Probable": 3,
    "Available": 4,
}

app = FastAPI(title="NBA Injury Demo")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class PlayerDbEntry(BaseModel):
    player_name: str
    nba_team: str
    importance: int


class PlayerDbDelete(BaseModel):
    player_name: str


class TransitionConfigEntry(BaseModel):
    transition_type: str
    from_state: str
    to_state: str
    score: int


class DemoState:
    def __init__(self):
        self.records: list[dict] = []
        self.notifications: list[dict] = []
        self.news_log: list[dict] = []
        self.transition_configs: list[dict] = []
        self.upcoming_games: list[dict] = []
        self.demo_games: list[dict] = []
        self.last_report_at: str = ""
        self.last_fetch: datetime | None = None
        self.last_upcoming_games_fetch: datetime | None = None
        self.players_db: list[dict] = []
        self.tick: int = 0
        self.lock = threading.Lock()


state = DemoState()


def _normalize_player_row(row: dict) -> dict:
    player_name = str(row.get("player_name", "")).strip()
    nba_team = str(row.get("nba_team", "")).strip()
    importance_raw = row.get("importance", 0)
    try:
        importance = int(importance_raw)
    except (TypeError, ValueError):
        importance = 0
    if not player_name:
        raise ValueError("player_name is required")
    if not nba_team:
        raise ValueError("nba_team is required")
    return {
        "player_name": player_name,
        "nba_team": nba_team,
        "importance": importance,
    }


def _load_players_db() -> list[dict]:
    if not CSV_PATH.exists():
        return []
    with CSV_PATH.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = []
        for row in reader:
            try:
                rows.append(_normalize_player_row(row))
            except ValueError:
                continue
        return rows


def _transition_key(row: dict) -> str:
    return f"{row['transition_type']}|{row['from_state']}|{row['to_state']}"


def _clamp_score(score: int) -> int:
    return max(-100, min(100, int(score)))


def _default_transition_score(transition_type: str, from_state: str, to_state: str) -> int:
    if transition_type == "added":
        return _clamp_score((STATUS_RANK.get(to_state, 0) - STATUS_RANK["Available"]) * 25)
    if transition_type == "removed":
        return _clamp_score((STATUS_RANK["Available"] - STATUS_RANK.get(from_state, 0)) * 25)
    return _clamp_score((STATUS_RANK.get(to_state, 0) - STATUS_RANK.get(from_state, 0)) * 25)


def _sort_transition_configs(rows: list[dict]) -> list[dict]:
    return sorted(
        rows,
        key=lambda row: (
            TRANSITION_TYPE_ORDER.index(row["transition_type"]),
            -1 if row["from_state"] == NOT_ON_REPORT_STATE else STATUS_ORDER.index(row["from_state"]),
            len(STATUS_ORDER) if row["to_state"] == REMOVED_STATE else STATUS_ORDER.index(row["to_state"]),
        ),
    )


def _normalize_transition_row(row: dict) -> dict:
    transition_type = str(row.get("transition_type", "")).strip()
    from_state = str(row.get("from_state", "")).strip()
    to_state = str(row.get("to_state", "")).strip()
    score_raw = row.get("score", 0)
    try:
        score = _clamp_score(int(score_raw))
    except (TypeError, ValueError):
        score = 0

    if transition_type not in TRANSITION_TYPE_ORDER:
        raise ValueError("transition_type is invalid")
    if not from_state:
        raise ValueError("from_state is required")
    if not to_state:
        raise ValueError("to_state is required")
    if transition_type == "added" and (from_state != NOT_ON_REPORT_STATE or to_state not in STATUS_ORDER):
        raise ValueError("added transition is invalid")
    if transition_type == "removed" and (from_state not in STATUS_ORDER or to_state != REMOVED_STATE):
        raise ValueError("removed transition is invalid")
    if transition_type == "status_change" and (
        from_state not in STATUS_ORDER or to_state not in STATUS_ORDER or from_state == to_state
    ):
        raise ValueError("status_change transition is invalid")

    return {
        "transition_type": transition_type,
        "from_state": from_state,
        "to_state": to_state,
        "score": score,
    }


def _default_transition_configs() -> list[dict]:
    rows = []
    for status in STATUS_ORDER:
        rows.append({
            "transition_type": "added",
            "from_state": NOT_ON_REPORT_STATE,
            "to_state": status,
            "score": _default_transition_score("added", NOT_ON_REPORT_STATE, status),
        })

    for from_state in STATUS_ORDER:
        for to_state in STATUS_ORDER:
            if from_state == to_state:
                continue
            rows.append({
                "transition_type": "status_change",
                "from_state": from_state,
                "to_state": to_state,
                "score": _default_transition_score("status_change", from_state, to_state),
            })

    for status in STATUS_ORDER:
        rows.append({
            "transition_type": "removed",
            "from_state": status,
            "to_state": REMOVED_STATE,
            "score": _default_transition_score("removed", status, REMOVED_STATE),
        })

    return _sort_transition_configs(rows)


def _load_transition_configs() -> list[dict]:
    defaults = {_transition_key(row): row for row in _default_transition_configs()}
    if TRANSITION_CONFIG_PATH.exists():
        try:
            with TRANSITION_CONFIG_PATH.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                for row in data:
                    try:
                        normalized = _normalize_transition_row(row)
                    except ValueError:
                        continue
                    key = _transition_key(normalized)
                    if key in defaults:
                        defaults[key]["score"] = normalized["score"]
        except Exception:
            pass
    return _sort_transition_configs(list(defaults.values()))


def _parse_iso_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _fetch_upcoming_games_from_schedule() -> list[dict]:
    now = datetime.now(ET)
    window_end = now + timedelta(days=UPCOMING_GAMES_WINDOW_DAYS)
    response = httpx.get(
        SCHEDULE_URL,
        timeout=20.0,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    response.raise_for_status()
    data = response.json()
    rows = []

    for game_date in data.get("leagueSchedule", {}).get("gameDates", []):
        for game in game_date.get("games", []):
            start_dt = (
                _parse_iso_datetime(game.get("gameDateTimeUTC") or "")
                or _parse_iso_datetime(game.get("gameDateUTC") or "")
                or _parse_iso_datetime(game.get("gameDateTimeEst") or "")
            )
            if start_dt is None:
                continue

            start_et = start_dt.astimezone(ET)
            if start_et < now or start_et > window_end:
                continue

            away = game.get("awayTeam") or {}
            home = game.get("homeTeam") or {}
            national_broadcasters = game.get("broadcasters", {}).get("nationalBroadcasters") or []
            broadcast = ", ".join(
                b.get("broadcasterAbbreviation") or b.get("broadcasterDisplay") or ""
                for b in national_broadcasters
                if b.get("broadcasterAbbreviation") or b.get("broadcasterDisplay")
            )

            rows.append({
                "game_id": game.get("gameId") or "",
                "game_datetime": start_et.isoformat(),
                "matchup": f"{away.get('teamTricode', '')} @ {home.get('teamTricode', '')}".strip(),
                "away_team": f"{away.get('teamCity', '')} {away.get('teamName', '')}".strip(),
                "home_team": f"{home.get('teamCity', '')} {home.get('teamName', '')}".strip(),
                "away_tricode": away.get("teamTricode") or "",
                "home_tricode": home.get("teamTricode") or "",
                "status": game.get("gameStatusText") or "Scheduled",
                "arena": game.get("arenaName") or "",
                "arena_city": game.get("arenaCity") or "",
                "arena_state": game.get("arenaState") or "",
                "broadcast": broadcast,
                "game_label": game.get("gameLabel") or "",
            })

    rows.sort(key=lambda row: row["game_datetime"])
    return rows


def _get_upcoming_games() -> list[dict]:
    with state.lock:
        cached_rows = copy.deepcopy(state.upcoming_games)
        last_fetch = state.last_upcoming_games_fetch

    now = datetime.now(ET)
    if (
        last_fetch is not None
        and (now - last_fetch).total_seconds() < UPCOMING_GAMES_CACHE_SECONDS
        and cached_rows
    ):
        return cached_rows

    try:
        rows = _fetch_upcoming_games_from_schedule()
    except Exception:
        return cached_rows

    with state.lock:
        state.upcoming_games = rows
        state.last_upcoming_games_fetch = now
    return rows


def _fallback_demo_games(report_dt: datetime) -> list[dict]:
    game1_dt = report_dt.replace(hour=19, minute=0)
    game2_dt = report_dt.replace(hour=21, minute=30)
    return [
        {
            "game_id": "demo-1",
            "game_datetime": game1_dt.isoformat(),
            "matchup": "NYK @ BOS",
            "away_team": "New York Knicks",
            "home_team": "Boston Celtics",
            "away_tricode": "NYK",
            "home_tricode": "BOS",
            "status": "Scheduled",
            "arena": "TD Garden",
            "arena_city": "Boston",
            "arena_state": "MA",
            "broadcast": "Demo Feed",
            "game_label": "Demo",
        },
        {
            "game_id": "demo-2",
            "game_datetime": game2_dt.isoformat(),
            "matchup": "LAL @ DEN",
            "away_team": "Los Angeles Lakers",
            "home_team": "Denver Nuggets",
            "away_tricode": "LAL",
            "home_tricode": "DEN",
            "status": "Scheduled",
            "arena": "Ball Arena",
            "arena_city": "Denver",
            "arena_state": "CO",
            "broadcast": "Demo Feed",
            "game_label": "Demo",
        },
    ]


def _player_names_for_team(team_name: str, players_db: list[dict], fallback_prefix: str, count: int = 3) -> list[str]:
    matching = [
        row for row in players_db
        if str(row.get("nba_team", "")).strip().lower() == str(team_name).strip().lower()
    ]
    matching.sort(key=lambda row: (-int(row.get("importance", 0)), str(row.get("player_name", ""))))
    names = [str(row.get("player_name", "")).strip() for row in matching if str(row.get("player_name", "")).strip()]
    while len(names) < count:
        names.append(f"{fallback_prefix} Demo {len(names) + 1}")
    return names[:count]


def _select_demo_games(report_dt: datetime, upcoming_games: list[dict], players_db: list[dict]) -> list[dict]:
    source_games = upcoming_games[:2] if upcoming_games else _fallback_demo_games(report_dt)
    selected = []
    for game in source_games:
        row = dict(game)
        row["away_players"] = _player_names_for_team(row.get("away_team", ""), players_db, row.get("away_tricode", "AWAY"))
        row["home_players"] = _player_names_for_team(row.get("home_team", ""), players_db, row.get("home_tricode", "HOME"))
        selected.append(row)
    return selected


def _record(game: dict, side: str, player: str, status: str, injury: str) -> dict:
    team = game["away_team"] if side == "away" else game["home_team"]
    team_tricode = game["away_tricode"] if side == "away" else game["home_tricode"]
    opponent_tricode = game["home_tricode"] if side == "away" else game["away_tricode"]
    game_dt = _parse_iso_datetime(game.get("game_datetime", "")) or datetime.now(ET)
    return {
        "team": team,
        "matchup": (game.get("matchup", "") or "").replace(" @ ", "@"),
        "player": player,
        "status": status,
        "injury": injury,
        "game_date": game_dt.strftime("%m/%d/%Y"),
        "game_time": game_dt.strftime("%I:%M (ET)"),
        "game_datetime_et": game_dt.isoformat(),
        "game_id": game.get("game_id", ""),
        "scheduled_game_datetime": game.get("game_datetime", ""),
        "scheduled_matchup": game.get("matchup", ""),
        "away_tricode": game.get("away_tricode", ""),
        "home_tricode": game.get("home_tricode", ""),
        "team_tricode": team_tricode,
        "opponent_tricode": opponent_tricode,
        "last_update_at": game_dt.isoformat(),
    }


def _base_records(demo_games: list[dict]) -> list[dict]:
    rows = []
    if not demo_games:
        return rows

    game1 = demo_games[0]
    rows.extend([
        _record(game1, "away", game1["away_players"][0], "Questionable", "Injury/Illness - Ankle; Soreness"),
        _record(game1, "away", game1["away_players"][1], "Available", "Available"),
        _record(game1, "home", game1["home_players"][0], "Probable", "Injury/Illness - Hamstring; Management"),
        _record(game1, "home", game1["home_players"][1], "Available", "Available"),
    ])

    if len(demo_games) > 1:
        game2 = demo_games[1]
        rows.extend([
            _record(game2, "away", game2["away_players"][0], "Questionable", "Injury/Illness - Foot; Soreness"),
            _record(game2, "away", game2["away_players"][1], "Available", "Available"),
            _record(game2, "home", game2["home_players"][0], "Probable", "Injury/Illness - Wrist; Soreness"),
            _record(game2, "home", game2["home_players"][1], "Questionable", "Injury/Illness - Hip; Tightness"),
        ])

    return rows


def _record_key(record: dict) -> str:
    return f"{record['team']}|{record['player']}"


def _remove_player(rows: list[dict], player_name: str) -> list[dict]:
    return [row for row in rows if row["player"] != player_name]


def _upsert_player(rows: list[dict], record: dict) -> None:
    for idx, row in enumerate(rows):
        if row["player"] == record["player"]:
            rows[idx] = record
            return
    rows.append(record)


def _set_player_status(rows: list[dict], player_name: str, status: str, injury: str) -> None:
    for row in rows:
        if row["player"] == player_name:
            row["status"] = status
            row["injury"] = injury
            return


def _event_tone(event_type: str, old_status: str = "", new_status: str = "") -> str:
    if event_type == "injury_change":
        return "neutral"
    if event_type == "removed":
        return "positive"
    if event_type == "added":
        if new_status == "Available":
            return "neutral"
        return "negative"

    old_rank = STATUS_RANK.get(old_status, -1)
    new_rank = STATUS_RANK.get(new_status, -1)
    if new_rank > old_rank:
        return "positive"
    if new_rank < old_rank:
        return "negative"
    return "neutral"


def _hydrate_last_updates(old: list[dict], new: list[dict], report_at: str) -> list[dict]:
    old_map = {_record_key(r): r for r in old}
    hydrated = []
    for record in new:
        key = _record_key(record)
        previous = old_map.get(key)
        if previous and (
            previous.get("status") == record.get("status")
            and previous.get("injury") == record.get("injury")
            and previous.get("matchup") == record.get("matchup")
        ):
            record["last_update_at"] = previous.get("last_update_at", report_at)
        else:
            record["last_update_at"] = report_at
        hydrated.append(record)
    return hydrated


def _diff(old: list[dict], new: list[dict], report_at: str) -> list[dict]:
    old_map = {_record_key(r): r for r in old}
    new_map = {_record_key(r): r for r in new}
    changes = []

    for key in sorted(set(new_map) - set(old_map)):
        r = new_map[key]
        changes.append({
            "type": "added",
            "player": r["player"],
            "team": r["team"],
            "status": r["status"],
            "from_status": NOT_ON_REPORT_STATE,
            "to_status": r["status"],
            "matchup": r["matchup"],
            "game_datetime_et": r.get("game_datetime_et"),
            "game_id": r.get("game_id", ""),
            "scheduled_game_datetime": r.get("scheduled_game_datetime", ""),
            "scheduled_matchup": r.get("scheduled_matchup", ""),
            "away_tricode": r.get("away_tricode", ""),
            "home_tricode": r.get("home_tricode", ""),
            "team_tricode": r.get("team_tricode", ""),
            "opponent_tricode": r.get("opponent_tricode", ""),
            "injury": r["injury"],
            "tone": _event_tone("added", "", r["status"]),
            "detail": f"Added — {r['status']}",
            "timestamp_at": report_at,
        })

    for key in sorted(set(old_map) - set(new_map)):
        r = old_map[key]
        if r.get("status") == "Available":
            continue
        changes.append({
            "type": "removed",
            "player": r["player"],
            "team": r["team"],
            "status": r["status"],
            "from_status": r["status"],
            "to_status": REMOVED_STATE,
            "matchup": r["matchup"],
            "game_datetime_et": r.get("game_datetime_et"),
            "game_id": r.get("game_id", ""),
            "scheduled_game_datetime": r.get("scheduled_game_datetime", ""),
            "scheduled_matchup": r.get("scheduled_matchup", ""),
            "away_tricode": r.get("away_tricode", ""),
            "home_tricode": r.get("home_tricode", ""),
            "team_tricode": r.get("team_tricode", ""),
            "opponent_tricode": r.get("opponent_tricode", ""),
            "injury": r["injury"],
            "tone": _event_tone("removed", r["status"], ""),
            "detail": "Removed from report",
            "timestamp_at": report_at,
        })

    for key in sorted(set(old_map) & set(new_map)):
        o, n = old_map[key], new_map[key]
        if o["status"] != n["status"]:
            changes.append({
                "type": "status_change",
                "player": n["player"],
                "team": n["team"],
                "status": n["status"],
                "from_status": o["status"],
                "to_status": n["status"],
                "matchup": n["matchup"],
                "game_datetime_et": n.get("game_datetime_et"),
                "game_id": n.get("game_id", ""),
                "scheduled_game_datetime": n.get("scheduled_game_datetime", ""),
                "scheduled_matchup": n.get("scheduled_matchup", ""),
                "away_tricode": n.get("away_tricode", ""),
                "home_tricode": n.get("home_tricode", ""),
                "team_tricode": n.get("team_tricode", ""),
                "opponent_tricode": n.get("opponent_tricode", ""),
                "injury": n["injury"],
                "tone": _event_tone("status_change", o["status"], n["status"]),
                "detail": f"{o['status']} → {n['status']}",
                "timestamp_at": report_at,
            })
        elif o["injury"] != n["injury"]:
            changes.append({
                "type": "injury_change",
                "player": n["player"],
                "team": n["team"],
                "status": n["status"],
                "from_status": o["status"],
                "to_status": n["status"],
                "matchup": n["matchup"],
                "game_datetime_et": n.get("game_datetime_et"),
                "game_id": n.get("game_id", ""),
                "scheduled_game_datetime": n.get("scheduled_game_datetime", ""),
                "scheduled_matchup": n.get("scheduled_matchup", ""),
                "away_tricode": n.get("away_tricode", ""),
                "home_tricode": n.get("home_tricode", ""),
                "team_tricode": n.get("team_tricode", ""),
                "opponent_tricode": n.get("opponent_tricode", ""),
                "injury": n["injury"],
                "tone": _event_tone("injury_change", o["status"], n["status"]),
                "detail": "Injury details changed",
                "timestamp_at": report_at,
            })

    return changes


def _apply_demo_mutation(records: list[dict], step: int, report_dt: datetime, demo_games: list[dict]) -> list[dict]:
    rows = copy.deepcopy(records)
    mod = step % 6
    if not demo_games:
        return rows

    game1 = demo_games[0]
    away1 = game1["away_players"]
    home1 = game1["home_players"]
    game2 = demo_games[1] if len(demo_games) > 1 else demo_games[0]
    away2 = game2["away_players"]
    home2 = game2["home_players"]

    if mod == 0:
        _set_player_status(rows, away2[0], "Available", "Available")
        rows = _remove_player(rows, away2[2])
        _set_player_status(rows, home2[0], "Available", "Available")
        rows = _remove_player(rows, home2[2])
        _upsert_player(rows, _record(game2, "away", away2[1], "Available", "Available"))
        _upsert_player(rows, _record(game2, "home", home2[1], "Questionable", "Injury/Illness - Hip; Tightness"))
    elif mod == 1:
        _set_player_status(rows, away1[0], "Out", "Injury/Illness - Ankle; Sprain")
        _set_player_status(rows, away1[1], "Questionable", "Injury/Illness - Knee; Management")
        _upsert_player(rows, _record(game1, "away", away1[2], "Probable", "Injury/Illness - Hamstring; Injury Management"))
    elif mod == 2:
        rows = _remove_player(rows, away2[0])
        _set_player_status(rows, home2[1], "Out", "Injury/Illness - Hip; Spasms")
        _upsert_player(rows, _record(game2, "away", away2[2], "Questionable", "Injury/Illness - Shoulder; Soreness"))
        _upsert_player(rows, _record(game2, "away", away2[1], "Available", "Available"))
    elif mod == 3:
        _set_player_status(rows, home1[0], "Out", "Injury/Illness - Foot; Re-aggravation")
        _upsert_player(rows, _record(game1, "home", home1[2], "Doubtful", "Injury/Illness - Wrist; Soreness"))
        _set_player_status(rows, home2[0], "Probable", "Injury/Illness - Wrist; Warmup limitation")
        _upsert_player(rows, _record(game2, "home", home2[2], "Available", "Available"))
    elif mod == 4:
        _set_player_status(rows, away1[0], "Available", "Available")
        _set_player_status(rows, away1[1], "Available", "Available")
        rows = _remove_player(rows, away1[2])
        _upsert_player(rows, _record(game1, "home", home1[1], "Available", "Available"))
    elif mod == 5:
        rows = _remove_player(rows, home2[1])
        _set_player_status(rows, away2[2], "Available", "Available")
        _upsert_player(rows, _record(game2, "home", home2[2], "Questionable", "Injury/Illness - Knee; Soreness"))

    return rows


def _demo_loop():
    base_report = datetime.now(ET).replace(second=0, microsecond=0)
    with state.lock:
        players_db = copy.deepcopy(state.players_db)
    demo_games = _select_demo_games(base_report, _get_upcoming_games(), players_db)
    records = _base_records(demo_games)

    with state.lock:
        state.demo_games = copy.deepcopy(demo_games)
        state.records = _hydrate_last_updates([], records, base_report.isoformat())
        state.last_report_at = base_report.isoformat()
        state.last_fetch = datetime.now(ET)

    while True:
        time.sleep(DEMO_TICK_SECONDS)
        with state.lock:
            previous = copy.deepcopy(state.records)
            demo_games = copy.deepcopy(state.demo_games)
            state.tick += 1
            step = state.tick

        report_dt = base_report + timedelta(minutes=DEMO_REPORT_INTERVAL_MINUTES * step)
        next_records = _apply_demo_mutation(previous, step, report_dt, demo_games)
        next_records = _hydrate_last_updates(previous, next_records, report_dt.isoformat())
        changes = _diff(previous, next_records, report_dt.isoformat())

        with state.lock:
            state.records = next_records
            state.last_report_at = report_dt.isoformat()
            state.last_fetch = datetime.now(ET)
            if changes:
                state.notifications = changes + state.notifications
                state.notifications = state.notifications[:500]
                state.news_log = changes + state.news_log
                state.news_log = state.news_log[:5000]


@app.on_event("startup")
def startup():
    with state.lock:
        state.players_db = _load_players_db()
        state.notifications = []
        state.news_log = []
        state.transition_configs = _load_transition_configs()
    t = threading.Thread(target=_demo_loop, daemon=True)
    t.start()


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/report")
def get_report():
    with state.lock:
        return JSONResponse({
            "records": state.records,
            "last_report_at": state.last_report_at,
            "total": len(state.records),
            "status": {
                "connected": True,
                "poll_mode": "normal",
                "next_target_at": (datetime.fromisoformat(state.last_report_at) + timedelta(minutes=DEMO_REPORT_INTERVAL_MINUTES)).isoformat() if state.last_report_at else None,
                "aggressive_start_at": None,
                "last_fetch_at": state.last_fetch.isoformat() if state.last_fetch else None,
                "baseline_interval_seconds": DEMO_TICK_SECONDS,
                "aggressive_interval_seconds": 1,
            },
        })


@app.get("/api/notifications")
def get_notifications():
    with state.lock:
        return JSONResponse({
            "notifications": state.notifications,
            "count": len(state.notifications),
        })


@app.get("/api/upcoming-games")
def get_upcoming_games():
    rows = _get_upcoming_games()
    if not rows:
        with state.lock:
            rows = copy.deepcopy(state.demo_games)
    return JSONResponse({
        "rows": rows,
        "count": len(rows),
        "window_days": UPCOMING_GAMES_WINDOW_DAYS,
    })


@app.get("/api/clear-notifications")
def clear_notifications():
    with state.lock:
        state.notifications = []
    return JSONResponse({"ok": True})


GAMMA_API_URL = "https://gamma-api.polymarket.com/events"


@app.get("/api/polymarket-game")
def get_polymarket_game(slug: str = ""):
    slug = slug.strip()
    if not slug:
        raise HTTPException(status_code=400, detail="slug is required")
    try:
        resp = httpx.get(
            GAMMA_API_URL,
            params={"slug": slug},
            timeout=15.0,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp.raise_for_status()
        events = resp.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if not events:
        raise HTTPException(status_code=404, detail="event not found")
    evt = events[0]
    markets = evt.get("markets") or []
    return JSONResponse({
        "title": evt.get("title", ""),
        "slug": slug,
        "markets": [
            {
                "question": m.get("question", ""),
                "type": m.get("sportsMarketType", ""),
                "groupItemTitle": m.get("groupItemTitle", ""),
                "outcomes": json.loads(m["outcomes"]) if isinstance(m.get("outcomes"), str) else (m.get("outcomes") or []),
                "prices": json.loads(m["outcomePrices"]) if isinstance(m.get("outcomePrices"), str) else (m.get("outcomePrices") or []),
                "line": m.get("line"),
            }
            for m in markets
            if m.get("sportsMarketType") in ("moneyline", "spreads", "totals", "first_half_moneyline", "first_half_spreads", "first_half_totals")
        ],
    })


@app.get("/api/news-log")
def get_news_log():
    with state.lock:
        return JSONResponse({
            "rows": state.news_log,
            "count": len(state.news_log),
        })


@app.get("/api/clear-news-log")
def clear_news_log():
    with state.lock:
        state.news_log = []
    return JSONResponse({"ok": True})


@app.get("/api/players-db")
def get_players_db():
    with state.lock:
        rows = copy.deepcopy(state.players_db)
    return JSONResponse({
        "players": rows,
        "total": len(rows),
        "teams": sorted({row["nba_team"] for row in rows}),
    })


@app.get("/api/transition-config")
def get_transition_config():
    with state.lock:
        rows = copy.deepcopy(state.transition_configs)
    return JSONResponse({
        "rows": rows,
        "total": len(rows),
    })


@app.post("/api/transition-config/upsert")
def upsert_transition_config(entry: TransitionConfigEntry):
    try:
        normalized = _normalize_transition_row(entry.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    with state.lock:
        updated = False
        for idx, row in enumerate(state.transition_configs):
            if _transition_key(row) == _transition_key(normalized):
                state.transition_configs[idx] = normalized
                updated = True
                break
        if not updated:
            state.transition_configs.append(normalized)
        state.transition_configs = _sort_transition_configs(state.transition_configs)

    return JSONResponse({
        "ok": True,
        "mode": "updated" if updated else "added",
        "row": normalized,
    })


@app.post("/api/players-db/upsert")
def upsert_player_db(entry: PlayerDbEntry):
    try:
        normalized = _normalize_player_row(entry.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    with state.lock:
        updated = False
        for idx, row in enumerate(state.players_db):
            if row["player_name"].lower() == normalized["player_name"].lower():
                state.players_db[idx] = normalized
                updated = True
                break
        if not updated:
            state.players_db.append(normalized)

    return JSONResponse({
        "ok": True,
        "mode": "updated" if updated else "added",
        "player": normalized,
    })


@app.post("/api/players-db/delete")
def delete_player_db(payload: PlayerDbDelete):
    player_name = payload.player_name.strip()
    if not player_name:
        raise HTTPException(status_code=400, detail="player_name is required")

    with state.lock:
        new_rows = [row for row in state.players_db if row["player_name"].lower() != player_name.lower()]
        if len(new_rows) == len(state.players_db):
            raise HTTPException(status_code=404, detail="player not found")
        state.players_db = new_rows

    return JSONResponse({"ok": True, "player_name": player_name})
