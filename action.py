"""Kodi actions for setup, cleanup requests, and local log viewing."""

import sys
from pathlib import Path

import xbmcaddon
import xbmcgui
import xbmcvfs

from resources.lib.runtime import ADDON_ID, queue_cleanup
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
    else:
        queue_cleanup(addon, dialog, xbmcgui.Window(10000), live=action == "live")


if __name__ == "__main__":
    main()
