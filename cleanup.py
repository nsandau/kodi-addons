#!/usr/bin/env python3
"""Delete watched media through Radarr/Sonarr, with two explicit live-run gates."""

import argparse
import json
import logging
import math
import posixpath
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit

from resources.lib.transport import Session

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None

LOG = logging.getLogger("kodi-cleanup")


def api(session, method, url, *, params=None, payload=None):
    response = session.request(method, url, params=params, json=payload, timeout=30)
    response.raise_for_status()
    return response.json() if response.content else None


def make_session(api_key=None, username=None, password=None):
    return Session(api_key, username, password)


def kodi_items(session, endpoint, method, key):
    result = []
    start = 0
    properties = ["file", "playcount", "lastplayed"]
    if key == "episodes":
        properties += ["season", "episode"]
    while True:
        reply = api(
            session,
            "POST",
            endpoint,
            payload={
                "jsonrpc": "2.0",
                "id": 1,
                "method": method,
                "params": {
                    "properties": properties,
                    "limits": {"start": start, "end": start + 500},
                },
            },
        )
        if not isinstance(reply, dict) or "error" in reply or "result" not in reply:
            raise RuntimeError(f"Kodi {method}: invalid or error response: {reply}")
        data = reply["result"]
        page = data.get(key, [])
        result.extend(page)
        limits = data.get("limits", {})
        end = limits.get("end", start + len(page))
        total = limits.get("total", len(result))
        if end >= total:
            break
        if not page or end <= start:
            raise RuntimeError("Kodi pagination did not advance")
        start = end
    return result


def normalized_path(value):
    """Accept absolute filesystem paths, not network/media URLs or UNC paths."""
    if not isinstance(value, str) or not value or "\x00" in value:
        return None
    if value.startswith("file://"):
        parsed = urlsplit(value)
        if parsed.netloc not in ("", "localhost") or parsed.query or parsed.fragment:
            return None
        value = unquote(parsed.path)
    if "://" in value or "\x00" in value:
        return None
    value = value.replace("\\", "/")
    if value.startswith("//"):
        return None
    if not (
        value.startswith("/")
        or (len(value) > 2 and value[1:3] == ":/" and value[0].isalpha())
    ):
        return None
    norm = posixpath.normpath(value)
    return norm if norm not in ("/", ".") and not norm.endswith(":") else None


def network_path(value):
    """Network URLs require explicit mappings and never receive a fallback."""
    if not isinstance(value, str) or "\x00" in value or "\\" in value:
        return None
    parsed = urlsplit(value)
    if (
        parsed.scheme not in ("smb", "nfs")
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        return None
    path = unquote(parsed.path)
    if "\x00" in path or "\\" in path or not path.startswith("/"):
        return None
    if any(part in (".", "..") for part in path.split("/")):
        return None
    path = posixpath.normpath(path)
    if path == "/":
        return None
    return parsed.scheme + "://" + parsed.netloc.lower() + path


def mapping_source(value):
    return normalized_path(value) or network_path(value)


def map_path(path, mappings):
    path = mapping_source(path)
    if path is None:
        return None
    matches = []
    for mapping in mappings:
        src = mapping_source(mapping["kodi"])
        dst = normalized_path(mapping["arr"])
        if src is None or dst is None:
            raise ValueError(
                "Mappings require a local or SMB/NFS source and an absolute local destination"
            )
        if path == src or path.startswith(src.rstrip("/") + "/"):
            suffix = path[len(src) :].lstrip("/")
            matches.append((len(src), posixpath.normpath(posixpath.join(dst, suffix))))
    if not matches:
        return None if "://" in path else path
    best = max(length for length, _ in matches)
    candidates = {mapped for length, mapped in matches if length == best}
    if len(candidates) != 1:
        raise ValueError(f"Conflicting Kodi path mappings for {path}")
    return next(iter(candidates))


def configured_zone(config):
    name = config.get("kodi_timezone", "Europe/Copenhagen")
    if name == "local":
        return None
    if ZoneInfo is None:
        raise ValueError("Named timezones require Python 3.9+. Use local on Kodi")
    return ZoneInfo(name)


def parse_lastplayed(value, local_zone):
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if dt.tzinfo is None and local_zone is not None:
            dt = dt.replace(tzinfo=local_zone)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def watch_status(item, cutoff, zone):
    """Return the interpreted timestamp and the shared eligibility explanation."""
    played = parse_lastplayed(item.get("lastplayed"), zone)
    try:
        watched = int(item.get("playcount") or 0) > 0
    except (TypeError, ValueError):
        return played, "invalid_playcount"
    if not watched:
        return played, "unwatched"
    if played is None:
        return None, "missing_or_invalid_lastplayed"
    if played > cutoff:
        return played, "retention_not_elapsed"
    return played, "eligible"


def eligible(item, cutoff, zone):
    return watch_status(item, cutoff, zone)[1] == "eligible"


def indexed_kodi(items, mappings, *, verbose=False, kind=None, cutoff=None, zone=None):
    by_path = defaultdict(list)
    for item in items:
        source = item.get("file")
        path = map_path(source, mappings)
        if verbose:
            played, reason = watch_status(item, cutoff, zone)
            # Never expose credentials or query tokens from rejected media URLs.
            safe_source = (
                mapping_source(source) or "[unsupported or credential-bearing path]"
            )
            LOG.info(
                "WATCH %s id=%r file=%r mapped=%r playcount=%r lastplayed=%r "
                "lastplayed_utc=%s retention_status=%s path_status=%s",
                kind,
                item.get("movieid" if kind == "movie" else "episodeid"),
                safe_source,
                path,
                item.get("playcount"),
                item.get("lastplayed"),
                played.isoformat() if played else "unknown",
                reason,
                "mapped_or_same_path" if path else "unmapped_or_unsupported",
            )
        if path:
            by_path[path].append(item)
    return by_path


def validate_service(name, service):
    """Validate one endpoint without requiring the other services or mappings."""
    parsed = urlsplit(service["url"])
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError(f"Invalid {name} URL")
    # Accessing port also rejects malformed or out-of-range ports before HTTP.
    _ = parsed.port
    if name == "kodi":
        if not parsed.path.rstrip("/").endswith("/jsonrpc"):
            raise ValueError("kodi.url must end with /jsonrpc")
    else:
        if not service.get("api_key") or "REPLACE" in service["api_key"]:
            raise ValueError(f"Configure the {name} API key")
        if parsed.path.rstrip("/").endswith("/api/v3"):
            raise ValueError(f"{name}.url must be a base URL without /api/v3")


def validate_config(config):
    days = config.get("retention_days", 3)
    if (
        isinstance(days, bool)
        or not isinstance(days, (int, float))
        or not math.isfinite(days)
        or days <= 0
    ):
        raise ValueError("retention_days must be a positive finite number")
    if not isinstance(config.get("dry_run", True), bool):
        raise TypeError("dry_run must be a JSON boolean")
    if not isinstance(config.get("verbose_logs", False), bool):
        raise TypeError("verbose_logs must be a JSON boolean")
    configured_zone(config)
    for name in ("kodi", "sonarr", "radarr"):
        validate_service(name, config[name])
    for mapping in config.get("path_mappings", []):
        if (
            mapping_source(mapping["kodi"]) is None
            or normalized_path(mapping["arr"]) is None
        ):
            raise ValueError(
                "Mappings require a local or SMB/NFS source and an absolute local destination"
            )
    # Also reject identical source prefixes with conflicting destinations up front.
    for mapping in config.get("path_mappings", []):
        map_path(mapping["kodi"], config["path_mappings"])


def arr_url(config, service, resource):
    return config[service]["url"].rstrip("/") + "/api/v3/" + resource


def unique_index(records, path_fn):
    index = defaultdict(list)
    for record in records:
        path = normalized_path(path_fn(record))
        if path:
            index[path].append(record)
    return index


def build_plan(config, sessions, now=None):
    """Read all services and build a conservative plan before any mutation."""
    zone = configured_zone(config)
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(
        days=config.get("retention_days", 3)
    )
    mappings = config.get("path_mappings", [])
    kodi_url = config["kodi"]["url"]
    verbose = config.get("verbose_logs", False)
    if verbose:
        LOG.info(
            "Watch-history details: timezone=%s retention_days=%s cutoff_utc=%s. "
            "Retention eligibility alone does not authorize deletion",
            config.get("kodi_timezone", "Europe/Copenhagen"),
            config.get("retention_days", 3),
            cutoff.isoformat(),
        )
    movies = indexed_kodi(
        kodi_items(sessions["kodi"], kodi_url, "VideoLibrary.GetMovies", "movies"),
        mappings,
        verbose=verbose,
        kind="movie",
        cutoff=cutoff,
        zone=zone,
    )
    episodes = indexed_kodi(
        kodi_items(sessions["kodi"], kodi_url, "VideoLibrary.GetEpisodes", "episodes"),
        mappings,
        verbose=verbose,
        kind="episode",
        cutoff=cutoff,
        zone=zone,
    )
    LOG.info(
        "Cutoff=%s Kodi movie paths=%d episode paths=%d",
        cutoff.isoformat(),
        len(movies),
        len(episodes),
    )
    plan = []
    radarr_movies = api(sessions["radarr"], "GET", arr_url(config, "radarr", "movie"))
    radarr_index = unique_index(
        radarr_movies, lambda movie: movie.get("movieFile", {}).get("path")
    )
    for path, records in movies.items():
        matches = radarr_index.get(path, [])
        if len(matches) != 1 or not all(
            eligible(item, cutoff, zone) for item in records
        ):
            LOG.info("SKIP movie %s: unmatched, ambiguous, or not eligible", path)
            continue
        movie = matches[0]
        # Deleting a movie removes its entire folder. Reject extra movie versions/files.
        files = api(
            sessions["radarr"],
            "GET",
            arr_url(config, "radarr", "moviefile"),
            params={"movieId": movie["id"]},
        )
        if len(files) != 1 or normalized_path(files[0].get("path")) != path:
            LOG.warning(
                "SKIP movie %s: file inventory is not exactly the watched file", path
            )
            continue
        plan.append({"service": "radarr", "id": movie["id"], "path": path})
    series = api(sessions["sonarr"], "GET", arr_url(config, "sonarr", "series"))
    file_index = defaultdict(list)
    for show in series:
        files = api(
            sessions["sonarr"],
            "GET",
            arr_url(config, "sonarr", "episodefile"),
            params={"seriesId": show["id"]},
        )
        candidate_files = [
            file for file in files if normalized_path(file.get("path")) in episodes
        ]
        if not candidate_files:
            continue
        show_episodes = api(
            sessions["sonarr"],
            "GET",
            arr_url(config, "sonarr", "episode"),
            params={"seriesId": show["id"]},
        )
        for file in candidate_files:
            assigned = [
                episode
                for episode in show_episodes
                if episode.get("episodeFileId") == file["id"]
            ]
            file_index[normalized_path(file["path"])].append((file, assigned))
    for path, records in episodes.items():
        matches = file_index.get(path, [])
        if len(matches) != 1 or not all(
            eligible(item, cutoff, zone) for item in records
        ):
            LOG.info(
                "SKIP episode file %s: unmatched, ambiguous, or not eligible", path
            )
            continue
        file, assigned = matches[0]
        kodi_numbers = {(item.get("season"), item.get("episode")) for item in records}
        arr_numbers = {
            (item["seasonNumber"], item["episodeNumber"]) for item in assigned
        }
        if not assigned or kodi_numbers != arr_numbers:
            LOG.warning(
                "SKIP episode file %s: Kodi does not exactly cover Sonarr episodes",
                path,
            )
            continue
        plan.append(
            {
                "service": "sonarr",
                "id": file["id"],
                "path": path,
                "episode_ids": [item["id"] for item in assigned],
            }
        )
    return plan


def apply_plan(config, sessions, plan, execute=False, guard=None):
    live = execute and config.get("dry_run", True) is False
    for item in plan:
        service = item["service"]
        LOG.info(
            "%s %s id=%s path=%s",
            "DELETE" if live else "DRY RUN",
            service,
            item["id"],
            item["path"],
        )
        if not live:
            continue
        if guard:
            guard()
        if service == "radarr":
            api(
                sessions[service],
                "DELETE",
                arr_url(config, service, f"movie/{item['id']}"),
                params={"deleteFiles": "true", "addImportExclusion": "true"},
            )
        else:
            api(
                sessions[service],
                "PUT",
                arr_url(config, service, "episode/monitor"),
                payload={"episodeIds": item["episode_ids"], "monitored": False},
            )
            if guard:
                guard()
            api(
                sessions[service],
                "DELETE",
                arr_url(config, service, f"episodefile/{item['id']}"),
            )
    LOG.info(
        "Completed: %d candidates, mode=%s", len(plan), "LIVE" if live else "DRY RUN"
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=Path(__file__).with_name("config.json")
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Allow deletion only when config dry_run is also false",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    sessions = {}
    try:
        config = json.loads(args.config.read_text())
        validate_config(config)
        for service in ("kodi", "sonarr", "radarr"):
            settings = config[service]
            sessions[service] = make_session(
                settings.get("api_key"),
                settings.get("username"),
                settings.get("password"),
            )
        plan = build_plan(config, sessions)
        apply_plan(config, sessions, plan, args.execute)
    except Exception:
        LOG.exception("Cleanup failed. Stopping without further deletions")
        return 1
    finally:
        for session in sessions.values():
            session.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
