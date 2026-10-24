import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from xml.etree import ElementTree
from zipfile import ZipFile

import cleanup
from resources.lib import runtime
from resources.lib.transport import KodiSession, NoRedirect, Session
from scripts.build_release import ROOT, build


class AddonTests(unittest.TestCase):
    def setUp(self):
        self.values = {
            "enabled": "true",
            "dry_run": "true",
            "allow_deletion": "false",
            "sonarr_url": "http://sonarr:8989",
            "sonarr_api_key": "sonarr-key",
            "radarr_url": "http://radarr:7878",
            "radarr_api_key": "radarr-key",
        }
        self.addon = MagicMock()
        self.addon.getSetting.side_effect = lambda key: self.values.get(key, "")
        self.xbmc = MagicMock()
        self.xbmc.Player.return_value.isPlaying.return_value = False
        self.xbmc.getCondVisibility.return_value = False
        self.monitor = MagicMock()
        self.monitor.abortRequested.return_value = False
        self.monitor.revision = 0
        self.factory = lambda: self.addon

    def test_defaults_are_safe(self):
        self.values.pop("dry_run")
        self.values.pop("allow_deletion")
        config, armed, interval = runtime.read_settings(self.addon)
        self.assertTrue(config["dry_run"])
        self.assertFalse(armed)
        self.assertEqual(interval, 3600)
        self.assertIsNone(cleanup.configured_zone(config))

    def test_boolean_and_interval_validation(self):
        for key, value in (
            ("dry_run", "False"),
            ("interval_minutes", "0"),
            ("interval_minutes", "1441"),
            ("retention_days", "0"),
            ("tv_kodi_path", "smb://nas/tv"),
        ):
            with self.subTest(key=key):
                original = copy.deepcopy(self.values)
                self.values[key] = value
                with self.assertRaises(ValueError):
                    runtime.read_settings(self.addon)
                self.values = original

    def test_network_sources_require_explicit_mapping(self):
        mappings = [
            {"kodi": "smb://NAS/media/TV", "arr": "/data/tv"},
            {"kodi": "nfs://nas/export/movies", "arr": "/movies"},
        ]
        self.assertEqual(
            cleanup.map_path("smb://nas/media/TV/Show/a%20b.mkv", mappings),
            "/data/tv/Show/a b.mkv",
        )
        self.assertEqual(
            cleanup.map_path("nfs://nas/export/movies/a.mkv", mappings), "/movies/a.mkv"
        )
        for path in (
            "smb://nas/media/TV2/a.mkv",
            "smb://other/media/TV/a.mkv",
            "smb://nas/media/TV/../a.mkv",
            "smb://nas/media/TV/%2e%2e/a",
            "smb://user:password@nas/media/TV/a",
            "smb://nas/media/TV/a?x=1",
            "plugin://video/a",
            "stack://a,b",
            "smb://nas/media/TV/a%00b",
        ):
            with self.subTest(path=path):
                self.assertIsNone(cleanup.map_path(path, mappings))
        self.assertIsNone(cleanup.map_path("smb://nas/media/TV/a.mkv", []))

    def test_network_mapping_conflicts_fail(self):
        mappings = [
            {"kodi": "smb://NAS/tv", "arr": "/one"},
            {"kodi": "smb://nas/tv", "arr": "/two"},
        ]
        with self.assertRaises(ValueError):
            cleanup.map_path("smb://nas/tv/a", mappings)
        with self.assertRaises(ValueError):
            cleanup.map_path(
                "smb://nas/tv/a", [{"kodi": "smb://nas/tv", "arr": "smb://nas/other"}]
            )

    def test_internal_rpc_without_http(self):
        calls = []

        def rpc(payload):
            calls.append(json.loads(payload))
            return json.dumps(
                {"result": {"movies": [], "limits": {"end": 0, "total": 0}}}
            )

        records = cleanup.kodi_items(
            KodiSession(rpc), "unused", "VideoLibrary.GetMovies", "movies"
        )
        self.assertEqual(records, [])
        self.assertEqual(calls[0]["method"], "VideoLibrary.GetMovies")

    def run_mocked(self, force_dry=False, effect=None):
        with (
            patch.object(cleanup, "build_plan", return_value=[]) as plan,
            patch.object(cleanup, "apply_plan", side_effect=effect) as apply,
            patch.object(runtime, "Session"),
        ):
            result = runtime.run_check(self.xbmc, self.monitor, self.factory, force_dry)
            return result, plan, apply

    def test_live_requires_both_settings(self):
        for dry, armed in (
            ("true", "false"),
            ("true", "true"),
            ("false", "false"),
            ("false", "true"),
        ):
            self.values.update(dry_run=dry, allow_deletion=armed)
            result, _, apply = self.run_mocked()
            live = dry == "false" and armed == "true"
            self.assertEqual(apply.call_args.kwargs["execute"], live)
            self.assertEqual(result["mode"], "LIVE" if live else "DRY RUN")

    def test_manual_dry_run_never_executes(self):
        self.values.update(dry_run="false", allow_deletion="true")
        result, _, apply = self.run_mocked(force_dry=True)
        self.assertFalse(apply.call_args.kwargs["execute"])
        self.assertEqual(result["mode"], "DRY RUN")

    def test_busy_or_disabled_prevents_planning(self):
        for reason in ("playback", "scan", "shutdown", "disabled"):
            with self.subTest(reason=reason):
                self.xbmc.Player.return_value.isPlaying.return_value = (
                    reason == "playback"
                )
                self.xbmc.getCondVisibility.return_value = reason == "scan"
                self.monitor.abortRequested.return_value = reason == "shutdown"
                self.values["enabled"] = "false" if reason == "disabled" else "true"
                with patch.object(cleanup, "build_plan") as plan:
                    with self.assertRaises(runtime.CheckInterrupted):
                        runtime.run_check(self.xbmc, self.monitor, self.factory)
                    plan.assert_not_called()

    def test_changes_during_planning_stop_mutations(self):
        def plan(*args):
            self.monitor.revision += 1
            return []

        with (
            patch.object(cleanup, "build_plan", side_effect=plan),
            patch.object(cleanup, "apply_plan") as apply,
        ):
            with self.assertRaises(runtime.CheckInterrupted):
                runtime.run_check(self.xbmc, self.monitor, self.factory)
            apply.assert_not_called()

    def test_settings_change_guard(self):
        def apply(*args, **kwargs):
            self.values["dry_run"] = "false"
            kwargs["guard"]()

        with self.assertRaises(runtime.CheckInterrupted):
            self.run_mocked(effect=apply)

    def test_guard_between_sonarr_unmonitor_and_delete(self):
        plan = [{"service": "sonarr", "id": 1, "path": "/tv/a", "episode_ids": [4]}]
        config, _, _ = runtime.read_settings(self.addon)
        config["dry_run"] = False
        guard = MagicMock(
            side_effect=[None, runtime.CheckInterrupted("Playback started")]
        )
        with patch.object(cleanup, "api") as api:
            with self.assertRaises(runtime.CheckInterrupted):
                cleanup.apply_plan(
                    config, {"sonarr": object()}, plan, execute=True, guard=guard
                )
            self.assertEqual(api.call_count, 1)
            self.assertEqual(api.call_args.args[1], "PUT")

    def test_transport_headers_body_and_empty_response(self):
        session = Session(api_key="secret")
        session.opener = MagicMock()
        session.opener.open.return_value.__enter__.return_value.read.return_value = b""
        response = session.request(
            "PUT",
            "http://sonarr/api/v3/episode/monitor",
            params={"id": 3},
            json={"monitored": False},
        )
        request = session.opener.open.call_args.args[0]
        self.assertEqual(request.get_header("X-api-key"), "secret")
        self.assertEqual(request.full_url, "http://sonarr/api/v3/episode/monitor?id=3")
        self.assertEqual(json.loads(request.data), {"monitored": False})
        self.assertEqual(response.content, b"")
        self.assertIsNone(
            NoRedirect().redirect_request(None, None, 302, "", {}, "http://other")
        )

    def test_release_zip_contains_only_allowlisted_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = build(temporary)
            with ZipFile(path) as archive:
                names = archive.namelist()
                self.assertIn("service.kodi.watchedcleanup/addon.xml", names)
                self.assertIn(
                    "service.kodi.watchedcleanup/resources/settings.xml", names
                )
                self.assertFalse(
                    any(
                        "config.json" in name or ".env" in name or "__pycache__" in name
                        for name in names
                    )
                )
                addon = ElementTree.fromstring(archive.read(names[0]))
                self.assertEqual(addon.attrib["id"], "service.kodi.watchedcleanup")
                self.assertIsNotNone(addon.find("extension[@point='xbmc.service']"))

    def test_service_checks_after_simulated_sleep(self):
        class Monitor:
            def abortRequested(self):
                return False

            def waitForAbort(self, seconds):
                waits.append(seconds)
                return len(waits) == 2

        waits = []
        self.xbmc.Monitor = Monitor
        addon_module = MagicMock()
        addon_module.Addon.return_value = self.addon
        gui = MagicMock()
        gui.Window.return_value.getProperty.return_value = ""
        vfs = MagicMock()
        with tempfile.TemporaryDirectory() as directory:
            vfs.translatePath.return_value = directory
            result = {"mode": "DRY RUN", "candidates": 0}
            with (
                patch.object(runtime, "configure_logging"),
                patch.object(runtime.time, "time", side_effect=[0, 0, 7200, 7200]),
                patch.object(runtime, "run_check", return_value=result) as check,
            ):
                runtime.serve(self.xbmc, addon_module, gui, vfs)
            check.assert_called_once()
            self.assertEqual(waits, [10, 10])
            self.assertEqual(
                json.loads((Path(directory) / "last_run.json").read_text()), result
            )

    def test_settings_xml_defaults_and_keys(self):
        settings = ElementTree.parse(ROOT / "resources/settings.xml")
        entries = {item.attrib["id"]: item for item in settings.findall(".//setting")}
        self.assertEqual(entries["dry_run"].findtext("default"), "true")
        self.assertEqual(entries["allow_deletion"].findtext("default"), "false")
        self.assertEqual(entries["enabled"].findtext("default"), "false")
        self.assertEqual(entries["sonarr_api_key"].findtext("control/hidden"), "true")
        for key in self.values:
            self.assertIn(key, entries)


if __name__ == "__main__":
    unittest.main()
