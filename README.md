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
3. Select **repository.nsandau.kodi-1.0.1.zip**. If your skin only shows folders, browse **addons > repository.nsandau.kodi** to find it.
4. Open **Install from repository > nsandau's Kodi Addons > Program add-ons > Watched Media Cleanup > Install**.
5. Open **Watched Media Cleanup > Configure** and follow the dry-run instructions below.

The repository supplies future addon updates through Kodi's normal update mechanism. Allow automatic updates for this addon in Kodi if desired. Installing the repository does not enable live deletion or provide your server credentials.

Targets Python 3 Kodi releases, Kodi 19 and newer. Device compatibility still needs an initial on-device dry run. The installable addon ZIP is also available under [GitHub Releases](https://github.com/nsandau/kodi-addons/releases), if you prefer manual installation.

## Configure and dry run

In addon settings:

1. Enter Sonarr and Radarr base URLs and API keys. Both services are required for cleanup. Use LAN addresses reachable from the Chromecast, not `localhost`. URLs must not include `/api/v3`. Press **OK once to save new or changed URLs/API keys**, then reopen settings and use the separate **Test Sonarr connection** and **Test Radarr connection** buttons. Kodi scripts can only read saved credentials. Each performs a read-only `/api/v3/system/status` request and verifies the application and version. The other service, path mappings, and background checks need not be configured to test one server.
2. Under Path mappings, select **Kodi TV folder** and **Kodi movie folder** to select the corresponding SMB/NFS folders from Kodi's video sources. Choose the media root, not a particular show or movie folder. Use **Choose Sonarr root folder from server...** and **Choose Radarr root folder from server...** to select roots returned by `/api/v3/rootfolder`. These server paths do not need to be browsable on the Chromecast. Arr path fields remain manually editable. Leave both fields blank for a media type only if paths are already identical.
3. Set retention days, default **3 rolling 24-hour periods**.
4. Keep **Dry run** on and **Allow permanent deletion** off.
5. Turn on **Enable background checks**, save settings, then run **Run dry run now**, or launch the addon from Program add-ons.
6. Open **Show cleanup log** to review matched paths, IDs, cutoff and skipped files. The dry-run request waits until playback and library scanning stop. It never performs mutations, even if live settings are otherwise enabled.

The Kodi folder fields use native folder settings. Both those controls and the Arr root selectors leave the same settings dialog and tab open. Select all paths, then press **OK** to save the form. Cancelling a picker leaves that mapping unchanged. Connection results and setup errors use non-modal notifications, not confirmation boxes. Even a single Arr root requires explicit selection. No background guesses, automatic mapping changes, or media mutations are made by these buttons.

After upgrading from 0.2.1 or earlier, reselect any paths that failed to persist, press **OK**, then reopen Path mappings to verify them. No credential reset is needed. The addon keeps the same setting IDs.

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

To start immediately, select **Run live cleanup now...** under Cleanup and confirm the permanent-deletion prompt. The button saves/closes settings intentionally before confirmation so the worker reads committed settings. Background checks must be enabled, **Dry run** must be off, and **Allow permanent deletion** must be on. It does not bypass retention or path/episode safety checks, and the action runs through the same single service worker, never as a second cleanup process.

A manual live request waits for playback/scanning to stop, expires after five minutes, and is cancelled if settings change before execution. A failure or interruption clears the manual live request rather than automatically retrying it. Background checks retain their normal schedule and live/dry settings. This button is not a one-off override of dry-run mode.

Both settings are required for automated deletion. Turning either safety gate back on stops live operation. The **Run dry run now** action always remains read-only.

### Deletion behavior

- **Radarr:** removes the movie record, deletes its files, and adds an import-list exclusion. Skips movies with multiple managed video files or ambiguous path matches.
- **Sonarr:** unmonitors all episodes assigned to the matched file before deleting that file. Never removes the series itself. Kodi must have watched records covering exactly the season/episode pairs assigned to the file, including multi-episode files.
- Every Kodi record for the matched path must be watched and older than the cutoff. A recent or unwatched duplicate blocks deletion.
- The full plan is read before any mutation. An API error stops that check, with retry after five minutes. Playback or settings interruptions retry after one minute.
- Mutations are not transactional. If Sonarr deletion fails after unmonitoring, episodes can remain unmonitored. Review partial failures before retrying.

### Verbose watch-history logs

Enable **Verbose watch-history logging** under Cleanup, save settings, then select **Run dry run now**. No Kodi-wide debug logging is needed. Each check writes one `WATCH` line per Kodi library movie or episode record, including unwatched, unsupported, unmapped, and duplicate-file records. Files absent from Kodi's library cannot have watch history reported.

Each line shows the record ID, file path, mapped Arr path, play count, raw Kodi `lastplayed`, its interpreted UTC timestamp, retention status, and path-mapping status. Missing or invalid timestamps are marked `unknown`, not invented. Retention status explains whether the record is old enough, not whether deletion is authorized. Exact Arr matches, complete episode coverage, and both live-mode safety gates still apply. The UTC cutoff and configured timezone are included in a per-check header. On Kodi, `local` means the device's system timezone.

Verbose logging defaults to off and does not change the cleanup plan or make extra API requests. With it off, routine per-file `WATCH` and ordinary `SKIP` details are suppressed. Normal logs retain per-check counts, deletion/dry-run audit lines, safety warnings, and errors. Each check starts with `Planning cleanup: verbose_logs=on/off` using the saved setting. Save the toggle with OK before running a new check. A check already in progress keeps its starting mode. Logs are appended, so older verbose entries remain visible after the toggle is turned off. Rejected URLs are redacted so credentials and query tokens are not exposed. Ordinary file paths and watch timestamps are private viewing-history data, so review logs before sharing. The optional CLI accepts `"verbose_logs": true` in its config.

Large libraries can fill the rotating logs. The in-addon viewer displays the latest 64,000 characters of the current log, not the entire library history. To inspect more, copy `cleanup.log`, `cleanup.log.1`, and `cleanup.log.2` from the addon profile before another check rotates them.

Logs go to Kodi's log and a rotating `cleanup.log` in `special://profile/addon_data/service.kodi.watchedcleanup/`. The last successful check's summary is saved in `last_run.json` there. No watch-history cache is maintained.

## Development and release

The checkout lives at `/home/nicolai/sandbox/kodi-addons`, outside xyOps workflows. The cleanup engine remains in `cleanup.py`. Both the Kodi service and optional CLI use it, with no duplicate deletion implementation. Run development tools using micromamba `base`:

```bash
micromamba run -n base python -m unittest discover -s tests -v
micromamba run -n base python -m scripts.build_repository
```

The builder creates a complete static Kodi repository under `dist/site`, including both addon ZIPs, the combined metadata index, its SHA-256 change token, and browsable HTML listings. ZIPs are built from explicit file lists, excluding config, credentials, tests, caches, and Git metadata. Repository output must be empty before building, so stale or unrelated files cannot be published by accident.

GitHub Actions tests and packages each push. A version tag matching the service's `addon.xml`, such as `v0.2.4`, publishes both ZIPs as release assets and deploys the tested repository to GitHub Pages. Untagged development pushes do not update the live repository. The repository addon's own metadata is maintained in `repository/addon.xml` and its package is indexed alongside the service so it can also receive updates.

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
