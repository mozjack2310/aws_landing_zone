#!/usr/bin/env python3
"""
ForRad Phase 5 FastAPI SSE Multiplexer (Hop 3C - v2.2 Cloudflare Edge Hardened)
- Enforces Redis 7 ACL authentication (user forrad_broker) per ADR-008.
- Emits immediate SSE preamble (: connected) to flush HTTP/2 headers through cloudflared in <1ms.
- Defends against Cloudflare/NGINX proxy buffering (X-Accel-Buffering: no, Cache-Control: no-cache, no-transform).
- Defends against Cloudflare 100s idle timeout via 15-second ': heartbeat\n\n' keepalive comments (ADR-006).
- Implements Last-Event-ID gap replay via Redis Streams XRANGE before live Pub/Sub fan-out.
- Self-healing singleton Pub/Sub listener with explicit /healthz task verification (REL05-BP04).
"""

import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
from typing import AsyncGenerator, Set
from fastapi import FastAPI, Header, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
import redis.asyncio as aioredis

REDIS_HOST = os.getenv("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_USERNAME = os.getenv("REDIS_USERNAME", "default")
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", None)

STREAM_KEY = "telemetry:stream"
PUBSUB_CHANNEL = "ui:radar_tick"
HEARTBEAT_INTERVAL_SEC = 15.0
MAX_CLIENT_QUEUE_SIZE = 64

CLIENT_QUEUES: Set[asyncio.Queue] = set()
REDIS_CLIENT: aioredis.Redis | None = None
BROADCASTER_TASK: asyncio.Task | None = None


async def pubsub_broadcaster() -> None:
    """Self-healing singleton background worker reading ui:radar_tick and fanning out in-memory."""
    backoff = 1.0
    while True:
        pubsub = REDIS_CLIENT.pubsub()
        try:
            await pubsub.subscribe(PUBSUB_CHANNEL)
            print(f"[SSE_Broadcaster] Subscribed to Redis channel '{PUBSUB_CHANNEL}' (Singleton Active)...")
            backoff = 1.0

            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue

                payload = message["data"]
                for q in list(CLIENT_QUEUES):
                    try:
                        q.put_nowait(payload)
                    except asyncio.QueueFull:
                        try:
                            q.get_nowait()
                        except asyncio.QueueEmpty:
                            pass
                        q.put_nowait(payload)
        except asyncio.CancelledError:
            await pubsub.unsubscribe(PUBSUB_CHANNEL)
            await pubsub.aclose()
            print("[SSE_Broadcaster] Broadcaster task cleanly cancelled.")
            raise
        except Exception as exc:
            print(f"[SSE_Broadcaster_Warn] Pub/Sub connection interrupted ({exc}); retrying in {backoff:.1f}s...")
            try:
                await pubsub.aclose()
            except Exception:
                pass
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2.0, 15.0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle manager initializing ACL-authenticated Redis and singleton broadcaster."""
    global REDIS_CLIENT, BROADCASTER_TASK
    REDIS_CLIENT = aioredis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        username=REDIS_USERNAME if REDIS_PASSWORD else None,
        password=REDIS_PASSWORD,
        decode_responses=True,
        socket_connect_timeout=3,
        socket_timeout=5,
        health_check_interval=15,
    )
    BROADCASTER_TASK = asyncio.create_task(pubsub_broadcaster())
    yield
    if BROADCASTER_TASK:
        BROADCASTER_TASK.cancel()
        await asyncio.gather(BROADCASTER_TASK, return_exceptions=True)
    if REDIS_CLIENT:
        await REDIS_CLIENT.aclose()


app = FastAPI(title="ForRad Telemetry SSE Gateway", version="2.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://forradwx.app",
        "https://forradwx.com",
        "http://127.0.0.1:8000",
        "http://localhost:8000",
    ],
    allow_credentials=True,
    allow_methods=["GET", "HEAD", "OPTIONS"],
    allow_headers=["*", "Last-Event-ID", "Cache-Control"],
)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """Enforce zero-trust origin security headers on all responses (ADR-009)."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"
    return response


@app.api_route("/", methods=["GET", "HEAD"])
async def serve_dashboard():
    """Serve the Esri Dark Canvas Tactical HUD (supports GET and HEAD probes)."""
    for candidate in ("/app/static/index.html", "edge-broker/static/index.html", "static/index.html"):
        if os.path.exists(candidate):
            return FileResponse(candidate)
    return FileResponse(Path(__file__).resolve().parent / "static" / "index.html")


@app.api_route("/healthz", methods=["GET", "HEAD"])
async def health_check(response: Response):
    """Deep readiness probe checking Redis ACL ping and singleton broadcaster task state."""
    redis_ok = False
    if REDIS_CLIENT:
        try:
            redis_ok = bool(await REDIS_CLIENT.ping())
        except Exception:
            redis_ok = False

    broadcaster_ok = BROADCASTER_TASK is not None and not BROADCASTER_TASK.done()
    is_healthy = redis_ok and broadcaster_ok

    if not is_healthy:
        response.status_code = 503

    return {
        "status": "HEALTHY" if is_healthy else "UNHEALTHY",
        "redis_connected": redis_ok,
        "broadcaster_alive": broadcaster_ok,
        "active_sse_subscribers": len(CLIENT_QUEUES),
        "stream_key": STREAM_KEY,
    }


async def sse_event_generator(request: Request, last_event_id: str | None) -> AsyncGenerator[str, None]:
    """Generates SSE stream: flushes preamble, replays missed frames via XRANGE, and streams live ticks."""
    client_queue: asyncio.Queue = asyncio.Queue(maxsize=MAX_CLIENT_QUEUE_SIZE)
    CLIENT_QUEUES.add(client_queue)

    try:
        # Phase 0: Immediate preamble flush so Uvicorn & Cloudflare transmit 200 OK headers in <1ms
        yield ": connected\nretry: 3000\n\n"

        # Phase 1: Replay missed events from Redis Stream if Last-Event-ID header or query param is present
        if last_event_id and last_event_id.strip():
            clean_id = last_event_id.strip()
            if "-" in clean_id:
                start_cursor = f"({clean_id}"
                try:
                    missed_entries = await REDIS_CLIENT.xrange(STREAM_KEY, min=start_cursor, max="+", count=50)
                    if missed_entries:
                        print(f"[SSE_Replay] Replaying {len(missed_entries)} missed frames starting at {start_cursor}")
                        for stream_id, fields in missed_entries:
                            if fields.get("event_type") == "SYSTEM_INIT":
                                continue
                            yield f"id: {stream_id}\nevent: radar_tick\ndata: {json.dumps(fields)}\n\n"
                except Exception as exc:
                    print(f"[SSE_Replay_Warn] XRANGE replay skipped for {clean_id}: {exc}")

        # Phase 2: Live event loop with 15s heartbeats (<100s Cloudflare idle timeout)
        while True:
            if await request.is_disconnected():
                break

            try:
                payload = await asyncio.wait_for(client_queue.get(), timeout=HEARTBEAT_INTERVAL_SEC)
                try:
                    data_dict = json.loads(payload)
                    stream_id = data_dict.get("stream_id") or data_dict.get("RedisStreamID", "")
                except Exception:
                    stream_id = ""
                if stream_id:
                    yield f"id: {stream_id}\nevent: radar_tick\ndata: {payload}\n\n"
                else:
                    yield f"event: radar_tick\ndata: {payload}\n\n"
            except asyncio.TimeoutError:
                # 15s idle heartbeat comment (preserves Cloudflare QUIC & reverse-proxy sockets)
                yield ": heartbeat\n\n"

    finally:
        CLIENT_QUEUES.discard(client_queue)


@app.get("/api/v1/telemetry/sse")
@app.get("/api/v1/stream")
@app.get("/api/v1/telemetry/stream")
async def stream_telemetry(
    request: Request,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
):
    """Streaming endpoint delivering Server-Sent Events with strict anti-buffering headers."""
    effective_last_id = last_event_id or request.query_params.get("lastEventId")
    return StreamingResponse(
        sse_event_generator(request, effective_last_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("sse_broadcaster:app", host="127.0.0.1", port=8000, log_level="info")
