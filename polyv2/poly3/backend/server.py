from __future__ import annotations

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

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / ".env")

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

# Add nbainjuries to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "parse_nba_injuries_repo" / "parse_nba_injuries"))

# Add twitter_parser to path
TWITTER_PARSER_DIR = Path(__file__).resolve().parent.parent.parent / "twitter_parser" / "twitter_parser"
if str(TWITTER_PARSER_DIR) not in sys.path:
    sys.path.insert(0, str(TWITTER_PARSER_DIR))

with _suppress_stdio():
    from nbainjuries import injury

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import httpx
from pydantic import BaseModel

import database as db

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("nba-injuries")

POLY_PRIVATE_KEY = os.environ.get("POLY_PRIVATE_KEY", "")
POLY_FUNDER_ADDRESS = os.environ.get("POLY_FUNDER_ADDRESS", "")
POLY_SIGNATURE_TYPE = int(os.environ.get("POLY_SIGNATURE_TYPE", "1"))

ET = ZoneInfo("America/New_York")
POLL_SECONDS = 120
AGGRESSIVE_LEAD_SECONDS = 120
CSV_PATH = Path(__file__).resolve().parent.parent.parent / "parse_nba_injuries_repo" / "top_300_nba_players.csv"
SCHEDULE_URL = "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json"
UPCOMING_GAMES_WINDOW_DAYS = 2
UPCOMING_GAMES_CACHE_SECONDS = 600
POLYMARKET_CACHE_TTL_SECONDS = 300
POLYMARKET_FAST_POLL_INTERVAL = 0.5
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

GAMMA_API_URL = "https://gamma-api.polymarket.com/events"

app = FastAPI(title="NBA Injury Report Live")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_clob_client = None

def _get_clob_client():
    global _clob_client
    if _clob_client is not None:
        return _clob_client
    if not POLY_PRIVATE_KEY:
        return None
    try:
        from py_clob_client.client import ClobClient
        _clob_client = ClobClient(
            host="https://clob.polymarket.com",
            key=POLY_PRIVATE_KEY,
            chain_id=137,
            signature_type=POLY_SIGNATURE_TYPE,
            funder=POLY_FUNDER_ADDRESS,
        )
        creds = _clob_client.create_or_derive_api_creds()
        _clob_client.set_api_creds(creds)
        log.info("Polymarket CLOB client initialized")
    except Exception as exc:
        log.warning(f"CLOB client init failed: {exc}")
        _clob_client = None
    return _clob_client


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
        self.auto_trade_enabled: bool = False
        self.bet_threshold: float = 10.0
        self.bet_amount: float = 10.0
        self.bet_log: list[dict] = []
        self.auto_trade_armed_at: str = ""  # only trade batches newer than this
        # Skip auto-trade on pure injury-report batches whose bucket time (ET)
        # falls inside [start, end). News items still trade.
        self.bet_block_hour_start: int = 16
        self.bet_block_hour_end: int = 18
        self.bet_block_minute_start: int = 0
        self.bet_block_minute_end: int = 0
        self.twitter_injuries: dict = {}  # player_key → { status, timestamp }
        self.twitter_enabled: bool = True  # manual on/off
        self.twitter_blackout_until: float = 0  # epoch — skip tweet processing until this time
        self.polymarket_live_odds: dict = {}
        self.polymarket_cache: dict = {}
        self.lock = threading.Lock()

state = AppState()


class PlayerDbEntry(BaseModel):
    player_name: str
    nba_team: str
    importance: int


class PlayerDbDelete(BaseModel):
    player_name: str


class BettingConfigUpdate(BaseModel):
    auto_trade_enabled: bool
    threshold: float
    bet_amount: float = 10.0
    block_hour_start: int = 16
    block_hour_end: int = 18


class SimulateInjuryRequest(BaseModel):
    player_name: str
    target_status: str


class TransitionConfigEntry(BaseModel):
    transition_type: str
    from_state: str
    to_state: str
    score: int


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

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


def _load_transition_configs_with_defaults() -> list[dict]:
    """Load transition configs from DB, merging with defaults so every
    expected transition is present."""
    defaults = {_transition_key(row): row for row in _default_transition_configs()}
    saved = db.load_transition_configs()
    if saved:
        for row in saved:
            try:
                normalized = _normalize_transition_row(row)
            except ValueError:
                continue
            key = _transition_key(normalized)
            if key in defaults:
                defaults[key]["score"] = normalized["score"]
    return _sort_transition_configs(list(defaults.values()))


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
                "detail": f"{o['status']} \u2192 {n['status']}",
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


def _build_polymarket_slug(game: dict) -> str:
    away = str(game.get("away_tricode", "")).strip().lower()
    home = str(game.get("home_tricode", "")).strip().lower()
    game_dt = str(game.get("game_datetime", ""))
    if not away or not home or not game_dt:
        return ""
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", game_dt)
    if not m:
        return ""
    return f"nba-{away}-{home}-{m.group(1)}-{m.group(2)}-{m.group(3)}"


def _fetch_polymarket_for_slug(slug: str) -> dict | None:
    try:
        resp = httpx.get(
            GAMMA_API_URL,
            params={"slug": slug},
            timeout=10.0,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp.raise_for_status()
        events = resp.json()
        if not events:
            return None
        evt = events[0]
        markets = evt.get("markets") or []
        return {
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
                    "clob_token_ids": json.loads(m["clobTokenIds"]) if isinstance(m.get("clobTokenIds"), str) else (m.get("clobTokenIds") or []),
                    "condition_id": m.get("conditionId", ""),
                }
                for m in markets
                if m.get("sportsMarketType") in ("moneyline", "spreads", "totals", "first_half_moneyline", "first_half_spreads", "first_half_totals")
            ],
            "fetched_at": time.time(),
        }
    except Exception as exc:
        log.warning(f"Polymarket fetch error for {slug}: {exc}")
        return None


def _polymarket_poll_loop():
    while True:
        try:
            games = _get_upcoming_games()
            slugs = []
            for game in games:
                slug = _build_polymarket_slug(game)
                if slug:
                    slugs.append(slug)
            if slugs:
                delay_per = max(0.05, POLYMARKET_FAST_POLL_INTERVAL / len(slugs))
                for slug in slugs:
                    data = _fetch_polymarket_for_slug(slug)
                    if data:
                        with state.lock:
                            state.polymarket_live_odds[slug] = data
                    time.sleep(delay_per)
            else:
                time.sleep(POLYMARKET_FAST_POLL_INTERVAL)
        except Exception as exc:
            log.warning(f"Polymarket poll loop error: {exc}")
            time.sleep(POLYMARKET_FAST_POLL_INTERVAL)


def _pick_best_market(slug: str) -> dict | None:
    with state.lock:
        odds_data = state.polymarket_live_odds.get(slug)
    if not odds_data:
        return None
    markets = odds_data.get("markets", [])
    best = None
    best_closeness = float("inf")
    for m in markets:
        mtype = m.get("type", "")
        if mtype not in ("moneyline", "spreads"):
            continue
        prices = m.get("prices", [])
        if not prices:
            continue
        closeness = min(abs(float(p) - 0.5) for p in prices)
        if closeness < best_closeness:
            best_closeness = closeness
            best = {
                "market_type": mtype,
                "question": m.get("question", ""),
                "outcomes": m.get("outcomes", []),
                "prices": prices,
                "closeness": round(closeness, 4),
                "clob_token_ids": m.get("clob_token_ids", []),
                "condition_id": m.get("condition_id", ""),
            }
    return best


def _place_polymarket_bet(batch: dict, market_info: dict, bet_amount: float) -> dict | None:
    """Attempt to place a bet on Polymarket. Returns position dict or None."""
    client = _get_clob_client()
    profitable_team = batch["home_tricode"] if batch["edge_score"] > 0 else batch["away_tricode"]

    outcomes = market_info.get("outcomes", [])
    prices = market_info.get("prices", [])
    token_ids = market_info.get("clob_token_ids", [])
    condition_id = market_info.get("condition_id", "")

    # Find the outcome matching the profitable team
    # Build reverse map: tricode → team name keywords (e.g. GSW → "golden state warriors")
    tricode_to_names = {}
    for full_name, tricode in TEAM_NAME_TO_TRICODE.items():
        tricode_to_names.setdefault(tricode, []).append(full_name.lower())

    team_keywords = tricode_to_names.get(profitable_team, [])

    target_idx = None
    for i, outcome in enumerate(outcomes):
        outcome_lower = str(outcome).lower()
        # Direct tricode match
        if profitable_team.lower() == outcome_lower:
            target_idx = i
            break
        # Check if any word from the team name appears in the outcome
        for full_name in team_keywords:
            # Match on city or mascot (e.g. "Warriors" in "Golden State Warriors")
            name_parts = full_name.split()
            if any(part in outcome_lower for part in name_parts):
                target_idx = i
                break
        if target_idx is not None:
            break

    if target_idx is None or target_idx >= len(token_ids) or target_idx >= len(prices):
        log.warning(f"Could not match outcome for team {profitable_team} in {outcomes}")
        return None

    token_id = str(token_ids[target_idx])
    buy_price = float(prices[target_idx])

    if buy_price <= 0 or buy_price >= 1:
        log.warning(f"Invalid buy price {buy_price}")
        return None

    shares = round(bet_amount / buy_price, 2)

    # Build position record
    now_iso = datetime.now(ET).isoformat()
    slug = ""
    for game in _get_upcoming_games():
        s = _build_polymarket_slug(game)
        if s and game.get("away_tricode") == batch.get("away_tricode") and game.get("home_tricode") == batch.get("home_tricode"):
            slug = s
            break

    position = {
        "id": f"pos-{int(time.time() * 1000)}",
        "created_at": now_iso,
        "game_matchup": batch.get("matchup", ""),
        "game_datetime": batch.get("game_datetime_et", ""),
        "away_tricode": batch.get("away_tricode", ""),
        "home_tricode": batch.get("home_tricode", ""),
        "market_type": market_info.get("market_type", ""),
        "market_question": market_info.get("question", ""),
        "bet_team": profitable_team,
        "token_id": token_id,
        "outcome": str(outcomes[target_idx]),
        "condition_id": condition_id,
        "buy_price": buy_price,
        "current_price": buy_price,
        "shares": shares,
        "amount_usd": round(shares * buy_price, 2),
        "pnl": 0.0,
        "status": "open",
        "sell_price": None,
        "sold_at": None,
        "order_id": "",
        "sell_order_id": None,
        "slug": slug,
        "batch_key": batch.get("key", ""),
    }

    # Try to place order via CLOB
    if client and token_id:
        try:
            from py_clob_client.clob_types import OrderArgs
            order_price = min(0.99, round(buy_price + 0.02, 2))
            order = client.create_and_post_order(OrderArgs(
                token_id=token_id,
                price=order_price,
                size=shares,
                side="BUY",
            ))
            position["order_id"] = order.get("orderID", "") if isinstance(order, dict) else str(order)
            position["status"] = "open"
            log.info(f"CLOB order placed: {position['order_id']} for {profitable_team} {shares} shares @ {buy_price}")
        except Exception as exc:
            log.error(f"CLOB order failed: {exc}")
            position["status"] = "logged"
    else:
        position["status"] = "logged"
        log.info(f"No CLOB client, position logged only: {profitable_team} {shares} shares @ {buy_price}")

    db.save_position(position)
    return position


def _normalize_name_tokens(value: str) -> list[str]:
    return [t for t in re.sub(r"['\u2019`.]+", "", value.lower()).split() if re.match(r"^[a-z0-9]+$", t)]


def _player_name_lookup_keys(value: str) -> list[str]:
    tokens = _normalize_name_tokens(value.strip())
    if not tokens:
        return []
    keys = set()
    keys.add(" ".join(tokens))
    keys.add(" ".join(sorted(tokens)))
    if "," in value:
        parts = [p.strip() for p in value.split(",") if p.strip()]
        if len(parts) >= 2:
            reordered = _normalize_name_tokens(" ".join(parts[1:]) + " " + parts[0])
            keys.add(" ".join(reordered))
            keys.add(" ".join(sorted(reordered)))
    return list(keys)


def _build_latest_batch() -> list[dict]:
    players = db.load_players()
    importance_map: dict[str, int] = {}
    for row in players:
        imp = int(row.get("importance", 0))
        for key in _player_name_lookup_keys(row.get("player_name", "")):
            if imp > importance_map.get(key, 0):
                importance_map[key] = imp

    with state.lock:
        transition_configs = list(state.transition_configs)
        news = list(state.news_log)
        upcoming = list(state.upcoming_games)

    transition_map: dict[str, int] = {}
    for row in transition_configs:
        k = f"{row['transition_type']}|{row['from_state']}|{row['to_state']}"
        transition_map[k] = int(row.get("score", 0))

    valid_game_keys = set()
    for game in upcoming:
        gid = game.get("game_id", "")
        if gid:
            valid_game_keys.add(f"id:{gid}")
        gdt = game.get("game_datetime", "")
        gm = game.get("matchup", "")
        if gdt and gm:
            valid_game_keys.add(f"slot:{gdt}|{gm}")

    now_ms = time.time() * 1000
    two_days_ms = 2 * 24 * 60 * 60 * 1000

    def _time_value(v: str) -> float | None:
        if not v:
            return None
        try:
            dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
            return dt.timestamp() * 1000
        except Exception:
            return None

    def _canonical_game_key(row: dict) -> str:
        gid = row.get("game_id", "")
        if gid:
            return f"id:{gid}"
        gdt = row.get("scheduled_game_datetime", "")
        gm = row.get("scheduled_matchup", "")
        if gdt and gm:
            return f"slot:{gdt}|{gm}"
        return ""

    grouped: dict[str, dict] = {}
    for row in news:
        game_key = _canonical_game_key(row)
        if not game_key or game_key not in valid_game_keys:
            continue
        game_time_ms = _time_value(row.get("scheduled_game_datetime", "") or row.get("game_datetime_et", ""))
        if game_time_ms is None or game_time_ms < now_ms or game_time_ms > now_ms + two_days_ms:
            continue
        if not row.get("away_tricode") or not row.get("home_tricode") or not row.get("team_tricode") or not row.get("opponent_tricode"):
            continue

        # Score the row
        lookup_keys = _player_name_lookup_keys(row.get("player", ""))
        player_importance = 0
        for k in lookup_keys:
            v = importance_map.get(k, 0)
            if v > player_importance:
                player_importance = v

        rtype = row.get("type", "")
        if rtype == "added":
            t_type, t_from, t_to = "added", row.get("from_status", "Not On Report"), row.get("to_status", "") or row.get("status", "")
        elif rtype == "removed":
            t_type, t_from, t_to = "removed", row.get("from_status", "") or row.get("status", ""), row.get("to_status", "Removed")
        elif rtype == "status_change":
            t_type, t_from, t_to = "status_change", row.get("from_status", ""), row.get("to_status", "") or row.get("status", "")
        else:
            t_type, t_from, t_to = rtype, row.get("from_status", ""), row.get("to_status", "")

        transition_score = transition_map.get(f"{t_type}|{t_from}|{t_to}", 0)
        score = 0.0 if rtype == "injury_change" or not player_importance else round(player_importance ** 2 * transition_score / 100, 2)

        credited_team = ""
        own_team = row.get("team_tricode", "")
        opponent_team = row.get("opponent_tricode", "")
        if score > 0:
            credited_team = own_team
        elif score < 0:
            credited_team = opponent_team

        ts_at = row.get("timestamp_at", "")
        bucket_ms = (int(_time_value(ts_at) or now_ms) // 5000) * 5000
        batch_key = f"{bucket_ms}|{game_key}"

        if batch_key not in grouped:
            grouped[batch_key] = {
                "key": batch_key,
                "game_id": row.get("game_id", ""),
                "batch_time": datetime.fromtimestamp(bucket_ms / 1000, tz=ET).isoformat(),
                "game_datetime_et": row.get("scheduled_game_datetime", "") or row.get("game_datetime_et", ""),
                "matchup": row.get("scheduled_matchup", "") or row.get("matchup", ""),
                "away_tricode": row.get("away_tricode", ""),
                "home_tricode": row.get("home_tricode", ""),
                "away_score": 0.0,
                "home_score": 0.0,
                "items": [],
            }

        g = grouped[batch_key]
        impact_value = abs(score)
        if credited_team and impact_value:
            if credited_team == g["away_tricode"]:
                g["away_score"] = round(g["away_score"] + impact_value, 2)
            if credited_team == g["home_tricode"]:
                g["home_score"] = round(g["home_score"] + impact_value, 2)
        g["items"].append({**row, "score": score, "credited_team": credited_team, "impact_value": round(impact_value, 2)})

    batches = []
    for g in grouped.values():
        edge = round(g["home_score"] - g["away_score"], 2)
        batches.append({**g, "edge_score": edge})

    batches.sort(key=lambda b: (_time_value(b["batch_time"]) or 0), reverse=True)
    return batches


def _is_blocked_injury_report_batch(batch: dict, block_start: int, block_end: int) -> bool:
    if block_start == block_end:
        return False
    batch_time = batch.get("batch_time", "")
    if not batch_time:
        return False
    try:
        dt = datetime.fromisoformat(batch_time)
    except Exception:
        return False
    try:
        hour = dt.astimezone(ET).hour
    except Exception:
        hour = dt.hour
    # Support wrap-around windows (e.g. 22–02)
    if block_start < block_end:
        in_window = block_start <= hour < block_end
    else:
        in_window = hour >= block_start or hour < block_end
    if not in_window:
        return False
    items = batch.get("items", []) or []
    if not items:
        return False
    # Pure injury-report batch: no item carries a news source tag
    return all(not str(item.get("source") or "").strip() for item in items)


def _evaluate_auto_trade(force: bool = False):
    with state.lock:
        if not force and not state.auto_trade_enabled:
            return
        threshold = state.bet_threshold
        armed_at = state.auto_trade_armed_at
        block_start = int(state.bet_block_hour_start)
        block_end = int(state.bet_block_hour_end)

    batches = _build_latest_batch()
    if not batches:
        return

    latest_batch_time = batches[0]["batch_time"] if batches else None
    if not latest_batch_time:
        return

    # Only consider batches from the most recent batch_time
    latest_batches = [b for b in batches if b["batch_time"] == latest_batch_time]

    for batch in latest_batches:
        batch_time = batch.get("batch_time", "")

        # Skip batches older than our armed watermark — never trade old news
        if armed_at and batch_time <= armed_at:
            continue

        # Skip pure injury-report batches whose ET hour falls inside the block
        # window. During that window the official injury report drops and most
        # "out" players are already known — news items (twitter/Underdog/etc.)
        # still trigger trades because they have a non-empty `source`.
        if _is_blocked_injury_report_batch(batch, block_start, block_end):
            log.info(f"Auto-trade skipped (block window {block_start}-{block_end}): {batch.get('matchup', '')} batch_time={batch_time}")
            continue

        edge_score = batch.get("edge_score", 0)
        if abs(edge_score) < threshold:
            continue

        game_id = batch.get("game_id", "") or batch.get("key", "")
        dedup_key = f"{batch_time}|{game_id}"

        with state.lock:
            already_logged = any(
                entry.get("_dedup_key") == dedup_key
                for entry in state.bet_log
            )
        if already_logged:
            continue

        profitable_team = batch["home_tricode"] if edge_score > 0 else batch["away_tricode"]
        slug = ""
        for game in _get_upcoming_games():
            s = _build_polymarket_slug(game)
            if s and (
                (game.get("away_tricode") == batch.get("away_tricode") and game.get("home_tricode") == batch.get("home_tricode"))
            ):
                slug = s
                break

        market_info = _pick_best_market(slug) if slug else None

        is_simulated = any(item.get("simulated") for item in batch.get("items", []))

        # ── Place real bet on Polymarket ──
        position = None
        with state.lock:
            bet_amount = state.bet_amount
        if market_info and bet_amount > 0:
            try:
                position = _place_polymarket_bet(batch, market_info, bet_amount)
            except Exception as exc:
                log.error(f"Position placement error: {exc}")

        trade_status = "executed" if (position and position.get("order_id")) else "logged"

        bet_entry = {
            "id": f"bet-{int(time.time() * 1000)}",
            "timestamp": datetime.now(ET).isoformat(),
            "batch_time": batch_time,
            "game_id": game_id,
            "matchup": batch.get("matchup", ""),
            "away_tricode": batch.get("away_tricode", ""),
            "home_tricode": batch.get("home_tricode", ""),
            "edge_score": edge_score,
            "threshold": threshold,
            "profitable_team": profitable_team,
            "market_type": market_info["market_type"] if market_info else "",
            "market_question": market_info["question"] if market_info else "",
            "market_outcomes": market_info["outcomes"] if market_info else [],
            "market_prices": market_info["prices"] if market_info else [],
            "market_closeness": market_info["closeness"] if market_info else None,
            "status": trade_status,
            "simulated": is_simulated,
            "position_id": position["id"] if position else "",
            "_dedup_key": dedup_key,
        }

        with state.lock:
            state.bet_log.insert(0, bet_entry)
            state.bet_log = state.bet_log[:500]
            db.save_bet_log(state.bet_log)

        log.info(f"Auto-trade {trade_status}: {batch.get('matchup', '')} edge={edge_score} team={profitable_team} position={position['id'] if position else 'none'}")


# ── Process tweet injury events into the scoring pipeline ──

_STATUS_NORMALIZE = {"out": "Out", "doubtful": "Doubtful", "questionable": "Questionable",
                     "probable": "Probable", "available": "Available"}


def _process_tweet_events(events: list[dict]):
    """When TweetAnalyzer finds injury events, feed them into the news/scoring pipeline."""
    if not events:
        return

    with state.lock:
        if not state.twitter_enabled:
            return
        if time.time() < state.twitter_blackout_until:
            remaining = int(state.twitter_blackout_until - time.time())
            log.info(f"[Tweet] Skipping processing — blackout for {remaining}s more (report window)")
            return

    players = db.load_players()
    player_team_map: dict[str, str] = {}
    for p in players:
        for key in _player_name_lookup_keys(p.get("player_name", "")):
            if key not in player_team_map:
                player_team_map[key] = p.get("nba_team", "")

    upcoming = _get_upcoming_games()
    now_iso = datetime.now(ET).isoformat()
    new_entries = []

    for event in events:
        player_name = (event.get("player_name") or "").strip()
        raw_status = (event.get("status") or "").strip()
        injury_reason = event.get("injury") or "Per @UnderdogNBA"

        new_status = _STATUS_NORMALIZE.get(raw_status.lower(), "")
        if not player_name or not new_status:
            continue

        # Find team from DB
        lookup_keys = _player_name_lookup_keys(player_name)
        team_name = ""
        for key in lookup_keys:
            if key in player_team_map:
                team_name = player_team_map[key]
                break
        if not team_name:
            log.info(f"[Tweet] Player '{player_name}' not in DB, skipping")
            continue

        # Canonical key for twitter_injuries tracking
        tw_key = " ".join(sorted(lookup_keys[0].split())) if lookup_keys else ""

        # Check if we already tweeted the same status
        with state.lock:
            existing_tw = state.twitter_injuries.get(tw_key)
        if existing_tw and existing_tw.get("status") == new_status:
            continue  # Already have this from a previous tweet

        # Check current status from official NBA records
        from_status = None
        with state.lock:
            for r in state.records:
                record_keys = _player_name_lookup_keys(r.get("player", ""))
                if any(k in lookup_keys for k in record_keys):
                    from_status = r.get("status", "")
                    break

        # Also check prior tweet status
        if from_status is None and existing_tw:
            from_status = existing_tw.get("status")

        if from_status is None:
            from_status = NOT_ON_REPORT_STATE

        if from_status == new_status:
            continue  # No change

        # Derive transition
        if from_status == NOT_ON_REPORT_STATE:
            transition_type = "added"
            detail = f"[TWEET] Added \u2014 {new_status}"
        else:
            transition_type = "status_change"
            detail = f"[TWEET] {from_status} \u2192 {new_status}"

        # Match to upcoming game
        team_tricode = _team_tricode_from_name(team_name, upcoming)
        fake_record = {"team": team_name, "matchup": "", "game_datetime_et": None}
        matched_game = _match_schedule_game(fake_record, upcoming)

        away_tricode = home_tricode = opponent_tricode = ""
        game_id = scheduled_game_datetime = scheduled_matchup = ""

        if matched_game:
            away_tricode = str(matched_game.get("away_tricode", ""))
            home_tricode = str(matched_game.get("home_tricode", ""))
            game_id = matched_game.get("game_id", "")
            scheduled_game_datetime = matched_game.get("game_datetime", "")
            scheduled_matchup = matched_game.get("matchup", "")
            if not team_tricode:
                if team_name == matched_game.get("away_team"):
                    team_tricode = away_tricode
                elif team_name == matched_game.get("home_team"):
                    team_tricode = home_tricode
            if team_tricode == away_tricode:
                opponent_tricode = home_tricode
            elif team_tricode == home_tricode:
                opponent_tricode = away_tricode

        tone = _event_tone(transition_type, from_status if from_status != NOT_ON_REPORT_STATE else "", new_status)

        entry = {
            "type": transition_type,
            "player": player_name,
            "team": team_name,
            "status": new_status,
            "from_status": from_status,
            "to_status": new_status,
            "matchup": scheduled_matchup,
            "game_datetime_et": scheduled_game_datetime,
            "game_id": game_id,
            "scheduled_game_datetime": scheduled_game_datetime,
            "scheduled_matchup": scheduled_matchup,
            "away_tricode": away_tricode,
            "home_tricode": home_tricode,
            "team_tricode": team_tricode,
            "opponent_tricode": opponent_tricode,
            "injury": injury_reason,
            "tone": tone,
            "detail": detail,
            "timestamp_at": now_iso,
            "source": "twitter",
        }
        new_entries.append(entry)

        # Track this tweet-sourced injury
        with state.lock:
            state.twitter_injuries[tw_key] = {"status": new_status, "timestamp": now_iso}

    if not new_entries:
        return

    # Inject into news_log + notifications
    with state.lock:
        state.news_log = new_entries + state.news_log
        state.news_log = state.news_log[:5000]
        state.notifications = new_entries + state.notifications
        state.notifications = state.notifications[:500]

    # Save outside lock
    news_snapshot = None
    with state.lock:
        news_snapshot = list(state.news_log)
    db.save_news_log(news_snapshot)

    log.info(f"[Tweet] {len(new_entries)} new injury events injected into pipeline")

    # Trigger auto-trade
    try:
        _evaluate_auto_trade(force=True)
    except Exception as exc:
        log.error(f"[Tweet] Auto-trade eval error: {exc}")


# ── Twitter stream log ring buffer ──
_twitter_log_buffer: list[dict] = []
_TWITTER_LOG_MAX = 200


def _tw_log(level: str, msg: str):
    """Append to the in-memory twitter log buffer and also write to main log."""
    now_iso = datetime.now(ET).isoformat()
    _twitter_log_buffer.append({"ts": now_iso, "level": level, "msg": msg})
    if len(_twitter_log_buffer) > _TWITTER_LOG_MAX:
        del _twitter_log_buffer[: len(_twitter_log_buffer) - _TWITTER_LOG_MAX]
    if level == "ERROR":
        log.error(f"[Twitter] {msg}")
    elif level == "WARN":
        log.warning(f"[Twitter] {msg}")
    else:
        log.info(f"[Twitter] {msg}")


class _PrintCapture:
    """Redirect XStreamListener print() calls → _tw_log."""
    def __init__(self, real_stdout):
        self._real = real_stdout

    def write(self, s):
        text = s.rstrip("\n")
        if text:
            # Parse the XStreamListener format: [timestamp] [LEVEL] message
            import re as _re
            m = _re.match(r"^\[[\d\-: ]+\]\s*\[(\w+)\]\s*(.*)", text)
            if m:
                _tw_log(m.group(1), m.group(2))
            else:
                _tw_log("INFO", text)
        return self._real.write(s)

    def flush(self):
        self._real.flush()


def _twitter_stream_loop():
    """Background thread: listen to @UnderdogNBA stream and save parsed tweets."""
    try:
        from listen_twitter.x_listener import XStreamListener
        from tweet_analyzer.tweet_analyzer import TweetAnalyzer
    except ImportError as exc:
        log.warning(f"Twitter parser not available: {exc}")
        return

    from datetime import timezone as _utc_tz

    # Capture XStreamListener's print() output into our log buffer
    import sys as _sys
    _sys.stdout = _PrintCapture(_sys.stdout)

    listener = XStreamListener(
        query='from:UnderdogNBA -is:retweet ("will play" OR "available to play" OR "listed available" OR "not listed on injury report" OR "probable" OR "questionable" OR "doubtful" OR "ruled out" OR "out for" OR "will not play" OR "upgraded to" OR "downgraded to" OR "Injuries ") -("lineup alert" OR "starting lineup" OR "in place of" OR "start second half")'
    )
    analyzer = TweetAnalyzer()
    _tw_log("INFO", "Twitter stream listener starting for @UnderdogNBA")

    tweet_count = 0
    try:
        for tweet in listener.listen():
            tweet_count += 1
            tweet_id = tweet.get("id", "")
            created_at = tweet.get("created_at", "")
            text = (tweet.get("text") or "").strip()
            now_iso = datetime.now(ET).isoformat()

            lag_seconds = None
            if created_at:
                try:
                    created_dt = datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%S.000Z").replace(tzinfo=_utc_tz.utc)
                    lag_seconds = round((datetime.now(_utc_tz.utc) - created_dt).total_seconds(), 2)
                except Exception:
                    pass

            try:
                parsed_events = analyzer.analyze(text)
            except Exception as exc:
                _tw_log("ERROR", f"Tweet analyzer failed: {exc}")
                parsed_events = []

            _tw_log("INFO", f"Tweet #{tweet_count} id={tweet_id} events={len(parsed_events)}" + (f" lag={lag_seconds:.1f}s" if lag_seconds else ""))

            entry = {
                "id": f"tw-{tweet_id or int(time.time() * 1000)}",
                "tweet_id": str(tweet_id),
                "created_at": created_at,
                "received_at": now_iso,
                "raw_text": text,
                "parsed_events": parsed_events,
                "source": "UnderdogNBA",
                "lag_seconds": lag_seconds,
            }
            db.save_tweet(entry)

            # Feed parsed events into the injury/scoring pipeline
            if parsed_events:
                try:
                    _process_tweet_events(parsed_events)
                except Exception as pe:
                    _tw_log("ERROR", f"Pipeline injection failed: {pe}")

    except Exception as exc:
        _tw_log("ERROR", f"Twitter stream loop error: {exc}")


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

            # Blackout twitter 5s before each 15-min report mark
            seconds_to_target = (target_dt - now).total_seconds()
            if 0 < seconds_to_target <= 5:
                with state.lock:
                    if state.twitter_blackout_until < time.time():
                        state.twitter_blackout_until = time.time() + 300  # preliminary 5-min blackout
                        log.info(f"[Twitter] Blackout started — report due in {seconds_to_target:.0f}s")

            if should_check:
                with state.lock:
                    state.last_fetch = now

                if _report_exists(target_dt):
                    log.info(f"Fetching report for {target_dt.strftime('%m/%d/%Y, %I:%M %p %Z')}")
                    new_records = _fetch_report(target_dt)
                    report_at = target_iso

                    save_needed = False
                    with state.lock:
                        if state.records:
                            new_records = _hydrate_last_updates(state.records, new_records, report_at)
                            changes = _diff(state.records, new_records, report_at)
                            if changes:
                                # Separate tweet-confirmed changes from genuinely new ones
                                scored_changes = []
                                confirm_notifs = []
                                tw_confirmed_keys = set()

                                for change in changes:
                                    player_keys = _player_name_lookup_keys(change.get("player", ""))
                                    tw_key = " ".join(sorted(player_keys[0].split())) if player_keys else ""
                                    tw_existing = state.twitter_injuries.get(tw_key)

                                    change_status = change.get("to_status", change.get("status", ""))
                                    if tw_existing and tw_existing.get("status") == change_status:
                                        # NBA report confirms tweet — no score, just notification
                                        tw_confirmed_keys.add(tw_key)
                                        confirm = dict(change)
                                        confirm["detail"] = f"[NBA CONFIRMED] {change.get('detail', '')}"
                                        confirm["source"] = "nba_confirmed"
                                        confirm_notifs.append(confirm)
                                    else:
                                        scored_changes.append(change)

                                # Clean up confirmed tweet injuries
                                for key in tw_confirmed_keys:
                                    state.twitter_injuries.pop(key, None)

                                # Scored changes go to both news_log (for scoring) and notifications
                                if scored_changes:
                                    state.news_log = scored_changes + state.news_log
                                    state.news_log = state.news_log[:5000]
                                    state.notifications = scored_changes + state.notifications

                                # Confirmations go to notifications only (no scoring)
                                if confirm_notifs:
                                    state.notifications = confirm_notifs + state.notifications

                                state.notifications = state.notifications[:500]

                                if scored_changes:
                                    save_needed = list(state.news_log)

                                log.info(f"{len(changes)} changes: {len(scored_changes)} new, {len(confirm_notifs)} tweet-confirmed")
                            else:
                                log.info("No changes")
                        else:
                            new_records = _hydrate_last_updates([], new_records, report_at)
                            log.info(f"Initial load: {len(new_records)} records")

                        state.records = new_records
                        state.last_report_at = report_at

                    # Save and evaluate outside the lock to avoid blocking API
                    if save_needed:
                        db.save_news_log(save_needed)
                        try:
                            _evaluate_auto_trade()
                        except Exception as ate:
                            log.error(f"Auto-trade eval error: {ate}")

                    # Resume twitter after 40s post-report
                    with state.lock:
                        state.twitter_blackout_until = time.time() + 40
                    log.info("[Twitter] Report processed — resuming tweet processing in 40s")

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


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

@app.on_event("startup")
def startup():
    db.init_db(str(CSV_PATH))
    with state.lock:
        state.news_log = db.load_news_log()
        state.transition_configs = _load_transition_configs_with_defaults()
        betting_cfg = db.load_betting_config()
        state.auto_trade_enabled = betting_cfg["auto_trade_enabled"]
        state.bet_threshold = betting_cfg["threshold"]
        state.bet_amount = betting_cfg.get("bet_amount", 10.0)
        state.bet_block_hour_start = int(betting_cfg.get("block_hour_start", 16))
        state.bet_block_hour_end = int(betting_cfg.get("block_hour_end", 18))
        state.bet_log = db.load_bet_log()
        # Arm watermark = now, so we never trade on old batches after restart
        state.auto_trade_armed_at = datetime.now(ET).isoformat()
    t = threading.Thread(target=_poll_loop, daemon=True)
    t.start()
    t2 = threading.Thread(target=_polymarket_poll_loop, daemon=True)
    t2.start()
    t3 = threading.Thread(target=_twitter_stream_loop, daemon=True)
    t3.start()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

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


@app.get("/api/polymarket-game")
def get_polymarket_game(slug: str = ""):
    slug = slug.strip()
    if not slug:
        raise HTTPException(status_code=400, detail="slug is required")

    # Use in-memory cache instead of file-based cache
    with state.lock:
        entry = state.polymarket_cache.get(slug)
    now = time.time()
    if entry and now - entry.get("fetched_at", 0) < POLYMARKET_CACHE_TTL_SECONDS:
        return JSONResponse(entry["data"])

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
        if entry:
            return JSONResponse(entry["data"])
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if not events:
        raise HTTPException(status_code=404, detail="event not found")
    evt = events[0]
    markets = evt.get("markets") or []
    result = {
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
    }

    with state.lock:
        state.polymarket_cache[slug] = {"fetched_at": now, "data": result}

    return JSONResponse(result)


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
        db.save_news_log(state.news_log)
    return JSONResponse({"ok": True})


@app.get("/api/clear-notifications")
def clear_notifications():
    with state.lock:
        state.notifications = []
    return JSONResponse({"ok": True})


@app.get("/api/players-db")
def get_players_db():
    rows = db.load_players()
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
        db.save_transition_configs(state.transition_configs)

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

    # Check if player already exists to determine mode
    existing = db.load_players()
    updated = any(row["player_name"].lower() == normalized["player_name"].lower() for row in existing)
    db.upsert_player(normalized["player_name"], normalized["nba_team"], normalized["importance"])

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

    deleted = db.delete_player(player_name)
    if not deleted:
        raise HTTPException(status_code=404, detail="player not found")

    return JSONResponse({"ok": True, "player_name": player_name})


@app.get("/api/betting-config")
def get_betting_config():
    with state.lock:
        return JSONResponse({
            "auto_trade_enabled": state.auto_trade_enabled,
            "threshold": state.bet_threshold,
            "bet_amount": state.bet_amount,
            "block_hour_start": state.bet_block_hour_start,
            "block_hour_end": state.bet_block_hour_end,
        })


@app.post("/api/betting-config/update")
def update_betting_config(payload: BettingConfigUpdate):
    with state.lock:
        was_off = not state.auto_trade_enabled
        state.auto_trade_enabled = payload.auto_trade_enabled
        state.bet_threshold = payload.threshold
        state.bet_amount = payload.bet_amount
        state.bet_block_hour_start = max(0, min(23, int(payload.block_hour_start)))
        state.bet_block_hour_end = max(0, min(24, int(payload.block_hour_end)))
        # Arm watermark when toggling ON — only trade batches arriving after this moment
        if was_off and state.auto_trade_enabled:
            state.auto_trade_armed_at = datetime.now(ET).isoformat()
            log.info(f"Auto-trade armed at {state.auto_trade_armed_at}")
        db.save_betting_config(
            state.auto_trade_enabled, state.bet_threshold, state.bet_amount,
            state.bet_block_hour_start, state.bet_block_hour_end,
        )
    return JSONResponse({
        "ok": True,
        "auto_trade_enabled": state.auto_trade_enabled,
        "threshold": state.bet_threshold,
        "bet_amount": state.bet_amount,
        "block_hour_start": state.bet_block_hour_start,
        "block_hour_end": state.bet_block_hour_end,
    })


@app.get("/api/bet-log")
def get_bet_log():
    with state.lock:
        return JSONResponse({
            "rows": state.bet_log,
            "count": len(state.bet_log),
        })


@app.get("/api/clear-bet-log")
def clear_bet_log():
    with state.lock:
        state.bet_log = []
        db.save_bet_log(state.bet_log)
    return JSONResponse({"ok": True})


@app.get("/api/polymarket-live")
def get_polymarket_live():
    with state.lock:
        return JSONResponse({
            "odds": dict(state.polymarket_live_odds),
            "count": len(state.polymarket_live_odds),
        })


class SellPositionRequest(BaseModel):
    position_id: str


@app.get("/api/positions")
def get_positions():
    positions = db.load_positions()
    # Update current prices from live odds
    with state.lock:
        live_odds = dict(state.polymarket_live_odds)
    for pos in positions:
        if pos.get("status") != "open":
            continue
        slug = pos.get("slug", "")
        odds_data = live_odds.get(slug)
        if not odds_data:
            continue
        token_id = pos.get("token_id", "")
        for m in odds_data.get("markets", []):
            clob_ids = m.get("clob_token_ids", [])
            prices = m.get("prices", [])
            if token_id in [str(t) for t in clob_ids]:
                idx = [str(t) for t in clob_ids].index(token_id)
                if idx < len(prices):
                    current = float(prices[idx])
                    pos["current_price"] = current
                    buy = pos.get("buy_price", current)
                    shares = pos.get("shares", 0)
                    pos["pnl"] = round((current - buy) * shares, 2)
    return JSONResponse({"positions": positions, "count": len(positions)})


@app.post("/api/positions/sell")
def sell_position(payload: SellPositionRequest):
    position_id = payload.position_id.strip()
    if not position_id:
        raise HTTPException(status_code=400, detail="position_id required")

    positions = db.load_positions()
    pos = None
    for p in positions:
        if p["id"] == position_id:
            pos = p
            break
    if not pos:
        raise HTTPException(status_code=404, detail="Position not found")
    if pos["status"] not in ("open", "logged"):
        raise HTTPException(status_code=400, detail="Position is already closed")

    sell_price = pos.get("current_price", pos.get("buy_price", 0))
    sell_order_id = ""
    clob_error = ""

    # Try CLOB sell
    client = _get_clob_client()
    if client and pos.get("token_id") and pos.get("order_id"):
        try:
            from py_clob_client.clob_types import OrderArgs
            order = client.create_and_post_order(OrderArgs(
                token_id=pos["token_id"],
                price=max(0.01, round(sell_price - 0.02, 2)),
                size=pos.get("shares", 0),
                side="SELL",
            ))
            sell_order_id = order.get("orderID", "") if isinstance(order, dict) else str(order)
            log.info(f"CLOB sell order placed: {sell_order_id}")
        except Exception as exc:
            clob_error = str(exc)
            log.error(f"CLOB sell failed: {exc}")
            # If CLOB fails, still mark as sold locally (position was likely never filled)

    now_iso = datetime.now(ET).isoformat()
    pnl = round((sell_price - pos.get("buy_price", 0)) * pos.get("shares", 0), 2)
    db.update_position(position_id, {
        "status": "sold",
        "sell_price": sell_price,
        "sold_at": now_iso,
        "sell_order_id": sell_order_id,
        "pnl": pnl,
        "current_price": sell_price,
    })

    return JSONResponse({
        "ok": True,
        "position_id": position_id,
        "sell_price": sell_price,
        "pnl": pnl,
        "clob_sold": bool(sell_order_id),
        "clob_error": clob_error,
    })


@app.get("/api/tweets")
def get_tweets():
    tweets = db.load_tweets(limit=500)
    return JSONResponse({"tweets": tweets, "count": len(tweets)})


@app.get("/api/clear-tweets")
def clear_tweets():
    db.clear_tweets()
    return JSONResponse({"ok": True})


@app.get("/api/twitter-log")
def get_twitter_log():
    return JSONResponse({"logs": list(_twitter_log_buffer), "count": len(_twitter_log_buffer)})


@app.get("/api/twitter-status")
def get_twitter_status():
    with state.lock:
        blackout_remaining = max(0, state.twitter_blackout_until - time.time())
        return JSONResponse({
            "enabled": state.twitter_enabled,
            "blackout": blackout_remaining > 0,
            "blackout_remaining_seconds": round(blackout_remaining),
            "status": "blackout" if blackout_remaining > 0 else ("active" if state.twitter_enabled else "off"),
        })


@app.get("/api/twitter-toggle")
def toggle_twitter():
    with state.lock:
        state.twitter_enabled = not state.twitter_enabled
        enabled = state.twitter_enabled
    log.info(f"[Twitter] Manual toggle → {'ON' if enabled else 'OFF'}")
    return JSONResponse({"ok": True, "enabled": enabled})


VALID_SIM_STATUSES = {"Out", "Doubtful", "Questionable", "Probable", "Available", "Remove from report"}


@app.post("/api/simulate-injury")
def simulate_injury(payload: SimulateInjuryRequest):
    player_name = payload.player_name.strip()
    target_status = payload.target_status.strip()

    if not player_name:
        raise HTTPException(status_code=400, detail="player_name is required")
    if target_status not in VALID_SIM_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid target_status. Must be one of: {', '.join(sorted(VALID_SIM_STATUSES))}")

    # Validate player exists in DB
    players = db.load_players()
    matched_player = None
    for p in players:
        if p["player_name"].lower() == player_name.lower():
            matched_player = p
            break
    if not matched_player:
        raise HTTPException(status_code=400, detail=f"Player '{player_name}' not found in DB")

    team_name = matched_player["nba_team"]

    # Determine from_status by scanning current records
    from_status = None
    with state.lock:
        for r in state.records:
            if r["player"].lower() == player_name.lower():
                from_status = r.get("status", "")
                break

    on_report = from_status is not None
    if not on_report:
        from_status = NOT_ON_REPORT_STATE

    # Derive transition type
    if not on_report and target_status != "Remove from report":
        transition_type = "added"
        detail = f"[SIM] Added \u2014 {target_status}"
        to_status = target_status
    elif on_report and target_status == "Remove from report":
        transition_type = "removed"
        detail = "[SIM] Removed from report"
        to_status = REMOVED_STATE
    elif on_report and target_status != "Remove from report" and from_status != target_status:
        transition_type = "status_change"
        detail = f"[SIM] {from_status} \u2192 {target_status}"
        to_status = target_status
    else:
        if not on_report and target_status == "Remove from report":
            raise HTTPException(status_code=400, detail="Player is not on report \u2014 cannot remove")
        raise HTTPException(status_code=400, detail=f"Player already has status '{from_status}' \u2014 no change")

    # Match to upcoming game
    upcoming = _get_upcoming_games()
    team_tricode = _team_tricode_from_name(team_name, upcoming)

    # Build a fake record to feed _match_schedule_game
    fake_record = {"team": team_name, "matchup": "", "game_datetime_et": None}
    matched_game = _match_schedule_game(fake_record, upcoming)
    if not matched_game:
        raise HTTPException(status_code=400, detail=f"No upcoming game found for {team_name}")

    away_tricode = str(matched_game.get("away_tricode", ""))
    home_tricode = str(matched_game.get("home_tricode", ""))
    if not team_tricode:
        if team_name == matched_game.get("away_team"):
            team_tricode = away_tricode
        elif team_name == matched_game.get("home_team"):
            team_tricode = home_tricode

    opponent_tricode = ""
    if team_tricode == away_tricode:
        opponent_tricode = home_tricode
    elif team_tricode == home_tricode:
        opponent_tricode = away_tricode

    tone = _event_tone(transition_type, from_status if on_report else "", to_status if to_status != REMOVED_STATE else "")

    now_iso = datetime.now(ET).isoformat()
    entry = {
        "type": transition_type,
        "player": matched_player["player_name"],
        "team": team_name,
        "status": to_status if to_status != REMOVED_STATE else (from_status if on_report else ""),
        "from_status": from_status,
        "to_status": to_status,
        "matchup": matched_game.get("matchup", ""),
        "game_datetime_et": matched_game.get("game_datetime", ""),
        "game_id": matched_game.get("game_id", ""),
        "scheduled_game_datetime": matched_game.get("game_datetime", ""),
        "scheduled_matchup": matched_game.get("matchup", ""),
        "away_tricode": away_tricode,
        "home_tricode": home_tricode,
        "team_tricode": team_tricode,
        "opponent_tricode": opponent_tricode,
        "injury": "Simulated",
        "tone": tone,
        "detail": detail,
        "timestamp_at": now_iso,
        "simulated": True,
    }

    with state.lock:
        state.news_log = [entry] + state.news_log
        state.news_log = state.news_log[:5000]
        db.save_news_log(state.news_log)
        state.notifications = [entry] + state.notifications
        state.notifications = state.notifications[:500]

    try:
        _evaluate_auto_trade(force=True)
    except Exception as exc:
        log.error(f"Simulate auto-trade eval error: {exc}")

    return JSONResponse({"ok": True, "entry": entry})


@app.get("/api/latest-batch")
def get_latest_batch():
    batches = _build_latest_batch()
    return JSONResponse({
        "batches": batches,
        "count": len(batches),
    })
