# main.py
import sys
import os
import json
import asyncio
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, FileResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from sat_tracker import SatelliteTracker, ConstellationTracker, SimClock, to_iso

app = FastAPI(title="locaSAT GCS", version="2.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

SINGLE_INTERVAL_S = 0.2         # 5 Hz telemetry for single satellite
CONSTELLATION_INTERVAL_S = 0.25  # 4 Hz frames for constellations (client interpolates between t0/t1)


def get_resource_path(relative_path):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative_path)


@app.get("/", response_class=HTMLResponse)
async def get_index():
    with open(get_resource_path("index.html"), "r", encoding="utf-8") as f:
        return f.read()


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    favicon_path = get_resource_path("favicon.ico")
    if os.path.exists(favicon_path):
        return FileResponse(favicon_path)
    return Response(status_code=204)


class ConnectionState:
    """ Per-connection simulation clock + ground station, updated from client messages. """
    def __init__(self):
        self.clock = SimClock()
        self.station = {"lat": 41.0082, "lon": 28.9784, "alt_m": 0.0}

    def apply(self, data):
        if not isinstance(data, dict):
            return
        self.clock.update(data)
        st = data.get("station")
        if isinstance(st, dict):
            try:
                self.station = {
                    "lat": float(st.get("lat", self.station["lat"])),
                    "lon": float(st.get("lon", self.station["lon"])),
                    "alt_m": float(st.get("alt_m", self.station["alt_m"]) or 0.0),
                }
            except (TypeError, ValueError):
                pass


async def _listen(websocket: WebSocket, state: ConnectionState):
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                state.apply(json.loads(raw))
            except json.JSONDecodeError:
                continue
    except (WebSocketDisconnect, RuntimeError):
        pass


async def _run_stream(websocket: WebSocket, state: ConnectionState, produce, interval: float):
    listen_task = asyncio.create_task(_listen(websocket, state))
    loop = asyncio.get_running_loop()
    try:
        while not listen_task.done():
            started = loop.time()
            payload = await asyncio.to_thread(produce)
            await websocket.send_text(json.dumps(payload, separators=(",", ":")))
            await asyncio.sleep(max(0.0, interval - (loop.time() - started)))
    except (WebSocketDisconnect, RuntimeError):
        pass
    except Exception as e:
        print(f"⚠️ Stream error: {e}")
    finally:
        listen_task.cancel()


@app.websocket("/ws/orbit/{norad_id}")
async def websocket_single_orbit(websocket: WebSocket, norad_id: int):
    await websocket.accept()
    state = ConnectionState()
    tracker = await asyncio.to_thread(SatelliteTracker, norad_id)

    def produce():
        t0 = state.clock.now()
        t1 = state.clock.advance(t0, SINGLE_INTERVAL_S)
        st = state.station
        return tracker.get_current_telemetry(
            station_lat=st["lat"], station_lon=st["lon"], station_alt_m=st["alt_m"],
            sim_dt=t0, next_dt=t1,
        )

    await _run_stream(websocket, state, produce, SINGLE_INTERVAL_S)


@app.websocket("/ws/constellation/{group_name}")
async def websocket_constellation(websocket: WebSocket, group_name: str):
    await websocket.accept()
    state = ConnectionState()
    tracker = await asyncio.to_thread(ConstellationTracker, group_name)
    names_sent = False

    def produce():
        nonlocal names_sent
        t0 = state.clock.now()
        t1 = state.clock.advance(t0, CONSTELLATION_INTERVAL_S)
        frame = {"t0": to_iso(t0), "t1": to_iso(t1), "sats": tracker.get_frame(t0, t1)}
        if not names_sent:
            frame["names"] = tracker.names
            names_sent = True
        return frame

    await _run_stream(websocket, state, produce, CONSTELLATION_INTERVAL_S)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000, use_colors=False, log_config=None)
