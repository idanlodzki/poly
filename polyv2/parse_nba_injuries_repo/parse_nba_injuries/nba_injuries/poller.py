from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Callable

from nba_injuries.config import REPORT_INTERVAL_MINUTES
from nba_injuries.fetcher import fetch_report, ET_OFFSET
from nba_injuries.models import InjuryRecord, InjuryReport, ReportChange


def diff_reports(old: InjuryReport | None, new: InjuryReport) -> ReportChange:
    if old is None:
        return ReportChange(new_injuries=new.records)

    old_map: dict[str, InjuryRecord] = {r.key: r for r in old.records}
    new_map: dict[str, InjuryRecord] = {r.key: r for r in new.records}

    old_keys = set(old_map.keys())
    new_keys = set(new_map.keys())

    added = [new_map[k] for k in sorted(new_keys - old_keys)]
    removed = [old_map[k] for k in sorted(old_keys - new_keys)]

    status_changes = []
    reason_changes = []
    for k in sorted(old_keys & new_keys):
        o, n = old_map[k], new_map[k]
        if o.status != n.status:
            status_changes.append({
                "player": n.player_name,
                "team": n.team,
                "old_status": o.status,
                "new_status": n.status,
                "reason": n.reason,
            })
        elif o.reason != n.reason:
            reason_changes.append({
                "player": n.player_name,
                "team": n.team,
                "old_reason": o.reason,
                "new_reason": n.reason,
                "status": n.status,
            })

    return ReportChange(
        new_injuries=added,
        removed_injuries=removed,
        status_changes=status_changes,
        reason_changes=reason_changes,
    )


def _next_report_time(now: datetime) -> datetime:
    minute = ((now.minute // REPORT_INTERVAL_MINUTES) + 1) * REPORT_INTERVAL_MINUTES
    if minute >= 60:
        result = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    else:
        result = now.replace(minute=minute, second=0, microsecond=0)
    return result + timedelta(seconds=45)


def poll(
    on_update: Callable[[InjuryReport, ReportChange], None] | None = None,
    on_error: Callable[[Exception], None] | None = None,
    on_no_change: Callable[[datetime], None] | None = None,
    max_retries: int = 3,
):
    prev_report: InjuryReport | None = None

    while True:
        now = datetime.now(ET_OFFSET)
        try:
            report = _fetch_with_retry(now, max_retries)
            if report is None:
                if on_error:
                    on_error(RuntimeError(f"Failed to fetch after {max_retries} retries"))
            else:
                changes = diff_reports(prev_report, report)

                if changes.has_changes and on_update:
                    on_update(report, changes)
                elif not changes.has_changes and on_no_change:
                    on_no_change(now)

                prev_report = report

        except Exception as e:
            if on_error:
                on_error(e)

        next_time = _next_report_time(datetime.now(ET_OFFSET))
        wait = (next_time - datetime.now(ET_OFFSET)).total_seconds()
        time.sleep(max(wait, 30))


def _fetch_with_retry(dt: datetime, retries: int) -> InjuryReport | None:
    for attempt in range(retries):
        try:
            return fetch_report(dt)
        except Exception:
            if attempt < retries - 1:
                time.sleep(5 * (attempt + 1))
    return None
