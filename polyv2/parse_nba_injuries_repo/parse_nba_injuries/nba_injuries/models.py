from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel


class InjuryRecord(BaseModel):
    game_date: str
    game_time: str
    matchup: str
    team: str
    player_name: str
    status: str
    reason: str

    @property
    def key(self) -> str:
        return f"{self.team}|{self.player_name}"


class InjuryReport(BaseModel):
    report_timestamp: datetime
    records: list[InjuryRecord]

    @property
    def by_team(self) -> dict[str, list[InjuryRecord]]:
        grouped: dict[str, list[InjuryRecord]] = {}
        for r in self.records:
            grouped.setdefault(r.team, []).append(r)
        return grouped

    @property
    def by_game(self) -> dict[str, list[InjuryRecord]]:
        grouped: dict[str, list[InjuryRecord]] = {}
        for r in self.records:
            grouped.setdefault(r.matchup, []).append(r)
        return grouped


class ReportChange(BaseModel):
    new_injuries: list[InjuryRecord] = []
    removed_injuries: list[InjuryRecord] = []
    status_changes: list[dict] = []
    reason_changes: list[dict] = []

    @property
    def has_changes(self) -> bool:
        return bool(
            self.new_injuries
            or self.removed_injuries
            or self.status_changes
            or self.reason_changes
        )

    @property
    def summary_count(self) -> int:
        return (
            len(self.new_injuries)
            + len(self.removed_injuries)
            + len(self.status_changes)
            + len(self.reason_changes)
        )
