from tweet_analyzer import TweetAnalyzer

analyzer = TweetAnalyzer()

print("""
Paste ANY tweet (can be multiple lines).
End with --- (on same line or new line).
Type EXIT to quit.
""")

while True:
    lines = []

    while True:
        line = input()

        if line.strip().upper() == "EXIT":
            raise SystemExit

        # handle --- even if attached to text
        if "---" in line:
            line = line.replace("---", "")
            lines.append(line)
            break

        lines.append(line)

    tweet = "\n".join(lines).strip()

    if not tweet:
        print("⚠️ Empty input\n")
        continue

    print("\n" + "=" * 80)
    print("TWEET:")
    print(tweet)

    parsed = analyzer.analyze(tweet)

    print("\nPARSED:")
    print(parsed)
    print("=" * 80 + "\n")