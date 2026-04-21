import os

BEARER_TOKEN = os.environ.get("X_BEARER_TOKEN", "").strip()

if not BEARER_TOKEN:
    raise RuntimeError(
        "X_BEARER_TOKEN is not set. Define it in poly3/backend/.env "
        "(loaded by server.py) or export it in your shell before starting the listener."
    )
