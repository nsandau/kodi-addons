import json
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from xml.etree import ElementTree

import cleanup
from resources.lib import runtime
from scripts.build_release import ROOT


class ManualCleanupTests(unittest.TestCase):
    def setUp(self):
        self.values = {
            "enabled": "true",
            "dry_run": "false",
            "allow_deletion": "true",
            "sonarr_url": "http://sonarr",
            "sonarr_api_key": "sonarr-secret",
            "radarr_url": "http://radarr",
            "radarr_api_key": "radarr-secret",
        }
        self.addon = MagicMock()
        self.addon.getSetting.side_effect = lambda key: self.values.get(key, "")
        self.dialog = MagicMock()
        self.dialog.yesno.return_value = True
        self.window = MagicMock()
        self.xbmc = MagicMock()
        self.xbmc.Player.return_value.isPlaying.return_value = False
        self.xbmc.getCondVisibility.return_value = False
        self.monitor = MagicMock()
        self.monitor.abortRequested.return_value = False
        self.monitor.revision = 0

    def test_live_request_requires_both_gates_and_enabled_service(self):
        for key, value in (
            ("dry_run", "true"),
            ("allow_deletion", "false"),
            ("enabled", "false"),
        ):
            original = self.values[key]
            self.values[key] = value
            self.assertFalse(
                runtime.queue_cleanup(self.addon, self.dialog, self.window, live=True)
            )
            self.values[key] = original
        self.window.setProperty.assert_not_called()
        self.dialog.yesno.assert_not_called()
        self.addon.setSetting.assert_not_called()

    def test_confirmation_cancel_does_not_queue_or_change_settings(self):
        self.dialog.yesno.return_value = False
        self.assertFalse(
            runtime.queue_cleanup(self.addon, self.dialog, self.window, live=True)
        )
        self.dialog.yesno.assert_called_once()
        self.window.setProperty.assert_not_called()
        self.addon.setSetting.assert_not_called()

    def test_confirmed_live_request_is_bound_to_current_settings_and_expires(self):
        with patch.object(runtime.time, "time", return_value=100):
            self.assertTrue(
                runtime.queue_cleanup(self.addon, self.dialog, self.window, live=True)
            )
        key, payload = self.window.setProperty.call_args.args
        self.assertEqual(key, runtime.REQUEST_KEY)
        request = json.loads(payload)
        self.assertEqual(
            request,
            {
                "mode": "live",
                "expires": 400,
                "settings": runtime.settings_fingerprint(
                    runtime.read_settings(self.addon)
                ),
            },
        )
        self.assertNotIn("secret", payload)
        self.assertNotIn("http", payload)
        self.addon.setSetting.assert_not_called()

    def test_dry_request_stays_dry_even_when_live_gates_are_enabled(self):
        self.assertTrue(runtime.queue_cleanup(self.addon, self.dialog, self.window))
        self.assertEqual(
            json.loads(self.window.setProperty.call_args.args[1]), {"mode": "dry"}
        )
        self.dialog.yesno.assert_not_called()

    def test_worker_revalidates_gates_before_any_planning(self):
        self.values["dry_run"] = "true"
        with (
            patch.object(cleanup, "build_plan") as plan,
            self.assertRaises(runtime.CheckInterrupted),
        ):
            runtime.run_check(
                self.xbmc, self.monitor, lambda: self.addon, require_live=True
            )
        plan.assert_not_called()

    def test_worker_cancels_if_settings_changed_since_confirmation(self):
        fingerprint = runtime.settings_fingerprint(runtime.read_settings(self.addon))
        self.values["retention_days"] = "5"
        with (
            patch.object(cleanup, "build_plan") as plan,
            self.assertRaises(runtime.CheckInterrupted),
        ):
            runtime.run_check(
                self.xbmc,
                self.monitor,
                lambda: self.addon,
                require_live=True,
                expected_settings=fingerprint,
            )
        plan.assert_not_called()

    def test_manual_live_uses_existing_planner_and_apply_with_guard(self):
        fingerprint = runtime.settings_fingerprint(runtime.read_settings(self.addon))
        with (
            patch.object(cleanup, "build_plan", return_value=[]) as plan,
            patch.object(cleanup, "apply_plan") as apply,
            patch.object(runtime, "Session"),
        ):
            result = runtime.run_check(
                self.xbmc,
                self.monitor,
                lambda: self.addon,
                require_live=True,
                expected_settings=fingerprint,
            )
        plan.assert_called_once()
        self.assertTrue(apply.call_args.kwargs["execute"])
        self.assertTrue(callable(apply.call_args.kwargs["guard"]))
        self.assertEqual(result["mode"], "LIVE")

    def serve(self, request, error=None):
        waits = []

        class Monitor:
            def abortRequested(self):
                return False

            def waitForAbort(self, seconds):
                waits.append(seconds)
                return len(waits) >= 2

        self.xbmc.Monitor = Monitor
        addon_module = MagicMock()
        addon_module.Addon.return_value = self.addon
        gui = MagicMock()
        gui.Window.return_value = self.window
        gui.Dialog.return_value = self.dialog
        properties = {runtime.REQUEST_KEY: json.dumps(request)}
        self.window.getProperty.side_effect = lambda key: properties.get(key, "")
        self.window.clearProperty.side_effect = lambda key: properties.pop(key, None)
        vfs = MagicMock()
        with tempfile.TemporaryDirectory() as directory:
            vfs.translatePath.return_value = directory
            with (
                patch.object(runtime, "configure_logging"),
                patch.object(runtime.time, "time", return_value=100),
                patch.object(
                    runtime,
                    "run_check",
                    return_value={"mode": "LIVE", "candidates": 2},
                    side_effect=error,
                ) as check,
            ):
                runtime.serve(self.xbmc, addon_module, gui, vfs)
            return check

    def test_manual_live_is_dispatched_once_by_single_worker(self):
        request = {"mode": "live", "expires": 400, "settings": "fingerprint"}
        check = self.serve(request)
        check.assert_called_once()
        self.assertEqual(
            check.call_args.kwargs,
            {
                "force_dry": False,
                "require_live": True,
                "expected_settings": "fingerprint",
            },
        )
        self.assertIn("LIVE completed", self.dialog.notification.call_args.args[1])

    def test_expired_manual_live_never_reaches_worker(self):
        with self.assertLogs(runtime.LOG, level="INFO"):
            check = self.serve(
                {"mode": "live", "expires": 99, "settings": "fingerprint"}
            )
        check.assert_not_called()
        self.assertIn("expired", self.dialog.notification.call_args.args[1])

    def test_failed_manual_live_is_not_automatically_retried(self):
        with self.assertLogs(runtime.LOG, level="ERROR"):
            check = self.serve(
                {"mode": "live", "expires": 400, "settings": "fingerprint"},
                error=RuntimeError("API failed"),
            )
        check.assert_called_once()
        self.assertIn("failed", self.dialog.notification.call_args.args[1])

    def test_invalid_manual_live_confirmation_never_runs(self):
        with self.assertLogs(runtime.LOG, level="ERROR"):
            check = self.serve({"mode": "live"})
        check.assert_not_called()

    def test_button_commits_settings_before_live_confirmation(self):
        settings = ElementTree.parse(ROOT / "resources/settings.xml")
        entry = settings.find(".//setting[@id='run_live']")
        self.assertEqual(
            entry.findtext("data"), "RunScript(service.kodi.watchedcleanup,live)"
        )
        self.assertEqual(entry.findtext("control/close"), "true")


if __name__ == "__main__":
    unittest.main()
