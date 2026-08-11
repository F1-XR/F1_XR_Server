from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import catalog_service
import chunk_service
import main
from models import CreateDatasetRequest


def request() -> CreateDatasetRequest:
    return CreateDatasetRequest(
        sessionKey=9472,
        chunkMinutes=2,
        overlapSeconds=2,
        requestedMinutes=10,
        skipWarmupLap=True,
    )


def complete_manifest() -> dict:
    return {
        "datasetId": "cached",
        "status": "complete",
        "sessionKey": 9472,
        "chunkMinutes": 2,
        "overlapSeconds": 2,
        "durationSeconds": 7200.0,
        "requestedDurationSeconds": 822.341,
        "playbackStartT": 222.341,
        "readyUntilT": 822.341,
        "chunks": [
            {
                "index": 0,
                "status": "ready",
                "sampleCount": 10,
            }
        ],
    }


class DatasetCacheTests(unittest.TestCase):
    def test_complete_matching_cache_refreshes_stale_events_without_openf1(
        self,
    ) -> None:
        cached = complete_manifest()
        refreshed_events = [{
            "eventId": "pit_9472_63_12",
            "eventType": "PitStop",
        }]

        with (
            patch.object(
                chunk_service,
                "find_ready_cached_manifest",
                return_value=cached,
            ),
            patch.object(
                chunk_service,
                "create_dataset_from_openf1",
            ) as create_from_openf1,
            patch.object(
                chunk_service,
                "build_replay_events",
                return_value=refreshed_events,
            ) as build_events,
            patch.object(
                chunk_service,
                "save_manifest",
            ) as save_manifest,
        ):
            result = chunk_service.create_dataset(request())

        self.assertIs(result, cached)
        self.assertEqual(result["events"], refreshed_events)
        self.assertEqual(
            result["eventBuildVersion"],
            chunk_service.EVENT_BUILD_VERSION,
        )
        build_events.assert_called_once_with("cached", cached)
        save_manifest.assert_called_once_with("cached", cached)
        create_from_openf1.assert_not_called()

    def test_current_event_cache_returns_without_rebuilding(self) -> None:
        cached = complete_manifest()
        cached["eventBuildVersion"] = chunk_service.EVENT_BUILD_VERSION

        with (
            patch.object(
                chunk_service,
                "find_ready_cached_manifest",
                return_value=cached,
            ),
            patch.object(
                chunk_service,
                "build_replay_events",
            ) as build_events,
            patch.object(
                chunk_service,
                "save_manifest",
            ) as save_manifest,
            patch.object(
                chunk_service,
                "create_dataset_from_openf1",
            ) as create_from_openf1,
        ):
            result = chunk_service.create_dataset(request())

        self.assertIs(result, cached)
        build_events.assert_not_called()
        save_manifest.assert_not_called()
        create_from_openf1.assert_not_called()

    def test_event_refresh_failure_keeps_ready_cache(self) -> None:
        cached = complete_manifest()

        with (
            patch.object(
                chunk_service,
                "find_ready_cached_manifest",
                return_value=cached,
            ),
            patch.object(
                chunk_service,
                "build_replay_events",
                side_effect=ValueError("invalid raw event data"),
            ),
            patch.object(
                chunk_service,
                "save_manifest",
            ) as save_manifest,
            patch.object(
                chunk_service,
                "create_dataset_from_openf1",
            ) as create_from_openf1,
        ):
            result = chunk_service.create_dataset(request())

        self.assertIs(result, cached)
        self.assertNotIn("eventBuildVersion", result)
        save_manifest.assert_not_called()
        create_from_openf1.assert_not_called()

    def test_ready_cache_requires_matching_complete_files(self) -> None:
        cached = complete_manifest()

        with tempfile.TemporaryDirectory() as temporary:
            dataset_path = Path(temporary) / "cached"
            chunks_path = dataset_path / "chunks"
            chunks_path.mkdir(parents=True)
            (chunks_path / "chunk_0000.json").write_text("{}", encoding="utf-8")

            with (
                patch.object(chunk_service, "DATA_ROOT", Path(temporary)),
                patch.object(
                    chunk_service,
                    "load_manifest",
                    return_value=cached,
                ),
                patch.object(
                    chunk_service,
                    "chunk_exists",
                    side_effect=lambda dataset_id, index: (
                        Path(temporary)
                        / dataset_id
                        / "chunks"
                        / f"chunk_{index:04d}.json"
                    ).exists(),
                ),
            ):
                result = chunk_service.find_ready_cached_manifest(request())

        self.assertIs(result, cached)

    def test_complete_cache_does_not_schedule_download(self) -> None:
        background_tasks = Mock()

        with patch.object(
            main,
            "create_dataset",
            return_value=complete_manifest(),
        ):
            main.datasets_create(request(), background_tasks)

        background_tasks.add_task.assert_not_called()


class CatalogCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        catalog_service._session_cache.clear()

    def tearDown(self) -> None:
        catalog_service._session_cache.clear()

    def test_tracks_and_sessions_share_recent_year_response(self) -> None:
        sessions = [
            {
                "session_key": 9472,
                "meeting_key": 1229,
                "circuit_key": 63,
                "circuit_short_name": "Sakhir",
                "location": "Sakhir",
                "country_name": "Bahrain",
                "meeting_name": "Bahrain Grand Prix",
                "session_name": "Race",
                "session_type": "Race",
                "date_start": "2024-03-02T15:00:00+00:00",
                "date_end": "2024-03-02T17:00:00+00:00",
                "year": 2024,
            }
        ]

        with (
            patch.object(
                catalog_service,
                "fetch_sessions",
                return_value=sessions,
            ) as fetch,
            patch.object(
                catalog_service,
                "monotonic",
                side_effect=(100.0, 101.0),
            ),
        ):
            catalog_service.get_tracks(2024)
            catalog_service.get_sessions(2024, 63)

        fetch.assert_called_once_with(2024)


if __name__ == "__main__":
    unittest.main()
