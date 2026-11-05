#!/usr/bin/env python3
"""
ForRad Sprint 2 (Track B) FastAPI SSE Multiplexer (Hop 3)
- Provides /api/v1/telemetry/sse endpoint for high-frequency dashboard streaming.
- Implements Last-Event-ID gap replay via Redis Streams `XRANGE`.
- Implements Singleton Broadcaster over Redis Pub/Sub `ui:radar_tick` (avoids connection bombs).
- Defends against proxy buffering (X-Accel-Buffering: no) and idle drops (15s heartbeats).
"""

import asyncio
from contextlib import asynccontextmanager
import json
import os
from typing import AsyncGenerator, Set
from fastapi import FastAPI, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import redis.asyncio as aioredis

REDIS_HOST = os.getenv("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
STREAM_KEY = "telemetry:stream"
PUBSUB_CHANNEL = "ui:radar_tick"

# Global client connection registry for singleton Pub/Sub fanout
CLIENT_QUEUES: Set[asyncio.Queue] = set()
REDIS_CLIENT: aioredis.Redis | None = None
BROADCASTER_TASK: asyncio.Task | None = None


async def pubsub_broadcaster():
    """Singleton background worker reading ui:radar_tick and fanning out in-memory."""
    pubsub = REDIS_CLIENT.pubsub()
    await pubsub.subscribe(PUBSUB_CHANNEL)
    print(f"[SSE_Broadcaster] Subscribed to Redis channel '{PUBSUB_CHANNEL}' (Singleton Active)...")

    try:
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue

            payload = message["data"]
            # Fan out to all active browser subscriber queues
            for q in list(CLIENT_QUEUES):
                try:
                    q.put_nowait(payload)
                except asyncio.QueueFull:
                    # Drop oldest tick for slow clients (REL05-BP04: Limit queues)
                    try:
                        q.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                    q.put_nowait(payload)
    except asyncio.CancelledError:
        await pubsub.unsubscribe(PUBSUB_CHANNEL)
        await pubsub.close()
        print("[SSE_Broadcaster] Broadcaster task cleanly cancelled.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle manager initializing Redis and background singleton broadcaster."""
    global REDIS_CLIENT, BROADCASTER_TASK
    REDIS_CLIENT = aioredis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        decode_responses=True,
        socket_connect_timeout=3,
        socket_timeout=5,
    )
    BROADCASTER_TASK = asyncio.create_task(pubsub_broadcaster())
    yield
    if BROADCASTER_TASK:
        BROADCASTER_TASK.cancel()
    if REDIS_CLIENT:
        await REDIS_CLIENT.aclose()


app = FastAPI(title="ForRad Telemetry SSE Gateway", version="2.1", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://forradwx.app", "http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/healthz")
async def health_check():
    """Readiness probe checking Redis ping and subscriber count."""
    is_healthy = await REDIS_CLIENT.ping() if REDIS_CLIENT else False
    return {
        "status": "HEALTHY" if is_healthy else "UNHEALTHY",
        "active_sse_subscribers": len(CLIENT_QUEUES),
        "stream_key": STREAM_KEY,
    }


async def sse_event_generator(request: Request, last_event_id: str | None) -> AsyncGenerator[str, None]:
    """Generates SSE stream: replays missed stream frames first, then streams live ticks."""
    client_queue: asyncio.Queue = asyncio.Queue(maxsize=64)
    CLIENT_QUEUES.add(client_queue)

    try:
        # Phase 1: Replay missed events from Redis Stream if Last-Event-ID header is present
        if last_event_id and last_event_id.strip():
            start_cursor = f"({last_event_id.strip()}"  # Exclusive range
            missed_entries = await REDIS_CLIENT.xrange(STREAM_KEY, min=start_cursor, max="+", count=50)
            if missed_entries:
                print(f"[SSE_Replay] Replaying {len(missed_entries)} missed frames starting at {start_cursor}")
                for stream_id, fields in missed_entries:
                    yield f"id: {stream_id}\nevent: radar_tick\ndata: {json.dumps(fields)}\n\n"

        # Phase 2: Live event loop with 15s heartbeats to keep proxy sockets alive
        while True:
            if await request.is_disconnected():
                break

            try:
                # Wait up to 15 seconds for a live tick
                payload = await asyncio.wait_for(client_queue.get(), timeout=15.0)
                data_dict = json.loads(payload)
                stream_id = data_dict.get("stream_id", "")
                yield f"id: {stream_id}\nevent: radar_tick\ndata: {payload}\n\n"
            except asyncio.TimeoutError:
                # 15s idle heartbeat comment (preserves Cloudflare and reverse-proxy sockets)
                yield ": heartbeat\n\n"

    finally:
        CLIENT_QUEUES.discard(client_queue)


@app.get("/api/v1/telemetry/sse")
async def stream_telemetry(request: Request, last_event_id: str | None = Header(default=None)):
    """Streaming endpoint delivering Server-Sent Events."""
    response = StreamingResponse(
        sse_event_generator(request, last_event_id),
        media_type="text/event-stream",
    )
    # Defense against reverse proxy buffering and transformation
    response.headers["Cache-Control"] = "no-cache, no-transform"
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Connection"] = "keep-alive"
    return response


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("sse_broadcaster:app", host="127.0.0.1", port=8000, log_level="info")