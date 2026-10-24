import copy
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

import cleanup

NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)
PATH = "/data/tv/Show/double.mkv"


def watched(number=1, **overrides):
    return {
        "file": PATH,
        "season": 1,
        "episode": number,
        "playcount": 1,
        "lastplayed": "2026-05-01 12:00:00",
        **overrides,
    }


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "dry_run": True,
            "retention_days": 3,
            "kodi_timezone": "Europe/Copenhagen",
            "path_mappings": [],
            "kodi": {"url": "http://kodi/jsonrpc"},
            "sonarr": {"url": "http://sonarr", "api_key": "test"},
            "radarr": {"url": "http://radarr", "api_key": "test"},
        }
        self.sessions = {name: object() for name in ("kodi", "sonarr", "radarr")}
        self.assigned = [
            {"id": n, "seasonNumber": 1, "episodeNumber": n, "episodeFileId": 10}
            for n in (1, 2)
        ]

    def test_normalization(self):
        for path in (
            "smb://server/a",
            "nfs://host/a",
            "plugin://x",
            "relative/a",
            "/",
            "\\\\server\\share",
            "file://host/a",
            "file:///a%00b",
        ):
            with self.subTest(path=path):
                self.assertIsNone(cleanup.normalized_path(path))
        self.assertEqual(cleanup.normalized_path("C:\\media\\a.mkv"), "C:/media/a.mkv")
        self.assertEqual(
            cleanup.normalized_path("file:///data/a%20b.mkv"), "/data/a b.mkv"
        )

    def test_mapping_boundaries_longest_and_conflicts(self):
        mappings = [{"kodi": "/mnt", "arr": "/data"}, {"kodi": "/mnt/tv", "arr": "/tv"}]
        self.assertEqual(cleanup.map_path("/mnt/tv/a", mappings), "/tv/a")
        self.assertEqual(cleanup.map_path("/mnt-other/a", mappings), "/mnt-other/a")
        with self.assertRaises(ValueError):
            cleanup.map_path("/mnt/a", mappings + [{"kodi": "/mnt", "arr": "/other"}])

    def test_retention(self):
        cutoff = NOW - timedelta(days=3)
        zone = ZoneInfo("Europe/Copenhagen")
        for overrides in (
            {"playcount": 0},
            {"lastplayed": "bad"},
            {"lastplayed": "2026-06-01"},
            {"playcount": "bad"},
        ):
            self.assertFalse(cleanup.eligible(watched(**overrides), cutoff, zone))
        self.assertTrue(
            cleanup.eligible(watched(lastplayed=cutoff.isoformat()), cutoff, zone)
        )
        self.assertEqual(cleanup.parse_lastplayed("2026-05-01 12:00:00", zone).hour, 10)

    def test_config_rejects_unsafe_values(self):
        for field, value in (
            ("dry_run", "false"),
            ("retention_days", -1),
            ("retention_days", float("nan")),
            ("retention_days", True),
        ):
            config = copy.deepcopy(self.config)
            config[field] = value
            with self.assertRaises((TypeError, ValueError)):
                cleanup.validate_config(config)

    def test_both_live_gates_required(self):
        plan = [{"service": "radarr", "id": 1, "path": "/movie.mkv"}]
        for dry_run, execute in ((True, False), (True, True), (False, False)):
            with patch.object(cleanup, "api") as api:
                cleanup.apply_plan(
                    {**self.config, "dry_run": dry_run}, self.sessions, plan, execute
                )
                api.assert_not_called()
        with patch.object(cleanup, "api") as api:
            cleanup.apply_plan(
                {**self.config, "dry_run": False}, self.sessions, plan, True
            )
            self.assertEqual(api.call_args.args[1], "DELETE")
            self.assertEqual(
                api.call_args.kwargs["params"],
                {"deleteFiles": "true", "addImportExclusion": "true"},
            )

    def test_unmonitor_before_delete_and_stop_on_error(self):
        plan = [{"service": "sonarr", "id": 10, "path": PATH, "episode_ids": [1, 2]}]
        config = {**self.config, "dry_run": False}
        with patch.object(cleanup, "api") as api:
            cleanup.apply_plan(config, self.sessions, plan, True)
            self.assertEqual(
                [call.args[1] for call in api.call_args_list], ["PUT", "DELETE"]
            )
            self.assertEqual(
                api.call_args_list[0].kwargs["payload"],
                {"episodeIds": [1, 2], "monitored": False},
            )
        with patch.object(cleanup, "api", side_effect=RuntimeError("failed")) as api:
            with self.assertRaises(RuntimeError):
                cleanup.apply_plan(config, self.sessions, plan, True)
            self.assertEqual(api.call_count, 1)

    def plan(self, records, assigned=None, extra_files=False, movies=None):
        def fake_api(session, method, url, **kwargs):
            if url.endswith("/movie"):
                return movies or []
            if url.endswith("/moviefile"):
                files = [{"id": 90, "path": "/data/movie.mkv"}]
                return files + (
                    [{"id": 91, "path": "/data/other.mkv"}] if extra_files else []
                )
            if url.endswith("/series"):
                return [{"id": 3}]
            if url.endswith("/episodefile"):
                return [{"id": 10, "path": PATH}]
            if url.endswith("/episode"):
                return self.assigned if assigned is None else assigned
            raise AssertionError(url)

        kodi_movies = [watched(file="/data/movie.mkv")]
        with (
            patch.object(cleanup, "kodi_items", side_effect=[kodi_movies, records]),
            patch.object(cleanup, "api", side_effect=fake_api),
        ):
            return cleanup.build_plan(self.config, self.sessions, NOW)

    def test_multi_episode_requires_exact_coverage(self):
        self.assertEqual(self.plan([watched()]), [])
        self.assertEqual(self.plan([watched(), watched(3)]), [])
        self.assertEqual(self.plan([watched(), watched(2, playcount=0)]), [])
        self.assertEqual(self.plan([watched(), watched(2)], assigned=[]), [])
        plan = self.plan([watched(), watched(2)])
        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0]["episode_ids"], [1, 2])

    def test_duplicate_recent_record_blocks_deletion(self):
        self.assertEqual(
            self.plan([watched(), watched(2), watched(lastplayed="2026-06-01")]), []
        )

    def test_movie_requires_single_file(self):
        movies = [{"id": 7, "movieFile": {"path": "/data/movie.mkv"}}]
        self.assertEqual(self.plan([], movies=movies)[0]["id"], 7)
        self.assertEqual(self.plan([], movies=movies, extra_files=True), [])
        self.assertEqual(self.plan([], movies=movies + movies), [])

    def test_pagination_stall_fails(self):
        response = {"result": {"movies": [], "limits": {"end": 0, "total": 3}}}
        with (
            patch.object(cleanup, "api", return_value=response),
            self.assertRaises(RuntimeError),
        ):
            cleanup.kodi_items(
                None, "http://kodi/jsonrpc", "VideoLibrary.GetMovies", "movies"
            )

    def test_pagination_and_properties(self):
        pages = [
            {"result": {"episodes": [watched()], "limits": {"end": 1, "total": 2}}},
            {"result": {"episodes": [watched(2)], "limits": {"end": 2, "total": 2}}},
        ]
        with patch.object(cleanup, "api", side_effect=pages) as api:
            records = cleanup.kodi_items(
                None, "http://kodi/jsonrpc", "VideoLibrary.GetEpisodes", "episodes"
            )
            self.assertEqual(len(records), 2)
            params = api.call_args.kwargs["payload"]["params"]
            self.assertEqual(params["limits"]["start"], 1)
            self.assertIn("episode", params["properties"])


if __name__ == "__main__":
    unittest.main()
