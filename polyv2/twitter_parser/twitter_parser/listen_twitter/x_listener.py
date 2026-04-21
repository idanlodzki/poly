import json
import time
from datetime import datetime
from typing import Generator, Dict, Any

import requests
from .tokens import BEARER_TOKEN


class XStreamListener:
    RULES_URL = "https://api.x.com/2/tweets/search/stream/rules"
    STREAM_URL = "https://api.x.com/2/tweets/search/stream"

    # --------------------------
    # Tuning constants
    # --------------------------

    CONNECT_TIMEOUT_SECONDS = 10
    READ_TIMEOUT_SECONDS = 75

    KEEPALIVE_EXPECTED_SECONDS = 20

    BACKOFF_INITIAL_SECONDS = 5
    BACKOFF_MAX_SECONDS = 60

    RULE_REQUEST_TIMEOUT_SECONDS = 30

    def __init__(self, query: str):
        self.query = query
        self.tag = "listener_rule"

    # --------------------------
    # Logging helpers
    # --------------------------

    @staticmethod
    def _ts() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    @classmethod
    def _log(cls, level: str, msg: str) -> None:
        print(f"[{cls._ts()}] [{level}] {msg}")

    # --------------------------
    # Public API
    # --------------------------

    def listen(self) -> Generator[Dict[str, Any], None, None]:
        """
        Generator that yields tweets as they arrive.
        """

        self._log("INFO", f"Listener starting. query={self.query!r}, tag={self.tag!r}")
        self._ensure_rule()

        backoff = self.BACKOFF_INITIAL_SECONDS
        reconnect_count = 0

        while True:
            reconnect_count += 1
            stream_started_at = time.time()
            last_data_at = None

            try:
                self._log(
                    "INFO",
                    f"Opening stream connection #{reconnect_count} "
                    f"(connect_timeout={self.CONNECT_TIMEOUT_SECONDS}s, "
                    f"read_timeout={self.READ_TIMEOUT_SECONDS}s)"
                )

                with self._connect_stream() as response:
                    stream_opened_after = time.time() - stream_started_at

                    self._log("INFO", f"Stream HTTP status={response.status_code}")
                    self._log(
                        "DEBUG",
                        f"Connected to stream after {stream_opened_after:.1f}s. "
                        f"Response headers: {dict(response.headers)}"
                    )
                    response.raise_for_status()

                    self._log(
                        "INFO",
                        f"Connected. Listening for tweets... "
                        f"(expected keep-alive about every {self.KEEPALIVE_EXPECTED_SECONDS}s)"
                    )

                    backoff = self.BACKOFF_INITIAL_SECONDS

                    for line in response.iter_lines():
                        now = time.time()

                        if not line:
                            idle_for = now - (last_data_at or stream_started_at)
                            self._log(
                                "DEBUG",
                                f"Keep-alive received after {idle_for:.1f}s idle"
                            )
                            last_data_at = now
                            continue

                        idle_for = now - (last_data_at or stream_started_at)
                        last_data_at = now

                        self._log(
                            "DEBUG",
                            f"Raw line bytes={len(line)} after {idle_for:.1f}s idle"
                        )

                        tweet = self._parse_line(line)
                        if tweet:
                            tweet_id = tweet.get("id")
                            created_at = tweet.get("created_at")
                            text = tweet.get("text", "").replace("\n", " ")[:140]

                            self._log(
                                "INFO",
                                f"Tweet received: id={tweet_id}, created_at={created_at}, text={text!r}"
                            )
                            yield tweet
                        else:
                            preview = line[:300].decode("utf-8", errors="replace")
                            self._log("WARN", f"Failed to parse line. preview={preview!r}")

            except KeyboardInterrupt:
                self._log("INFO", "Stopped by user.")
                break

            except requests.ReadTimeout as e:
                alive_for = time.time() - stream_started_at
                self._log(
                    "WARN",
                    f"ReadTimeout after {alive_for:.1f}s. "
                    f"Will reconnect in {backoff}s. error={e}"
                )
                time.sleep(backoff)
                backoff = min(backoff * 2, self.BACKOFF_MAX_SECONDS)

            except requests.ConnectionError as e:
                alive_for = time.time() - stream_started_at
                self._log(
                    "WARN",
                    f"ConnectionError after {alive_for:.1f}s. "
                    f"Will reconnect in {backoff}s. error={e}"
                )
                time.sleep(backoff)
                backoff = min(backoff * 2, self.BACKOFF_MAX_SECONDS)

            except requests.HTTPError as e:
                retry_after_seconds = self._handle_http_error(e)

                if retry_after_seconds is not None:
                    sleep_seconds = max(retry_after_seconds, backoff)
                else:
                    sleep_seconds = backoff

                self._log("WARN", f"Retrying in {sleep_seconds}s...")
                time.sleep(sleep_seconds)
                backoff = min(backoff * 2, self.BACKOFF_MAX_SECONDS)

            except Exception as e:
                alive_for = time.time() - stream_started_at
                self._log(
                    "ERROR",
                    f"Unexpected {type(e).__name__} after {alive_for:.1f}s: {e}"
                )
                time.sleep(backoff)
                backoff = min(backoff * 2, self.BACKOFF_MAX_SECONDS)

    # --------------------------
    # Internal methods
    # --------------------------

    @staticmethod
    def _headers():
        return {
            "Authorization": f"Bearer {BEARER_TOKEN}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _get_rules(self):
        self._log("DEBUG", f"Fetching rules from {self.RULES_URL}")
        response = requests.get(
            self.RULES_URL,
            headers=self._headers(),
            timeout=self.RULE_REQUEST_TIMEOUT_SECONDS,
        )
        self._log("DEBUG", f"Rules GET status={response.status_code}")
        response.raise_for_status()
        data = response.json()
        self._log("DEBUG", f"Rules response={data}")
        return data

    def _delete_rules(self, rule_ids):
        if not rule_ids:
            self._log("DEBUG", "No rules to delete.")
            return

        payload = {"delete": {"ids": rule_ids}}
        self._log("INFO", f"Deleting rules: ids={rule_ids}")

        response = requests.post(
            self.RULES_URL,
            headers=self._headers(),
            json=payload,
            timeout=self.RULE_REQUEST_TIMEOUT_SECONDS,
        )
        self._log(
            "DEBUG",
            f"Delete rules status={response.status_code}, body={response.text[:1000]}"
        )
        response.raise_for_status()

    def _ensure_rule(self):
        self._log("INFO", "Ensuring stream rule exists...")
        current = self._get_rules()
        existing = current.get("data", [])

        self._log("INFO", f"Existing rules count={len(existing)}")

        for index, rule in enumerate(existing, start=1):
            self._log(
                "DEBUG",
                f"Existing rule #{index}: id={rule.get('id')}, "
                f"tag={rule.get('tag')}, value={rule.get('value')!r}"
            )

        for rule in existing:
            if rule.get("value") == self.query and rule.get("tag") == self.tag:
                self._log("INFO", "Matching rule already exists.")
                return

        old_ids = [rule["id"] for rule in existing]
        if old_ids:
            self._log("WARN", f"Deleting old rules before adding new one: {old_ids}")
            self._delete_rules(old_ids)

        payload = {
            "add": [
                {
                    "value": self.query,
                    "tag": self.tag,
                }
            ]
        }

        self._log("INFO", f"Adding rule: {payload}")
        response = requests.post(
            self.RULES_URL,
            headers=self._headers(),
            json=payload,
            timeout=self.RULE_REQUEST_TIMEOUT_SECONDS,
        )
        self._log(
            "DEBUG",
            f"Add rule status={response.status_code}, body={response.text[:1000]}"
        )
        response.raise_for_status()

        self._log("INFO", f"Rule set successfully: {self.query!r}")

    def _connect_stream(self):
        params = {
            "tweet.fields": "created_at",
        }

        self._log(
            "DEBUG",
            f"Connecting to stream url={self.STREAM_URL}, params={params}, "
            f"timeout=({self.CONNECT_TIMEOUT_SECONDS}, {self.READ_TIMEOUT_SECONDS})"
        )

        return requests.get(
            self.STREAM_URL,
            headers=self._headers(),
            params=params,
            stream=True,
            timeout=(self.CONNECT_TIMEOUT_SECONDS, self.READ_TIMEOUT_SECONDS),
        )

    @staticmethod
    def _parse_line(line: bytes) -> Dict[str, Any] | None:
        try:
            payload = json.loads(line.decode("utf-8"))
            return payload.get("data")
        except Exception:
            return None

    def _handle_http_error(self, e: requests.HTTPError) -> int | None:
        status = getattr(e.response, "status_code", None)
        self._log("ERROR", f"HTTPError status={status}")

        retry_after_seconds = None

        try:
            body = e.response.text[:1000]
            self._log("ERROR", f"Response body={body}")
        except Exception:
            self._log("ERROR", "Could not read HTTP error response body.")

        try:
            retry_after_header = e.response.headers.get("Retry-After")
            if retry_after_header:
                retry_after_seconds = int(retry_after_header)
                self._log("WARN", f"Server requested retry after {retry_after_seconds}s")
        except Exception:
            pass

        return retry_after_seconds