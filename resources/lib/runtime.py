"""Kodi service orchestration. The cleanup engine remains the single owner of deletion."""

import json
import logging
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

import cleanup
from resources.lib.transport import KodiSession, Session

ADDON_ID = "service.kodi.watchedcleanup"
REQUEST_KEY = ADDON_ID + ".dry_run"
LOG = cleanup.LOG


class CheckInterrupted(RuntimeError):
    """Playback, settings changes or shutdown stopped a cleanup safely."""


def boolean(addon, key, default):
    value = addon.getSetting(key)
    if value == "":
        return default
    if value not in ("true", "false"):
        raise ValueError("Invalid boolean setting: " + key)
    return value == "true"


def read_settings(addon):
    mappings = []
    for name in ("tv", "movies"):
        source = addon.getSetting(name + "_kodi_path").strip()
        destination = addon.getSetting(name + "_arr_path").strip()
        if bool(source) != bool(destination):
            raise ValueError("Set both source and destination for " + name)
        if source:
            mappings.append({"kodi": source, "arr": destination})
    interval = int(addon.getSetting("interval_minutes") or "60")
    if not 5 <= interval <= 1440:
        raise ValueError("Check interval must be between 5 and 1440 minutes")
    config = {
        "dry_run": boolean(addon, "dry_run", True),
        "verbose_logs": boolean(addon, "verbose_logs", False),
        "retention_days": int(addon.getSetting("retention_days") or "3"),
        "kodi_timezone": "local",
        # KodiSession dispatches this internally, without an HTTP connection.
        "kodi": {"url": "http://localhost/jsonrpc"},
        "path_mappings": mappings,
    }
    for name in ("sonarr", "radarr"):
        config[name] = {
            "url": addon.getSetting(name + "_url").strip(),
            "api_key": addon.getSetting(name + "_api_key").strip(),
        }
    cleanup.validate_config(config)
    return config, boolean(addon, "allow_deletion", False), interval * 60


def busy(xbmc):
    return xbmc.Player().isPlaying() or xbmc.getCondVisibility(
        "Library.IsScanningVideo"
    )


def run_check(xbmc, monitor, addon_factory, force_dry=False):
    config, armed, interval = read_settings(addon_factory())
    original = (config, armed, interval)
    revision = getattr(monitor, "revision", 0)

    def guard():
        if (
            monitor.abortRequested()
            or busy(xbmc)
            or getattr(monitor, "revision", 0) != revision
        ):
            raise CheckInterrupted(
                "Cleanup paused for playback, library scan or shutdown"
            )
        current_addon = addon_factory()
        if not boolean(current_addon, "enabled", False):
            raise CheckInterrupted("Cleanup disabled")
        if read_settings(current_addon) != original:
            raise CheckInterrupted("Settings changed during cleanup")

    guard()
    sessions = {"kodi": KodiSession(xbmc.executeJSONRPC)}
    try:
        for name in ("sonarr", "radarr"):
            sessions[name] = Session(api_key=config[name]["api_key"])
        plan = cleanup.build_plan(config, sessions)
        guard()
        live = armed and not config["dry_run"] and not force_dry
        cleanup.apply_plan(config, sessions, plan, execute=live, guard=guard)
        return {
            "time": datetime.now(timezone.utc).isoformat(),
            "mode": "LIVE" if live else "DRY RUN",
            "candidates": len(plan),
        }
    finally:
        for session in sessions.values():
            session.close()


class KodiLogHandler(logging.Handler):
    def __init__(self, xbmc):
        super().__init__()
        self.xbmc = xbmc

    def emit(self, record):
        level = (
            self.xbmc.LOGERROR if record.levelno >= logging.ERROR else self.xbmc.LOGINFO
        )
        self.xbmc.log("[Watched cleanup] " + self.format(record), level)


def configure_logging(xbmc, profile):
    profile.mkdir(parents=True, exist_ok=True)
    LOG.setLevel(logging.INFO)
    LOG.propagate = False
    file_handler = RotatingFileHandler(
        str(profile / "cleanup.log"), maxBytes=1048576, backupCount=2, encoding="utf-8"
    )
    for handler in (KodiLogHandler(xbmc), file_handler):
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        LOG.addHandler(handler)


def serve(xbmc, xbmcaddon, xbmcgui, xbmcvfs):
    class Monitor(xbmc.Monitor):
        revision = 0

        def onSettingsChanged(self):
            self.revision += 1

        def onNotification(self, sender, method, data):
            if method.startswith(("Player.", "VideoLibrary.")):
                self.revision += 1

    monitor = Monitor()
    window = xbmcgui.Window(10000)

    def factory():
        return xbmcaddon.Addon(ADDON_ID)

    profile = Path(xbmcvfs.translatePath(factory().getAddonInfo("profile")))
    configure_logging(xbmc, profile)
    LOG.info("Service started. Default mode is dry run. Waiting for Kodi startup")
    next_check = time.time() + 60
    pending_dry = False
    while not monitor.abortRequested():
        if window.getProperty(REQUEST_KEY):
            window.clearProperty(REQUEST_KEY)
            pending_dry = True
            next_check = 0
        try:
            addon = factory()
            enabled = boolean(addon, "enabled", False)
            if enabled and time.time() >= next_check and not busy(xbmc):
                result = run_check(xbmc, monitor, factory, force_dry=pending_dry)
                (profile / "last_run.json").write_text(
                    json.dumps(result, indent=2), encoding="utf-8"
                )
                if pending_dry:
                    xbmcgui.Dialog().notification(
                        "Watched media cleanup",
                        "Dry run: {} candidates. See addon logs".format(
                            result["candidates"]
                        ),
                    )
                pending_dry = False
                next_check = time.time() + read_settings(factory())[2]
        except CheckInterrupted as error:
            LOG.info("%s", error)
            next_check = time.time() + 60
        except Exception:
            # Do not log full settings, JSON-RPC data or HTTP request headers.
            LOG.exception("Cleanup failed. Stopping this check without further changes")
            next_check = time.time() + 300
        if monitor.waitForAbort(10):
            break
    LOG.info("Service stopped")
