# sat_tracker.py
import os
import time
import threading
import requests
import numpy as np
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from sgp4.api import SatrecArray, jday
from skyfield.api import load, wgs84, EarthSatellite
from skyfield.sgp4lib import theta_GMST1982
from dotenv import load_dotenv

load_dotenv()

SPACETRACK_USER = os.getenv("SPACETRACK_USER")
SPACETRACK_PASS = os.getenv("SPACETRACK_PASS")

CACHE_DIR = os.getenv("TLE_CACHE_DIR", "tle_backup")
CACHE_EXPIRY_SECONDS = 43200  # 12 Hours Cache Duration
HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

TS = load.timescale(builtin=True)

FALLBACK_ISS_TLE = (
    "ISS (ZARYA)",
    "1 25544U 98067A   26203.50000000  .00016717  00000-0  30000-3 0  9993",
    "2 25544  51.6400 200.0000 0005000 100.0000 260.0000 15.49000000000000"
)

os.makedirs(CACHE_DIR, exist_ok=True)

# Expected thresholds to validate payload completeness
EXPECTED_MIN_COUNTS = {
    "starlink": 3000,
    "active": 1000,
    "unified_catalog": 3000,
    "oneweb": 300,
    "planet": 100,
    "iridium-next": 50,
    "geo": 300,
    "gnss": 100,
    "stations": 5
}

REGIME_MODES = ("active", "all", "leo", "meo", "geo")

# WGS84 ellipsoid
_WGS84_A = 6378.137
_WGS84_F = 1 / 298.257223563
_WGS84_E2 = _WGS84_F * (2 - _WGS84_F)
_EARTH_RADIUS_KM = 6378.137
_MU_EARTH = 398600.4418  # km^3/s^2


def _purge_invalid_cache():
    """ Purges corrupted or incomplete group TLE cache files from disk on startup """
    for fname in os.listdir(CACHE_DIR):
        if not (fname.startswith("group_") and fname.endswith(".tle")):
            continue
        group_key = fname[len("group_"):-len(".tle")].lower()
        min_cnt = EXPECTED_MIN_COUNTS.get(group_key)
        if not min_cnt:
            continue
        fpath = os.path.join(CACHE_DIR, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                tle_line_count = sum(1 for l in f if l.startswith(("1 ", "2 ")))
            if tle_line_count < min_cnt * 2:
                os.remove(fpath)
                print(f"🗑️ Purged incomplete cache: {fname}")
        except OSError:
            pass


_purge_invalid_cache()


def parse_iso_time(iso_str):
    """ Parses an ISO timestamp to an aware UTC datetime. Returns None for live/invalid values. """
    if not iso_str or iso_str in ("null", "None"):
        return None
    try:
        dt = datetime.fromisoformat(str(iso_str).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _http_get_text(url: str, timeout: float):
    res = requests.get(url, headers=HTTP_HEADERS, timeout=timeout)
    text = res.text
    if res.status_code != 200 or text.lstrip().startswith(("<html", "<!DOCTYPE")):
        return None
    return text


def _ecef_to_geodetic(x, y, z):
    """ Vectorized ECEF (km) -> WGS84 geodetic (lon deg, lat deg, alt km). """
    lon = np.arctan2(y, x)
    p = np.hypot(x, y)
    lat = np.arctan2(z, p * (1 - _WGS84_E2))
    for _ in range(5):
        sin_lat = np.sin(lat)
        n = _WGS84_A / np.sqrt(1 - _WGS84_E2 * sin_lat * sin_lat)
        lat = np.arctan2(z + _WGS84_E2 * n * sin_lat, p)
    sin_lat = np.sin(lat)
    cos_lat = np.cos(lat)
    n = _WGS84_A / np.sqrt(1 - _WGS84_E2 * sin_lat * sin_lat)
    with np.errstate(divide="ignore", invalid="ignore"):
        alt = np.where(np.abs(cos_lat) > 1e-9, p / cos_lat - n, np.abs(z) - n * (1 - _WGS84_E2))
    return np.degrees(lon), np.degrees(lat), alt


def _mean_altitude_km(sat: EarthSatellite) -> float:
    n_rad_s = sat.model.no_kozai / 60.0
    if n_rad_s <= 0:
        return float("inf")
    a = (_MU_EARTH / (n_rad_s ** 2)) ** (1.0 / 3.0)
    return a - _EARTH_RADIUS_KM


class SatelliteTracker:
    """ Single satellite detailed tracking engine with persistent disk backing """
    def __init__(self, norad_id: int):
        self.norad_id = norad_id
        self.ts = TS
        self.satellite = self._fetch_tle()

    def _read_backup(self, backup_file):
        try:
            with open(backup_file, "r", encoding="utf-8") as f:
                return self._parse(f.read())
        except (OSError, ValueError):
            return None

    def _parse(self, text: str):
        lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
        idx = next((i for i in range(len(lines) - 1)
                    if lines[i].startswith("1 ") and lines[i + 1].startswith("2 ")), None)
        if idx is None:
            return None
        name = lines[idx - 1] if idx > 0 else f"NORAD-{self.norad_id}"
        return EarthSatellite(lines[idx], lines[idx + 1], name, self.ts)

    def _fetch_tle(self) -> EarthSatellite:
        backup_file = os.path.join(CACHE_DIR, f"norad_{self.norad_id}.tle")
        url = f"https://celestrak.org/NORAD/elements/gp.php?CATNR={self.norad_id}&FORMAT=TLE"

        if os.path.exists(backup_file) and (time.time() - os.path.getmtime(backup_file) < CACHE_EXPIRY_SECONDS):
            sat = self._read_backup(backup_file)
            if sat:
                return sat

        try:
            text = _http_get_text(url, timeout=10)
            sat = self._parse(text) if text else None
            if sat:
                with open(backup_file, "w", encoding="utf-8") as f:
                    f.write(text)
                return sat
        except Exception as e:
            print(f"⚠️ Live fetch failed for NORAD {self.norad_id}: {e}")

        if os.path.exists(backup_file):
            sat = self._read_backup(backup_file)
            if sat:
                return sat

        print(f"🟠 Using built-in fallback TLE for NORAD {self.norad_id}")
        return EarthSatellite(FALLBACK_ISS_TLE[1], FALLBACK_ISS_TLE[2], FALLBACK_ISS_TLE[0], self.ts)

    def _position(self, t):
        sub = wgs84.subpoint(self.satellite.at(t))
        return {
            "timestamp_utc": to_iso(t.utc_datetime()),
            "latitude": round(sub.latitude.degrees, 4),
            "longitude": round(sub.longitude.degrees, 4),
            "altitude_km": round(sub.elevation.km, 2),
        }

    def get_current_telemetry(self, station_lat: float = 41.0082, station_lon: float = 28.9784,
                              station_alt_m: float = 0.0, sim_time_iso: str = None,
                              sim_dt: datetime = None, next_dt: datetime = None) -> dict:
        dt = sim_dt or parse_iso_time(sim_time_iso) or datetime.now(timezone.utc)
        now = self.ts.from_datetime(dt)
        station = wgs84.latlon(station_lat, station_lon, elevation_m=station_alt_m)
        alt, az, distance = (self.satellite - station).at(now).altaz()

        telemetry = {
            "satellite_name": self.satellite.name,
            "norad_id": self.norad_id,
            **self._position(now),
            "azimuth_deg": round(az.degrees, 2),
            "elevation_deg": round(alt.degrees, 2),
            "distance_km": round(distance.km, 2),
        }
        if next_dt is not None:
            telemetry["next"] = self._position(self.ts.from_datetime(next_dt))
        return telemetry


class ConstellationTracker:
    """ Constellation Engine with Full Parallel Fetching across all NORAD Catalogs """
    _memory_cache = {}
    _cache_timestamps = {}
    _lock = threading.Lock()
    _build_locks = {}

    def __init__(self, group_name: str = "starlink"):
        self.ts = TS
        self.group_name = group_name.lower()
        all_sats = self._get_or_refresh_satellites()
        self.satellites = self._filter_regime(all_sats)
        self.names = [sat.name for sat in self.satellites]
        self._satrec_array = SatrecArray([sat.model for sat in self.satellites]) if self.satellites else None

    def _filter_regime(self, sats):
        # Filter once by mean orbital altitude so the satellite index set stays stable over time
        if self.group_name == "leo":
            return [s for s in sats if _mean_altitude_km(s) <= 2000]
        if self.group_name == "meo":
            return [s for s in sats if 2000 < _mean_altitude_km(s) < 35000]
        if self.group_name == "geo":
            return [s for s in sats if _mean_altitude_km(s) >= 35000]
        return sats

    def _parse_tle_lines(self, lines: list) -> list:
        sats = []
        clean_lines = [l.strip() for l in lines if l.strip()]

        i = 0
        while i < len(clean_lines) - 1:
            if clean_lines[i].startswith("1 ") and clean_lines[i + 1].startswith("2 "):
                name = "SATELLITE"
                if i > 0 and not clean_lines[i - 1].startswith(("1 ", "2 ")):
                    name = clean_lines[i - 1]
                    if name.startswith("0 "):
                        name = name[2:].strip()
                try:
                    sats.append(EarthSatellite(clean_lines[i], clean_lines[i + 1], name, self.ts))
                except Exception:
                    pass
                i += 2
            else:
                i += 1
        return sats

    def _read_group_file(self, backup_file):
        try:
            with open(backup_file, "r", encoding="utf-8") as f:
                return self._parse_tle_lines(f.readlines())
        except OSError:
            return []

    def _fetch_single_group(self, group: str) -> list:
        group_key = group.lower()
        min_expected = EXPECTED_MIN_COUNTS.get(group_key, 10)
        backup_file = os.path.join(CACHE_DIR, f"group_{group_key}.tle")

        if os.path.exists(backup_file) and (time.time() - os.path.getmtime(backup_file) < CACHE_EXPIRY_SECONDS):
            disk_sats = self._read_group_file(backup_file)
            if len(disk_sats) >= min_expected:
                return disk_sats

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
        else:
            urls = [f"https://celestrak.org/NORAD/elements/gp.php?GROUP={group_key}&FORMAT=tle"]

        req_timeout = 35 if group_key in ("starlink", "active", "geo", "gnss") else 15
        for url in urls:
            try:
                text = _http_get_text(url, timeout=req_timeout)
                if text and len(text) > 500:
                    sats = self._parse_tle_lines(text.splitlines())
                    if len(sats) >= min_expected:
                        with open(backup_file, "w", encoding="utf-8") as f:
                            f.write(text)
                        print(f"🟢 LIVE TLE UPDATED [{group.upper()}]: {len(sats)} Satellites")
                        return sats
            except Exception as e:
                print(f"⚠️ Network fetch failed for [{group.upper()}] from {url}: {e}")

        if os.path.exists(backup_file):
            disk_sats = self._read_group_file(backup_file)
            if disk_sats:
                print(f"🟡 SERVING PERSISTENT DISK BACKUP FOR [{group.upper()}]: {len(disk_sats)} Satellites")
                return disk_sats

        return []

    @classmethod
    def _cached(cls, cache_key):
        with cls._lock:
            cached = cls._memory_cache.get(cache_key)
            if cached is None:
                return None
            if time.time() - cls._cache_timestamps.get(cache_key, 0) >= CACHE_EXPIRY_SECONDS:
                return None
            if len(cached) < EXPECTED_MIN_COUNTS.get(cache_key, 1):
                return None
            return cached

    def _get_or_refresh_satellites(self) -> list:
        cache_key = "unified_catalog" if self.group_name in REGIME_MODES else self.group_name

        cached = self._cached(cache_key)
        if cached is not None:
            return cached

        with ConstellationTracker._lock:
            build_lock = ConstellationTracker._build_locks.setdefault(cache_key, threading.Lock())

        # Only one connection builds a given catalog; others wait and reuse it
        with build_lock:
            cached = self._cached(cache_key)
            if cached is not None:
                return cached

            if cache_key == "unified_catalog":
                print("🔄 Building full unified orbital catalog across all NORAD channels...")
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
                    unique_dict.setdefault(sat.model.satnum, sat)
                result_sats = list(unique_dict.values())
            else:
                result_sats = self._fetch_single_group(self.group_name)

            if not result_sats and self.group_name != "stations":
                result_sats = self._fetch_single_group("stations")

            if result_sats:
                with ConstellationTracker._lock:
                    ConstellationTracker._memory_cache[cache_key] = result_sats
                    ConstellationTracker._cache_timestamps[cache_key] = time.time()

        print(f"🚀 TOTAL {len(result_sats)} SATELLITES LOADED TO UNIFIED ENGINE! [{self.group_name.upper()}]")
        return result_sats

    def _propagate(self, datetimes):
        """ Vectorized SGP4 for all satellites at the given UTC datetimes.
        Returns lon, lat, alt arrays of shape (n_sats, n_times) and a validity mask. """
        jd = np.empty(len(datetimes))
        fr = np.empty(len(datetimes))
        for k, dt in enumerate(datetimes):
            jd[k], fr[k] = jday(dt.year, dt.month, dt.day, dt.hour, dt.minute,
                                dt.second + dt.microsecond / 1e6)

        err, r, _ = self._satrec_array.sgp4(jd, fr)  # TEME km, shape (n_sats, n_times, 3)
        theta, _ = theta_GMST1982(jd, fr)
        cos_t, sin_t = np.cos(theta), np.sin(theta)
        x = cos_t * r[..., 0] + sin_t * r[..., 1]
        y = -sin_t * r[..., 0] + cos_t * r[..., 1]
        z = r[..., 2]
        lon, lat, alt = _ecef_to_geodetic(x, y, z)
        valid = (err == 0).all(axis=1) & np.isfinite(alt).all(axis=1)
        return lon, lat, alt, valid

    def get_frame(self, t0: datetime, t1: datetime) -> list:
        """ Positions for every satellite at t0 and t1 as [lon0, lat0, alt0, lon1, lat1, alt1];
        invalid (decayed) satellites are returned as null to keep indices aligned with self.names. """
        if not self.satellites:
            return []
        lon, lat, alt, valid = self._propagate([t0, t1])
        data = np.stack([lon[:, 0], lat[:, 0], alt[:, 0], lon[:, 1], lat[:, 1], alt[:, 1]], axis=1)
        data = np.round(data, 4).tolist()
        for i in np.flatnonzero(~valid):
            data[i] = None
        return data

    def get_compact_telemetries(self, sim_time_iso: str = None) -> list:
        """ Legacy format: [[lon, lat, alt_km, name], ...] at a single time """
        if not self.satellites:
            return []
        dt = parse_iso_time(sim_time_iso) or datetime.now(timezone.utc)
        lon, lat, alt, valid = self._propagate([dt])
        return [
            [round(float(lon[i, 0]), 6), round(float(lat[i, 0]), 6), round(float(alt[i, 0]), 2), self.names[i]]
            for i in np.flatnonzero(valid)
        ]


class SimClock:
    """ Server-side mirror of the browser's simulation clock (anchor time + rate). """
    def __init__(self):
        self.anchor = None          # None = live
        self.anchor_mono = time.monotonic()
        self.rate = 1.0
        self.paused = False

    def update(self, data: dict):
        if "rate" in data or "paused" in data:
            # Re-anchor at the current estimate before changing speed
            if self.anchor is not None:
                self.anchor = self.now()
                self.anchor_mono = time.monotonic()
        if "rate" in data:
            try:
                rate = float(data["rate"])
                if np.isfinite(rate):
                    self.rate = rate
            except (TypeError, ValueError):
                pass
        if "paused" in data:
            self.paused = bool(data["paused"])
        if "sim_time" in data:
            self.anchor = parse_iso_time(data["sim_time"])
            self.anchor_mono = time.monotonic()
            if self.anchor is None:
                self.rate = 1.0
                self.paused = False

    @property
    def is_live(self):
        return self.anchor is None

    def now(self) -> datetime:
        if self.anchor is None:
            return datetime.now(timezone.utc)
        if self.paused:
            return self.anchor
        try:
            return self.anchor + timedelta(seconds=self.rate * (time.monotonic() - self.anchor_mono))
        except OverflowError:
            return self.anchor

    def advance(self, dt: datetime, wall_seconds: float) -> datetime:
        if self.anchor is None:
            return dt + timedelta(seconds=wall_seconds)
        if self.paused:
            return dt
        try:
            return dt + timedelta(seconds=self.rate * wall_seconds)
        except OverflowError:
            return dt
