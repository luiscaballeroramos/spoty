# spoty
## CSV/Git migration prototype

The production app and workflows have **not** switched away from Neon yet.
The independent `spoty` package prototypes listening, metadata and serialized favorites using
versioned CSV and a local bare Git data repository. It imports no application
configuration, reads no `.env`, calls no providers and requires only Python
3.10+ and Git. Run the synthetic demo from the repository root:

```powershell
.\venv\Scripts\python.exe -m examples.csv_git_demo
.\venv\Scripts\python.exe -m unittest tests.test_csv_git_prototype tests.test_csv_metadata_prototype tests.test_csv_favorites_prototype
```

The demo creates and cleans up its own temporary repository. It publishes a
durable listening, metadata and favorite batches, consolidates catalog, history and audio,
archives the original captures, and checks idempotent retries. No real data is used.
For persistent **local** experiments, `LocalGitStore.initialize(Path(...))`
requires a new or empty directory; existing application worktrees are refused.
The local demo never uses the network. A separate GitHub API adapter is available,
validated offline with simulated responses; no real repository has been configured.

`ListeningBatch.from_items(account, producer, captured_at, items)` validates
Spotify-shaped input and preserves its complete JSON payload. `publish_batch`
returns `saved` or `already_saved` only after local Git persistence; failures
raise `StorageError` or `PublicationConflict`, never a success-shaped fallback.
After an uncertain failure, retry the same batch (including its original
capture timestamp). `consolidate` publishes catalog, history, archived captures,
receipts and a checksummed manifest atomically. Invalid batches are preserved
in quarantine and reported through receipts and result counters. Summary/history
read consolidated data only; pending counts count **batches**, not distinct plays.

CSV v1 uses UTF-8, LF, fixed headers and JSON cells for structured values.
`\N` represents a null cell; literal initial backslashes are escaped by adding
one backslash. Empty strings, zero, false, null cells and JSON null remain
distinct (`JSON_NULL` represents a top-level JSON null in the Python codec).
Partitions are limited to exactly 20 MiB including headers/escaping, with 16
stable SHA-256 key buckets; history and receipts also partition by UTC month.
Readers pin a commit and verify file hashes,
sizes, row counts and unique keys.

Metadata uses the same publication/archive/quarantine flow:

- `MetadataBatch.from_smarterplaylists(account, producer, captured_at, captures,
  live_track=True)` accepts a completed single-track live lookup even before
  catalog capture. With the default `live_track=False`, entities must exist in
  the consolidated catalog. Unknown fields remain in the original capture.
- Normalization and per-field timestamp/provenance merging reuse the existing
  **pure**, offline `services.smarterplaylists` functions. Partial and older
  captures cannot erase newer fields; equal-time conflicting fields reject the
  whole batch. Nested artist/album values do not create separate metadata rows.
- `MetadataBatch.from_enrichment_records(account, producer, fetched_at, records)`
  accepts the normalized outputs of the existing Last.fm, MusicBrainz and
  AcousticBrainz enrichment service, adding the acquisition timestamp. It
  validates identifiers, provider states and content consistency, but does not
  perform new matching or normalize arbitrary raw provider responses.
  These providers retain **first-consolidated-record wins**, including negative
  matches; AcousticBrainz low/high levels have independent keys. Every incoming
  accepted capture remains archived even when its stored record is a duplicate.
- Receipts count `saved_metadata`/`duplicate_metadata` separately from listening
  events. Manifest v2 declares the added datasets and receipt CSV v2. Existing
  listening-only manifest v1 snapshots remain readable, with new metadata
  datasets empty and historical metadata counters zero. Consolidation upgrades
  them atomically, without changing the historical commit.
- `PulseQueryService(snapshot).recent_audio_history(...)` returns Pulse's current
  and history row shape, using the small `track_audio` projection instead of
  reading raw metadata. Its rows work with the unchanged `services.pulse.build_pulse`.
  This is a consolidated reader, **not** a replacement for live Spotify lookup
  or immediate Streamlit session display. Pending metadata batches can be counted
  using `snapshot.pending_batches("metadata_capture")`.

### Serialized favorites (local coordination prototype)

Spotify remains the authority; `liked_tracks` is a consolidated replica.
`FavoritesCoordinator(store, account, producer)` adds small durable coordination
commits, rather than treating unordered inbox captures as sufficient:

1. Generate and retain a stable request ID (32 lowercase hexadecimal characters).
   `begin_change(request_id, track_id, desired_liked, started_at)` must persist
   **before** any remote mutation. Only a returned `reserved` result authorizes
   the caller to dispatch once. `already_pending`/`already_resolved` retries must
   **not** dispatch another mutation.
2. A pending request blocks another favorite change and a new full download for
   that account. `pending_change()` lets a new process recover its journal.
   There is no time-based expiry or automatic unlock.
3. After the request is known to have settled, `resolve_change(request_id,
   observed_liked, observed_at, resolution="confirmed", remote_settled=True)`
   records a successful acknowledged state. For an uncertain request, query
   Spotify's current state and use `resolution="reconciled"`; do not replay an
   old intent. The caller must ensure no original request remains in flight:
   an elapsed timeout alone does not prove that. The prototype does not
   implement this remote recovery or dispatch calls itself.
4. Resolution atomically clears the pending flag and publishes the observed
   state as an ordered CSV inbox batch. Failure leaves the durable operation
   pending, or an idempotently recoverable completed commit. `saved` means
   **locally durable**, not already consolidated or remotely atomic.

For full downloads, call `reserve_snapshot(sync_id, started_at)` **before**
acquisition, then `submit_snapshot(sync_id, completed_at, pages)` with the full
Spotify-shaped page responses. A changed revision, newer reservation or pending
mutation makes the capture `superseded`: originals are retained in the
coordination journal, but the replica is not replaced. Incomplete, inconsistent,
duplicate or unavailable-track pagination is recorded as `invalid` and raises
`FavoriteCaptureRejected`. A complete empty library may remove all favorites.
Stable totals and complete pagination cannot detect every concurrent external
Spotify edit (for example, a same-size swap); later reconciliation is still needed.

Successful snapshots and observations receive consecutive revisions in the
same commit as their inbox batch and checksummed reference. Consolidation
applies those revisions in order, independently of timestamps or batch hashes.
Missing/corrupt/ungated favorite revisions raise `FavoriteRevisionBlocked` and
leave the snapshot/inbox unchanged; they cannot be skipped to invent a replica.
This deliberately blocks account consolidation pending repair, unlike isolated
invalid listening/metadata captures, which can be quarantined independently.
`LocalGitStore.publish_batch` refuses favorites; they must pass through the
coordinator. This is a cooperating-code invariant, not token-level authorization.

Manifest/receipt v3 add `liked_tracks`, `favorites_replica_state`, and separate
`saved_favorites`/`removed_favorites` counters. Snapshots v1/v2 remain readable
and upgrade atomically on consolidation; older favorite datasets/counters are
empty/zero because those prototypes did not contain them.
`FavoriteQueryService(snapshot).liked_ids()` and `.is_liked(track_id)` read only
the pinned consolidated replica, not pending changes or live Spotify state.

### GitHub data transport (offline-validated)

`GitHubGitStore(owner, repository, transport, branch="main")` implements the
same `DataStore` contract as `LocalGitStore`. Snapshot readers, consolidation,
batch publication and favorite coordination use that contract; domain/services
do not construct HTTP clients or read credentials.

For a future explicitly approved deployment, compose it with
`RequestsGitHubTransport(token_provider)`, where `token_provider` is a callable
that obtains the credential from the deployment's secrets. Do not paste tokens
into code, CSV, commit messages, URLs or the chat. This step does not read
environment configuration, create a repository, initialize its branch or call
GitHub with real data.

- Only an **existing, non-archived private repository** with an initialized data
  branch is accepted; the application's own repository is refused. The repository
  identity/privacy is checked on each `head()` and before publication.
- Writes create base64 blobs, a tree based on the pinned parent and one commit,
  then advance the reference with `force=False`. Files, deletion entries,
  manifests and favorite coordination become visible together. Missing-file
  deletion and unchanged bytes do not create unnecessary commits.
- GitHub's reference API provides **fast-forward**, not the local backend's
  literal expected-SHA compare-and-swap. With a branch that only advances,
  sibling commits lose the race without overwriting each other; callers retry
  boundedly from the new head. Protect the data branch against force pushes,
  deletion and out-of-band history rewinds. That deployment rule is required:
  this adapter alone cannot enforce it or claim CAS across arbitrary rewinds.
- Reads use immutable commit/tree/blob SHA identities, never moving download
  URLs. Trees are traversed one directory at a time with cached immutable
  descriptors; incomplete/truncated trees are errors, not partial results.
  Blob sizes and Git hashes are checked, in addition to snapshot SHA-256 checks.
  Per-file limit remains 20 MiB; HTTP responses and tree traversal are bounded.
- HTTP uses the fixed GitHub API origin, TLS, a bounded timeout and no redirects
  or environment proxies/netrc. Credentials are supplied at request time.
  Transport errors omit response bodies/tokens. Auth, rate-limit and server
  failures raise explicit errors; `GitHubHTTPError` retains status and rate-limit
  headers for the eventual UI/job policy, without automatically sleeping/retrying.
- A timeout is an **uncertain result**, not success. Retry the same batch/request
  identity to discover already-published inbox/archive/journal entries.
  A `422` reference error is a concurrency conflict only when the branch actually
  moved; otherwise permission/validation failures remain explicit.

Run the offline transport suite:

```powershell
.\venv\Scripts\python.exe -m unittest tests.test_github_data_store
```

For an approved real **synthetic-only** check, an explicit local credential
loader is available. It reads only `GH_TOKEN`/`GITHUB_TOKEN` from the supplied
file, supports quoted values, and never loads Spotify/Neon settings or populates
the process environment. Environment tokens take precedence. The file must
remain ignored and untracked; do not paste its contents into chat or commit it.

```powershell
# Read-only preflight: private repository, main and active rules without bypass.
.\venv\Scripts\python.exe -m examples.github_data_demo --owner OWNER --repository DATA_REPO --env-file .env

# Only after preflight succeeds and publication is explicitly approved:
.\venv\Scripts\python.exe -m examples.github_data_demo --owner OWNER --repository DATA_REPO --env-file .env --publish-synthetic --account synthetic-UNIQUE-ID
```

The preflight verifies active repository-level deletion/force-push rules and
their empty bypass lists; denied or unverifiable rules stop the check. The
publication flag requires a new `synthetic-` namespace and leaves its captures
available for inspection. It tests listening, metadata and synthetic favorite
observations, consolidation, retries, pinned reads and stale-head rejection.
No Spotify calls, real migration, force push or deletion of test history occurs.
The CLI reports sanitized failures; no credentials or response bodies are printed.
Reading rule details may require additional permission depending on GitHub's
repository policy; failures must be resolved before publication, not bypassed.

The tests use an in-memory GitHub object server and mocked HTTP responses,
including all three capture kinds, concurrency and lost acknowledgments.
Real GitHub permissions/branch rules, Community Cloud connectivity, API quotas,
large-export memory/cost and performance remain unverified. Cross-repository
writes will need a fine-grained PAT or GitHub App credential restricted to the
private data repository (`Contents: write`); a code-repository `GITHUB_TOKEN`
does not grant those permissions. Token choice and workflow location remain
deployment decisions. There is no per-folder token isolation.

The prototype does not migrate the existing CSV/COPY formats or taxonomy,
wire Streamlit/Actions or certify performance on the
full export. It currently loads/repartitions the small prototype datasets in
memory; scaling and incremental partition updates remain separate work.
Provider HTTP acquisition, candidate selection, refresh scheduling/retries and
metadata-coverage UI queries have not yet been migrated.

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

### Responding to an exposed Neon credential

Treat any committed database password as compromised, even after removing it
from the latest source code.

1. Open the [Neon Console](https://console.neon.tech/) directly, locate the
   affected project and branch, and reset the affected database role's password.
   Update or revoke that role on any other branches where its old password is
   still valid.
2. Replace `DATABASE_URL` wherever the application runs: the local `.env`,
   GitHub Actions repository/environment secrets, Streamlit deployment secrets,
   and any other deployment configuration. Restart running applications to load
   the replacement value. Never paste the new password into source code,
   issues, chat, or logs.
3. Verify that the application connects using the replacement credential and
   that the old password no longer authenticates. Review available access logs
   and database changes for unexpected activity.
4. After revocation, coordinate any Git history cleanup with collaborators.
   Deleting a file or making a new commit does not remove the secret from older
   commits, forks, or clones; rewriting history is not a substitute for rotation.

Local `.env` files and `.streamlit/secrets.toml` are ignored by Git. This does
not protect files already tracked, so review staged changes before committing.

### One-off Neon CSV export

Run from the repository root using the existing `DATABASE_URL` in `.env`:

```powershell
.\venv\Scripts\python.exe export_neon_csv.py
```

This standalone script needs no Spotify credentials or extra packages. It creates
a new `database_backups/neon/csv_<UTC timestamp>_<unique suffix>/` directory
(ignored by Git), with a CSV per table, grouped by schema. `manifest.json` marks
completion and lists the original table names and file paths. Without that
manifest the export is incomplete; partial files are retained for inspection.
An optional `--output "D:\private-backups\neon-export"` chooses a new directory;
existing directories are never overwritten. Custom locations are not automatically
ignored by Git. Keep these files private: they contain your database contents.

The export uses one connection and one read-only, repeatable-read transaction,
streaming native PostgreSQL COPY output directly to disk. It reads each table
once without row counts, sorting, pagination, parallel queries or automatic
retries. Partitioned tables include their partitions once. All non-system schemas
in the connected database are included, but not views, foreign tables, other
databases or other Neon branches. Insufficient permissions or row-level security
restrictions fail the export instead of silently reporting a complete backup.
Avoid concurrent schema migrations during export.

CSV files use UTF-8, headers and PostgreSQL CSV escaping (including embedded
newlines and JSON). NULL is an unquoted empty field; an empty string is quoted.
CSV is a data export, not a restorable schema backup: it omits table definitions,
indexes, constraints, sequence state and roles. Use `pg_dump` when those are needed.

Manual CSV export offers no inherent reduction in database reads or transfer.
This script still wakes a suspended Neon compute, transfers all table data and
keeps its snapshot open until finished. Run once with a reliable connection and
preferably little write activity; no full export can avoid reading the data.
No live database export is performed by tests.

### Local listening-event capture

When the Streamlit app is open in a browser, it checks Spotify for recent
listening events every five minutes and saves new events to
`data/listening_events.csv`, inside the repository and not excluded by Git. The
CSV includes the event timestamp and
context, plus the Spotify item needed to recreate the track, album and artist
records during import. Repeated results are skipped using the same
`(track_id, played_at)` key as the database. Review the CSV before committing:
it contains your personal listening history and Spotify metadata.

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
