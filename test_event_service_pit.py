from datetime import datetime, timezone
import unittest

from event_service import detect_pit_stops, merge_events
from models import ReplayEvent


class PitStopEventTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2024, 1, 1, tzinfo=timezone.utc)

    def test_builds_stable_event_with_authoritative_stop_duration(self):
        rows = [{
            "date": "2024-01-01T00:10:30+00:00",
            "driver_number": 63,
            "lap_number": 12,
            "lane_duration": 24.0,
            "stop_duration": 2.4,
        }]

        first = detect_pit_stops(
            9472, self.start, rows, {63: "RUS"}, 0.0, 900.0
        )
        second = detect_pit_stops(
            9472, self.start, rows, {63: "RUS"}, 0.0, 900.0
        )

        self.assertEqual(first, second)
        self.assertEqual(first[0]["eventId"], "pit_9472_63_12")
        self.assertEqual(first[0]["anchorTime"], 618.0)
        self.assertEqual(first[0]["pitStopDuration"], 2.4)
        self.assertEqual(first[0]["timingSource"], "OpenF1StopDuration")

    def test_omits_events_outside_requested_range(self):
        rows = [{
            "date": "2024-01-01T00:20:30+00:00",
            "driver_number": 16,
            "lap_number": 20,
            "lane_duration": 22.0,
        }]

        events = detect_pit_stops(9472, self.start, rows, {}, 0.0, 600.0)

        self.assertEqual(events, [])

    def test_missing_stop_duration_remains_unknown(self):
        rows = [{
            "date": "2024-01-01T00:05:00+00:00",
            "driver_number": 1,
            "lap_number": 5,
            "pit_duration": 20.0,
        }]

        event = detect_pit_stops(1, self.start, rows, {}, 0.0, 600.0)[0]

        self.assertEqual(event["pitStopDuration"], -1.0)
        self.assertEqual(event["timingSource"], "OpenF1PitLane")

    def test_legacy_event_payload_keeps_compatible_defaults(self):
        event = ReplayEvent.model_validate({
            "eventId": "legacy",
            "eventType": "Overtake",
            "anchorTime": 10.0,
            "startTime": 5.0,
            "endTime": 15.0,
            "driverNumbers": [1, 2],
        })

        self.assertIsNone(event.lapNumber)
        self.assertEqual(event.pitLaneDuration, -1.0)
        self.assertEqual(event.pitStopDuration, -1.0)
        self.assertIsNone(event.timingSource)

    def test_pit_event_does_not_hide_overtake_fixture(self):
        pit = detect_pit_stops(9472, self.start, [{
            "date": "2024-01-01T00:05:00+00:00",
            "driver_number": 63,
            "lap_number": 5,
            "lane_duration": 20.0,
        }], {}, 0.0, 600.0)
        fixture = [{
            "eventId": "fixture_overtake",
            "eventType": "Overtake",
            "anchorTime": 290.0,
            "startTime": 285.0,
            "endTime": 295.0,
            "driverNumbers": [63, 16],
        }]

        merged = merge_events(pit, fixture)

        self.assertEqual(len(merged), 2)
        self.assertEqual(
            {event["eventType"] for event in merged},
            {"PitStop", "Overtake"},
        )


if __name__ == "__main__":
    unittest.main()
