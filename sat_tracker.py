# sat_tracker.py
import os
import requests
from datetime import timedelta
from skyfield.api import load, wgs84, EarthSatellite
from dotenv import load_dotenv

load_dotenv()

SPACETRACK_USER = os.getenv("SPACETRACK_USER")
SPACETRACK_PASS = os.getenv("SPACETRACK_PASS")

FALLBACK_ISS_TLE = (
    "ISS (ZARYA)",
    "1 25544U 98067A   26203.50000000  .00016717  00000-0  30000-3 0  9993",
    "2 25544  51.6400 200.0000 0005000 100.0000 260.0000 15.49000000000000"
)

class SatelliteTracker:
    """Tekli uydu detay takibi ve gecit tahmin motoru."""
    def __init__(self, norad_id: int):
        self.norad_id = norad_id
        self.ts = load.timescale(builtin=True)
        self.satellite = self._fetch_tle()

    def _fetch_tle(self) -> EarthSatellite:
        url = f"https://celestrak.org/NORAD/elements/gp.php?CATNR={self.norad_id}&FORMAT=TLE"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        try:
            res = requests.get(url, headers=headers, timeout=5)
            if res.status_code == 200:
                lines = [l.strip() for l in res.text.strip().splitlines() if l.strip()]
                if len(lines) >= 2:
                    name = lines[0] if len(lines) == 3 else f"NORAD-{self.norad_id}"
                    return EarthSatellite(lines[-2], lines[-1], name, self.ts)
        except Exception:
            pass
        return EarthSatellite(FALLBACK_ISS_TLE[1], FALLBACK_ISS_TLE[2], FALLBACK_ISS_TLE[0], self.ts)

    def get_current_telemetry(self, station_lat: float = 41.0082, station_lon: float = 28.9784) -> dict:
        now = self.ts.now()
        geocentric = self.satellite.at(now)
        subpoint = wgs84.subpoint(geocentric)
        station = wgs84.latlon(station_lat, station_lon)
        topocentric = (self.satellite - station).at(now)
        alt, az, distance = topocentric.altaz()
        
        return {
            "satellite_name": self.satellite.name,
            "norad_id": self.norad_id,
            "timestamp_utc": now.utc_iso(),
            "latitude": round(subpoint.latitude.degrees, 4),
            "longitude": round(subpoint.longitude.degrees, 4),
            "altitude_km": round(subpoint.elevation.km, 2),
            "azimuth_deg": round(az.degrees, 2),
            "elevation_deg": round(alt.degrees, 2),
            "distance_km": round(distance.km, 2),
        }


class ConstellationTracker:
    """Coklu LEO Filolari ve Takimyildiz Takip Motoru."""
    def __init__(self, group_name: str = "starlink"):
        self.ts = load.timescale(builtin=True)
        self.group_name = group_name.lower()
        self.satellites = self._fetch_all_tles()

    def _parse_tle_lines(self, lines: list[str]) -> list[EarthSatellite]:
        sats = []
        clean_lines = [l.strip() for l in lines if l.strip()]
        
        i = 0
        while i < len(clean_lines) - 1:
            if clean_lines[i].startswith("1 ") and clean_lines[i+1].startswith("2 "):
                line1 = clean_lines[i]
                line2 = clean_lines[i+1]
                name = "SATELLITE"
                if i > 0 and not clean_lines[i-1].startswith(("1 ", "2 ")):
                    name = clean_lines[i-1]
                    if name.startswith("0 "):
                        name = name[2:].strip()
                try:
                    sats.append(EarthSatellite(line1, line2, name, self.ts))
                except Exception:
                    pass
                i += 2
            else:
                i += 1
        return sats

    def _fetch_from_celestrak(self) -> list[EarthSatellite]:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        
        if self.group_name == "starlink":
            urls = ["https://celestrak.org/NORAD/elements/supplemental/sup-gp.php?FILE=starlink&FORMAT=tle"]
        elif self.group_name == "oneweb":
            urls = ["https://celestrak.org/NORAD/elements/supplemental/sup-gp.php?FILE=oneweb&FORMAT=tle"]
        elif self.group_name == "active":
            urls = ["https://celestrak.org/NORAD/elements/gp.php?GROUP=active&FORMAT=tle"]
        else:
            urls = [f"https://celestrak.org/NORAD/elements/gp.php?GROUP={self.group_name}&FORMAT=tle"]

        for url in urls:
            try:
                res = requests.get(url, headers=headers, timeout=60)
                if res.status_code == 200 and len(res.text) > 200:
                    sats = self._parse_tle_lines(res.text.splitlines())
                    if sats:
                        return sats
            except Exception as e:
                print(f"⚠️ CelesTrak Baglanti Hatasi ({url}): {e}")

        return []

    def _fetch_all_tles(self) -> list[EarthSatellite]:
        # Eğer grup 'active' ise, CelesTrak'in genel listesi yerine 
        # sadece LEO filolarını birleştirerek çek
        if self.group_name == "active":
            sats = []
            for grp in ["starlink", "oneweb", "stations", "iridium-NEXT"]:
                self.group_name = grp
                sats.extend(self._fetch_from_celestrak())
            self.group_name = "active" # Loglama için ismi geri düzelt
        else:
            sats = self._fetch_from_celestrak()

        # Eğer hala uydu yoksa ve grup 'stations' değilse istasyonları çek (Fallback)
        if not sats and self.group_name != "stations":
            self.group_name = "stations"
            sats = self._fetch_from_celestrak()

        print(f"🚀 TOPLAM {len(sats)} ADET AKTIF UYDU MOTORUNA YUKLENDI! [{self.group_name.upper()}]")
        return sats
        
    def get_compact_telemetries(self) -> list[list]:
        now = self.ts.now()
        results = []
        subpoint_fn = wgs84.subpoint
        
        for sat in self.satellites:
            try:
                sub = subpoint_fn(sat.at(now))
                results.append([
                    round(sub.longitude.degrees, 6),
                    round(sub.latitude.degrees, 6),
                    round(sub.elevation.km, 2),
                    sat.name
                ])
            except Exception:
                continue
        return results
