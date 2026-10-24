import unittest
from unittest.mock import MagicMock, patch
from xml.etree import ElementTree

from resources.lib import setup
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
        self.assertIn("successful", self.dialog.ok.call_args.args[1])
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
        self.dialog.ok.assert_not_called()

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

    def test_session_closed_on_api_error_and_dialog_returns_to_settings(self):
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
        self.assertIn("401", self.dialog.ok.call_args.args[1])
        self.assertNotIn("secret", self.dialog.ok.call_args.args[1])
        self.addon.openSettings.assert_called_once()
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

    def test_kodi_folder_picker_uses_video_sources_and_preserves_network_path(self):
        for path in ("smb://nas/media/TV/", "nfs://nas/export/TV/", "/mnt/TV/"):
            self.dialog.browseSingle.return_value = path
            setup.choose_kodi_root(self.addon, self.dialog, "tv")
            self.addon.setSetting.assert_called_with("tv_kodi_path", path)
        self.dialog.browseSingle.assert_called_with(
            0, "Choose Kodi TV source folder", "video", defaultt="", treatAsFolder=True
        )

    def test_cancel_kodi_picker_does_not_clear_existing_mapping(self):
        self.dialog.browseSingle.return_value = ""
        self.values["tv_kodi_path"] = "smb://nas/tv"
        setup.choose_kodi_root(self.addon, self.dialog, "tv")
        self.addon.setSetting.assert_not_called()

    def test_kodi_cancel_returning_default_does_not_write_mapping(self):
        self.values["tv_kodi_path"] = "smb://nas/tv"
        self.dialog.browseSingle.return_value = "smb://nas/tv"
        setup.choose_kodi_root(self.addon, self.dialog, "tv")
        self.addon.setSetting.assert_not_called()

    def test_unsupported_or_credential_bearing_kodi_path_is_rejected(self):
        for path in (
            "smb://user:password@nas/tv",
            "plugin://video",
            "relative",
            "smb://nas/a/../tv",
        ):
            self.dialog.browseSingle.return_value = path
            with self.assertRaises(ValueError):
                setup.choose_kodi_root(self.addon, self.dialog, "tv")
        self.addon.setSetting.assert_not_called()

    def test_actions_are_defined_and_save_settings_before_execution(self):
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
            "choose_tv_kodi",
            "choose_movies_kodi",
        ):
            entry = actions["RunScript(service.kodi.watchedcleanup," + action + ")"]
            self.assertEqual(entry.findtext("control/close"), "true")
        self.assertFalse(setup.run_setup_action(self.addon, self.dialog, "logs"))
        self.addon.openSettings.assert_not_called()


if __name__ == "__main__":
    unittest.main()
