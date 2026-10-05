#!/usr/bin/env python3
"""Find and test TRUSTED_PROXIES before deploying: no database, no app code, nothing is touched.

It starts a tiny web server that answers the question "which visitor IP would SpondBot record?"
with the same uvicorn settings the app uses.

  python scripts/proxy_check.py peer     which address do connections come from? That address is
                                         your reverse proxy: the value for TRUSTED_PROXIES.
  python scripts/proxy_check.py verify   behave like the app with the current TRUSTED_PROXIES
                                         (from the environment, default 127.0.0.1) and show which
                                         visitor IP would be recorded.

In Docker, next to the running app (see docs/setup.md, "Find and test TRUSTED_PROXIES"):
  docker compose run --rm --no-deps -p 8099:8080 app python scripts/proxy_check.py peer
"""
import argparse
import os

import uvicorn

DEFAULT_TRUSTED = "127.0.0.1"  # keep in sync with the Dockerfile


def trusted_proxies() -> str:
    return os.environ.get("TRUSTED_PROXIES") or DEFAULT_TRUSTED


def build_app(mode: str, trusted: str):
    async def app(scope, receive, send):
        if scope["type"] != "http":
            return
        seen = scope["client"][0] if scope.get("client") else "unknown"
        forwarded = next((v.decode("latin-1") for k, v in scope["headers"] if k == b"x-forwarded-for"), None)
        lines = [f"SpondBot proxy check (mode: {mode})", ""]
        if mode == "peer":
            lines += [
                f"Connection comes from: {seen}",
                "",
                f"If this request came from your reverse proxy, put this in .env:  TRUSTED_PROXIES={seen}",
                "(If it came from anywhere else, run the check again from the proxy machine.)",
            ]
        else:
            lines.append(f"TRUSTED_PROXIES: {trusted}")
            lines.append(f"X-Forwarded-For header received: {forwarded or '(none)'}")
            lines.append(f"SpondBot would record this visitor as: {seen}")
            lines.append("")
            if not forwarded:
                lines.append("No X-Forwarded-For header was sent: send one with  curl -H 'X-Forwarded-For: 203.0.113.9' ...")
            elif seen in [part.strip() for part in forwarded.split(",")]:
                lines.append("RESULT: header honored. This connection came from a trusted proxy.")
            else:
                lines.append("RESULT: header ignored. This connection did not come from a trusted proxy.")
            if trusted.strip() == "*":
                lines.append("WARNING: * trusts every sender, so anyone can fake their IP. Enter your proxy's address instead.")
        body = ("\n".join(lines) + "\n").encode()
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"text/plain; charset=utf-8"), (b"cache-control", b"no-store")]})
        await send({"type": "http.response.body", "body": body})

    return app


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("mode", choices=["peer", "verify"])
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args(argv)

    trusted = trusted_proxies()
    print(f"proxy_check ({args.mode}) listening on {args.host}:{args.port}"
          + (f", TRUSTED_PROXIES={trusted}" if args.mode == "verify" else "")
          + ". Open it from your proxy machine; stop with Ctrl+C.", flush=True)
    uvicorn.run(
        build_app(args.mode, trusted),
        host=args.host,
        port=args.port,
        # "peer" must see the raw connection, so it never rewrites the address from headers
        proxy_headers=args.mode == "verify",
        forwarded_allow_ips=trusted,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
