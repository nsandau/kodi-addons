import unittest
from unittest.mock import MagicMock, patch
from xml.etree import ElementTree

from resources.lib import runtime, setup
from scripts.build_release import ROOT


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.values = {
            "sonarr_url": "http://sonarr:8989/base",
            "sonarr_api_key": "secret",
            "radarr_url": "http://radarr:7878",
            "radarr_api_key": "other",
        }
        self.addon = MagicMock()
        self.addon.getSetting.side_effect = lambda key: self.values.get(key, "")
        self.dialog = MagicMock()
        self.dialog.select.return_value = 0

    def test_connection_works_without_other_service_mappings_or_enabled_flag(self):
        self.values.pop("radarr_api_key")
        self.values.pop("radarr_url")
        session = MagicMock()
        with (
            patch.object(setup.cleanup, "make_session", return_value=session),
            patch.object(
                setup.cleanup,
                "api",
                return_value={"appName": "Sonarr", "version": "4.0"},
            ) as api,
        ):
            setup.test_connection(self.addon, self.dialog, "sonarr")
        api.assert_called_once_with(
            session, "GET", "http://sonarr:8989/base/api/v3/system/status"
        )
        session.close.assert_called_once()
        self.assertIn("successful", self.dialog.notification.call_args.args[1])
        self.addon.setSetting.assert_not_called()

    def test_wrong_app_or_response_is_not_success(self):
        for response in (
            {"appName": "Radarr", "version": "5"},
            {"version": "4"},
            [],
            None,
        ):
            with (
                self.subTest(response=response),
                patch.object(setup, "fetch_service", return_value=response),
                self.assertRaises(ValueError),
            ):
                setup.test_connection(self.addon, self.dialog, "sonarr")
        self.dialog.notification.assert_not_called()

    def test_missing_credentials_or_invalid_url_never_calls_network(self):
        for key, value in (
            ("sonarr_api_key", ""),
            ("sonarr_url", "ftp://sonarr"),
            ("sonarr_url", "http://sonarr/api/v3"),
            ("sonarr_url", "http://user:password@sonarr"),
        ):
            old = dict(self.values)
            self.values[key] = value
            with patch.object(setup.cleanup, "make_session") as session:
                with self.assertRaises(ValueError):
                    setup.fetch_service(self.addon, "sonarr", "system/status")
                session.assert_not_called()
            self.values = old

    def test_api_error_keeps_settings_open_and_shows_only_notification(self):
        session = MagicMock()
        with (
            patch.object(setup.cleanup, "make_session", return_value=session),
            patch.object(
                setup.cleanup, "api", side_effect=RuntimeError("API returned HTTP 401")
            ),
        ):
            self.assertTrue(
                setup.run_setup_action(self.addon, self.dialog, "test_sonarr")
            )
        session.close.assert_called_once()
        self.assertIn("401", self.dialog.notification.call_args.args[1])
        self.assertNotIn("secret", self.dialog.notification.call_args.args[1])
        self.addon.openSettings.assert_not_called()
        self.addon.setSetting.assert_not_called()

    def test_choose_root_exactly_as_reported_and_only_after_selection(self):
        self.dialog.select.return_value = 1
        with patch.object(
            setup,
            "fetch_service",
            return_value=[{"path": "/data/tv"}, {"path": "/archive/tv/"}],
        ) as fetch:
            setup.choose_arr_root(self.addon, self.dialog, "tv")
        fetch.assert_called_once_with(self.addon, "sonarr", "rootfolder")
        self.addon.setSetting.assert_called_once_with("tv_arr_path", "/archive/tv/")

    def test_cancel_and_empty_roots_do_not_change_settings(self):
        for records in ([], [{"path": "/data/movies"}]):
            self.dialog.select.return_value = -1
            with patch.object(setup, "fetch_service", return_value=records):
                setup.choose_arr_root(self.addon, self.dialog, "movies")
        self.addon.setSetting.assert_not_called()

    def test_invalid_root_response_never_changes_settings(self):
        for response in (
            {},
            [{"path": "smb://host/media"}],
            [{"path": "relative"}],
            [None],
        ):
            with (
                patch.object(setup, "fetch_service", return_value=response),
                self.assertRaises((TypeError, ValueError)),
            ):
                setup.choose_arr_root(self.addon, self.dialog, "tv")
        self.addon.setSetting.assert_not_called()

    def test_kodi_folders_use_native_settings_controls_not_scripts(self):
        settings = ElementTree.parse(ROOT / "resources/settings.xml")
        for key in ("tv_kodi_path", "movies_kodi_path"):
            entry = settings.find(".//setting[@id='" + key + "']")
            self.assertEqual(entry.attrib["type"], "path")
            self.assertEqual(
                entry.find("control").attrib, {"type": "button", "format": "path"}
            )
            self.assertEqual(entry.findtext("constraints/sources/source"), "video")
            self.assertEqual(entry.findtext("constraints/allowempty"), "true")
            self.assertIsNone(entry.find("data"))
        self.assertIsNone(settings.find(".//setting[@id='browse_tv_kodi']"))
        self.assertIsNone(settings.find(".//setting[@id='browse_movies_kodi']"))

    def test_arr_picker_updates_live_dialog_without_reopening_or_notifications(self):
        with patch.object(
            setup, "fetch_service", return_value=[{"path": "/data/media/movies"}]
        ):
            self.assertTrue(
                setup.run_setup_action(self.addon, self.dialog, "choose_movies_arr")
            )
        self.addon.setSetting.assert_called_once_with(
            "movies_arr_path", "/data/media/movies"
        )
        self.addon.openSettings.assert_not_called()
        self.dialog.ok.assert_not_called()
        self.dialog.notification.assert_not_called()

    def test_multiple_mapping_selections_commit_together_on_ok(self):
        # Model Kodi's documented active-dialog setSetting route: selections
        # update the existing pending settings, then OK persists the whole form.
        # This is not an on-device Kodi GUI test.
        persisted = dict(self.values)
        pending = dict(persisted)
        pending["movies_kodi_path"] = (
            "nfs://192.168.4.200/home/nicolai/data/media/movies/"
        )
        pending["tv_kodi_path"] = "nfs://192.168.4.200/home/nicolai/data/media/tvshows/"
        self.addon.setSetting.side_effect = pending.__setitem__
        self.dialog.select.return_value = 0
        with patch.object(
            setup,
            "fetch_service",
            side_effect=[
                [{"path": "/data/media/movies"}],
                [{"path": "/data/media/tvshows"}],
            ],
        ):
            setup.run_setup_action(self.addon, self.dialog, "choose_movies_arr")
            setup.run_setup_action(self.addon, self.dialog, "choose_tv_arr")
        self.assertNotIn("movies_arr_path", persisted)
        self.addon.openSettings.assert_not_called()
        self.dialog.ok.assert_not_called()
        persisted.update(pending)  # The user presses the existing dialog's OK.
        reopened = MagicMock()
        reopened.getSetting.side_effect = lambda key: persisted.get(key, "")
        config, _, _ = runtime.read_settings(reopened)
        self.assertEqual(
            config["path_mappings"],
            [
                {"kodi": pending["tv_kodi_path"], "arr": "/data/media/tvshows"},
                {"kodi": pending["movies_kodi_path"], "arr": "/data/media/movies"},
            ],
        )
        self.assertEqual(
            setup.cleanup.map_path(
                pending["movies_kodi_path"] + "Movie/movie.mkv", config["path_mappings"]
            ),
            "/data/media/movies/Movie/movie.mkv",
        )

    def test_setup_actions_never_close_the_settings_dialog(self):
        settings = ElementTree.parse(ROOT / "resources/settings.xml")
        actions = {
            entry.findtext("data"): entry
            for entry in settings.findall(".//setting[@type='action']")
        }
        for action in (
            "test_sonarr",
            "test_radarr",
            "choose_tv_arr",
            "choose_movies_arr",
        ):
            entry = actions["RunScript(service.kodi.watchedcleanup," + action + ")"]
            self.assertEqual(entry.findtext("control/close"), "false")
        self.assertFalse(setup.run_setup_action(self.addon, self.dialog, "logs"))
        self.addon.openSettings.assert_not_called()


if __name__ == "__main__":
    unittest.main()
