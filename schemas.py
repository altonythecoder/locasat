# schemas.py
from pydantic import BaseModel, Field
from typing import Optional, List

class PassPrediction(BaseModel):
    AOS: str = Field(..., description="Signal Acquisition Time (UTC ISO)")
    MAX_ELEVATION_TIME: Optional[str] = Field(None, description="Maximum Elevation Angle Time (UTC ISO)")
    MAX_ELEVATION_DEG: Optional[float] = Field(None, description="Maximum Elevation Angle (°)")
    MAX_ELEVATION_AZIMUTH_DEG: Optional[float] = Field(None, description="Azimuth at Maximum Elevation (°)")
    LOS: Optional[str] = Field(None, description="Signal Loss Time (UTC ISO)")

class PassPredictionResponse(BaseModel):
    norad_id: int
    satellite_name: str
    ground_station: dict
    passes_count: int
    passes: List[PassPrediction]

class LiveSatelliteTelemetry(BaseModel):
    satellite_name: str
    norad_id: int
    timestamp_utc: str
    latitude: float
    longitude: float
    altitude_km: float
    azimuth_deg: Optional[float] = Field(None, description="Angle relative to ground station")
    elevation_deg: Optional[float] = Field(None, description="Vertical elevation angle relative to ground station")
    distance_km: Optional[float] = Field(None, description="Line-of-sight distance to ground station")
