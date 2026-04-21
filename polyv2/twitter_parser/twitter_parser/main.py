from datetime import datetime, timezone
from listen_twitter.x_listener import XStreamListener
from tweet_analyzer.tweet_analyzer import TweetAnalyzer


def log(level: str, message: str) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] [{level}] {message}")


def parse_twitter_time(ts: str) -> datetime | None:
    """
    Convert X created_at (UTC ISO) → datetime
    Example: '2026-04-15T00:15:56.000Z'
    """
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.000Z").replace(tzinfo=timezone.utc)
    except Exception:
        return None


def main() -> None:
    listener = XStreamListener(query="from:UnderdogNBA -is:retweet")
    analyzer = TweetAnalyzer()

    print("Starting main pipeline...")
    print("Listening to @UnderdogNBA")
    print("For each new tweet: parse and print\n")

    tweet_count = 0

    try:
        for tweet in listener.listen():
            tweet_count += 1

            tweet_id = tweet.get("id")
            created_at_raw = tweet.get("created_at")
            text = tweet.get("text", "").strip()

            now_dt = datetime.now(timezone.utc)
            now_local_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            created_dt = parse_twitter_time(created_at_raw)

            lag_sec = None
            if created_dt:
                lag_sec = (now_dt - created_dt).total_seconds()

            print("\n" + "=" * 100)
            print("NEW TWEET")
            print(f"count:      {tweet_count}")
            print(f"id:         {tweet_id}")
            print(f"created_at: {created_at_raw}")
            print(f"received:   {now_local_str}")

            if lag_sec is not None:
                print(f"lag:        {lag_sec:.2f}s")

            print("text:")
            print(text)

            log("MAIN", f"Received tweet #{tweet_count} id={tweet_id}")

            # --------------------------
            # Analyzer
            # --------------------------
            log("MAIN", "Sending tweet to analyzer...")
            started_at = datetime.now()

            try:
                parsed = analyzer.analyze(text)
            except Exception as e:
                duration = (datetime.now() - started_at).total_seconds()
                log("ERROR", f"Analyzer failed after {duration:.2f}s: {type(e).__name__}: {e}")

                print("\nPARSED:")
                print([])
                print("=" * 100)
                continue

            duration = (datetime.now() - started_at).total_seconds()
            log("MAIN", f"Analyzer finished in {duration:.2f}s")

            print("\nPARSED:")
            print(parsed)
            print("=" * 100)

    except KeyboardInterrupt:
        log("MAIN", "Stopped by user.")


if __name__ == "__main__":
    main()