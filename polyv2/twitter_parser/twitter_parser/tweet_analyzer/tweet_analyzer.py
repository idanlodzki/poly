import json
import re
from typing import Any, Dict, List, Optional, Pattern


class TweetAnalyzer:
    ALLOWED_STATUSES = {
        "available",
        "probable",
        "questionable",
        "doubtful",
        "out",
    }

    HARD_REJECT_PATTERNS = [
        r"\blineup alert\b",
        r"\bstarting lineup\b",
        r"\bstart second half\b",
        r"\bin place of\b",
        r"\bmoves? to the bench\b",
        r"\benters? the starting lineup\b",
        r"\bstarting in place of\b",
        r"\bstarting five\b",
        r"\bwill start\b",
    ]

    def __init__(self):
        self._simple_patterns = self._build_simple_patterns()
        self._injury_block_pattern = re.compile(
            r"^(?P<player>.+?)\s*-\s*(?P<status>available|probable|questionable|doubtful|out)$",
            re.IGNORECASE,
        )
        self._team_header_pattern = re.compile(r"^[A-Z]{2,4}:$")

    def analyze(self, tweet_text: str) -> List[Dict[str, Any]]:
        text = self._normalize_text(tweet_text)
        if not text:
            return []

        result = self._run_reject_stage(text)
        if result is not None:
            return result

        result = self._run_injury_block_stage(text)
        if result is not None:
            return result

        result = self._run_simple_pattern_stage(text)
        if result is not None:
            return result

        return []

    # ------------------------------------------------------------------
    # Pipeline stages
    # ------------------------------------------------------------------

    def _run_reject_stage(self, text: str) -> Optional[List[Dict[str, Any]]]:
        if self._is_hard_reject(text):
            return []
        return None

    def _run_injury_block_stage(self, text: str) -> Optional[List[Dict[str, Any]]]:
        events = self._parse_injury_block(text)
        if events:
            return events
        return None

    def _run_simple_pattern_stage(self, text: str) -> Optional[List[Dict[str, Any]]]:
        events = self._parse_simple_status_tweet(text)
        if events:
            return events
        return None

    # ------------------------------------------------------------------
    # Stage helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_text(text: str) -> str:
        return text.strip()

    def _is_hard_reject(self, text: str) -> bool:
        for pattern in self.HARD_REJECT_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return True
        return False

    def _parse_injury_block(self, text: str) -> List[Dict[str, Any]]:
        lines = self._split_nonempty_lines(text)
        if not self._looks_like_injury_block(lines):
            return []

        events: List[Dict[str, Any]] = []

        for line in lines:
            if self._is_team_header(line):
                continue
            if line.lower() == "none":
                continue

            match = self._injury_block_pattern.match(line)
            if not match:
                continue

            event = self._build_event(
                player_name=match.group("player"),
                injury=None,
                status=match.group("status"),
            )
            if event:
                events.append(event)

        return events

    def _parse_simple_status_tweet(self, text: str) -> List[Dict[str, Any]]:
        for pattern, forced_status in self._simple_patterns:
            match = pattern.search(text)
            if not match:
                continue

            status = forced_status or match.group("status")
            event = self._build_event(
                player_name=match.group("player"),
                injury=match.group("injury"),
                status=status,
            )
            if event:
                return [event]

        return []

    # ------------------------------------------------------------------
    # Parsing utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _split_nonempty_lines(text: str) -> List[str]:
        return [line.strip() for line in text.splitlines() if line.strip()]

    @staticmethod
    def _looks_like_injury_block(lines: List[str]) -> bool:
        if not lines:
            return False
        return any("injuries" in line.lower() for line in lines[:2])

    def _is_team_header(self, line: str) -> bool:
        return bool(self._team_header_pattern.match(line))

    @classmethod
    def _normalize_injury(cls, injury: Optional[Any]) -> Optional[str]:
        if not isinstance(injury, str):
            return None

        injury = injury.strip()
        if not injury:
            return None

        lowered = injury.lower()
        if lowered in {"null", "none", "n/a"}:
            return None

        return injury

    @classmethod
    def _normalize_status(cls, status: Optional[Any]) -> Optional[str]:
        if not isinstance(status, str):
            return None

        status = status.strip().lower()
        if status not in cls.ALLOWED_STATUSES:
            return None

        return status

    @classmethod
    def _build_event(
        cls,
        player_name: Optional[Any],
        injury: Optional[Any],
        status: Optional[Any],
    ) -> Optional[Dict[str, Any]]:
        if not isinstance(player_name, str) or not player_name.strip():
            return None

        normalized_status = cls._normalize_status(status)
        if not normalized_status:
            return None

        return {
            "player_name": player_name.strip(),
            "injury": cls._normalize_injury(injury),
            "status": normalized_status,
        }

    @classmethod
    def _validate_events(cls, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        validated: List[Dict[str, Any]] = []

        for event in events:
            if not isinstance(event, dict):
                continue

            normalized = cls._build_event(
                player_name=event.get("player_name"),
                injury=event.get("injury"),
                status=event.get("status"),
            )
            if normalized:
                validated.append(normalized)

        return validated

    # ------------------------------------------------------------------
    # JSON utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_json_load(text: str) -> Optional[Dict[str, Any]]:
        text = text.strip()

        if text.endswith('"') and text.count("{") == text.count("}"):
            text = text[:-1]

        try:
            data = json.loads(text)
        except Exception:
            print("Bad JSON:")
            print(text)
            return None

        if not isinstance(data, dict):
            return None

        return data

    # ------------------------------------------------------------------
    # Pattern configuration
    # ------------------------------------------------------------------

    @classmethod
    def _build_simple_patterns(cls) -> List[tuple[Pattern[str], Optional[str]]]:
        flags = re.IGNORECASE

        return [
            (
                re.compile(
                    r"^(?P<player>[A-Za-z.\-\'\s]+?)\s*\((?P<injury>[^)]+)\)\s*upgraded to\s*(?P<status>probable|questionable|doubtful|out|available)\b",
                    flags,
                ),
                None,
            ),
            (
                re.compile(
                    r"^(?P<player>[A-Za-z.\-\'\s]+?)\s*\((?P<injury>[^)]+)\)\s*downgraded to\s*(?P<status>probable|questionable|doubtful|out|available)\b",
                    flags,
                ),
                None,
            ),
            (
                re.compile(
                    r"^(?P<player>[A-Za-z.\-\'\s]+?)\s*\((?P<injury>[^)]+)\)\s*listed\s*(?P<status>probable|questionable|doubtful|out)\b",
                    flags,
                ),
                None,
            ),
            (
                re.compile(
                    r"^(?P<player>[A-Za-z.\-\'\s]+?)\s*\((?P<injury>[^)]+)\)\s*(?:listed\s+)?available to play\b",
                    flags,
                ),
                "available",
            ),
            (
                re.compile(
                    r"^(?P<player>[A-Za-z.\-\'\s]+?)\s*\((?P<injury>[^)]+)\)\s*will play\b",
                    flags,
                ),
                "available",
            ),
            (
                re.compile(
                    r"^(?P<player>[A-Za-z.\-\'\s]+?)\s*\((?P<injury>[^)]+)\)\s*expected to play\b",
                    flags,
                ),
                "available",
            ),
            (
                re.compile(
                    r"^(?P<player>[A-Za-z.\-\'\s]+?)\s*\((?P<injury>[^)]+)\)\s*likely to play\b",
                    flags,
                ),
                "available",
            ),
            (
                re.compile(
                    r"^(?P<player>[A-Za-z.\-\'\s]+?)\s*\((?P<injury>[^)]+)\)\s*not listed on injury report\b",
                    flags,
                ),
                "available",
            ),
            (
                re.compile(
                    r"^(?P<player>[A-Za-z.\-\'\s]+?)\s*\((?P<injury>[^)]+)\)\s*ruled out\b",
                    flags,
                ),
                "out",
            ),
            (
                re.compile(
                    r"^(?P<player>[A-Za-z.\-\'\s]+?)\s*\((?P<injury>[^)]+)\)\s*will not play\b",
                    flags,
                ),
                "out",
            ),
            (
                re.compile(
                    r"^(?P<player>[A-Za-z.\-\'\s]+?)\s*\((?P<injury>[^)]+)\)\s*out for\b",
                    flags,
                ),
                "out",
            ),
            (
                re.compile(
                    r"^(?P<player>[A-Za-z.\-\'\s]+?)\s*\((?P<injury>[^)]+)\)\s*questionable\b",
                    flags,
                ),
                "questionable",
            ),
            (
                re.compile(
                    r"^(?P<player>[A-Za-z.\-\'\s]+?)\s*\((?P<injury>[^)]+)\)\s*doubtful\b",
                    flags,
                ),
                "doubtful",
            ),
            (
                re.compile(
                    r"^(?P<player>[A-Za-z.\-\'\s]+?)\s*\((?P<injury>[^)]+)\)\s*probable\b",
                    flags,
                ),
                "probable",
            ),
        ]