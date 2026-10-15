# spoty
## Installation

From the repository root, install the project and its Streamlit UI extra:

```powershell
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

This editable installation includes the eight `spoty-*` console commands listed
below. Start the web app with:

```powershell
.\venv\Scripts\python.exe -m streamlit run streamlit_app.py
```

## Configuration

Create a `.env` file in the project root with:

```dotenv
SPOTIFY_CLIENT_ID=tu_client_id
SPOTIFY_CLIENT_SECRET=tu_client_secret
SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888/
DATABASE_URL=postgresql://user:password@ep-example.us-east-2.aws.neon.tech/spoty?sslmode=require
SPOTY_DISPLAY_TIMEZONE=Europe/Rome
# Optional: enable Last.fm enrichment
LASTFM_API_KEY=tu_api_key
```

`DATABASE_URL` must point to a Neon PostgreSQL host. Spotify credentials are
required only by operations that use Spotify; database analysis can run without
them.

The installed commands are `spoty-register`, `spoty-currently-playing`,
`spoty-sync-liked`, `spoty-enrich`, `spoty-print-summary`, and
`spoty-print-liked`, plus `spoty-import-smarterplaylists` and
`spoty-refresh-smarterplaylists`.

Run one bounded metadata-enrichment batch for a specific source:

```powershell
spoty-enrich --provider lastfm --limit 50
spoty-enrich --provider musicbrainz --limit 50
spoty-enrich --provider acousticbrainz --limit 50
```

Only the selected provider's adapter is constructed, pending rows are filtered
for that source, and other providers are not called or written. Explicit Last.fm
requires `LASTFM_API_KEY`. AcousticBrainz uses already-matched MusicBrainz IDs in
the database; it does not call MusicBrainz or trigger its workflow. Tracks without
a matched ID remain pending until a future run. Schedules do not guarantee order.
The manual combined command `spoty-enrich --limit 50` remains available (equivalent
to `--provider all`); Summary keeps its existing combined manual action.

The repository defines five independent GitHub Actions workflows:

| Workflow | Daily Schedule (UTC) | Manual Trigger |
| --- | --- | --- |
| Liked Tracks | 04:00 | `workflow_dispatch` / `run-liked-tracks` |
| Last.fm Metadata | 04:10 | `workflow_dispatch` / `run-lastfm-enrichment` |
| MusicBrainz Metadata | 04:20 | `workflow_dispatch` / `run-musicbrainz-enrichment` |
| AcousticBrainz Metadata | 04:30 | `workflow_dispatch` / `run-acousticbrainz-enrichment` |
| Smarter Playlists Metadata | 04:40 | `workflow_dispatch` / `run-smarterplaylists` |

Each has its own concurrency group, timeout and credentials, without `needs`
dependencies. Schedules only become active after publishing the workflows to the
default branch and configuring their secrets. Existing provider enrichment scans
missing metadata; Smarter Playlists also revisits expired captures.
The Summary page also has a manual provider-enrichment action
for batches of 1 to 100 tracks. Before using enrichment, apply the metadata schema
migration explicitly with the command below. Until the migration exists, the
scheduled job reports enrichment as deferred and leaves liked-song sync intact.

## Smarter Playlists capture imports

Validate an existing capture without database access or Spotify authentication:

```powershell
.\venv\Scripts\python.exe -m _smarterplaylists experiments/smarterplaylists/report.json --dry-run
```

Dry-run is the default. It validates original responses, not just `summary.json`,
and does not check whether the entities exist in the local database. Captures
must contain completed results without playlist output, consistent Spotify IDs,
no conflicting values, and timezone-aware capture timestamps.

Database import is a separate, explicit operation. After authorizing and applying
`0003_smarterplaylists_metadata` through the migration command below, run:

```powershell
.\venv\Scripts\python.exe -m _smarterplaylists experiments/smarterplaylists/report.json --apply
```

After reinstalling the project, the equivalent console command is
`spoty-import-smarterplaylists PATH [--dry-run | --apply]`.
An import verifies that all referenced entities already exist and writes the
capture atomically into provider-owned tables. Repeating the same capture is a
no-op; older or partial captures cannot overwrite newer fields or erase absent
ones. Per-field provenance distinguishes capture time from unknown upstream
cache age. No core tables, playlists, provider jobs or UI behavior are modified.

The migration and `--apply` command modify the configured database: do not run
them against production without an approved pilot. Failed validation/imports emit a
nonzero exit and sanitized JSON diagnostics; rolled-back attempts do not create
successful import records.

The opt-in views `smarterplaylists_track_features`,
`smarterplaylists_artist_details` and `smarterplaylists_album_details` expose the
new metadata without changing existing analysis queries. Audio values in the
track view prefer this resource and fall back to core data, with provenance for
each field. Popularity and followers from different sources remain separate.

## Daily Smarter Playlists Refresh

`0004_smarterplaylists_refresh_state` adds per-entity retry state. With an
authorized private session file, scan all due entities in bounded batches:

```powershell
.\venv\Scripts\python.exe -m _smarterplaylists_refresh --all --batch-size 50 --session-file .secrets/smarterplaylists-session.json
```

The daily Action uses `--all`: no total entity cap. It snapshots all due IDs,
then processes at most 50 entities of each type per batch without repeating IDs
within that execution. The snapshot is read before HTTP, not inside a long DB
transaction. Manual Actions runs expose batch size (1..100) and refresh age.
Batch size is not the total number of entities checked.

`--refresh-hours` defaults to 20 to tolerate daily schedule jitter (1..168 hours).
New entities precede expired ones. Already-fresh metadata and retry-backoff IDs
are skipped, so this is a complete scan of eligible pending/expired entities,
not a forced refetch of every row regardless of age. `--all --dry-run` reports
the full eligible counts without HTTP or writes.

Each batch emits progress JSON. The default `--max-minutes 300` gives the bulk
process a five-hour budget; Actions has a 330-minute timeout. Budget exhaustion
reports `incomplete` and remaining unattempted counts with a nonzero exit. Saved
results are retained and another execution resumes from due database state.
Expired authentication or global rate limits stop subsequent batches. There is
no guarantee that a large library will finish within one provider/time budget.

For a deliberately small test, the original `--limit 10` mode remains available
(10 per type by default when `--all` is absent). It cannot be combined with `--all`.

The process closes database connections before HTTP, uses three marked programs
named `Spoty Daily Metadata track/artist/album`, requests `save=false`, and refuses
to overwrite unmarked programs. Missing/invalid responses never erase existing
data or prove `not_found`. Failures have backoff from 15 minutes to 24 hours;
successful partial/complete responses reset failures. Expired sessions or partial
batches return nonzero exits with sanitized JSON, not silent success.

For Actions, configure repository secrets `DATABASE_URL` and
`SMARTERPLAYLISTS_SESSION_COOKIE`. The cookie is the private Smarter Playlists
`session` value, not a Spotify OAuth token. Never put it in source code, artifacts,
cache, command arguments or chat. The local `.secrets/` folder is ignored by Git.
With GitHub CLI installed and authenticated, transfer it without printing it:

```powershell
(Get-Content .secrets/smarterplaylists-session.json -Raw | ConvertFrom-Json).session_cookie | gh secret set SMARTERPLAYLISTS_SESSION_COOKIE
```

Sessions expire. Renew through the authorized browser and replace the secret as
needed; there is no login bypass or permanent authorization guarantee. Requests
disable redirects and never send the cookie to another host. `cache_mode:
no-cache` does not prove the age of upstream metadata; upstream timestamps remain
unknown. Raw-response cleanup is not automatic.

On 2026-10-15, migrations 0003/0004 and the example capture were applied to Neon
after real-SQL validation in rolled-back schemas. One-per-type and ten-per-type
refreshes completed without errors. This does not mean the complete library is
backfilled or GitHub schedules are published/active. No listening events or liked
memberships were created by these operations.

For development, install `.[ui,test]` for YAML workflow checks. The opt-in
PostgreSQL test requires `SPOTY_TEST_DATABASE_URL`; it creates and rolls back its
own isolated schema. Ordinary unit tests do not connect to a database.

## Database schema

See [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md) for the entity-relationship diagram,
column types, and current database constraints.

Schema changes are versioned and are not run by normal application startup. For
a new database, or to register the existing schema before upgrading the app,
run the migration command explicitly:

```powershell
.\venv\Scripts\python.exe -m register.migrations
```

The runner verifies required columns and the unique listening-event key before
recording a migration version. It stops rather than silently altering an
existing table whose schema does not match.

`SPOTY_DISPLAY_TIMEZONE` accepts an IANA timezone name and defaults to
`Europe/Rome`, which follows Italy's daylight-saving rules. Set it to
`Europe/Madrid` to use Spain's local time instead.

## Operation logging

Spotify requests, authentication, response validation, and local liked-track
writes emit JSON diagnostics through the `spoty.operations` logger. They appear
on stderr in the terminal running Streamlit or a script, and in GitHub Actions
job output. No external logging service or log file is required.

Events include `operation`, `provider`, `run_id`, `duration_ms`, error category,
and HTTP status or `retry_after` when available. `run_id` identifies an operation
or grouped incident within a process, not an entire GitHub Actions job.
Raw exceptions, request arguments, tokens, OAuth callback codes, DSNs, and
response payloads are excluded from these diagnostic events.

At the default `INFO` level, the logger reports a failure once per operation and
error category. Repeated failures are counted silently until a successful,
validated response emits `operation_recovered` with the same ID and count.
Normal successful operations are only visible at `DEBUG`. Logs do not add UI alerts.

To enable detailed operation events, set the level before starting the process:

```powershell
$env:SPOTY_LOG_LEVEL = "DEBUG"
.\venv\Scripts\python.exe -m streamlit run streamlit_app.py
```

Use `INFO` for normal operation. Changing the level requires restarting the
process. Existing progress prints and third-party logs are separate from this
logger; this step does not replace all repository output or change retry policy.
