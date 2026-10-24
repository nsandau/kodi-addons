# nsandau's Kodi Addons

A Kodi repository for multiple addons. Currently includes **Watched Media Cleanup**, a service addon that removes watched media through Sonarr and Radarr after a configurable retention period. Built for devices such as Chromecast where Kodi is not always running.

**Dry run is the default. Background checks are off until you configure and enable them.** This is an initial release, tested with mocked Kodi and Arr APIs, not yet on a physical Chromecast.

## How it runs

- Starts with Kodi, waits one minute, then checks periodically while Kodi is active. The default interval is 60 minutes.
- If Android suspends or stops Kodi, nothing runs. On resume or restart, checks continue without needing a fixed daily schedule. Overdue media becomes eligible at the next check, not at an exact wall-clock time.
- Skips checks during playback or video-library scanning. Changes to playback, library state or settings interrupt a check before further mutations.
- Reads watch history through Kodi's internal JSON-RPC API. You do not need to enable Kodi's HTTP server.
- Uses Python's standard library. No pip, requests, micromamba, or extra Python addon dependency is needed on the Chromecast.

## Install the Kodi repository

Add this URL in **Settings > File manager > Add source**:

```text
https://nsandau.github.io/kodi-addons/
```

Name the source `nsandau Kodi`.

1. Allow **Settings > System > Add-ons > Unknown sources**.
2. Open **Add-ons > Install from zip file > nsandau Kodi**.
3. Select **repository.nsandau.kodi-1.0.0.zip**. If your skin only shows folders, browse **addons > repository.nsandau.kodi** to find it.
4. Open **Install from repository > nsandau's Kodi Addons > Program add-ons > Watched Media Cleanup > Install**.
5. Open **Watched Media Cleanup > Configure** and follow the dry-run instructions below.

The repository supplies future addon updates through Kodi's normal update mechanism. Allow automatic updates for this addon in Kodi if desired. Installing the repository does not enable live deletion or provide your server credentials.

Targets Python 3 Kodi releases, Kodi 19 and newer. Device compatibility still needs an initial on-device dry run. The installable addon ZIP is also available under [GitHub Releases](https://github.com/nsandau/kodi-addons/releases), if you prefer manual installation.

## Configure and dry run

In addon settings:

1. Enter Sonarr and Radarr base URLs and API keys. Both services are required in this first version. Use LAN addresses reachable from the Chromecast, not `localhost`. URLs must not include `/api/v3`.
2. Enter Kodi and Arr path prefixes for TV and movies. Leave both fields blank for a media type only if paths are already identical.
3. Set retention days, default **3 rolling 24-hour periods**.
4. Keep **Dry run** on and **Allow permanent deletion** off.
5. Turn on **Enable background checks**, save settings, then run **Run dry run now**, or launch the addon from Program add-ons.
6. Open **Show cleanup log** to review matched paths, IDs, cutoff and skipped files. The dry-run request waits until playback and library scanning stop. It never performs mutations, even if live settings are otherwise enabled.

The addon does not read the development machine's `config.json`. Credentials must be entered in Kodi. API keys are masked in the UI but stored unencrypted in Kodi's local addon settings. Do not upload your Kodi profile or settings to GitHub.

### Path mappings for Chromecast

For example:

| Setting | Value |
| --- | --- |
| Kodi TV prefix | `smb://nas/media/TV` |
| Sonarr TV prefix | `/data/tv` |
| Kodi movie prefix | `smb://nas/media/Movies` |
| Radarr movie prefix | `/data/movies` |

Use the actual Kodi library paths and actual Arr file paths. `nfs://` sources also work with explicit mappings. The addon only sends deletion requests to Arr. It never deletes directly through SMB/NFS.

Network sources **must** have an explicit matching mapping. Prefixes match directory boundaries, the longest prefix wins, and conflicting mappings fail. Network host names are compared case-insensitively, but path case is preserved. Credential-bearing URLs, traversal segments, query strings and plugin/stack/archive URLs are rejected. Keep SMB passwords in Kodi's normal source credentials, not in mapping URLs.

Offset-free `lastplayed` values use the Chromecast's system-local timezone. Make sure its timezone and clock are correct. The addon does not require an IANA timezone database on Android.

## Enable real deletion

Only after checking dry-run logs and backups:

- Turn **Dry run** off.
- Turn **Allow permanent deletion** on.

Both settings are required for automated deletion. Turning either safety gate back on stops live operation. The **Run dry run now** action always remains read-only.

### Deletion behavior

- **Radarr:** removes the movie record, deletes its files, and adds an import-list exclusion. Skips movies with multiple managed video files or ambiguous path matches.
- **Sonarr:** unmonitors all episodes assigned to the matched file before deleting that file. Never removes the series itself. Kodi must have watched records covering exactly the season/episode pairs assigned to the file, including multi-episode files.
- Every Kodi record for the matched path must be watched and older than the cutoff. A recent or unwatched duplicate blocks deletion.
- The full plan is read before any mutation. An API error stops that check, with retry after five minutes. Playback or settings interruptions retry after one minute.
- Mutations are not transactional. If Sonarr deletion fails after unmonitoring, episodes can remain unmonitored. Review partial failures before retrying.

Logs go to Kodi's log and a rotating `cleanup.log` in `special://profile/addon_data/service.kodi.watchedcleanup/`. The last successful check's summary is saved in `last_run.json` there. No watch-history cache is maintained.

## Development and release

The checkout lives at `/home/nicolai/sandbox/kodi-addons`, outside xyOps workflows. The cleanup engine remains in `cleanup.py`. Both the Kodi service and optional CLI use it, with no duplicate deletion implementation. Run development tools using micromamba `base`:

```bash
micromamba run -n base python -m unittest discover -s tests -v
micromamba run -n base python -m scripts.build_repository
```

The builder creates a complete static Kodi repository under `dist/site`, including both addon ZIPs, the combined metadata index, its SHA-256 change token, and browsable HTML listings. ZIPs are built from explicit file lists, excluding config, credentials, tests, caches, and Git metadata. Repository output must be empty before building, so stale or unrelated files cannot be published by accident.

GitHub Actions tests and packages each push. A version tag matching the service's `addon.xml`, such as `v0.1.2`, publishes both ZIPs as release assets and deploys the tested repository to GitHub Pages. Untagged development pushes do not update the live repository. The repository addon's own metadata is maintained in `repository/addon.xml` and its package is indexed alongside the service so it can also receive updates.

The repository uses HTTPS. Package-header hash checks are disabled because GitHub Pages does not expose Kodi's required content-hash headers. The SHA-256 index token tells Kodi when to refresh metadata. It is not a package signature.

The optional CLI still supports an ignored `config.json` based on `config.example.json`:

```bash
micromamba run -n base python cleanup.py --config config.json
```

CLI deletion requires both `--execute` and `dry_run: false`. Do not run it concurrently with the addon. The previous xyOps event `kodi_cleanup_daily` is superseded and should remain disabled.

## Cautions

- **Live mode permanently deletes media.** Maintain backups.
- Watch history is local to the Kodi library/profile being read. History from other devices is not automatically merged.
- No cleanup occurs while Kodi is stopped or suspended. This addon cannot wake a Chromecast or bypass Android lifecycle restrictions.
- Guards reduce races but do not make operations transactional across Kodi, Android, Sonarr, and Radarr.
- Kodi library entries are not automatically removed. Use normal Kodi library maintenance after verifying deletion.
- Application deletion may affect metadata or other movie-folder contents. Seeding hardlinks can prevent expected disk-space recovery.

## Kodi documentation

- [Service addons](https://kodi.wiki/view/Service_add-ons)
- [Addon manifest](https://kodi.wiki/view/Addon.xml)
- [Modern addon settings](https://kodi.wiki/view/Add-on_settings_conversion)
- [Kodi addon repositories](https://kodi.wiki/view/Add-on_repositories)
