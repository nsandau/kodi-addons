"""Kodi actions for setup, read-only dry-run requests, and local log viewing."""

import sys
from pathlib import Path

import xbmcaddon
import xbmcgui
import xbmcvfs

from resources.lib.runtime import ADDON_ID, REQUEST_KEY, boolean
from resources.lib.setup import run_setup_action


def main():
    addon = xbmcaddon.Addon(ADDON_ID)
    dialog = xbmcgui.Dialog()
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    if run_setup_action(addon, dialog, action):
        return
    if action == "logs":
        profile = Path(xbmcvfs.translatePath(addon.getAddonInfo("profile")))
        path = profile / "cleanup.log"
        content = (
            path.read_text(encoding="utf-8")
            if path.exists()
            else "No checks have run yet."
        )
        dialog.textviewer("Watched media cleanup", content[-64000:])
    elif not boolean(addon, "enabled", False):
        dialog.ok(
            "Watched media cleanup",
            "Configure the addon and enable background checks first. Keep Dry run enabled.",
        )
        addon.openSettings()
    else:
        xbmcgui.Window(10000).setProperty(REQUEST_KEY, "true")
        dialog.notification(
            "Watched media cleanup",
            "Dry run queued. Waiting until playback and library scans stop.",
        )


if __name__ == "__main__":
    main()
