"""Kodi service entry point."""

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

from resources.lib.runtime import serve

if __name__ == "__main__":
    serve(xbmc, xbmcaddon, xbmcgui, xbmcvfs)
