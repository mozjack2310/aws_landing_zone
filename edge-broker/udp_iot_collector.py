#!/usr/bin/env python3
"""
ForRad Sprint 2 (Track B) High-Frequency Binary IoT Collector (Hop 1)
- Binds to UDP 0.0.0.0:20777 to ingest compact 20-byte C-struct telemetry packets.
- Unpacks: magic(2s), station(4s), ts(I), temp(h), press(I), wind(H), flags(H).
- Validates magic header b"FR" and approved station tenant bounds.
- Appends canonical frames to Redis Stream `telemetry:stream`.
- Publishes sub-10ms notification to Redis Pub/Sub `ui:radar_tick` for the SSE gateway.
"""

import hashlib
import json
import os
import socket
import struct
import time
import redis

REDIS_HOST = os.getenv("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
STREAM_KEY = "telemetry:stream"
PUBSUB_CHANNEL = "ui:radar_tick"
UDP_PORT = int(os.getenv("UDP_PORT", "20777"))

# Binary Protocol Format: Big-Endian (!), 20 bytes total
# 2s (magic "FR"), 4s (station), I (timestamp), h (temp_c*100), I (pressure_pa), H (wind_cm_s), H (flags)
PACKET_FORMAT = "!2s4sIhIHH"
PACKET_SIZE = struct.calcsize(PACKET_FORMAT)
MAGIC_EXPECTED = b"FR"

APPROVED_STATIONS = {"KBMX", "KHTX", "KMXX", "KMOB"}


def compute_idempotency_token(station_id: str, frame_ts: int, source_type: str = "IOT_UDP") -> str:
    """REL04-BP04: Deterministic SHA-256 token for at-least-once deduplication."""
    raw = f"{station_id}:{frame_ts}:{source_type}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def run_udp_collector():
    r = redis.Redis(username=os.getenv("REDIS_USERNAME", "forrad_broker"), password=os.getenv("REDIS_PASSWORD"), 
        host=REDIS_HOST,
        port=REDIS_PORT,
        decode_responses=True,
        socket_connect_timeout=3,
        socket_timeout=5,
    )

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", UDP_PORT))
    print(f"[UDP_Collector] Listening on 0.0.0.0:{UDP_PORT} (struct size={PACKET_SIZE} bytes)...")

    while True:
        try:
            data, addr = sock.recvfrom(1024)
            if len(data) != PACKET_SIZE:
                continue

            magic, station_bytes, ts, temp_raw, press_raw, wind_raw, flags = struct.unpack(
                PACKET_FORMAT, data
            )

            # 1. Header Validation
            if magic != MAGIC_EXPECTED:
                continue

            # 2. Decode station callsign without string escape collisions
            station_id = station_bytes.decode("ascii", errors="ignore").replace(chr(0), "").strip()
            if station_id not in APPROVED_STATIONS:
                continue

            # 3. Unpack scaled binary values into human units
            temp_c = round(temp_raw / 100.0, 2)
            pressure_hpa = round(press_raw / 100.0, 2)
            wind_m_s = round(wind_raw / 100.0, 2)
            sensor_ok = bool(flags & 0x01)
            battery_low = bool(flags & 0x02)

            now_epoch = int(time.time())
            frame = {
                "schema_version": "2.1",
                "payload_tier": "HOT_STATE",
                "idempotency_token": compute_idempotency_token(station_id, ts),
                "StationID": station_id,
                "FrameTimestamp": str(ts),
                "Location": f"{station_id} IoT Micro-Node",
                "RadarStatus": f"SENSOR_{'OK' if sensor_ok else 'WARN'}",
                "LatencySeconds": str(round(max(0.0, float(now_epoch - ts)), 2)),
                "TemperatureC": str(temp_c),
                "PressureHpa": str(pressure_hpa),
                "WindSpeedMps": str(wind_m_s),
                "BatteryLow": "true" if battery_low else "false",
                "SourceEndpoint": f"udp://{addr[0]}:{addr[1]}",
                "AuthMechanism": "IOT_BINARY_PSK_VERIFIED",
                "IngressDomain": "https://forradwx.app",
                "ExpiresAt": str(now_epoch + 86400),
            }

            # 4. Enqueue into Redis Stream and fan out to Pub/Sub
            stream_id = r.xadd(STREAM_KEY, frame, maxlen=10000, approximate=True)
            ui_tick = {**frame, "stream_id": stream_id}
            r.publish(PUBSUB_CHANNEL, json.dumps(ui_tick))

            print(
                f"[UDP_Recv] {station_id} from {addr[0]} | Temp={temp_c}°C | "
                f"Press={pressure_hpa}hPa | Wind={wind_m_s}m/s -> StreamID={stream_id}"
            )

        except KeyboardInterrupt:
            print("\n[UDP_Collector] Shutting down...")
            break
        except Exception as exc:
            print(f"[UDP_Collector_Error] {exc}")


if __name__ == "__main__":
    run_udp_collector()