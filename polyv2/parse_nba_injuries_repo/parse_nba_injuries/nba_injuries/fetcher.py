from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import nba_injuries.config  # noqa: F401 - ensures JAVA_HOME is set


@contextmanager
def _suppress_stderr():
    old = sys.stderr
    sys.stderr = open(os.devnull, "w")
    try:
        yield
    finally:
        sys.stderr.close()
        sys.stderr = old


with _suppress_stderr():
    from nbainjuries import injury

from nba_injuries.models import InjuryRecord, InjuryReport

ET_OFFSET = timezone(timedelta(hours=-4))


def _round_to_15(dt: datetime) -> datetime:
    minute = (dt.minute // 15) * 15
    return dt.replace(minute=minute, second=0, microsecond=0)


def fetch_report(dt: datetime | None = None) -> InjuryReport:
    if dt is None:
        dt = datetime.now(ET_OFFSET)
    dt = _round_to_15(dt)

    dt_naive = dt.replace(tzinfo=None) if dt.tzinfo else dt
    old_stdout = sys.stdout
    sys.stdout = open(os.devnull, "w")
    try:
        raw = injury.get_reportdata(dt_naive)
    finally:
        sys.stdout.close()
        sys.stdout = old_stdout
    records_raw = json.loads(raw) if isinstance(raw, str) else raw

    records = []
    for r in records_raw:
        player = r.get("Player Name")
        status = r.get("Current Status")
        if not player or not status:
            continue
        records.append(InjuryRecord(
            game_date=r.get("Game Date") or "",
            game_time=r.get("Game Time") or "",
            matchup=r.get("Matchup") or "",
            team=r.get("Team") or "",
            player_name=str(player),
            status=str(status),
            reason=r.get("Reason") or "",
        ))

    return InjuryReport(
        report_timestamp=dt,
        records=records,
    )


def check_report_exists(dt: datetime) -> bool:
    dt = _round_to_15(dt)
    dt_naive = dt.replace(tzinfo=None) if dt.tzinfo else dt
    return injury.check_reportvalid(dt_naive)
