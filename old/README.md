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

### Consolidated summary and history (local prototype)

`spoty.services.summary_queries.SummaryQueryService(snapshot)` reads a single
account and commit, without SQL, credential loading or Streamlit configuration.
All storage I/O goes through the supplied snapshot's `DataStore`.
It preserves the existing counts/signature, recent listening-event rows and
whole-history play counts/ranking, artist lookup, metadata coverage and genre
label/family result contracts. Pulse history reuses `PulseQueryService`.
History ties use ascending track ID for reproducibility; the existing SQL only
orders history by time. Limits must be integers (not booleans); history clamps
to at least one row and genres to 1..20, as in the existing query service.

Pending inbox batches are excluded; report `snapshot.pending_batches()` separately.
`top_listened_genres(grouping="families")` resolves approved SmarterPlaylists
genre mappings to all root ancestors. Each track counts once per root even
across multiple artists, labels or ancestry paths. Overlap between roots is
intentional; percentages can sum above 100%. Snapshots older than v4 raise an
explicit taxonomy-unavailable error for family queries, not a misleading empty
result; other queries remain compatible. The optional Summary-only mode below
uses this reader; the default application still uses SQL.
Manifest v5/v6 coverage uses the lightweight projection below, not raw providers.
Legacy coverage and genre queries still read their provider CSV partitions.
Full-export and Cloud performance remain uncertified.

Validate without external services:

```powershell
.\venv\Scripts\python.exe -m unittest tests.test_csv_genre_taxonomy tests.test_csv_summary_queries tests.test_summary_queries -q
```

### Synthetic CSV/Git benchmark (Windows)

Run reproducible measurements without credentials, existing repositories or
provider calls. Each run creates and removes its own temporary bare Git repo:

```powershell
.\venv\Scripts\python.exe -m examples.benchmark_csv --output benchmark-compact.json
.\venv\Scripts\python.exe -m examples.benchmark_csv --raw-bytes-per-track 75000 --output benchmark-heavy.json
```

Output paths must not exist. JSON goes to stdout and optionally the new file.
The heavy run requires substantial free RAM/disk; it is not part of unit tests.
Both defaults use 7,297 tracks, 11,071 listens, 3,004 likes, 2,433 artists and
730 albums, followed by 50 listens to existing tracks and then 50 listens to
50 new tracks using an existing artist/album. It then inserts 50 Last.fm results
and updates 50 existing SmarterPlaylists tracks (energy/tempo), using the
`incremental_events` size for all four increments. CLI options allow smaller profiles.
Generated raw text is deterministic and varied, not real provider data.

Initial baseline, before v5 optimizations, on Windows/Python 3.13.12,
2026-11-09 (one run per profile):

| Measurement | Compact | 75,000 raw bytes/track |
|---|---:|---:|
| Initial CSV partitions | 3.71 MiB | 525.64 MiB |
| Initial Git directory (loose objects) | 0.40 MiB | 306.32 MiB |
| Load snapshot manifest | 1.13 s | 2.97 s |
| Library counts | 5.31 s | 6.76 s |
| Recent history | 3.11 s | 3.48 s |
| Genre labels / families | 3.59 / 3.75 s | 3.88 / 3.93 s |
| Track metadata coverage | 3.25 s | 33.38 s |
| Consolidate 50 new listens | 21.56 s | 112.02 s |
| Coverage sampled Python RSS peak | 56.55 MiB | 695.75 MiB |
| Consolidation sampled Python RSS peak | 75.32 MiB | 1,262.65 MiB |
| Git directory growth for the increment | 15.82 KiB | 16.83 KiB |

Verified exact catalog/listen/like counts, all-listen overlapping genre weights,
history length, metadata coverage, 50 accepted events, empty final inbox and
unchanged historical snapshot (11,071 vs 11,121 final listens).
Counts issued 96 Git subprocess calls in both runs. Heavy coverage read
549,543,541 bytes despite only returning coverage statistics.

**Not production/cloud certification.** Timings exclude Streamlit rendering and
network; operations share OS disk cache. RSS is sampled every 50 ms for Python
only, excluding Git child memory and possibly missing short peaks. The report
also labels the cumulative process-lifetime peak separately. No repack/gc or
long-lived history is modeled. Raw hexadecimal text, empty field provenance,
minimal taxonomy and missing other-provider payloads do not recreate the full
847 MiB historical export or its compressibility. Small Git growth here means
unchanged blobs are reused, **not** that consolidation avoids full reads/rebuilds.

After v5, the same profiles were repeated (same machine/day, single runs):

| Measurement | Compact v5 | Heavy v5 |
|---|---:|---:|
| Track metadata coverage | 1.54 s | 1.65 s |
| Coverage bytes read | 2.76 MiB | 2.76 MiB |
| Coverage sampled Python RSS peak | 51.21 MiB | 59.14 MiB |
| Consolidate 50 existing-track listens | 6.28 s | 6.34 s |
| Consolidation sampled Python RSS peak | 53.61 MiB | 59.05 MiB |
| Initial CSV partitions | 5.66 MiB | 527.59 MiB |
| Initial Git directory | 0.49 MiB | 306.42 MiB |
| Git directory growth for the increment | 16.78 KiB | 17.78 KiB |

Coverage now reads 2,895,233 bytes instead of 549,543,541 heavy-profile bytes.
Listening-only consolidation reads about 1.52 MiB and retains raw/projection
descriptors byte-for-byte; tests forbid raw reads on that path. Timings are
observations, not statistically isolated speedup factors: machine load/cache
also vary. Initial heavy snapshot creation still peaks near 1.2 GiB Python RSS.
At this stage counts still launched 96 Git subprocesses; the grouped-read
measurements below supersede that count.

```powershell
.\venv\Scripts\python.exe -m unittest tests.test_csv_benchmark -q
```

### Grouped local Git reads and new-track measurements

`LocalGitStore.read_many(commit, paths)` reads ordered blobs with one
`git cat-file --batch` subprocess per group. `Snapshot.rows` uses the optional
`GroupedReadStore` capability; the existing GitHub/in-memory stores without
that capability retain their single-read behavior. No persistent Git helper,
cache, protocol/schema version change or remote request was introduced.

Groups contain at most 20 MiB of **declared** partition data. This bounds
grouping for valid snapshots, not total Python memory or unexpectedly oversized
blobs. Binary/newline/empty content is parsed by Git's explicit blob length,
not by splitting payload lines. Commit/path validation, order and all existing
size/SHA-256/row-count/unique-key checks remain. Missing blobs, non-blob objects,
malformed/truncated responses or grouped I/O failures raise errors: there is no
silent retry via single reads. Read-only mode allows only the exact
`cat-file --batch` command in addition to its previous read operations.

Repeated synthetic profiles, Windows/Python 3.13.12, same single-run limitations:

| Operation | Git calls before / now | Compact grouped | Heavy grouped |
|---|---:|---:|---:|
| Library counts | 96 / 5 | 0.60 s | 1.34 s |
| Recent history | 64 / 3 | 0.34 s | 1.01 s |
| Genre labels | 64 / 3 | 0.38 s | 1.38 s |
| Genre families | 71 / 6 | 0.48 s | 1.50 s |
| Track coverage | 32 / 2 | 0.46 s | 1.91 s |
| Consolidate 50 known-track listens | 145 / 69 | 3.10 s | 5.21 s |
| Consolidate 50 **new tracks** | not previously measured / 226 or 273 | 10.80 s | 98.11 s |

Coverage still reads exactly 2,895,233 bytes; a lower process count does not
guarantee every timing improves (the heavy coverage run was 1.65 s before).
The new-track heavy rebuild read 553,294,532 bytes and reached about 1.25 GiB
sampled Python RSS. Grouping does **not** solve full-rebuild memory/latency.
New tracks have existing artist metadata and no track enrichment; final checks
require 7,347 tracks, 11,171 listens, 7,297 enriched tracks, all listens
classified by family, no pending inbox and both earlier snapshots unchanged.
Git growth from the new-track batch was about 219–220 KiB (loose objects).

Catalog expansion is now incremental in v6 (see below). Provider-metadata
measurements follow below; longer history, rendering, network and repeated
statistical trials remain pending. No data migration or production activation.

```powershell
.\venv\Scripts\python.exe -m unittest tests.test_csv_grouped_reads tests.test_csv_benchmark -q
```

### Coverage projection and listening-only consolidation (v5/v6)

Manifest v5 adds `metadata_coverage`, with entity type/ID, provider and sorted
nonempty top-level field names only. It preserves zero/false, excludes JSON/SQL
null, empty arrays/objects and space-only text, and retains existing provider
status and local-catalog filtering. Raw values are not copied into the projection.
Coverage retains its exact total/enriched/source/field result contract.

All full snapshot writers (consolidation, taxonomy edits, synthetic benchmark)
derive this projection atomically. v1-v4 snapshots remain readable: coverage
explicitly uses the legacy provider-backed computation when the old format
has no projection. Current full publications upgrade v1-v5 snapshots to v6.
Malformed/corrupt projection data raises an error, never a fallback to raw.
Receipt CSV remains v3; taxonomy introduced in v4 is unchanged.

On v6, a consolidation selecting **only listening batches**, with no new tracks,
artists or albums and the default partition limit, rewrites events/receipts only.
Catalog and other datasets retain the existing descriptor/path/checksum and Git
bytes. Archive/quarantine/inbox changes and the new manifest remain atomic,
with bounded conflict retries. Favorites, mixed listening/metadata/favorites
selections, v1-v5 manifests or custom partition limits still use the full rebuild
path. Metadata-only selections now have their own incremental path below.

**Catalog growth in v6:** the CSV dataset shapes are unchanged from v5, but
`metadata_coverage` now also retains valid provider fields for uncataloged entities.
Summary excludes these until their track/artist/album enters the catalog;
Spotify coverage referencing a missing catalog entity remains an error.
A listening-only catalog expansion rewrites catalog, events, receipts and the
two lightweight coverage/audio projections, reusing provider datasets unchanged.
Existing orphan provider coverage becomes visible without raw reads; new catalog
track names replace orphan audio names, preserving audio values. Existing catalog
rows remain insert-only, as before. No audio entry is invented without metadata.

v5 projections omitted orphan provider entries. They remain readable with their
original strict semantics; the first effective consolidation/full publication
reconstructs the projection once before enabling the v6 incremental path.
Historical commits are never modified.

Repeated synthetic profiles (2026-11-09), same inputs as the grouped baseline:

| 50 new tracks | Compact v6 | Heavy v6 | Previous heavy grouped |
|---|---:|---:|---:|
| Consolidation | 12.93 s | 13.32 s | 98.11 s |
| Sampled Python RSS | 73.41 MiB | 84.32 MiB | ~1.25 GiB |
| Bytes read | 4,123,357 | 4,126,428 | 553,294,532 |
| Git calls | 153 | 153 | 273 |

Both runs verify 7,347 tracks, 11,171 listens, 3,004 likes, 7,297 enriched tracks,
all listens classified, empty inbox and both earlier snapshots unchanged.
Compact timing did **not** improve over 10.80 s; one run per version/profile is
not an isolated statistical speedup. Heavy initial publication still took
88.67 s/~1.20 GiB Python RSS. Full rebuilds remain unoptimized. No Git-child RSS,
HTTP, rendering or cold-cache measurements. Provider-metadata measurements below
do not establish Streamlit Cloud readiness either.

**Integrity scope:** reused descriptor structure is validated but untouched
blobs are not reread or rehashed during incremental consolidation. This is
not a full-repository integrity audit. Each later `Snapshot.rows(dataset)` still
checks size/SHA-256/row counts/unique keys and fails on damaged/missing data;
existing snapshots remain pinned. Projection reads validate their own CSV and
semantic shape, not the current contents of unrelated raw blobs. Full rebuilds
still read/validate all datasets and can be memory-intensive.
No new cache, external dependency, production activation or remote write.

```powershell
.\venv\Scripts\python.exe -m unittest tests.test_csv_coverage_projection tests.test_csv_summary_queries tests.test_csv_benchmark -q
```

### Dataset-scoped metadata consolidation (v6)

With **only metadata batches** selected on v6 and the default partition limit,
consolidation reads and rewrites only their provider datasets, receipts and
coverage. It reads catalogs to validate SmarterPlaylists references and coverage,
and still checks pending favorite revisions. Unrelated datasets retain their
descriptors and pinned Git bytes; this has the integrity scope described above.

SmarterPlaylists also updates its import ledger, and its track updates rebuild
`track_audio`. Artist/album or Last.fm/MusicBrainz/AcousticBrainz updates do not
read/rebuild the track SmarterPlaylists dataset or audio. Coverage replaces only
the affected entity/provider sources, preserving all other sources and valid
orphan fields. Invalid coverage does not silently fall back to raw providers.
No CSV shape or manifest-version change is needed.

The existing merge/provenance, equal-time conflicts, older captures, insert-only
provider results, independent analysis levels and capture idempotency remain
unchanged. Staging copies only the affected maps: rows are replaced, not mutated.
Batch failure discards staged changes, preserves original capture in quarantine,
and reports the existing error receipt. Storage/integrity errors block publication;
conflicts retry from a fresh commit. Invalid envelopes do not trigger raw reads.
Legacy/custom-limit/mixed-kind selections retain the full path.

Synthetic Windows measurements, 7,297 initial tracks, followed by the same
listening increments above and then 50 Last.fm inserts / 50 SmarterPlaylists track
updates. One run per profile/version; memory samples include Python only:

| Metadata consolidation | Heavy before | Heavy dataset-scoped | Compact dataset-scoped |
|---|---:|---:|---:|
| Last.fm time | 38.95 s | 3.03 s | 2.90 s |
| Last.fm sampled RSS | ~1.25 GiB | 73.69 MiB | 70.16 MiB |
| Last.fm bytes read | 553,291,891 | 3,143,790 | 3,140,719 |
| Last.fm Git calls | 289 | 57 | 57 |
| SmarterPlaylists time | 43.61 s | 38.87 s | 4.96 s |
| SmarterPlaylists sampled RSS | ~1.25 GiB | ~1.23 GiB | 88.48 MiB |
| SmarterPlaylists bytes read | 553,321,245 | 552,336,226 | 5,054,795 |
| SmarterPlaylists Git calls | 305 | 139 | 92 |

Both updates save exactly 50 records and preserve counts (7,347 tracks,
11,171 listens, 3,004 likes), enrichment totals and earlier snapshots. The
SmarterPlaylists phase verifies audio values and catalog-name precedence.
There is no compact metadata baseline; prior compact runs did not time these
phases. This is not a statistical speedup claim or Cloud certification.

These are historical dataset-scoped measurements. The disk-staged path below
subsequently reduces large-provider memory, at an observed latency cost.

```powershell
.\venv\Scripts\python.exe -m unittest tests.test_csv_metadata_incremental tests.test_csv_metadata_prototype tests.test_csv_coverage_projection tests.test_csv_benchmark -q
```

### Temporary disk staging for large metadata updates

Metadata-only v6 consolidations now validate provider partitions incrementally,
stage their rows in a **temporary SQLite file**, merge only batch-targeted rows,
and spool the resulting CSV partitions to temporary files. SQLite is Python's
standard-library scratch space, not a new persistent database/backend; CSV/Git
remains authoritative. No dependency was added. Temporary files are removed on
success, errors and conflict retries. Disk exhaustion aborts without consuming
inbox or publishing a new head.

Coverage is derived during the scan; audio updates use the existing shared
projection helper. Every affected provider partition is still scanned/validated
and repartitioned. Old commits and unrelated provider blobs remain unchanged.
`Snapshot.iter_rows` retains per-partition integrity and cross-partition duplicate
checks; callers must exhaust it for complete dataset validation. Existing
`Snapshot.rows` keeps its eager result contract.

Data stores now accept a read-only `Mapping` of changes, preserving ordinary
dictionary callers. Local and GitHub publication sort **keys**, not all content
values, so disk-spooled publication loads one change at a time. Tests guard this
against accidental re-materialization. GitHub reference updates remain non-force.

Measured final heavy run: 50 SPL updates, 552,336,226 bytes read, 139 Git calls,
**237.52 MiB sampled Python RSS**, versus ~1.23 GiB previously. The measured
target of <300 MiB passed; this is not a universal bound. Catalogs, keys,
coverage, import ledger, audio and batch-targeted records remain in memory.
Memory still depends on partition sizes and the size/number of targeted records.
Initial publication, mixed batches and full rebuilds are not covered by this path.

**Trade-off:** the final verified heavy run took **68.56 s**, not an improvement
over 38.87 s. Earlier disk-staging runs measured 70.11 s/~237 MiB and
96.79 s/~240 MiB; shared-machine timings vary substantially.
The final compact run took 12.96 s/~80.51 MiB for SPL and 7.15 s/~75.93 MiB for
Last.fm; the verified heavy Last.fm measured 4.92 s/~80.56 MiB. Single runs on a shared machine,
without cold-cache isolation; do not infer statistically isolated speedups.
Temporary-disk peak usage was not measured. These results do not certify Cloud.
All final counts, coverage, audio and historical snapshot checks still pass.

### Acquisition, recovery and opt-in operations

`spoty.services.acquisition.CsvAcquisitionService` accepts injected existing-compatible
clients. It durably publishes recent listening, normalized enrichment and SPL
captures; reserves/traverses/submits complete favorite snapshots; and can
coordinate an explicit favorite change. `change_favorite` first journals a
stable request ID, dispatches at most once, then records the acknowledged result.
Remote or local errors leave a blocking pending intent; a retry never repeats
the mutation. `recover_pending_favorite` observes Spotify only after the caller
establishes that the original request has settled, and never replays like/unlike.
Interrupted downloads leave their durable reservation. Unknown/incomplete
pagination never replaces the favorite replica.
No SQL, implicit credential loading, network retries or consolidation occurs
inside these acquisition methods. Provider selection/scheduling and production
client wiring are still separate work.

Run the complete synthetic acquisition/recovery/query/audit demonstration:

```powershell
.\venv\Scripts\python.exe -m examples.csv_pipeline_demo
```

It creates/removes its own temporary bare repo and uses fake Spotify reads,
including idempotent captures and pinned historical queries. Tests forbid
HTTP/SQL and verify cleanup after failure.

Operations on an **existing synthetic-only** bare repo:

```powershell
.\venv\Scripts\python.exe -m spoty.cli.operations status --repository C:\synthetic-data\spoty-demo.git --account synthetic-demo
.\venv\Scripts\python.exe -m spoty.cli.operations audit --repository C:\synthetic-data\spoty-demo.git --account synthetic-demo
.\venv\Scripts\python.exe -m spoty.cli.operations consolidate --repository C:\synthetic-data\spoty-demo.git --account synthetic-demo --write-synthetic
```

Status/audit are read-only; audit exhausts streamed datasets and verifies
inbox/archive identities and favorite revisions, not quarantine contents.
Consolidation needs the explicit flag. Repositories containing non-synthetic
accounts are rejected; these commands do not initialize/clone/back up a repo.

The GitHub demo retains strict protections by default. Explicit
`--protection-policy allow-unprotected-private` expresses the previously accepted
Free risk, reports missing protections/residual risks and never calls them enforced.
Public/archived/wrong repositories, unverifiable rule responses and generic HTTP
errors still fail. On 2026-11-10 the actual 403 was diagnosed as GitHub's exact
private-repository **plan upgrade required** response, not a content-permission
denial or rate limit. Only that classified 403 at the branch-rules endpoint is
accepted by the explicit Free-risk policy after verifying private repository/head.
It reports `rules_verification=unavailable_on_current_plan`, both protections as
`null` (unknown), `branch_rules_verified=false`, and both residual risks.
Strict policy still refuses this response, and permission/rate-limit/other
403s and ruleset-detail errors remain blocking. No permission/plan was changed.

Real remote verification completed in `synthetic-validation-20261110-0945`:
listening/metadata/favorite publication and consolidation, inbox/archive retry
idempotency, pinned history and stale-head rejection. A fresh process then
recovered a pending synthetic unlike from a **fake settled observation**, applied
a Last.fm metadata-only update through disk staging, and verified zero pending
batches and historical favorite preservation. No Spotify mutation was executed.
This tests stale-head protection, not simultaneous multiwriter stress or
protection against external history rewinds/deletions.
CLI errors now report safe error type/HTTP status without response bodies/tokens.

`.github/workflows/csv-validation.yml` is a new **manual-only synthetic validation**
workflow: contents-read permission, no data-repository checkout, data secrets,
schedules or production capture. It has not been dispatched/pushed. Existing
production workflows and default SQL app behavior are unchanged.

Latest affected regression run: **288 tests passed**, including isolated Streamlit,
acquisition, staging, GitHub transport/preflight and synthetic backup tests.
Final focused revalidation: 126 tests passed, including an additional full-audio
oracle regression. These overlapping runs are not summed.
The full repository suite and real migration/deployment have not been certified.
See [the execution report](INFORME-REFACTORIZACION-2026-11-09.md) for gates and remaining work.

### Optional Streamlit Summary, Metadata, Pulse and History: local CSV, read-only

The existing entrypoint can run a **Summary, Metadata, historical Pulse and
listening history**
local preview. It skips production configuration, Streamlit secrets, OAuth,
production navigation, live playback, automatic captures and registration.
In CSV mode, the summary also avoids
importing the SQL summary service and Spotify synchronization/error modules;
the existing SQL path loads them only when selected. It does not initialize,
fetch, consolidate, back up or publish a repository. Only an **existing local bare Git repository**
with a consolidated account snapshot is accepted. `LocalGitStore(read_only=True)`
rejects publication and mutating Git commands in this mode.

Set process environment variables **before** launching; this early-start mode
does not read `.env` or Streamlit secrets. On this non-personal computer use
synthetic data only, not a clone/backup of the real data repository:

```powershell
$env:SPOTY_SUMMARY_BACKEND = "csv_local"
$env:SPOTY_CSV_REPOSITORY = "C:\synthetic-data\spoty-demo.git"
$env:SPOTY_CSV_ACCOUNT = "account-demo"
.\venv\Scripts\python.exe -m streamlit run streamlit_app.py
```

Replace the example path/account with an existing synthetic consolidated repo.
Optional `SPOTY_CSV_COMMIT` pins a full 40-character SHA-1; otherwise each rerun
resolves local `main` once. Counts, genres and pending batches use that same
commit. Account, commit and consolidation timestamp are displayed; pending
inbox batches remain outside totals. No refresh automatically fetches remote data.
The family/label selector retains existing behavior. The Metadata view reuses
the CSV coverage projection for tracks/artists/albums, including provider/field
charts, without refreshing any external provider. Older snapshots lacking
taxonomy show a genre error but can still display labels.
Pulse offers historical audio analysis from consolidated listening events and
the `track_audio` projection, with 1h/6h/1d/all-time windows and chart/hexagon
views. It does not show the current playing track, poll Spotify, or capture/
refresh data; un-consolidated inbox batches remain excluded.
History lists recent consolidated listening events, with UTC timestamps, track/
artist/album details, play counts and ranking. It is not interactive playback
and does not show liked status or submit like/unlike actions. Pending inbox events
are excluded.

SQL remains the default. Missing/invalid paths, account, commit, corrupt
partitions or unsupported backends produce UI incident notices, **never**
automatic SQL fallback. For the normal app, stop the preview and start a fresh
process with `SPOTY_SUMMARY_BACKEND=sql` (or remove that variable). Live
reproduction controls and the remaining production pages and synchronization still use
the existing implementation; they are intentionally not available in the
read-only preview.
This step does not configure Streamlit Cloud or authorize GitHub access.

```powershell
.\venv\Scripts\python.exe -m unittest tests.test_csv_summary_view tests.test_csv_summary_queries tests.test_summary_queries -q
```

AppTest covers all four views through the real entrypoint with temporary synthetic
Git/CSV data, including no SQL/HTTP, guarded production imports, forbidden writes,
selectors/charts, errors, pinned commits and separate pending counts. Pulse also
verifies that the CSV mode does not import Spotify playback/auth or metadata
refresh integrations. History verifies that recent events and artist names come
from the pinned snapshot without loading playback, Spotify, SQL or mutation code.
Regression checks verify that the SQL summary still renders with its existing
query service while the CSV entrypoint never imports SQL registration or Spotify
synchronization modules.

### CSV taxonomy (local prototype)

`spoty.services.genre_taxonomy.GenreTaxonomyRepository(store, account)` provides:

- `import_seed(seed)`: validate the existing JSON seed format and the combined
  graph; add missing genres/relations and approve new or pending mappings only.
  Existing names, approved mappings and excluded decisions remain unchanged.
- `discover_labels()`: read-only preview of unknown labels from cataloged artists.
  `discover_labels(apply=True)` explicitly saves them as **pending**, never
  auto-classifying them.

Import accepts the seed from the existing pure `services.genre_taxonomy.load_seed`
helper. Neither method loads credentials or contacts providers itself; reads
and explicit writes use the supplied `DataStore`. Tests use only temporary local
Git repositories and an in-memory store. Do not supply the real GitHub store
until the separate remote-validation/publication step is approved.

Manifest v4 introduced `genres`, `genre_relations` and `genre_label_mappings`, retaining
identifiers, status, provenance and UTC creation timestamps. Receipt CSV stays
v3. Current writers publish v6 with orphan-aware coverage and retain v1-v5
reader compatibility, without modifying old commits.
Each imported canonical seed is retained under the account's
`taxonomy/imports/<sha256>.json`. Reimport is idempotent and preserves creation
timestamps. Conflicts retry from a fresh commit with bounded attempts; other
storage errors propagate. No provider metadata or pending capture is consumed
by taxonomy edits. Both taxonomy edits and consolidation use the same snapshot
serializer and validate graph references, cycles and mapping status/targets.

These are explicit repository operations, not a new inbox batch kind or a
production CLI replacement. The existing `python -m services.genre_taxonomy`
commands below still target SQL. A manual CSV review editor, real export
migration and full-volume memory/time measurements remain pending.

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
3. `CsvAcquisitionService.change_favorite(...)` is the tested orchestration
   path: it journals first, dispatches only for a newly `reserved` request,
   then resolves a confirmed successful result. Any exception/unconfirmed
   response leaves the journal pending; an idempotent retry does not dispatch.
   After an uncertain request is known to have settled, `recover_pending_favorite`
   queries Spotify's current state and uses `resolution="reconciled"`; it does
   not replay an old intent.
4. `resolve_change` is a lower-level coordinator API; prefer the acquisition
   orchestration above. Recovery requires the caller to ensure no original
   request remains in flight: an elapsed timeout alone does not prove that.
   `recover_pending_favorite` records an observed state as reconciled.
5. Resolution atomically clears the pending flag and publishes the observed
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

To inspect an existing data repository without writing to GitHub, run:

```powershell
.\venv\Scripts\python.exe -m examples.inspect_spoty_data --env-file .env
```

The command defaults to `luiscaballeroramos/spoty-data` on `main`; pass
`--owner`, `--repository`, `--branch`, or `--account` to select another target.
It lists account keys, manifest-declared dataset row counts and sizes, pending,
archived and quarantined batch counts, and the branch head. It does not print
individual listening or metadata rows, and it performs only GitHub reads.
Dataset row counts come from snapshot manifests; CSV partition contents are
not downloaded or re-verified by this inventory.

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
their empty bypass lists by default. The explicit `allow-unprotected-private`
policy also accepts the exact plan-upgrade denial described above, reporting
unknown protections rather than verified/absent rules. Other denials block. The
publication flag requires a new `synthetic-` namespace and leaves its captures
available for inspection. It tests listening, metadata and synthetic favorite
observations, consolidation, retries, pinned reads and stale-head rejection.
No Spotify calls, real migration, force push or deletion of test history occurs.
The CLI reports sanitized failures; no credentials or response bodies are printed.
Reading rule details may require additional permission depending on GitHub's
repository policy; arbitrary permission failures are never treated as accepted risk.

To continue a completed synthetic demo with observation-only favorite recovery
and an incremental provider update (writes that synthetic account only):

```powershell
.\venv\Scripts\python.exe -m examples.github_data_demo --owner OWNER --repository DATA_REPO --env-file .env --protection-policy allow-unprotected-private --verify-synthetic-recovery --account synthetic-EXISTING-DEMO
```

This is a one-shot verification of the known demo fixture, not a general-purpose
repair command or permission to operate on real accounts.

The tests use an in-memory GitHub object server and mocked HTTP responses,
including all three capture kinds, concurrency and lost acknowledgments.
Real synthetic GitHub reads/writes were verified; branch protections remain
unverifiable on this plan. Community Cloud connectivity, API quotas under load,
large-export memory/cost and performance remain unverified. Cross-repository
writes will need a fine-grained PAT or GitHub App credential restricted to the
private data repository (`Contents: write`); a code-repository `GITHUB_TOKEN`
does not grant those permissions. Token choice and workflow location remain
deployment decisions. There is no per-folder token isolation.

### Offline backup for `spoty-data`

`examples.backup_spoty_data` makes an incremental bare Git mirror of the private
data repository at an explicit local destination. Before each fetch it creates
a permanent local checkpoint of the previous `main` tip. It never pushes or
prunes refs; if `main` disappears or changes during a fetch, the command fails
and keeps the prior checkpoint. This is a recovery copy, not branch protection;
it contains personal data and should live on an encrypted disk that can be
disconnected between backups.

Create the destination's parent directory on the encrypted disk, then
authenticate Git Credential Manager with a dedicated, read-only credential
restricted to `spoty-data` (`Contents: read`). Do not put a token in a URL,
command, script, `.env`, or this repository. Verify Git can read the branch,
then run the backup from the repository root:

```powershell
git ls-remote https://github.com/luiscaballeroramos/spoty-data.git refs/heads/main
.\venv\Scripts\python.exe -m examples.backup_spoty_data --destination "E:\private-backups\spoty-data.git"
```

Replace the sample path with the selected directory; the destination must be
outside and must not contain the application repository. The command prints
the saved checkpoint and exits nonzero on failure. A daily Task Scheduler job
can run the same Python command as the logged-in Windows user, with the
repository root as **Start in**; check its last-run result and keep the disk
available. Disconnect the encrypted disk after a successful run. Until a
destination is selected and this manual run succeeds, no task is scheduled.
The maximum recovery-point gap is at least the interval between successful
runs, and grows if the PC or disk is unavailable.

The prototype does not migrate the existing real CSV/COPY exports,
wire all Streamlit pages/Actions or certify performance on the
full export. It currently loads/repartitions the small prototype datasets in
memory; scaling and incremental partition updates remain separate work.
Provider HTTP acquisition, candidate selection, refresh scheduling/retries and
metadata-coverage UI wiring have not yet been migrated.

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

### Opt-in synthetic local writer

The CSV preview remains read-only. A separate local writer is available only
for explicitly opted-in synthetic tests/experiments:

```powershell
$env:SPOTY_CSV_ENABLE_SYNTHETIC_WRITES = "1"
$env:SPOTY_CSV_REPOSITORY = "C:\synthetic-data\spoty-demo.git"
$env:SPOTY_CSV_ACCOUNT = "synthetic-demo"
```

`load_synthetic_writable_store()` and `SyntheticLocalGitStore` both require the
exact opt-in value `1`. The target must be an existing bare repository with no
Git remotes, containing only `accounts/synthetic-*` data and the selected
synthetic account; writes are restricted to that account's path. This guard is
not a production authorization system. No Streamlit controls use this writer,
no Spotify credentials or calls are involved, and the default backend remains
SQL. Do not point it at real or production data.

The local integration test simulates a like that reaches the provider but whose
response is lost, reopens the Git store, verifies an idempotent retry does not
repeat the mutation, then recovers through a fake settled-state observation.
Any future UI must show a durable pending operation separately from consolidated
favorites, never infer settlement from elapsed time, and require explicit
confirmation before observation-based recovery. Recovery observes state; it
must not replay the original like/unlike. Mutation controls remain disabled
until real-provider wiring and the pending/recovery UX are separately reviewed.

The CSV preview shows the favorite journal status on Summary, Metadata, Pulse,
and Listening History. A pending intent is presented as a warning, distinct
from inbox batch counts and the selected consolidated snapshot. Its status is
read from the current local Git `HEAD`, even when historical data is pinned to
an older commit. In the actual app entry point, the warning offers no action;
an empty journal is also shown explicitly. This read-only status check uses no
SQL or provider calls.

The status component accepts an injected recovery callback for isolated
integration tests. If supplied, it enables recovery only after a checkbox
confirms the displayed request has settled; it passes that request ID to the
callback so a changed journal is rejected before provider observation. On
success it reports that the observed state is in the inbox, not yet consolidated.
`AuthenticatedSpotifyFavoriteRecovery` is the opt-in adapter: it loads only
Spotify secrets from Streamlit secrets when recovery is requested, then
delegates the read-only status call to the existing `SpotifyClient`. It checks
the durable request ID before constructing the client and exposes only the
favorite-status operation to the recovery service. The Spotify client no longer
imports the legacy registration/DB module until artist-specific methods need
it. The Streamlit entry point does not inject this adapter, so the real CSV UI
continues to show no recovery controls. End-to-end auth/deployment behavior
against Spotify has not been exercised.
