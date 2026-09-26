"""
Docker HEALTHCHECK probe for the SpondBot app container.

Exits 0 when GET /api/v1/health answers 200, and 1 otherwise (503 from a broken
database or stopped scheduler, any other error status, timeout, or no answer).
Standard library only, because the slim image has no curl.
"""
import os
import sys
import urllib.error
import urllib.request

URL = os.environ.get("HEALTHCHECK_URL", "http://127.0.0.1:8080/api/v1/health")


def main() -> int:
    try:
        with urllib.request.urlopen(URL, timeout=4) as resp:
            return 0 if resp.status == 200 else 1
    except (urllib.error.URLError, OSError) as exc:  # HTTPError (4xx/5xx) is a URLError
        print(f"unhealthy: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
