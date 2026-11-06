"""Container health probe: succeeds only when /health/ready/ reports ok."""

import json
import os
import sys
import urllib.error
import urllib.request

PORT = os.getenv("GUNICORN_BIND", "0.0.0.0:8000").rsplit(":", 1)[-1]
# Django rejects unknown Host headers, so probe with the first allowed host.
HOST = next(
    (
        host.strip().lstrip(".")
        for host in os.getenv("DJANGO_ALLOWED_HOSTS", "").split(",")
        if host.strip() and host.strip() != "*"
    ),
    "localhost",
)


def main() -> int:
    """Return 0 when the readiness endpoint reports ok, otherwise 1."""
    request = urllib.request.Request(
        f"http://127.0.0.1:{PORT}/health/ready/", headers={"Host": HOST}
    )
    try:
        with urllib.request.urlopen(request, timeout=4) as response:
            payload = json.load(response)
    except (urllib.error.URLError, OSError, ValueError):
        return 1
    return 0 if payload.get("status") == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
