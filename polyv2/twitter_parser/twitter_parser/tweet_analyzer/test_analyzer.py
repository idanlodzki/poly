from tweet_analyzer import TweetAnalyzer


def main() -> None:
    analyzer = TweetAnalyzer()

    test_cases = [
        {
            "tweet": "Pelle Larsson (leg) upgraded to probable Tuesday.",
            "expected": [{"player_name": "Pelle Larsson", "injury": "leg", "status": "probable"}],
        },
        {
            "tweet": "Nikola Jokic (injury management) will play Sunday.",
            "expected": [{"player_name": "Nikola Jokic", "injury": "injury management", "status": "available"}],
        },
        {
            "tweet": "Jerami Grant (calf) listed questionable for Tuesday.",
            "expected": [{"player_name": "Jerami Grant", "injury": "calf", "status": "questionable"}],
        },
        {
            "tweet": "LeBron James (foot) ruled out Tuesday.",
            "expected": [{"player_name": "LeBron James", "injury": "foot", "status": "out"}],
        },
        {
            "tweet": "Jalen Green (knee) not listed on injury report for Tuesday.",
            "expected": [{"player_name": "Jalen Green", "injury": "knee", "status": "available"}],
        },
        {
            "tweet": "Pelle Larsson (leg) listed available to play Tuesday.",
            "expected": [{"player_name": "Pelle Larsson", "injury": "leg", "status": "available"}],
        },
        {
            "tweet": "Matisse Thybulle (ankle) available to play Sunday.",
            "expected": [{"player_name": "Matisse Thybulle", "injury": "ankle", "status": "available"}],
        },
        {
            "tweet": "P.J. Washington (elbow) listed doubtful for Sunday.",
            "expected": [{"player_name": "P.J. Washington", "injury": "elbow", "status": "doubtful"}],
        },
        {
            "tweet": "Anthony Davis (ankle) downgraded to questionable Friday.",
            "expected": [{"player_name": "Anthony Davis", "injury": "ankle", "status": "questionable"}],
        },
        {
            "tweet": "Damian Lillard (calf) listed doubtful for Wednesday.",
            "expected": [{"player_name": "Damian Lillard", "injury": "calf", "status": "doubtful"}],
        },
        {
            "tweet": "Stephen Curry (ankle) expected to play tonight.",
            "expected": [{"player_name": "Stephen Curry", "injury": "ankle", "status": "available"}],
        },
        {
            "tweet": "Jimmy Butler (knee) likely to play Monday.",
            "expected": [{"player_name": "Jimmy Butler", "injury": "knee", "status": "available"}],
        },
        {
            "tweet": "Chris Paul (hand) will not play Sunday.",
            "expected": [{"player_name": "Chris Paul", "injury": "hand", "status": "out"}],
        },
        {
            "tweet": "Kawhi Leonard (rest) available to play tonight.",
            "expected": [{"player_name": "Kawhi Leonard", "injury": "rest", "status": "available"}],
        },
        {
            "tweet": "Brandon Ingram (ankle) out for Tuesday.",
            "expected": [{"player_name": "Brandon Ingram", "injury": "ankle", "status": "out"}],
        },
        {
            "tweet": "Injuries 4/14\n\nMIA:\nPelle Larsson - Questionable\nNikola Jovic - Out\n\nPOR:\nJerami Grant - Questionable\nDamian Lillard - Out",
            "expected": [
                {"player_name": "Pelle Larsson", "injury": None, "status": "questionable"},
                {"player_name": "Nikola Jovic", "injury": None, "status": "out"},
                {"player_name": "Jerami Grant", "injury": None, "status": "questionable"},
                {"player_name": "Damian Lillard", "injury": None, "status": "out"},
            ],
        },
        {
            "tweet": "Injuries 4/15\n\nDEN:\nNikola Jokic - Probable\nJamal Murray - Questionable\n\nLAL:\nLeBron James - Out",
            "expected": [
                {"player_name": "Nikola Jokic", "injury": None, "status": "probable"},
                {"player_name": "Jamal Murray", "injury": None, "status": "questionable"},
                {"player_name": "LeBron James", "injury": None, "status": "out"},
            ],
        },
        {
            "tweet": "Luka Doncic will be out for 2 months.",
            "expected": [],
        },
        {
            "tweet": "Zion Williamson is expected to miss extended time.",
            "expected": [],
        },
        {
            "tweet": "Ja Morant out indefinitely with shoulder injury.",
            "expected": [],
        },
        {
            "tweet": "Dumars: Pelicans have no intentions of trading Zion Williamson this offseason.",
            "expected": [],
        },
        {
            "tweet": "Dumars: James Borrego is a candidate for Pelicans' permanent head coaching position.",
            "expected": [],
        },
        {
            "tweet": "Lineup alert: Kings will start Carter, Clifford, Plowden, Achiuwa, Raynaud on Sunday.",
            "expected": [],
        },
        {
            "tweet": "Lineup alert: Bronny James, Jake LaRavia start second half in place of LeBron James, Luke Kennard on Sunday.",
            "expected": [],
        },
        {
            "tweet": "Starting lineup tonight: Curry, Thompson, Wiggins, Green, Looney.",
            "expected": [],
        },
        {
            "tweet": "The Celtics were without:\n\nJayson Tatum\nJaylen Brown\nDerrick White\n\nAnd beat the Magic.",
            "expected": [],
        },
        {
            "tweet": "Orlando needed a win to have a chance at avoiding the Play-In.",
            "expected": [],
        },
        {
            "tweet": "Apple releases new iPhone this September.",
            "expected": [],
        },
        {
            "tweet": "Nikola Jokic recorded a triple-double in the win.",
            "expected": [],
        },
        {
            "tweet": "Player X is on a minutes restriction tonight.",
            "expected": [],
        },
        {
            "tweet": "Kyrie Irving (foot) questionable for tonight’s game.",
            "expected": [{"player_name": "Kyrie Irving", "injury": "foot", "status": "questionable"}],
        },
        {
            "tweet": "Kevin Durant (hamstring) probable for Tuesday.",
            "expected": [{"player_name": "Kevin Durant", "injury": "hamstring", "status": "probable"}],
        },
        {
            "tweet": "Devin Booker (illness) listed out for Monday.",
            "expected": [{"player_name": "Devin Booker", "injury": "illness", "status": "out"}],
        },
        {
            "tweet": "Jrue Holiday (shoulder) upgraded to available Wednesday.",
            "expected": [{"player_name": "Jrue Holiday", "injury": "shoulder", "status": "available"}],
        },
        {
            "tweet": "OG Anunoby (elbow) downgraded to out Thursday.",
            "expected": [{"player_name": "OG Anunoby", "injury": "elbow", "status": "out"}],
        },
        {
            "tweet": "Karl-Anthony Towns (knee) listed probable for Friday.",
            "expected": [{"player_name": "Karl-Anthony Towns", "injury": "knee", "status": "probable"}],
        },
        {
            "tweet": "Gary Payton II (hamstring) will play Saturday.",
            "expected": [{"player_name": "Gary Payton II", "injury": "hamstring", "status": "available"}],
        },
        {
            "tweet": "AJ Johnson (ankle) ruled out Monday.",
            "expected": [{"player_name": "AJ Johnson", "injury": "ankle", "status": "out"}],
        },
        {
            "tweet": "Dereck Lively II (back) listed questionable for Sunday.",
            "expected": [{"player_name": "Dereck Lively II", "injury": "back", "status": "questionable"}],
        },
        {
            "tweet": "Cade Cunningham (rest) not listed on injury report for Sunday.",
            "expected": [{"player_name": "Cade Cunningham", "injury": "rest", "status": "available"}],
        },
        {
            "tweet": "P.J. Washington (elbow) listed doubtful for Sunday.",
            "expected": [{"player_name": "P.J. Washington", "injury": "elbow", "status": "doubtful"}],
        },
    ]

    passed = 0

    for i, case in enumerate(test_cases, start=1):
        tweet = case["tweet"]
        expected = case["expected"]
        parsed = analyzer.analyze(tweet)
        ok = parsed == expected

        print("\n" + "=" * 100)
        print(f"CASE {i}")
        print("TWEET:")
        print(tweet)
        print("\nEXPECTED:")
        print(expected)
        print("\nPARSED:")
        print(parsed)
        print(f"\nRESULT: {'PASS' if ok else 'FAIL'}")

        if ok:
            passed += 1

    total = len(test_cases)
    print("\n" + "=" * 100)
    print(f"FINAL SCORE: {passed}/{total} passed")


if __name__ == "__main__":
    main()