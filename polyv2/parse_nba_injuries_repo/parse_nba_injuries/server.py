from __future__ import annotations

import csv
import json
import logging
import os
import re
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

os.environ.setdefault("JAVA_HOME", "/opt/homebrew/opt/openjdk@11/libexec/openjdk.jdk/Contents/Home")
if "/opt/homebrew/opt/openjdk@11/bin" not in os.environ.get("PATH", ""):
    os.environ["PATH"] = "/opt/homebrew/opt/openjdk@11/bin:" + os.environ.get("PATH", "")

from contextlib import contextmanager

@contextmanager
def _suppress_stdio():
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = open(os.devnull, "w")
    sys.stderr = open(os.devnull, "w")
    try:
        yield
    finally:
        sys.stdout.close()
        sys.stderr.close()
        sys.stdout, sys.stderr = old_out, old_err

with _suppress_stdio():
    from nbainjuries import injury

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
import httpx
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("nba-injuries")

ET = ZoneInfo("America/New_York")
POLL_SECONDS = 120
AGGRESSIVE_LEAD_SECONDS = 120
CSV_PATH = Path(__file__).resolve().parent.parent / "top_300_nba_players.csv"
NEWS_LOG_PATH = Path(__file__).resolve().parent / "caught_news.json"
TRANSITION_CONFIG_PATH = Path(__file__).resolve().parent / "transition_scores.json"
SCHEDULE_URL = "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json"
UPCOMING_GAMES_WINDOW_DAYS = 2
UPCOMING_GAMES_CACHE_SECONDS = 600
INITIAL_REPORT_LOOKBACK_INTERVALS = 96
STATUS_ORDER = ["Out", "Doubtful", "Questionable", "Probable", "Available"]
TRANSITION_TYPE_ORDER = ["added", "status_change", "removed"]
NOT_ON_REPORT_STATE = "Not On Report"
REMOVED_STATE = "Removed"
TEAM_NAME_TO_TRICODE = {
    "Atlanta Hawks": "ATL",
    "Boston Celtics": "BOS",
    "Brooklyn Nets": "BKN",
    "Charlotte Hornets": "CHA",
    "Chicago Bulls": "CHI",
    "Cleveland Cavaliers": "CLE",
    "Dallas Mavericks": "DAL",
    "Denver Nuggets": "DEN",
    "Detroit Pistons": "DET",
    "Golden State Warriors": "GSW",
    "Houston Rockets": "HOU",
    "Indiana Pacers": "IND",
    "LA Clippers": "LAC",
    "Los Angeles Clippers": "LAC",
    "Los Angeles Lakers": "LAL",
    "Memphis Grizzlies": "MEM",
    "Miami Heat": "MIA",
    "Milwaukee Bucks": "MIL",
    "Minnesota Timberwolves": "MIN",
    "New Orleans Pelicans": "NOP",
    "New York Knicks": "NYK",
    "Oklahoma City Thunder": "OKC",
    "Orlando Magic": "ORL",
    "Philadelphia 76ers": "PHI",
    "Phoenix Suns": "PHX",
    "Portland Trail Blazers": "POR",
    "Sacramento Kings": "SAC",
    "San Antonio Spurs": "SAS",
    "Toronto Raptors": "TOR",
    "Utah Jazz": "UTA",
    "Washington Wizards": "WAS",
}
STATUS_RANK = {
    "Out": 0,
    "Doubtful": 1,
    "Questionable": 2,
    "Probable": 3,
    "Available": 4,
}

app = FastAPI(title="NBA Injury Report Live")
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


class AppState:
    def __init__(self):
        self.records: list[dict] = []
        self.notifications: list[dict] = []
        self.news_log: list[dict] = []
        self.transition_configs: list[dict] = []
        self.upcoming_games: list[dict] = []
        self.last_report_at: str = ""
        self.last_fetch: datetime | None = None
        self.last_upcoming_games_fetch: datetime | None = None
        self.lock = threading.Lock()

state = AppState()


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


def _round_15(dt: datetime) -> datetime:
    return dt.replace(minute=(dt.minute // 15) * 15, second=0, microsecond=0)


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


def _save_players_db(rows: list[dict]) -> None:
    rows = sorted(rows, key=lambda r: (-r["importance"], r["nba_team"], r["player_name"]))
    with CSV_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["player_name", "nba_team", "importance"])
        writer.writeheader()
        writer.writerows(rows)


def _load_news_log() -> list[dict]:
    if not NEWS_LOG_PATH.exists():
        return []
    try:
        with NEWS_LOG_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_news_log(rows: list[dict]) -> None:
    with NEWS_LOG_PATH.open("w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=True, indent=2)


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


def _save_transition_configs(rows: list[dict]) -> None:
    with TRANSITION_CONFIG_PATH.open("w", encoding="utf-8") as f:
        json.dump(_sort_transition_configs(rows), f, ensure_ascii=True, indent=2)


def _parse_iso_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_matchup_tricodes(matchup: str) -> tuple[str, str]:
    parts = re.findall(r"\b[A-Z]{2,4}\b", matchup or "")
    if len(parts) >= 2:
        return parts[0], parts[1]
    return "", ""


def _team_tricode_from_name(team_name: str, upcoming_games: list[dict]) -> str:
    if team_name in TEAM_NAME_TO_TRICODE:
        return TEAM_NAME_TO_TRICODE[team_name]
    team_name_lower = (team_name or "").strip().lower()
    if not team_name_lower:
        return ""
    for game in upcoming_games:
        if str(game.get("away_team", "")).strip().lower() == team_name_lower:
            return str(game.get("away_tricode", ""))
        if str(game.get("home_team", "")).strip().lower() == team_name_lower:
            return str(game.get("home_tricode", ""))
    return ""


def _match_schedule_game(record: dict, upcoming_games: list[dict]) -> dict | None:
    record_dt = _parse_iso_datetime(record.get("game_datetime_et") or "")
    away_code, home_code = _parse_matchup_tricodes(record.get("matchup", ""))
    team_tricode = _team_tricode_from_name(record.get("team", ""), upcoming_games)
    best_match = None
    best_score = None

    for game in upcoming_games:
        score = 0
        if away_code and home_code:
            if game.get("away_tricode") == away_code and game.get("home_tricode") == home_code:
                score += 100
            else:
                continue
        elif team_tricode:
            if team_tricode not in {game.get("away_tricode"), game.get("home_tricode")}:
                continue
            score += 30

        game_dt = _parse_iso_datetime(game.get("game_datetime") or "")
        if record_dt and game_dt:
            diff_seconds = abs((record_dt - game_dt).total_seconds())
            if diff_seconds > 12 * 60 * 60:
                continue
            score += max(0, 50 - int(diff_seconds // 900))

        if best_score is None or score > best_score:
            best_match = game
            best_score = score

    return best_match


def _enrich_records_with_schedule(records: list[dict], upcoming_games: list[dict]) -> list[dict]:
    enriched = []
    for record in records:
        row = dict(record)
        team_tricode = _team_tricode_from_name(row.get("team", ""), upcoming_games)
        matched_game = _match_schedule_game(row, upcoming_games)
        if matched_game:
            away_tricode = str(matched_game.get("away_tricode", ""))
            home_tricode = str(matched_game.get("home_tricode", ""))
            if not team_tricode:
                matchup_away, matchup_home = _parse_matchup_tricodes(row.get("matchup", ""))
                if matchup_away == away_tricode or matchup_home == home_tricode:
                    if row.get("team") == matched_game.get("away_team"):
                        team_tricode = away_tricode
                    elif row.get("team") == matched_game.get("home_team"):
                        team_tricode = home_tricode
            opponent_tricode = ""
            if team_tricode == away_tricode:
                opponent_tricode = home_tricode
            elif team_tricode == home_tricode:
                opponent_tricode = away_tricode

            row.update({
                "game_id": matched_game.get("game_id", ""),
                "scheduled_game_datetime": matched_game.get("game_datetime", ""),
                "scheduled_matchup": matched_game.get("matchup", ""),
                "away_tricode": away_tricode,
                "home_tricode": home_tricode,
                "team_tricode": team_tricode,
                "opponent_tricode": opponent_tricode,
            })
        else:
            row.update({
                "game_id": "",
                "scheduled_game_datetime": "",
                "scheduled_matchup": "",
                "away_tricode": "",
                "home_tricode": "",
                "team_tricode": team_tricode,
                "opponent_tricode": "",
            })
        enriched.append(row)
    return enriched


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
        cached_rows = list(state.upcoming_games)
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
    except Exception as exc:
        log.error(f"Upcoming games fetch error: {exc}")
        return cached_rows

    with state.lock:
        state.upcoming_games = rows
        state.last_upcoming_games_fetch = now
    return rows


def _next_15(dt: datetime) -> datetime:
    return dt.replace(second=0, microsecond=0) + timedelta(minutes=15)


def _compute_schedule(now: datetime, last_report_at: str) -> tuple[datetime, datetime, bool]:
    if last_report_at:
        target_dt = _next_15(datetime.fromisoformat(last_report_at))
    else:
        target_dt = _round_15(now)
    aggressive_start = target_dt - timedelta(seconds=AGGRESSIVE_LEAD_SECONDS)
    needs_target_report = last_report_at != target_dt.isoformat()
    aggressive_mode = needs_target_report and now >= aggressive_start
    return target_dt, aggressive_start, aggressive_mode


def _find_latest_available_report(now: datetime) -> datetime | None:
    candidate = _round_15(now)
    for _ in range(INITIAL_REPORT_LOOKBACK_INTERVALS + 1):
        if _report_exists(candidate):
            return candidate
        candidate -= timedelta(minutes=15)
    return None


def _record_key(record: dict) -> str:
    return f"{record['team']}|{record['player']}"


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


def _parse_game_datetime(game_date: str, game_time: str) -> str | None:
    if not game_date or not game_time:
        return None
    try:
        clean_time = game_time.replace(" (ET)", "")
        dt = datetime.strptime(f"{game_date} {clean_time}", "%m/%d/%Y %I:%M")
        return dt.replace(tzinfo=ET).isoformat()
    except ValueError:
        return None


def _fetch_report(dt: datetime) -> list[dict]:
    dt_naive = dt.replace(tzinfo=None) if dt.tzinfo else dt
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = open(os.devnull, "w")
    sys.stderr = open(os.devnull, "w")
    try:
        raw = injury.get_reportdata(dt_naive)
    finally:
        sys.stdout.close()
        sys.stderr.close()
        sys.stdout, sys.stderr = old_out, old_err

    data = json.loads(raw) if isinstance(raw, str) else raw
    records = []
    for r in data:
        player = r.get("Player Name")
        status = r.get("Current Status")
        if not player or not status:
            continue
        records.append({
            "team": r.get("Team") or "",
            "game_time": r.get("Game Time") or "",
            "game_date": r.get("Game Date") or "",
            "matchup": r.get("Matchup") or "",
            "player": str(player),
            "status": str(status),
            "injury": r.get("Reason") or "",
            "game_datetime_et": _parse_game_datetime(
                r.get("Game Date") or "",
                r.get("Game Time") or "",
            ),
        })
    return _enrich_records_with_schedule(records, _get_upcoming_games())


def _report_exists(dt: datetime) -> bool:
    dt_naive = dt.replace(tzinfo=None) if dt.tzinfo else dt
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = open(os.devnull, "w")
    sys.stderr = open(os.devnull, "w")
    try:
        return bool(injury.check_reportvalid(dt_naive))
    except Exception:
        return False
    finally:
        sys.stdout.close()
        sys.stderr.close()
        sys.stdout, sys.stderr = old_out, old_err


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
            and previous.get("game_time") == record.get("game_time")
            and previous.get("game_date") == record.get("game_date")
        ):
            record["last_update_at"] = previous.get("last_update_at", report_at)
        else:
            record["last_update_at"] = report_at
        hydrated.append(record)

    return hydrated


def _diff(old: list[dict], new: list[dict], report_at: str) -> list[dict]:
    old_map = {_record_key(r): r for r in old}
    new_map = {_record_key(r): r for r in new}
    notifications = []

    for key in sorted(set(new_map) - set(old_map)):
        r = new_map[key]
        notifications.append({
            "type": "added",
            "player": r["player"],
            "team": r["team"],
            "status": r["status"],
            "from_status": NOT_ON_REPORT_STATE,
            "to_status": r["status"],
            "matchup": r.get("matchup", ""),
            "game_datetime_et": r.get("game_datetime_et"),
            "game_id": r.get("game_id", ""),
            "scheduled_game_datetime": r.get("scheduled_game_datetime", ""),
            "scheduled_matchup": r.get("scheduled_matchup", ""),
            "away_tricode": r.get("away_tricode", ""),
            "home_tricode": r.get("home_tricode", ""),
            "team_tricode": r.get("team_tricode", ""),
            "opponent_tricode": r.get("opponent_tricode", ""),
            "injury": r.get("injury", ""),
            "tone": _event_tone("added", "", r["status"]),
            "detail": f"Added — {r['status']}",
            "timestamp_at": report_at,
        })

    for key in sorted(set(old_map) - set(new_map)):
        r = old_map[key]
        if r.get("status") == "Available":
            continue
        notifications.append({
            "type": "removed",
            "player": r["player"],
            "team": r["team"],
            "status": r.get("status", ""),
            "from_status": r.get("status", ""),
            "to_status": REMOVED_STATE,
            "matchup": r.get("matchup", ""),
            "game_datetime_et": r.get("game_datetime_et"),
            "game_id": r.get("game_id", ""),
            "scheduled_game_datetime": r.get("scheduled_game_datetime", ""),
            "scheduled_matchup": r.get("scheduled_matchup", ""),
            "away_tricode": r.get("away_tricode", ""),
            "home_tricode": r.get("home_tricode", ""),
            "team_tricode": r.get("team_tricode", ""),
            "opponent_tricode": r.get("opponent_tricode", ""),
            "injury": r.get("injury", ""),
            "tone": _event_tone("removed", r.get("status", ""), ""),
            "detail": "Removed from report",
            "timestamp_at": report_at,
        })

    for key in sorted(set(old_map) & set(new_map)):
        o, n = old_map[key], new_map[key]
        if o["status"] != n["status"]:
            notifications.append({
                "type": "status_change",
                "player": n["player"],
                "team": n["team"],
                "status": n["status"],
                "from_status": o["status"],
                "to_status": n["status"],
                "matchup": n.get("matchup", ""),
                "game_datetime_et": n.get("game_datetime_et"),
                "game_id": n.get("game_id", ""),
                "scheduled_game_datetime": n.get("scheduled_game_datetime", ""),
                "scheduled_matchup": n.get("scheduled_matchup", ""),
                "away_tricode": n.get("away_tricode", ""),
                "home_tricode": n.get("home_tricode", ""),
                "team_tricode": n.get("team_tricode", ""),
                "opponent_tricode": n.get("opponent_tricode", ""),
                "injury": n.get("injury", ""),
                "tone": _event_tone("status_change", o["status"], n["status"]),
                "detail": f"{o['status']} → {n['status']}",
                "timestamp_at": report_at,
            })
        elif o["injury"] != n["injury"]:
            notifications.append({
                "type": "injury_change",
                "player": n["player"],
                "team": n["team"],
                "status": n["status"],
                "from_status": o["status"],
                "to_status": n["status"],
                "matchup": n.get("matchup", ""),
                "game_datetime_et": n.get("game_datetime_et"),
                "game_id": n.get("game_id", ""),
                "scheduled_game_datetime": n.get("scheduled_game_datetime", ""),
                "scheduled_matchup": n.get("scheduled_matchup", ""),
                "away_tricode": n.get("away_tricode", ""),
                "home_tricode": n.get("home_tricode", ""),
                "team_tricode": n.get("team_tricode", ""),
                "opponent_tricode": n.get("opponent_tricode", ""),
                "injury": n.get("injury", ""),
                "tone": _event_tone("injury_change", o["status"], n["status"]),
                "detail": "Injury details changed",
                "timestamp_at": report_at,
            })

    return notifications


def _poll_loop():
    while True:
        sleep_seconds = POLL_SECONDS
        try:
            now = datetime.now(ET)
            with state.lock:
                last_report_at = state.last_report_at
                last_fetch = state.last_fetch
                has_records = bool(state.records)

            target_dt, aggressive_start, aggressive_mode = _compute_schedule(now, last_report_at)
            if not last_report_at and not has_records:
                initial_target = _find_latest_available_report(now)
                if initial_target is not None:
                    target_dt = initial_target
                    aggressive_start = target_dt - timedelta(seconds=AGGRESSIVE_LEAD_SECONDS)
                    aggressive_mode = False
            target_iso = target_dt.isoformat()
            elapsed = (now - last_fetch).total_seconds() if last_fetch else None
            should_check = last_fetch is None or aggressive_mode or (elapsed is not None and elapsed >= POLL_SECONDS)

            if should_check:
                with state.lock:
                    state.last_fetch = now

                if _report_exists(target_dt):
                    log.info(f"Fetching report for {target_dt.strftime('%m/%d/%Y, %I:%M %p %Z')}")
                    new_records = _fetch_report(target_dt)
                    report_at = target_iso

                    with state.lock:
                        if state.records:
                            new_records = _hydrate_last_updates(state.records, new_records, report_at)
                            changes = _diff(state.records, new_records, report_at)
                            if changes:
                                state.notifications = changes + state.notifications
                                state.notifications = state.notifications[:500]
                                state.news_log = changes + state.news_log
                                state.news_log = state.news_log[:5000]
                                _save_news_log(state.news_log)
                                log.info(f"{len(changes)} changes detected")
                            else:
                                log.info("No changes")
                        else:
                            new_records = _hydrate_last_updates([], new_records, report_at)
                            log.info(f"Initial load: {len(new_records)} records")

                        state.records = new_records
                        state.last_report_at = report_at
                elif aggressive_mode:
                    log.info(f"Waiting for overdue report {target_dt.strftime('%m/%d/%Y, %I:%M %p %Z')}")

            with state.lock:
                current_last_report_at = state.last_report_at
                current_last_fetch = state.last_fetch

            if current_last_report_at != target_iso and now >= aggressive_start:
                sleep_seconds = 1
            else:
                next_wakeup_target = (
                    _next_15(target_dt)
                    if current_last_report_at == target_iso
                    else aggressive_start
                )
                seconds_until_target = max(1, int((next_wakeup_target - now).total_seconds()))
                if current_last_fetch is None:
                    sleep_seconds = min(POLL_SECONDS, seconds_until_target)
                else:
                    baseline_remaining = max(1, int(POLL_SECONDS - (now - current_last_fetch).total_seconds()))
                    sleep_seconds = min(baseline_remaining, seconds_until_target)

        except Exception as e:
            log.error(f"Poll error: {e}")
            sleep_seconds = 1

        time.sleep(max(1, sleep_seconds))


@app.on_event("startup")
def startup():
    with state.lock:
        state.news_log = _load_news_log()
        state.transition_configs = _load_transition_configs()
    t = threading.Thread(target=_poll_loop, daemon=True)
    t.start()


@app.get("/")
def index():
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.get("/api/report")
def get_report():
    with state.lock:
        last_report_at = state.last_report_at
        last_fetch = state.last_fetch
        return JSONResponse({
            "records": state.records,
            "last_report_at": last_report_at,
            "total": len(state.records),
            "status": {
                "connected": True,
                "poll_mode": (
                    "aggressive"
                    if _compute_schedule(datetime.now(ET), last_report_at)[2]
                    else "normal"
                ),
                "next_target_at": _compute_schedule(datetime.now(ET), last_report_at)[0].isoformat(),
                "aggressive_start_at": _compute_schedule(datetime.now(ET), last_report_at)[1].isoformat(),
                "last_fetch_at": last_fetch.isoformat() if last_fetch else None,
                "baseline_interval_seconds": POLL_SECONDS,
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
    return JSONResponse({
        "rows": rows,
        "count": len(rows),
        "window_days": UPCOMING_GAMES_WINDOW_DAYS,
    })


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
        _save_news_log(state.news_log)
    return JSONResponse({"ok": True})


@app.get("/api/clear-notifications")
def clear_notifications():
    with state.lock:
        state.notifications = []
    return JSONResponse({"ok": True})


@app.get("/api/players-db")
def get_players_db():
    with state.lock:
        rows = _load_players_db()
    return JSONResponse({
        "players": rows,
        "total": len(rows),
        "teams": sorted({row["nba_team"] for row in rows}),
    })


@app.get("/api/transition-config")
def get_transition_config():
    with state.lock:
        rows = list(state.transition_configs)
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
        _save_transition_configs(state.transition_configs)

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
        rows = _load_players_db()
        updated = False
        for idx, row in enumerate(rows):
            if row["player_name"].lower() == normalized["player_name"].lower():
                rows[idx] = normalized
                updated = True
                break
        if not updated:
            rows.append(normalized)
        _save_players_db(rows)

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
        rows = _load_players_db()
        new_rows = [row for row in rows if row["player_name"].lower() != player_name.lower()]
        if len(new_rows) == len(rows):
            raise HTTPException(status_code=404, detail="player not found")
        _save_players_db(new_rows)

    return JSONResponse({"ok": True, "player_name": player_name})
