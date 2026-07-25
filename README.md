# 🛰️ locaSAT: Global LEO Satellite Tracker & Ground Control Station

![Version](https://img.shields.io/badge/version-2.0.0-blue.svg)
![Python](https://img.shields.io/badge/python-3.11-38bdf8.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-00a393.svg)
![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)

A highly responsive, real-time 3D Space Domain Awareness (SDA) platform designed to track Low Earth Orbit (LEO) constellations. Built with a high-performance Python backend and a WebGL frontend, **locaSAT** visualizes major satellite fleets including Starlink, OneWeb, Iridium NEXT, Planet Labs, and the ISS with 60 FPS smooth interpolation using SGP4 orbital mechanics.

[![Watch on YouTube](https://img.shields.io/badge/YouTube-Watch-red?style=for-the-badge&logo=youtube&logoColor=white)](https://www.youtube.com/watch?v=mw_Cf_Toy80)

## ✨ Key Features

### 🔭 Advanced Orbital Mechanics & Tracking
* **SGP4 Propagator:** Utilizes the `skyfield` library to calculate high-precision geocentric and topocentric satellite positions.
* **Live TLE Fetching:** Automatically retrieves the latest Two-Line Elements (TLE) from Celestrak for highly accurate telemetry. Includes built-in fallback TLEs for uninterrupted ISS tracking.
* **Massive Scale:** Capable of rendering over 10,000 active LEO space objects simultaneously, grouping them by operators (SpaceX, OneWeb, Iridium, etc.).

### 💻 Ground Control Station (GCS) Interface
* **3D WebGL Globe:** Powered by CesiumJS for seamless, hardware-accelerated Earth visualization.
* **Custom Ground Stations:** View satellites relative to major observatories such as DAG (3170m) and TÜBİTAK TUG (2500m), or input manual latitude/longitude coordinates.
* **Live GPS Integration:** Automatically locks the ground station to your current physical location via browser Geolocation API.
* **Glassmorphism UI:** A sleek, modern command panel with a dynamic "Fleet Info Card" displaying operator details, orbital speeds (~27,000 km/s), altitudes, and communication bands.
* **Map Layers:** Switch between Real-Time Satellite Imagery, ESRI Dark, and CartoDB Dark Matter themes.

### ⚡ Real-Time Data Streaming
* **WebSocket Architecture:** Telemetry data is streamed asynchronously from the FastAPI backend to the frontend every 1.0 second.
* **Data Validation:** Strict schema enforcement using Pydantic models for live telemetry and pass predictions (`LiveSatelliteTelemetry`, `PassPrediction`).

---

## 🏗️ System Architecture & Tech Stack

**Backend:**
* **Language:** Python 3.11
* **Framework:** FastAPI, Uvicorn
* **Astrodynamics:** Skyfield (SGP4), NumPy
* **Data Validation:** Pydantic

**Frontend:**
* **Core:** HTML5, CSS3, JavaScript (Vanilla)
* **3D Engine:** CesiumJS (v1.115)

**DevOps & Deployment:**
* Docker & Docker Compose
* Environment variable management via `.env`

---

## 🚀 Installation & Setup

### Prerequisites
* Docker & Docker Compose (Recommended)
* Python 3.11+ (For manual setup)
* Optional: Spacetrack account credentials for extended API access.

### Method 1: Docker (Recommended)
The easiest way to run locaSAT is using Docker.

```bash
# 1. Clone the repository
git clone https://github.com/altonythecoder/locasat-location.leosat
cd locasat-location.leosat

# 2. Build and launch the container
docker compose up --build
```

The application will be accessible at `http://localhost:8000`.

### Method 2: Manual Installation

```bash
# 1. Create a virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows use: .venv\Scripts\activate

# 2. Install dependencies
pip install --no-cache-dir -r requirements.txt

# 3. Start the ASGI Server
uvicorn main:app --host 0.0.0.0 --port 8000
```

---

## 📡 WebSocket API Reference

locaSAT exposes real-time endpoints for raw telemetry data.

### `WS /ws/orbit/{norad_id}`

Streams highly detailed telemetry for a single satellite relative to the ground station.
Yields (`LiveSatelliteTelemetry`):

```json
{
  "satellite_name": "ISS (ZARYA)",
  "norad_id": 25544,
  "timestamp_utc": "2026-07-23T10:00:00Z",
  "latitude": 51.64,
  "longitude": 120.5,
  "altitude_km": 415.2,
  "azimuth_deg": 145.2,
  "elevation_deg": 45.1,
  "distance_km": 520.4
}
```

### `WS /ws/constellation/{group_name}`

Streams compact coordinate arrays for entire constellations to optimize frontend rendering performance. Supported groups: `starlink`, `oneweb`, `iridium-NEXT`, `planet`, `stations`, `active`.

**Yields:**

```json
[
  [ -120.4532, 45.1234, 550.2, "STARLINK-1234" ],
  [ 45.1234, -12.3456, 549.8, "STARLINK-5678" ]
]
```

---

## ⚙️ Configuration

You can configure the backend by creating a `.env` file in the root directory:

```ini
SPACETRACK_USER=your_email@example.com
SPACETRACK_PASS=your_password
PYTHONUNBUFFERED=1
```

Note: The `.env` file is ignored by git for security purposes.

---

## 📜 License & Copyright

**Copyright © 2026 Altay. All rights reserved.**

This software and its source code are proprietary. Unauthorized copying, modification, distribution, or commercial use of this file, via any medium, is strictly prohibited.

---

*Built with 💻 and ☕ for Space Domain Awareness.*
