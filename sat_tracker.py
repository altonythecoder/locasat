# sat_tracker.py
import os
import time
import threading
import requests
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from skyfield.api import load, wgs84, EarthSatellite
from dotenv import load_dotenv

load_dotenv()

SPACETRACK_USER = os.getenv("SPACETRACK_USER")
SPACETRACK_PASS = os.getenv("SPACETRACK_PASS")

CACHE_DIR = "tle_backup"
CACHE_EXPIRY_SECONDS = 43200  # 12 Hours Cache Duration

TS = load.timescale(builtin=True)

FALLBACK_ISS_TLE = (
    "ISS (ZARYA)",
    "1 25544U 98067A   26203.50000000  .00016717  00000-0  30000-3 0  9993",
    "2 25544  51.6400 200.0000 0005000 100.0000 260.0000 15.49000000000000"
)

if not os.path.exists(CACHE_DIR):
    os.makedirs(CACHE_DIR)

# Expected thresholds to validate payload completeness
EXPECTED_MIN_COUNTS = {
    "starlink": 3000,
    "active": 1000,
    "all": 3000,
    "leo": 3000,
    "unified_catalog": 3000,
    "oneweb": 300,
    "planet": 100,
    "iridium-next": 50,
    "geo": 300,
    "gnss": 100,
    "meo": 100,
    "stations": 5
}

def _purge_invalid_cache():
    """ Purges corrupted or incomplete TLE cache files from disk on startup """
    if not os.path.exists(CACHE_DIR):
        return
    for fname in os.listdir(CACHE_DIR):
        if fname.endswith(".tle"):
            fpath = os.path.join(CACHE_DIR, fname)
            try:
                for grp_key, min_cnt in EXPECTED_MIN_COUNTS.items():
                    if grp_key in fname.lower():
                        with open(fpath, "r", encoding="utf-8") as f:
                            lines = f.readlines()
                            if len(lines) < min_cnt * 2:
                                f.close()
                                os.remove(fpath)
                                print(f"🧹 Purged incomplete cache file [{fname}]")
                        break
            except Exception:
                pass

_purge_invalid_cache()


def parse_iso_time(ts, iso_str: str):
    """ Parses ISO timestamp string to Skyfield timescale object. Returns live now if null. """
    if not iso_str or iso_str == "null" or iso_str == "None":
        return ts.now()
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return ts.from_datetime(dt)
    except Exception:
        return ts.now()


class SatelliteTracker:
    """ Single satellite detailed tracking engine with persistent disk backing """
    def __init__(self, norad_id: int):
        self.norad_id = norad_id
        self.ts = TS
        self.satellite = self._fetch_tle()

    def _fetch_tle(self) -> EarthSatellite:
        backup_file = os.path.join(CACHE_DIR, f"norad_{self.norad_id}.tle")
        url = f"https://celestrak.org/NORAD/elements/gp.php?CATNR={self.norad_id}&FORMAT=TLE"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

        if os.path.exists(backup_file) and (time.time() - os.path.getmtime(backup_file) < CACHE_EXPIRY_SECONDS):
            try:
                with open(backup_file, "r", encoding="utf-8") as f:
                    lines = [l.strip() for l in f.readlines() if l.strip()]
                    if len(lines) >= 2:
                        name = lines[0] if len(lines) == 3 else f"NORAD-{self.norad_id}"
                        return EarthSatellite(lines[-2], lines[-1], name, self.ts)
            except Exception:
                pass

        try:
            res = requests.get(url, headers=headers, timeout=10)
            if res.status_code == 200 and len(res.text) > 50 and not res.text.lstrip().startswith(("<html", "<!DOCTYPE")):
                lines = [l.strip() for l in res.text.strip().splitlines() if l.strip()]
                if len(lines) >= 2:
                    name = lines[0] if len(lines) == 3 else f"NORAD-{self.norad_id}"
                    with open(backup_file, "w", encoding="utf-8") as f:
                        f.write(res.text)
                    return EarthSatellite(lines[-2], lines[-1], name, self.ts)
        except Exception as e:
            print(f"⚠️ Live fetch failed for NORAD {self.norad_id}: {e}")

        if os.path.exists(backup_file):
            try:
                with open(backup_file, "r", encoding="utf-8") as f:
                    lines = [l.strip() for l in f.readlines() if l.strip()]
                    if len(lines) >= 2:
                        name = lines[0] if len(lines) == 3 else f"NORAD-{self.norad_id}"
                        return EarthSatellite(lines[-2], lines[-1], name, self.ts)
            except Exception:
                pass

        return EarthSatellite(FALLBACK_ISS_TLE[1], FALLBACK_ISS_TLE[2], FALLBACK_ISS_TLE[0], self.ts)

    def get_current_telemetry(self, station_lat: float = 41.0082, station_lon: float = 28.9784, sim_time_iso: str = None) -> dict:
        now = parse_iso_time(self.ts, sim_time_iso)
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
    """ Constellation Engine with Full Parallel Fetching across all NORAD Catalogs """
    _memory_cache = {}
    _cache_timestamps = {}
    _lock = threading.Lock()

    def __init__(self, group_name: str = "starlink"):
        self.ts = TS
        self.group_name = group_name.lower()
        self.satellites = self._get_or_refresh_satellites()

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

    def _fetch_single_group(self, group: str) -> list[EarthSatellite]:
        group_key = group.lower()
        min_expected = EXPECTED_MIN_COUNTS.get(group_key, 10)
        backup_file = os.path.join(CACHE_DIR, f"group_{group_key}.tle")
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

        if os.path.exists(backup_file):
            try:
                with open(backup_file, "r", encoding="utf-8") as f:
                    disk_sats = self._parse_tle_lines(f.readlines())
                    if len(disk_sats) >= min_expected and (time.time() - os.path.getmtime(backup_file) < CACHE_EXPIRY_SECONDS):
                        return disk_sats
            except Exception:
                pass

        if group_key == "starlink":
            urls = [
                "https://celestrak.org/NORAD/elements/supplemental/sup-gp.php?FILE=starlink&FORMAT=tle",
                "https://celestrak.org/NORAD/elements/gp.php?GROUP=starlink&FORMAT=tle"
            ]
        elif group_key == "oneweb":
            urls = [
                "https://celestrak.org/NORAD/elements/gp.php?GROUP=oneweb&FORMAT=tle",
                "https://celestrak.org/NORAD/elements/supplemental/sup-gp.php?FILE=oneweb&FORMAT=tle"
            ]
        elif group_key in ["gnss", "meo"]:
            urls = [
                "https://celestrak.org/NORAD/elements/gp.php?GROUP=gnss&FORMAT=tle"
            ]
        elif group_key == "geo":
            urls = [
                "https://celestrak.org/NORAD/elements/gp.php?GROUP=geo&FORMAT=tle"
            ]
        elif group_key in ["active", "all", "leo"]:
            urls = [
                "https://celestrak.org/NORAD/elements/gp.php?GROUP=active&FORMAT=tle"
            ]
        else:
            urls = [
                f"https://celestrak.org/NORAD/elements/gp.php?GROUP={group_key}&FORMAT=tle"
            ]

        for url in urls:
            try:
                req_timeout = 35 if group_key in ["starlink", "active", "all", "leo", "geo", "gnss"] else 15
                res = requests.get(url, headers=headers, timeout=req_timeout)
                if res.status_code == 200 and len(res.text) > 500 and not res.text.lstrip().startswith(("<html", "<!DOCTYPE")):
                    sats = self._parse_tle_lines(res.text.splitlines())
                    if len(sats) >= min_expected:
                        with open(backup_file, "w", encoding="utf-8") as f:
                            f.write(res.text)
                        print(f"🟢 LIVE TLE UPDATED [{group.upper()}]: {len(sats)} Satellites")
                        return sats
            except Exception as e:
                print(f"⚠️ Network fetch failed for [{group.upper()}] from {url}: {e}")

        if os.path.exists(backup_file):
            try:
                with open(backup_file, "r", encoding="utf-8") as f:
                    disk_sats = self._parse_tle_lines(f.readlines())
                    if disk_sats:
                        print(f"🟡 SERVING PERSISTENT DISK BACKUP FOR [{group.upper()}]: {len(disk_sats)} Satellites")
                        return disk_sats
            except Exception:
                pass

        return []

    def _get_or_refresh_satellites(self) -> list[EarthSatellite]:
        now_ts = time.time()

        regime_modes = ["active", "all", "leo", "meo", "geo"]
        cache_key = "unified_catalog" if self.group_name in regime_modes else self.group_name

        with ConstellationTracker._lock:
            if cache_key in ConstellationTracker._memory_cache:
                cache_time = ConstellationTracker._cache_timestamps.get(cache_key, 0)
                if now_ts - cache_time < CACHE_EXPIRY_SECONDS:
                    cached = ConstellationTracker._memory_cache[cache_key]
                    min_req = EXPECTED_MIN_COUNTS.get(cache_key, 10)
                    if len(cached) >= min_req:
                        return cached

        if self.group_name in regime_modes:
            print("🔄 Building full unified orbital catalog across all NORAD channels...")
            # Comprehensive list of sub-groups to ensure 100% full active catalog coverage (~16,000+ objects)
            sub_groups = [
                "starlink", "oneweb", "stations", "iridium-NEXT", "planet",
                "geo", "gnss", "weather", "resource", "science",
                "other-comm", "satnogs", "amateur", "active"
            ]
            all_sats = []

            with ThreadPoolExecutor(max_workers=len(sub_groups)) as executor:
                future_to_grp = {executor.submit(self._fetch_single_group, grp): grp for grp in sub_groups}
                for future in as_completed(future_to_grp):
                    grp_name = future_to_grp[future]
                    try:
                        res_sats = future.result()
                        all_sats.extend(res_sats)
                        print(f"📦 Group [{grp_name.upper()}] merged: {len(res_sats)} satellites")
                    except Exception as e:
                        print(f"⚠️ Error merging group [{grp_name.upper()}]: {e}")

            # Deduplicate by unique NORAD catalog ID
            unique_dict = {}
            for sat in all_sats:
                try:
                    sat_id = sat.model.satnum
                    if sat_id not in unique_dict:
                        unique_dict[sat_id] = sat
                except AttributeError:
                    unique_dict[sat.name] = sat
            result_sats = list(unique_dict.values())
        else:
            result_sats = self._fetch_single_group(self.group_name)

        if not result_sats and self.group_name != "stations":
            result_sats = self._fetch_single_group("stations")

        with ConstellationTracker._lock:
            ConstellationTracker._memory_cache[cache_key] = result_sats
            ConstellationTracker._cache_timestamps[cache_key] = now_ts

        print(f"🚀 TOTAL {len(result_sats)} SATELLITES LOADED TO UNIFIED ENGINE! [{self.group_name.upper()}]")
        return result_sats

    def get_compact_telemetries(self, sim_time_iso: str = None) -> list[list]:
        now = parse_iso_time(self.ts, sim_time_iso)
        results = []
        subpoint_fn = wgs84.subpoint

        for sat in self.satellites:
            try:
                sub = subpoint_fn(sat.at(now))
                alt_km = round(sub.elevation.km, 2)

                # Dynamic Orbital Regime Filtering
                if self.group_name == "leo" and alt_km > 2000:
                    continue
                elif self.group_name == "meo" and (alt_km <= 2000 or alt_km >= 35000):
                    continue
                elif self.group_name == "geo" and alt_km < 35000:
                    continue

                results.append([
                    round(sub.longitude.degrees, 6),
                    round(sub.latitude.degrees, 6),
                    alt_km,
                    sat.name
                ])
            except Exception:
                continue
        return results
