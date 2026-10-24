"""Read-only connection checks and explicit mapping selection for Kodi settings."""

import cleanup

SERVICES = {"sonarr": "Sonarr", "radarr": "Radarr"}
MEDIA = {"tv": ("sonarr", "TV"), "movies": ("radarr", "movies")}


def service_settings(addon, name):
    if name not in SERVICES:
        raise ValueError("Unknown service")
    settings = {
        "url": addon.getSetting(name + "_url").strip(),
        "api_key": addon.getSetting(name + "_api_key").strip(),
    }
    cleanup.validate_service(name, settings)
    return settings


def fetch_service(addon, name, resource):
    settings = service_settings(addon, name)
    session = cleanup.make_session(api_key=settings["api_key"])
    try:
        return cleanup.api(
            session, "GET", cleanup.arr_url({name: settings}, name, resource)
        )
    finally:
        session.close()


def test_connection(addon, dialog, name):
    """Only test this service. Never require mappings or background checks."""
    status = fetch_service(addon, name, "system/status")
    expected = SERVICES[name]
    if (
        not isinstance(status, dict)
        or status.get("appName", "").lower() != name
        or not status.get("version")
    ):
        raise ValueError(
            "Server did not return valid " + expected + " status. Check its URL"
        )
    dialog.notification(
        expected,
        "Connection successful. Version " + str(status["version"]),
        sound=False,
    )


def choose_arr_root(addon, dialog, media):
    name, label = MEDIA[media]
    records = fetch_service(addon, name, "rootfolder")
    if not isinstance(records, list):
        raise TypeError("Invalid root-folder response from " + SERVICES[name])
    paths = []
    for record in records:
        path = record.get("path") if isinstance(record, dict) else None
        if cleanup.normalized_path(path) is None:
            raise ValueError("Server returned an unsupported root folder")
        if path not in paths:
            paths.append(path)
    if not paths:
        dialog.notification(
            SERVICES[name], "No root folders configured on this server", sound=False
        )
        return
    selected = dialog.select("Choose " + SERVICES[name] + " root for " + label, paths)
    if 0 <= selected < len(paths):
        # Keep exactly the root reported by the API. Never guess or store a cache.
        addon.setSetting(media + "_arr_path", paths[selected])


def run_setup_action(addon, dialog, action):
    """Return False for unrelated actions so the entry point owns dispatch once."""
    actions = {
        "test_sonarr": (test_connection, "sonarr"),
        "test_radarr": (test_connection, "radarr"),
        "choose_tv_arr": (choose_arr_root, "tv"),
        "choose_movies_arr": (choose_arr_root, "movies"),
    }
    if action not in actions:
        return False
    function, argument = actions[action]
    try:
        function(addon, dialog, argument)
    except (ValueError, TypeError, KeyError, OSError, RuntimeError) as error:
        # Transport errors omit URLs, response bodies and authentication headers.
        dialog.notification(
            "Watched media cleanup", "Setup failed: " + str(error), sound=False
        )
    return True
