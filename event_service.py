from __future__ import annotations

import json
from bisect import bisect_left, bisect_right
from datetime import datetime
from math import hypot
from statistics import median

from config import DATA_ROOT, PROJECT_ROOT
from models import ReplayEvent


EVENT_FIXTURE_ROOT = PROJECT_ROOT / "fixtures" / "replay_events"
EVENT_BUILD_VERSION = 2
POSITION_INITIALIZATION_GAP_SECONDS = 0.5
POSITION_PERSISTENCE_SECONDS = 0.75
EVENT_APPROACH_SECONDS = 8.0
EVENT_RETURN_SECONDS = 5.0
LOCATION_INTERPOLATION_GAP_SECONDS = 1.0
OVERTAKE_ANCHOR_LOOKBACK_SECONDS = 12.0
OVERTAKE_ANCHOR_LOOKAHEAD_SECONDS = 1.0
OVERTAKE_ANCHOR_SAMPLE_SECONDS = 0.05
MAXIMUM_OVERTAKE_DISTANCE = 12.0
PIT_EVENT_MARGIN_SECONDS = 2.0
PIT_SHOWCASE_APPROACH_SECONDS = 4.0
PIT_SHOWCASE_EXIT_SECONDS = 4.0
SIDE_DIRECTION_WINDOW_SECONDS = 0.6
SIDE_SAMPLE_OFFSETS = (-0.8, -0.4, 0.0, 0.4, 0.8)
MINIMUM_SIDE_SIGNAL = 5.0


def load_replay_events(session_key: int) -> list[dict]:
    path = EVENT_FIXTURE_ROOT / f"{session_key}.json"
    if not path.exists():
        return []

    payload = json.loads(path.read_text(encoding="utf-8"))
    if int(payload.get("sessionKey", -1)) != session_key:
        raise ValueError(f"Replay event fixture session mismatch: {path}")

    return validate_events(payload.get("events", []))


def build_replay_events(dataset_id: str, manifest: dict) -> list[dict]:
    session_key = int(manifest.get("sessionKey", -1))
    fixtures = load_replay_events(session_key)
    raw_root = DATA_ROOT / dataset_id / "raw"
    session_path = raw_root / "session.json"

    if not session_path.exists():
        return fixtures

    pits = load_raw_file(raw_root / "pit.json")
    session = json.loads(session_path.read_text(encoding="utf-8"))
    replay_start = parse_iso(session["date_start"])
    drivers = driver_labels(manifest.get("drivers", []))
    minimum_time = float(manifest.get("playbackStartT") or 0.0)
    maximum_time = float(
        manifest.get("requestedDurationSeconds") or
        manifest.get("durationSeconds") or
        0.0
    )
    pit_events = detect_pit_stops(
        session_key,
        replay_start,
        pits,
        drivers,
        minimum_time,
        maximum_time,
    )

    positions = load_raw_chunks(raw_root, "position_chunk_*.json")
    if not positions:
        return merge_events(pit_events, fixtures)

    locations = load_raw_chunks(raw_root, "location_chunk_*.json")
    starting_grid = load_raw_file(raw_root / "starting_grid.json")
    expected_drivers = set(drivers)

    if not expected_drivers:
        expected_drivers = {
            int(row["driver_number"])
            for row in positions
            if row.get("driver_number") is not None
        }

    automatic = detect_overtakes(
        session_key,
        replay_start,
        positions,
        locations,
        pits,
        starting_grid,
        expected_drivers,
        drivers,
        minimum_time,
        maximum_time,
        position_coverage_end(raw_root, manifest),
    )
    return merge_events(automatic + pit_events, fixtures)


def detect_pit_stops(
    session_key: int,
    replay_start: datetime,
    pit_rows: list[dict],
    labels: dict[int, str],
    minimum_time: float = 0.0,
    maximum_time: float = 0.0,
) -> list[dict]:
    events = []

    for row in pit_rows:
        if row.get("date") is None or row.get("driver_number") is None:
            continue

        lane_duration = row.get("lane_duration")
        if lane_duration is None:
            lane_duration = row.get("pit_duration")
        if lane_duration is None or float(lane_duration) <= 0.0:
            continue

        driver = int(row["driver_number"])
        lap_number = row.get("lap_number")
        if driver <= 0 or lap_number is None:
            continue

        lane_duration = float(lane_duration)
        pit_end = relative_time(replay_start, row["date"])
        pit_start = pit_end - lane_duration
        if (
            pit_end < minimum_time or
            maximum_time > 0.0 and pit_start > maximum_time
        ):
            continue

        start_time = max(minimum_time, pit_start - PIT_SHOWCASE_APPROACH_SECONDS)
        end_time = pit_end + PIT_SHOWCASE_EXIT_SECONDS
        if maximum_time > 0.0:
            end_time = min(maximum_time, end_time)
        if end_time <= start_time:
            continue

        stop_duration_value = row.get("stop_duration")
        stop_duration = (
            float(stop_duration_value)
            if stop_duration_value is not None and float(stop_duration_value) > 0.0
            else -1.0
        )
        anchor = pit_start + lane_duration * 0.5
        label = labels.get(driver, str(driver))
        timing_source = (
            "OpenF1StopDuration"
            if stop_duration > 0.0
            else "OpenF1PitLane"
        )

        events.append({
            "eventId": f"pit_{session_key}_{driver}_{int(lap_number)}",
            "eventType": "PitStop",
            "anchorTime": round(anchor, 3),
            "startTime": round(max(0.0, start_time), 3),
            "endTime": round(end_time, 3),
            "driverNumbers": [driver],
            "confidence": 0.95 if stop_duration > 0.0 else 0.7,
            "motionProfile": "PitStop",
            "displayTitle": f"{label} PIT STOP",
            "displayDescription": f"Lap {int(lap_number)} pit lane visit.",
            "lapNumber": int(lap_number),
            "pitLaneDuration": round(lane_duration, 3),
            "pitStopDuration": round(stop_duration, 3),
            "timingSource": timing_source,
        })

    return validate_events(events)


def attach_replay_events(manifest: dict) -> dict:
    fixtures = load_replay_events(int(manifest.get("sessionKey", -1)))
    current = validate_events(manifest.get("events", []))
    events = merge_events(current, fixtures)
    if manifest.get("events") == events:
        return manifest

    result = dict(manifest)
    result["events"] = events
    return result


def detect_overtakes(
    session_key: int,
    replay_start: datetime,
    position_rows: list[dict],
    location_rows: list[dict],
    pit_rows: list[dict],
    starting_grid_rows: list[dict],
    expected_drivers: set[int],
    labels: dict[int, str],
    minimum_time: float = 0.0,
    maximum_time: float = 0.0,
    coverage_end: float = 0.0,
) -> list[dict]:
    snapshots = build_position_snapshots(
        replay_start,
        position_rows,
        expected_drivers,
        starting_grid_rows,
    )
    baseline = find_initialization_end(snapshots)
    if baseline < 0:
        return []

    locations = build_location_index(replay_start, location_rows)
    pit_intervals = build_pit_intervals(replay_start, pit_rows)
    candidates = find_persistent_overtakes(
        snapshots,
        baseline,
        coverage_end,
    )
    anchor_search_bounds = build_anchor_search_bounds(candidates)
    events = []

    for index, candidate in enumerate(candidates):
        anchor, overtaker, defender, old_position, new_position = candidate
        if is_pit_affected(anchor, overtaker, defender, pit_intervals):
            continue

        search_start, search_end = anchor_search_bounds[index]
        closest_approach = find_closest_approach(
            anchor,
            overtaker,
            defender,
            locations,
            search_start,
            search_end,
        )
        if (closest_approach is not None and
                closest_approach[1] > MAXIMUM_OVERTAKE_DISTANCE):
            continue

        visual_anchor = (
            closest_approach[0]
            if closest_approach is not None
            else anchor
        )
        event_id = (
            f"auto_{session_key}_overtake_{round(anchor * 1000):09d}_"
            f"{overtaker}_{defender}"
        )
        passing_side, side_source, side_confidence = infer_passing_side(
            event_id,
            visual_anchor,
            overtaker,
            defender,
            locations,
        )
        overtaker_label = labels.get(overtaker, str(overtaker))
        defender_label = labels.get(defender, str(defender))
        position_change = max(1, old_position - new_position)

        start_time = max(minimum_time, visual_anchor - EVENT_APPROACH_SECONDS)
        end_time = visual_anchor + EVENT_RETURN_SECONDS
        if maximum_time > 0.0:
            end_time = min(maximum_time, end_time)

        events.append({
            "eventId": event_id,
            "eventType": "Overtake",
            "anchorTime": round(visual_anchor, 3),
            "startTime": round(max(0.0, start_time), 3),
            "endTime": round(end_time, 3),
            "driverNumbers": [overtaker, defender],
            "progressStart": -1.0,
            "progressEnd": -1.0,
            "confidence": 0.85 if position_change == 1 else 0.7,
            "passingSide": passing_side,
            "sideSource": side_source,
            "sideConfidence": side_confidence,
            "motionProfile": "Auto",
            "overtakerShare": 0.5,
            "defenderShare": 0.5,
            "displayTitle": f"{overtaker_label} passes {defender_label}",
            "displayDescription": (
                f"Position data changes: {overtaker_label} "
                f"P{old_position} to P{new_position}."
            ),
        })

    separate_repeated_pair_windows(events)
    return validate_events(events)


def build_position_snapshots(
    replay_start: datetime,
    rows: list[dict],
    expected_drivers: set[int],
    starting_grid_rows: list[dict] | None = None,
) -> list[tuple[float, dict[int, int]]]:
    updates: dict[float, dict[int, int]] = {}

    for row in rows:
        if row.get("date") is None or row.get("driver_number") is None:
            continue

        driver = int(row["driver_number"])
        if driver not in expected_drivers:
            continue

        time = relative_time(replay_start, row["date"])
        position = int(row["position"])
        by_driver = updates.setdefault(time, {})
        previous = by_driver.get(driver)
        if previous is None or position < previous:
            by_driver[driver] = position

    state = {
        int(row["driver_number"]): int(row["position"])
        for row in starting_grid_rows or []
        if row.get("driver_number") is not None and
        row.get("position") is not None and
        int(row["driver_number"]) in expected_drivers
    }
    snapshots = []
    expected_positions = set(range(1, len(expected_drivers) + 1))

    if set(state) == expected_drivers and set(state.values()) == expected_positions:
        snapshots.append((0.0, dict(state)))

    for time in sorted(updates):
        state.update(updates[time])
        if set(state) != expected_drivers or set(state.values()) != expected_positions:
            continue

        snapshots.append((time, dict(state)))

    return snapshots


def find_initialization_end(
    snapshots: list[tuple[float, dict[int, int]]],
) -> int:
    for index in range(len(snapshots) - 1):
        if snapshots[index + 1][0] - snapshots[index][0] >= POSITION_INITIALIZATION_GAP_SECONDS:
            return index

    return -1


def find_persistent_overtakes(
    snapshots: list[tuple[float, dict[int, int]]],
    baseline: int,
    coverage_end: float = 0.0,
) -> list[tuple[float, int, int, int, int]]:
    result = []
    times = [item[0] for item in snapshots]

    for index in range(baseline + 1, len(snapshots)):
        anchor, after = snapshots[index]
        persistence_time = anchor + POSITION_PERSISTENCE_SECONDS
        if max(times[-1], coverage_end) < persistence_time:
            continue

        persistence_end = bisect_right(
            times,
            persistence_time,
            lo=index,
        ) - 1

        before = snapshots[index - 1][1]
        previous_driver_at_position = {
            position: driver
            for driver, position in before.items()
        }

        for overtaker in sorted(after):
            old_position = before[overtaker]
            new_position = after[overtaker]
            if new_position >= old_position:
                continue

            defender = previous_driver_at_position.get(new_position)
            if defender is None or defender == overtaker:
                continue
            if before[overtaker] <= before[defender] or after[overtaker] >= after[defender]:
                continue
            if not order_persists(
                    snapshots,
                    index,
                    persistence_end,
                    overtaker,
                    defender):
                continue

            result.append((
                anchor,
                overtaker,
                defender,
                old_position,
                new_position,
            ))

    result.sort(key=lambda item: (item[0], item[1], item[2]))
    return remove_repeated_pair_direction(result)


def remove_repeated_pair_direction(
    candidates: list[tuple[float, int, int, int, int]],
) -> list[tuple[float, int, int, int, int]]:
    result = []
    last_direction: dict[tuple[int, int], tuple[int, int]] = {}

    for candidate in candidates:
        overtaker = candidate[1]
        defender = candidate[2]
        pair = tuple(sorted((overtaker, defender)))
        direction = (overtaker, defender)
        if last_direction.get(pair) == direction:
            continue

        last_direction[pair] = direction
        result.append(candidate)

    return result


def build_anchor_search_bounds(
    candidates: list[tuple[float, int, int, int, int]],
) -> list[tuple[float, float]]:
    result = [
        (float("-inf"), float("inf"))
        for _ in candidates
    ]
    by_pair: dict[tuple[int, int], list[int]] = {}

    for index, candidate in enumerate(candidates):
        pair = tuple(sorted((candidate[1], candidate[2])))
        by_pair.setdefault(pair, []).append(index)

    for indices in by_pair.values():
        for offset, candidate_index in enumerate(indices):
            anchor = candidates[candidate_index][0]
            start = float("-inf")
            end = float("inf")
            if offset > 0:
                previous_anchor = candidates[indices[offset - 1]][0]
                start = (previous_anchor + anchor) * 0.5
            if offset + 1 < len(indices):
                next_anchor = candidates[indices[offset + 1]][0]
                end = (anchor + next_anchor) * 0.5

            result[candidate_index] = (start, end)

    return result


def separate_repeated_pair_windows(events: list[dict]) -> None:
    by_pair: dict[tuple[int, int], list[dict]] = {}
    for event in events:
        drivers = event.get("driverNumbers", [])
        if len(drivers) < 2:
            continue

        pair = tuple(sorted((int(drivers[0]), int(drivers[1]))))
        by_pair.setdefault(pair, []).append(event)

    for pair_events in by_pair.values():
        pair_events.sort(key=lambda item: (item["anchorTime"], item["eventId"]))
        for index in range(1, len(pair_events)):
            previous = pair_events[index - 1]
            current = pair_events[index]
            if previous["endTime"] <= current["startTime"]:
                continue

            boundary = round(
                (previous["anchorTime"] + current["anchorTime"]) * 0.5,
                3,
            )
            previous["endTime"] = min(previous["endTime"], boundary)
            current["startTime"] = max(current["startTime"], boundary)


def order_persists(
    snapshots: list[tuple[float, dict[int, int]]],
    start: int,
    end: int,
    overtaker: int,
    defender: int,
) -> bool:
    for index in range(start, end + 1):
        state = snapshots[index][1]
        if state[overtaker] >= state[defender]:
            return False

    return True


def build_location_index(
    replay_start: datetime,
    rows: list[dict],
) -> dict[int, list[tuple[float, float, float]]]:
    by_driver: dict[int, dict[float, tuple[float, float]]] = {}

    for row in rows:
        if row.get("date") is None or row.get("driver_number") is None:
            continue

        driver = int(row["driver_number"])
        time = relative_time(replay_start, row["date"])
        position = (float(row["x"]), float(row["y"]))
        samples = by_driver.setdefault(driver, {})
        previous = samples.get(time)
        if previous is None or position < previous:
            samples[time] = position

    return {
        driver: [
            (time, position[0], position[1])
            for time, position in sorted(samples.items())
        ]
        for driver, samples in by_driver.items()
    }


def build_pit_intervals(
    replay_start: datetime,
    rows: list[dict],
) -> dict[int, list[tuple[float, float]]]:
    result: dict[int, list[tuple[float, float]]] = {}

    for row in rows:
        if row.get("date") is None or row.get("driver_number") is None:
            continue

        lane_duration = row.get("lane_duration")
        if lane_duration is None:
            lane_duration = row.get("pit_duration")
        if lane_duration is None or float(lane_duration) <= 0.0:
            continue

        pit_end = relative_time(replay_start, row["date"])
        pit_start = pit_end - float(lane_duration)
        driver = int(row["driver_number"])
        result.setdefault(driver, []).append((
            pit_start - PIT_EVENT_MARGIN_SECONDS,
            pit_end + PIT_EVENT_MARGIN_SECONDS,
        ))

    for intervals in result.values():
        intervals.sort()

    return result


def is_pit_affected(
    time: float,
    overtaker: int,
    defender: int,
    intervals: dict[int, list[tuple[float, float]]],
) -> bool:
    for driver in (overtaker, defender):
        for start, end in intervals.get(driver, []):
            if start <= time <= end:
                return True

    return False


def find_closest_approach(
    position_anchor: float,
    overtaker: int,
    defender: int,
    locations: dict[int, list[tuple[float, float, float]]],
    minimum_time: float = float("-inf"),
    maximum_time: float = float("inf"),
) -> tuple[float, float] | None:
    overtaker_samples = locations.get(overtaker, [])
    defender_samples = locations.get(defender, [])
    window_start = max(
        0.0,
        position_anchor - OVERTAKE_ANCHOR_LOOKBACK_SECONDS,
        minimum_time,
    )
    window_end = min(
        position_anchor + OVERTAKE_ANCHOR_LOOKAHEAD_SECONDS,
        maximum_time,
    )
    if window_end <= window_start:
        return None
    step_count = int(round(
        (window_end - window_start) / OVERTAKE_ANCHOR_SAMPLE_SECONDS
    ))
    best_time = position_anchor
    best_distance = float("inf")

    for index in range(step_count + 1):
        time = window_start + index * OVERTAKE_ANCHOR_SAMPLE_SECONDS
        overtaker_position = interpolate_location(overtaker_samples, time)
        defender_position = interpolate_location(defender_samples, time)
        if overtaker_position is None or defender_position is None:
            continue

        distance = hypot(
            overtaker_position[0] - defender_position[0],
            overtaker_position[1] - defender_position[1],
        )
        if distance < best_distance:
            best_distance = distance
            best_time = time

    if best_distance == float("inf"):
        return None

    return round(best_time, 3), best_distance


def infer_passing_side(
    event_id: str,
    anchor: float,
    overtaker: int,
    defender: int,
    locations: dict[int, list[tuple[float, float, float]]],
) -> tuple[str, str, float]:
    overtaker_samples = locations.get(overtaker, [])
    defender_samples = locations.get(defender, [])
    signals = []

    for offset in SIDE_SAMPLE_OFFSETS:
        signal = lateral_signal(
            overtaker_samples,
            defender_samples,
            anchor + offset,
        )
        if signal is not None:
            signals.append(signal)

    if signals:
        signal = median(signals)
        matching = sum(
            1
            for value in signals
            if value == 0.0 or value * signal > 0.0
        )
        consistency = matching / len(signals)
        if abs(signal) >= MINIMUM_SIDE_SIGNAL and consistency >= 0.6:
            return (
                "Right" if signal > 0.0 else "Left",
                "Trajectory",
                round(min(1.0, abs(signal) / (MINIMUM_SIDE_SIGNAL * 4.0)), 3),
            )

    return deterministic_side(event_id), "DeterministicFallback", 0.0


def lateral_signal(
    overtaker: list[tuple[float, float, float]],
    defender: list[tuple[float, float, float]],
    time: float,
) -> float | None:
    before_time = time - SIDE_DIRECTION_WINDOW_SECONDS
    after_time = time + SIDE_DIRECTION_WINDOW_SECONDS
    overtaker_before = interpolate_location(overtaker, before_time)
    overtaker_after = interpolate_location(overtaker, after_time)
    defender_before = interpolate_location(defender, before_time)
    defender_after = interpolate_location(defender, after_time)
    overtaker_position = interpolate_location(overtaker, time)
    defender_position = interpolate_location(defender, time)

    values = (
        overtaker_before,
        overtaker_after,
        defender_before,
        defender_after,
        overtaker_position,
        defender_position,
    )
    if any(value is None for value in values):
        return None

    forward_x = (
        overtaker_after[0] - overtaker_before[0] +
        defender_after[0] - defender_before[0]
    )
    forward_y = (
        overtaker_after[1] - overtaker_before[1] +
        defender_after[1] - defender_before[1]
    )
    magnitude = hypot(forward_x, forward_y)
    if magnitude <= 0.001:
        return None

    right_x = forward_y / magnitude
    right_y = -forward_x / magnitude
    relative_x = overtaker_position[0] - defender_position[0]
    relative_y = overtaker_position[1] - defender_position[1]
    return relative_x * right_x + relative_y * right_y


def interpolate_location(
    samples: list[tuple[float, float, float]],
    time: float,
) -> tuple[float, float] | None:
    if len(samples) < 2:
        return None

    index = bisect_left(samples, (time, float("-inf"), float("-inf")))
    if index <= 0 or index >= len(samples):
        return None

    before = samples[index - 1]
    after = samples[index]
    duration = after[0] - before[0]
    if duration <= 0.0 or duration > LOCATION_INTERPOLATION_GAP_SECONDS:
        return None

    interpolation = max(0.0, min(1.0, (time - before[0]) / duration))
    return (
        before[1] + (after[1] - before[1]) * interpolation,
        before[2] + (after[2] - before[2]) * interpolation,
    )


def deterministic_side(event_id: str) -> str:
    value = 2166136261
    for character in event_id:
        value ^= ord(character)
        value = value * 16777619 & 0xFFFFFFFF

    return "Left" if value & 1 == 0 else "Right"


def merge_events(automatic: list[dict], fixtures: list[dict]) -> list[dict]:
    automatic = validate_events(automatic)
    fixtures = validate_events(fixtures)

    for fixture in fixtures:
        automatic = [
            item
            for item in automatic
            if not events_describe_same_pass(item, fixture)
        ]

    by_identity = {
        event_identity(item): item
        for item in automatic
    }
    for fixture in fixtures:
        by_identity[event_identity(fixture)] = fixture

    result = list(by_identity.values())
    result.sort(key=lambda item: (
        item["anchorTime"],
        item["eventId"],
    ))
    return result


def events_describe_same_pass(first: dict, second: dict) -> bool:
    if str(first.get("eventType", "")).lower() != str(
            second.get("eventType", "")).lower():
        return False

    first_drivers = tuple(int(item) for item in first.get("driverNumbers", [])[:2])
    second_drivers = tuple(int(item) for item in second.get("driverNumbers", [])[:2])
    if first_drivers != second_drivers:
        return False

    return abs(
        float(first.get("anchorTime", 0.0)) -
        float(second.get("anchorTime", 0.0))
    ) <= OVERTAKE_ANCHOR_LOOKBACK_SECONDS


def event_identity(event: dict) -> tuple:
    drivers = tuple(sorted(int(item) for item in event.get("driverNumbers", [])[:2]))
    return (
        str(event.get("eventType", "")).lower(),
        round(float(event.get("anchorTime", 0.0)), 3),
        drivers,
    )


def validate_events(events: list[dict]) -> list[dict]:
    return [
        ReplayEvent.model_validate(item).model_dump()
        for item in events
    ]


def load_raw_chunks(raw_root, pattern: str) -> list[dict]:
    rows = []
    for path in sorted(raw_root.glob(pattern)):
        rows.extend(json.loads(path.read_text(encoding="utf-8")))
    return rows


def load_raw_file(path) -> list[dict]:
    if not path.exists():
        return []

    return json.loads(path.read_text(encoding="utf-8"))


def position_coverage_end(raw_root, manifest: dict) -> float:
    result = 0.0
    for chunk in manifest.get("chunks", []):
        index = int(chunk.get("index", -1))
        if index < 0:
            continue
        if not (raw_root / f"position_chunk_{index:04d}.json").exists():
            continue

        result = max(result, float(chunk.get("endT") or 0.0))

    return result


def driver_labels(drivers: list[dict]) -> dict[int, str]:
    result = {}
    for driver in drivers:
        number = int(driver.get("driverNumber", 0))
        if number <= 0:
            continue

        label = str(driver.get("nameAcronym") or number)
        result[number] = label
    return result


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def relative_time(replay_start: datetime, value: str) -> float:
    return round((parse_iso(value) - replay_start).total_seconds(), 3)
