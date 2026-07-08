from __future__ import annotations

from pydantic import BaseModel, Field


class TrackOption(BaseModel):
    circuitKey: int
    circuitShortName: str
    location: str
    countryName: str
    meetingName: str


class SessionOption(BaseModel):
    sessionKey: int
    meetingKey: int
    circuitKey: int
    circuitShortName: str
    location: str
    countryName: str
    meetingName: str
    sessionName: str
    sessionType: str
    dateStart: str
    dateEnd: str | None = None
    year: int

class CreateDatasetRequest(BaseModel):
    sessionKey: int
    chunkMinutes: int = Field(default=2, ge=1, le=30)
    overlapSeconds: int = Field(default=2, ge=0, le=30)
    initialChunks: int = Field(default=1, ge=1, le=10)
    prefetchChunks: int = Field(default=2, ge=0, le=10)
    requestedMinutes: int = Field(default=6, ge=1, le=240)
    skipWarmupLap: bool = True


class ChunkInfo(BaseModel):
    index: int
    startT: float
    endT: float
    status: str
    sampleCount: int = 0
    error: str | None = None


class DriverInfo(BaseModel):
    driverNumber: int
    nameAcronym: str
    fullName: str
    teamName: str
    teamColour: str | None = None


class DatasetManifest(BaseModel):
    datasetId: str
    status: str = "pending"
    error: str | None = None
    year: int
    circuit: str
    sessionKey: int
    meetingKey: int
    sessionName: str
    drivers: list[DriverInfo] = []
    chunkMinutes: int
    overlapSeconds: int
    durationSeconds: float
    requestedDurationSeconds: float
    readyUntilT: float
    playbackStartChunkIndex: int = 0
    playbackStartT: float = 0.0
    chunks: list[ChunkInfo]


class LocationSample(BaseModel):
    t: float
    driverNumber: int
    x: float
    y: float
    z: float
    rpm: float = 0.0
    throttle: float = 0.0
    speed: float = 0.0
    nGear: int = 0
    n_gear: int = 0
    brake: int = 0
    drs: int = 0


class PositionSample(BaseModel):
    t: float
    driverNumber: int
    position: int


class TireSample(BaseModel):
    t: float
    driverNumber: int
    compound: str
    tireAge: int | None = None


class ReplayChunk(BaseModel):
    datasetId: str
    chunkIndex: int
    startT: float
    endT: float
    overlapSeconds: int
    samples: list[LocationSample]
    positions: list[PositionSample] = []
    tires: list[TireSample] = []
