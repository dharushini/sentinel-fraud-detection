"""Vercel serverless entrypoint.

Vercel functions are request-scoped: no background asyncio loop and no
WebSocket. `SENTINEL_SERVERLESS=1` switches the app to poll-driven mode — the
dashboard calls `POST /tick` for ambient traffic and injections run
synchronously. State is per-instance and resets on cold start (attach an
external Redis via SENTINEL_REDIS_URL for continuity).
"""
import os

os.environ.setdefault("SENTINEL_SERVERLESS", "1")

from sentinel.main import app  # noqa: E402  (ASGI app picked up by @vercel/python)
