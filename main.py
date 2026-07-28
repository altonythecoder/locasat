# main.py
import sys
import os
import uvicorn
import asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from sat_tracker import SatelliteTracker, ConstellationTracker

app = FastAPI(title="Global LEO Satellite Tracker GCS", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_resource_path(relative_path):
    """ Returns the temporary folder path when packaged with PyInstaller, or the local path during normal execution """
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
    try:
        while True:
            telemetry = tracker.get_current_telemetry()
            await websocket.send_json(telemetry)
            await asyncio.sleep(0.8)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"WS Single Error: {e}")

@app.websocket("/ws/constellation/{group_name}")
async def websocket_constellation(websocket: WebSocket, group_name: str):
    await websocket.accept()
    tracker = ConstellationTracker(group_name)
    try:
        while True:
            telemetries = tracker.get_compact_telemetries()
            await websocket.send_json(telemetries)
            await asyncio.sleep(1.0)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"WS Constellation Error: {e}")

# Block to automatically start the server when double-clicked as .exe:
if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
