# spoty
## Installation

From the repository root, install the project and its Streamlit UI extra:

```powershell
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

This editable installation includes the ten `spoty-*` console commands listed
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
required by operations that use Neon. Local listening-event capture does not
require `DATABASE_URL`; database analysis and importing the local CSV do.

### Local listening-event capture

When the Streamlit app is open in a browser, it checks Spotify for recent
listening events every five minutes and saves new events to
`data/listening_events.csv`. The CSV includes the event timestamp and context,
plus the Spotify item needed to recreate the track, album and artist records
during import. Repeated results are skipped using the same `(track_id, played_at)`
key as the database. This file contains personal listening history and Spotify
metadata; keep it local and do not commit it.

The capture runs only while a browser session is active. Spotify returns only
recent history (up to 50 items per request), so the CSV cannot recover listens
that are no longer available from Spotify after the app was closed. The
dashboard and other database-backed features still require Neon.

Set `SPOTY_LISTENING_EVENTS_CSV` to use another CSV path. Relative paths are
resolved from the project root. When Neon is available again, validate the file
without writing first, then apply it:

```powershell
spoty-import-listening-events --dry-run
spoty-import-listening-events --apply
```

To import another file, pass `--csv "path\to\listening_events.csv"`. Import
validates the whole CSV before connecting to Neon, saves catalog records and
listening events transactionally, and skips existing event keys. It is safe to
repeat the import; the local CSV is never deleted or cleared.

### Persisting listening history from GitHub Actions

The Registration workflow can keep its cumulative history in
`data/listening_events.csv.enc`. The public repository contains only AES-256-GCM
ciphertext; the plaintext CSV is restored to the runner's temporary directory
and removed before the workflow completes. The encryption key is never stored
in Git. Create a 32-byte key locally and save it as the repository Actions secret
`SPOTY_LISTENING_EVENTS_KEY`:

```powershell
$key = python -c "import secrets; print(secrets.token_hex(32))"
gh secret set SPOTY_LISTENING_EVENTS_KEY --body "$key"
```

To seed the encrypted file from an existing local CSV, set the same key in the
current PowerShell session, then run:

```powershell
$env:SPOTY_LISTENING_EVENTS_KEY = $key
python -m services.encrypted_listening_events encrypt `
  data\listening_events.csv data\listening_events.csv.enc
git add -- data/listening_events.csv.enc
git commit -m "Add encrypted listening history"
Remove-Item Env:SPOTY_LISTENING_EVENTS_KEY
Remove-Variable key
```

Review the encrypted file before committing it. Do not commit the plaintext
`data/listening_events.csv`; it is ignored by Git. The public `main` history was
rewritten to remove the previously committed plaintext CSV. Existing clones,
forks, and cached copies may still retain it; collaborators should re-clone or
otherwise ensure their branches do not reintroduce the old history.

Pulse's live lookup uses the optional SmarterPlaylists session. For a local
checkout, set `SMARTERPLAYLISTS_SESSION_FILE` or keep the session file at
`.secrets/smarterplaylists-session.json`. For Streamlit Community Cloud, add the
cookie under **App settings → Secrets**; local files are not deployed:

```toml
SMARTERPLAYLISTS_SESSION_COOKIE = "your-authorized-session-cookie"
```

The cookie is private and expires; never commit it to the repository. Refresh it
through an authorized SmarterPlaylists browser session when it expires.
When Pulse successfully looks up a current track, it stores the normalized
SmarterPlaylists metadata in PostgreSQL so later history queries can reuse it.
This live lookup can persist metadata for a track before it exists in the local
catalog; it does not create or modify core track, album, or artist rows.

The installed commands are `spoty-register`, `spoty-currently-playing`,
`spoty-sync-liked`, `spoty-enrich`, `spoty-print-summary`, and
`spoty-print-liked`, plus `spoty-import-smarterplaylists` and
`spoty-refresh-smarterplaylists`, `spoty-genres`, and
`spoty-import-listening-events`.

### Local genre taxonomy

The initial, editable classification lives in
[services/genre_taxonomy_seed.json](services/genre_taxonomy_seed.json). It is a
small editorial starting point, not an official Spotify hierarchy. PostgreSQL
is the operational authority after import; provider metadata is never rewritten.
Only artist `genres` reported by SmarterPlaylists are mapped in this first version.

The seed now includes an explicit editorial review of the 482 initially pending
labels. Musical, regional and contextual families are intentionally separate;
grouped regional labels are not claimed to be synonyms. All original labels
remain in provider metadata. See the generated
[family and subgenre diagrams](GENRE_TAXONOMY.md) for every relationship and mapping.
For a readable, navigable view, open the standalone
[interactive taxonomy viewer](GENRE_TAXONOMY.html). It works offline without a
server, with collapsible branches, label search, direct parents/children,
cross-family navigation and adjustable tree size.

```powershell
.\venv\Scripts\python.exe -m register.migrations
.\venv\Scripts\python.exe -m services.genre_taxonomy import
.\venv\Scripts\python.exe -m services.genre_taxonomy import --apply
.\venv\Scripts\python.exe -m services.genre_taxonomy discover
.\venv\Scripts\python.exe -m services.genre_taxonomy discover --apply
.\venv\Scripts\python.exe -m services.genre_taxonomy diagram --output GENRE_TAXONOMY.md
.\venv\Scripts\python.exe -m services.genre_taxonomy viewer --output GENRE_TAXONOMY.html
```

`spoty-genres` accepts the same arguments after installation. Import without
`--apply` validates the seed offline; discovery without `--apply` reads unknown
labels without writing. Import adds missing genres and relationships and approves
pending labels explicitly listed in the seed. Existing names, approved mappings
and exclusions are preserved. Repeat imports are idempotent; removals from the
file do not delete database rows. New relationships may change historical family
totals, so seed changes should be reviewed before applying.

To expand the classification, add canonical genres with stable `id`, display
`name`, a `parents` list and exact `labels`, then validate and explicitly import.
Use `--seed path/to/seed.json` for another seed. Version `1` identifies the file
format. Multiple parents are allowed; cycles, unknown parents and duplicate
normalized labels are rejected. The importer also validates the combined stored
and incoming graph under a transaction lock. Future graph editors must use the
same validation and lock; foreign keys alone do not prevent multi-node cycles.

Summary defaults to **Generos** (approved labels grouped by root ancestors),
shown on the left of the selector. **Subgeneros** preserves the original label
view. The percentage of included listens appears to the right of the selector;
the included/total counts are available on hover. A listen counts once per family even across
multiple artists or descendants. Families may overlap and percentages can sum
above 100%; percentages use listens with at least one resolved family. The
coverage numerator therefore differs between modes. Pending and excluded labels
do not contribute to family totals. Genre attribution is inherited from artists,
not inferred from the audio of each song.

Discovery is an explicit local operation; run it again after metadata refreshes.
The diagram command works offline and regenerates a family overview, one Mermaid
diagram per root and the associated source labels directly from the JSON. It does
not read or write PostgreSQL. Regenerate it whenever the seed changes; database-only
edits are not reflected in this seed diagram.
The viewer command also works offline and embeds the same seed in a single HTML
file, with no CDN, Mermaid renderer, database connection or external assets. It
uses a lazily expanded tree rather than drawing every relationship at once;
multiple parents remain available in the selected genre's detail panel.
No scheduled job or automatic musical classification is added. Reviewing existing
pending labels in the UI, richer audit history and materialized aggregates are
future extensions, not prerequisites for this first version.

Run one bounded metadata-enrichment batch for a specific source:

```powershell
spoty-enrich --provider lastfm --limit 50
spoty-enrich --provider musicbrainz --limit 50
spoty-enrich --provider acousticbrainz --limit 50
spoty-enrich --provider smarterplaylists --limit 50
```

Only the selected provider's adapter is constructed, pending rows are filtered
for that source, and other providers are not called or written. Explicit Last.fm
requires `LASTFM_API_KEY`. AcousticBrainz uses already-matched MusicBrainz IDs in
the database; it does not call MusicBrainz or trigger its workflow. Tracks without
a matched ID remain pending until a future run.
`spoty-enrich --limit 50` runs all configured sources through the same procedure
(equivalent to `--provider all`). Last.fm is skipped without its key; Smarter
Playlists is skipped without `SMARTERPLAYLISTS_SESSION_COOKIE`,
`SMARTERPLAYLISTS_SESSION_FILE` or `--session-file`. Skips are explicit in the
result, not authentication successes. Explicit source selection never invokes
another source. Summary also uses this shared procedure with a bounded batch.

All four sources support the same execution options:

```powershell
spoty-enrich --provider musicbrainz --all --batch-size 50
spoty-enrich --provider smarterplaylists --all --batch-size 50 --max-minutes 300
spoty-enrich --provider all --all --dry-run
```

`--all` scans an initial snapshot of eligible IDs in bounded batches, without
repeatedly selecting failed IDs during that run. `--dry-run` reads pending counts
without HTTP or writes. `--limit` and `--all` are mutually exclusive. The time
budget is shared across selected sources; a later source is reported as not
started if time is exhausted. In combined execution, MusicBrainz is persisted
before AcousticBrainz selects its candidates. A failed source does not prevent
the other configured sources from being attempted, subject to that budget.

Results use operation `metadata_enrichment`, source identity, status, entity
candidate counts, saved records, safe error categories, scope and dry-run flag.
Combined results include `provider_results` and `skipped_providers`; aggregated
candidates count provider/entity pairs, not distinct songs. Transient errors keep
status `partial` with exit 0; permanent/unknown errors, denied authentication,
missing migrations and unfinished bulk runs return nonzero. Partial results
remain visible in the UI even when retryable errors do not fail an Action.

Sources retain their adapters, tables, validation and provenance. This does not
merge MusicBrainz recordings with artist tags reported by Smarter Playlists or
give unspecified audio features an invented upstream source. Missing-only
selection remains the policy for Last.fm/MusicBrainz/AcousticBrainz; freshness
and backoff remain specific to Smarter Playlists. No schema/data migration is
required for the unified execution contract.

### External cron coordinators

The external daily cron triggers the **Daily** workflow in
`.github/workflows/daily.yml` through `repository_dispatch`:

```json
{"event_type": "run-daily"}
```

Send this body to `POST https://api.github.com/repos/luiscaballeroramos/spoty/dispatches`
using the existing external cron's GitHub authentication. The old event
`run-liked-tracks` is also accepted by Daily, so the current cron continues to work
without changing its request. It now runs the entire daily sequence, not just
liked tracks. Rename the external cron to `cronDaily` and optionally switch its
body to `run-daily`; do not create a second daily cron alongside the existing one.
Publish these workflow changes to the default branch before switching the event.

Daily calls reusable workflows consecutively in this order:

| Order | Workflow | Standalone Trigger |
| --- | --- | --- |
| 1 | Liked Tracks | `workflow_dispatch` |
| 2 | Last.fm Metadata | `workflow_dispatch` / `run-lastfm-enrichment` |
| 3 | MusicBrainz Metadata | `workflow_dispatch` / `run-musicbrainz-enrichment` |
| 4 | AcousticBrainz Metadata | `workflow_dispatch` / `run-acousticbrainz-enrichment` |
| 5 | Smarter Playlists Initial Metadata | `workflow_call` from Daily |

The complete `Smarter Playlists Metadata` workflow remains independently
available through `workflow_dispatch` and `run-smarterplaylists`; it also runs
weekly on Sunday at 03:00 UTC to refresh existing captures.

Liked Tracks downloads the full library before reconciling the database. It
retries rate-limited pages and restarts the scan once if the library changes
during pagination; a repeatedly changing or incomplete scan is never applied.

Each task retains its own timeout, credentials and concurrency group, including
standalone runs. A failed or timed-out task does not prevent subsequent tasks from
running, but the daily run still reports failure. Cancelling Daily stops the chain.
Daily also has its own concurrency group to prevent overlapping daily runs.
GitHub concurrency keeps at most one running and one pending run per group; newer
requests can replace pending requests, so this is not a durable cron backlog.
Individual native GitHub schedules are removed from the Daily tasks to avoid
duplicate daily runs. The complete Smarter Playlists workflow has its own weekly
schedule, separate from Daily's first-attempt-only check.

The separate external cron every 30 minutes still sends `run-registration` and
runs Registration independently. It can overlap with Daily: the daily sequence
is not a global Spotify API lock and does not replace rate-limit handling.
To add a daily task, expose it with `workflow_call` and add a job to Daily using
`needs`, `if: ${{ !cancelled() }}` and `secrets: inherit`, following its existing
jobs. To change its frequency, move its call to the corresponding coordinator
and reconnect the dependency chains; each coordinator needs its own external cron.

Existing provider enrichment scans missing metadata; Smarter Playlists also
revisits expired captures. Configure each task's required repository secrets.
The Summary page also has a manual provider-enrichment action
for batches of 1 to 100 tracks. Before using enrichment, apply the metadata schema
migration explicitly with the command below. Missing tables are reported as
deferred failures of that source; Daily continues the other tasks as configured.

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

## Smarter Playlists metadata workflows

`0004_smarterplaylists_refresh_state` adds per-entity retry state. With an
authorized private session file, the complete refresh scans all due entities in
bounded batches:

```powershell
.\venv\Scripts\python.exe -m _enrichment --provider smarterplaylists --all --batch-size 50 --session-file .secrets/smarterplaylists-session.json
```

The complete `Smarter Playlists Metadata` Action uses `--all`: no total entity
cap. It snapshots all due IDs, including entities with no metadata and entities
whose captures are expired or eligible for retry, then processes at most 50
entities of each type per batch without repeating IDs within that execution.
The snapshot is read before HTTP, not inside a long DB transaction. Manual
Actions runs expose batch size (1..100) and refresh age. Batch size is not the
total number of entities checked.

Daily uses the separate `Smarter Playlists Initial Metadata` Action. It also
processes all eligible IDs in bounded batches, but only entities with neither a
metadata row nor a refresh-attempt row. Entities with prior failed attempts and
entities with existing captures are left for the complete Action; this daily
check does not retry failures or refresh existing metadata. Both Actions share
the same concurrency group to prevent overlapping provider runs.

`--refresh-hours` defaults to 20 to tolerate schedule jitter (1..168 hours).
The complete Action selects new entities before expired ones. Already-fresh
metadata and retry-backoff IDs are skipped, so this is a complete scan of
eligible pending/expired entities, not a forced refetch of every row regardless
of age. `--all --dry-run` reports the full eligible counts without HTTP or writes;
add `--only-unattempted` to preview only entities with no metadata or attempt.

Each batch emits progress JSON. The default `--max-minutes 300` gives the bulk
process a five-hour budget; Actions has a 330-minute timeout. Budget exhaustion
reports `incomplete` and remaining unattempted counts with a nonzero exit. Saved
results are retained and another execution resumes from due database state.
Expired authentication or global rate limits stop subsequent batches. There is
no guarantee that a large library will finish within one provider/time budget.

For a deliberately small test, use `--limit 10`. The unified command defaults to
50 entities per type without `--all`. The old `spoty-refresh-smarterplaylists`
and `python -m _smarterplaylists_refresh` delegate to the unified command, retaining
their historical default of 10 only for compatibility. There is no separate CLI
error policy or authentication procedure in those aliases.

The process closes database connections before HTTP, uses three marked programs
named `Spoty Daily Metadata track/artist/album`, requests `save=false`, and refuses
to overwrite unmarked programs. Missing/invalid responses never erase existing
data or prove `not_found`. Failures have backoff from 15 minutes to 24 hours;
successful partial/complete responses reset failures. The common policy reports
transient errors as partial with exit 0, but expired sessions, permanent failures
and incomplete bulk snapshots fail visibly with sanitized JSON.

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
