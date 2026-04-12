import os

os.environ.setdefault("JAVA_HOME", "/opt/homebrew/opt/openjdk")
if "/opt/homebrew/opt/openjdk/bin" not in os.environ.get("PATH", ""):
    os.environ["PATH"] = "/opt/homebrew/opt/openjdk/bin:" + os.environ.get("PATH", "")

VALID_STATUSES = {"Out", "Questionable", "Probable", "Doubtful", "Available"}

REPORT_INTERVAL_MINUTES = 15
