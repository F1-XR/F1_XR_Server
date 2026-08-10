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


class RaceControlEvent(BaseModel):
    startT: float
    endT: float
    t: float
    date: str
    category: str
    flag: str
    scope: str
    sector: int = 0
    message: str


class ReplayEvent(BaseModel):
    eventId: str
    eventType: str
    anchorTime: float
    startTime: float
    endTime: float
    driverNumbers: list[int]
    progressStart: float = -1.0
    progressEnd: float = -1.0
    confidence: float = -1.0
    passingSide: str | None = None
    sideSource: str | None = None
    sideConfidence: float = -1.0
    motionProfile: str | None = None
    overtakerShare: float = -1.0
    defenderShare: float = -1.0
    displayTitle: str = ""
    displayDescription: str = ""
    lapNumber: int | None = None
    pitLaneDuration: float = -1.0
    pitStopDuration: float = -1.0
    timingSource: str | None = None


class DatasetManifest(BaseModel):
    datasetId: str
    status: str = "pending"
    error: str | None = None
    year: int
    circuit: str
    sessionKey: int
    meetingKey: int
    sessionName: str
    baseDate: str = ""   # t=0의 절대시각(ISO). 상대초 t = (절대시각 - baseDate). Unity 시각 변환용.
    drivers: list[DriverInfo] = []
    events: list[ReplayEvent] = Field(default_factory=list)
    chunkMinutes: int
    overlapSeconds: int
    durationSeconds: float
    requestedDurationSeconds: float
    readyUntilT: float
    playbackStartChunkIndex: int = 0
    playbackStartT: float = 0.0
    raceStartT: float = 0.0
    raceEndT: float = 0.0
    yellowFlags: list[RaceControlEvent] = Field(default_factory=list)
    redFlags: list[RaceControlEvent] = Field(default_factory=list)
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
