# main.py
import sys
import os
import uvicorn
import asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from sat_tracker import SatelliteTracker, ConstellationTracker

app = FastAPI(title="locaSAT GCS", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)

@app.get("/", response_class=HTMLResponse)
async def get_index():
    html_path = get_resource_path("index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        return f.read()

@app.get('/favicon.ico', include_in_schema=False)
async def favicon():
    favicon_path = get_resource_path("favicon.ico")
    return FileResponse(favicon_path)

@app.websocket("/ws/orbit/{norad_id}")
async def websocket_single_orbit(websocket: WebSocket, norad_id: int):
    await websocket.accept()
    tracker = SatelliteTracker(norad_id)
    sim_time_iso = None

    async def listen_time_updates():
        nonlocal sim_time_iso
        try:
            while True:
                data = await websocket.receive_json()
                if "sim_time" in data:
                    val = data.get("sim_time")
                    if val is None or val == "null" or val == "":
                        sim_time_iso = None
                    else:
                        sim_time_iso = val
        except Exception:
            pass

    listen_task = asyncio.create_task(listen_time_updates())

    try:
        while True:
            telemetry = tracker.get_current_telemetry(sim_time_iso=sim_time_iso)
            await websocket.send_json(telemetry)
            await asyncio.sleep(0.2)  # Fast 5Hz telemetry stream for high time-rate accuracy
    except WebSocketDisconnect:
        pass
    finally:
        listen_task.cancel()

@app.websocket("/ws/constellation/{group_name}")
async def websocket_constellation(websocket: WebSocket, group_name: str):
    await websocket.accept()
    tracker = ConstellationTracker(group_name)
    sim_time_iso = None

    async def listen_time_updates():
        nonlocal sim_time_iso
        try:
            while True:
                data = await websocket.receive_json()
                if "sim_time" in data:
                    val = data.get("sim_time")
                    if val is None or val == "null" or val == "":
                        sim_time_iso = None
                    else:
                        sim_time_iso = val
        except Exception:
            pass

    listen_task = asyncio.create_task(listen_time_updates())

    try:
        while True:
            telemetries = tracker.get_compact_telemetries(sim_time_iso=sim_time_iso)
            await websocket.send_json(telemetries)
            await asyncio.sleep(0.2)  # Fast 5Hz telemetry stream for high time-rate accuracy
    except WebSocketDisconnect:
        pass
    finally:
        listen_task.cancel()

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000, use_colors=False, log_config=None)
