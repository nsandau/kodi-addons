import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import cleanup
from resources.lib import runtime
from scripts.build_release import ROOT


class VerboseLogTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 6, 1, tzinfo=timezone.utc)
        self.cutoff = self.now - timedelta(days=3)
        self.zone = ZoneInfo("Europe/Copenhagen")
        self.records = [
            {
                "episodeid": 1,
                "file": "smb://nas/tv/Show/a.mkv",
                "playcount": 2,
                "lastplayed": "2026-05-01 12:00:00",
            },
            {
                "episodeid": 2,
                "file": "smb://nas/tv/Show/b.mkv",
                "playcount": 1,
                "lastplayed": "2026-05-31T12:00:00Z",
            },
            {
                "episodeid": 3,
                "file": "smb://nas/tv/Show/c.mkv",
                "playcount": 0,
                "lastplayed": "",
            },
            {
                "episodeid": 4,
                "file": "smb://other/tv/d.mkv",
                "playcount": 1,
                "lastplayed": "bad",
            },
            {
                "episodeid": 5,
                "file": "plugin://video/item?token=secret",
                "playcount": 1,
                "lastplayed": None,
            },
        ]
        self.mappings = [{"kodi": "smb://nas/tv", "arr": "/data/tv"}]

    def index(self, records=None, verbose=True):
        return cleanup.indexed_kodi(
            self.records if records is None else records,
            self.mappings,
            verbose=verbose,
            kind="episode",
            cutoff=self.cutoff,
            zone=self.zone,
        )

    def test_every_record_logged_once_including_unwatched_and_unmapped(self):
        with self.assertLogs(cleanup.LOG, level="INFO") as captured:
            index = self.index()
        self.assertEqual(len(captured.output), len(self.records))
        for record, line in zip(self.records, captured.output, strict=True):
            self.assertIn("WATCH episode id=" + str(record["episodeid"]), line)
        text = "\n".join(captured.output)
        for reason in (
            "eligible",
            "retention_not_elapsed",
            "unwatched",
            "missing_or_invalid_lastplayed",
        ):
            self.assertIn("retention_status=" + reason, text)
        self.assertIn("lastplayed='2026-05-01 12:00:00'", captured.output[0])
        self.assertIn("lastplayed_utc=2026-05-01T10:00:00+00:00", captured.output[0])
        self.assertIn("mapped='/data/tv/Show/a.mkv'", captured.output[0])
        self.assertIn("path_status=unmapped_or_unsupported", captured.output[3])
        self.assertIn("lastplayed_utc=unknown", captured.output[4])
        self.assertNotIn("secret", text)
        self.assertEqual(len(index), 3)

    def test_toggle_off_suppresses_watch_details_and_preserves_index(self):
        with self.assertNoLogs(cleanup.LOG):
            normal = self.index(verbose=False)
        with self.assertLogs(cleanup.LOG):
            verbose = self.index()
        self.assertEqual(normal, verbose)

    def test_duplicate_file_records_keep_distinct_watch_times(self):
        first = self.records[0]
        second = {**first, "episodeid": 9, "lastplayed": "2026-05-31T12:00:00Z"}
        with self.assertLogs(cleanup.LOG) as captured:
            index = self.index([first, second])
        self.assertEqual(len(captured.output), 2)
        self.assertEqual(len(index["/data/tv/Show/a.mkv"]), 2)
        self.assertIn("retention_status=eligible", captured.output[0])
        self.assertIn("retention_status=retention_not_elapsed", captured.output[1])

    def test_credential_urls_redacted_and_control_characters_escaped(self):
        records = [
            {**self.records[0], "file": "smb://user:password@nas/tv/a.mkv"},
            {**self.records[0], "file": "/tv/line\nbreak.mkv"},
        ]
        with self.assertLogs(cleanup.LOG) as captured:
            self.index(records)
        self.assertNotIn("password", "\n".join(captured.output))
        self.assertNotIn("user:", "\n".join(captured.output))
        self.assertNotIn("\n", captured.output[1])
        self.assertIn("\\n", captured.output[1])

    def test_movie_id_and_eligibility_share_same_owner(self):
        record = {
            "movieid": 7,
            "file": "/movies/a.mkv",
            "playcount": "bad",
            "lastplayed": "2026-05-01",
        }
        with self.assertLogs(cleanup.LOG) as captured:
            cleanup.indexed_kodi(
                [record],
                [],
                verbose=True,
                kind="movie",
                cutoff=self.cutoff,
                zone=self.zone,
            )
        self.assertIn("WATCH movie id=7", captured.output[0])
        self.assertIn("retention_status=invalid_playcount", captured.output[0])
        self.assertFalse(cleanup.eligible(record, self.cutoff, self.zone))

    def test_build_plan_logs_history_before_arr_queries_and_does_not_change_plan(self):
        config = {
            "verbose_logs": True,
            "kodi_timezone": "Europe/Copenhagen",
            "kodi": {"url": "internal"},
            "sonarr": {"url": "http://sonarr"},
            "radarr": {"url": "http://radarr"},
        }
        movie = {
            "movieid": 7,
            "file": "/movies/a.mkv",
            "playcount": 1,
            "lastplayed": "2026-05-01",
        }
        with (
            patch.object(cleanup, "kodi_items", side_effect=[[movie], []]),
            patch.object(cleanup, "api", return_value=[]),
            self.assertLogs(cleanup.LOG) as captured,
        ):
            plan = cleanup.build_plan(
                config, {"kodi": None, "sonarr": None, "radarr": None}, self.now
            )
        self.assertEqual(plan, [])
        self.assertIn("cutoff_utc=2026-05-29T00:00:00+00:00", captured.output[0])
        self.assertEqual(sum("WATCH movie" in line for line in captured.output), 1)
        self.assertTrue(any("SKIP movie" in line for line in captured.output))

    def test_kodi_setting_is_off_by_default_and_validated(self):
        addon = MagicMock()
        values = {
            "sonarr_url": "http://sonarr",
            "sonarr_api_key": "key",
            "radarr_url": "http://radarr",
            "radarr_api_key": "key",
        }
        addon.getSetting.side_effect = lambda key: values.get(key, "")
        config, _, _ = runtime.read_settings(addon)
        self.assertFalse(config["verbose_logs"])
        values["verbose_logs"] = "true"
        config, _, _ = runtime.read_settings(addon)
        self.assertTrue(config["verbose_logs"])
        with self.assertRaises(TypeError):
            cleanup.validate_config({**config, "verbose_logs": "false"})
        settings = ElementTree.parse(ROOT / "resources/settings.xml")
        self.assertEqual(
            settings.findtext(".//setting[@id='verbose_logs']/default"), "false"
        )


if __name__ == "__main__":
    unittest.main()
