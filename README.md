# spoty
## Run update listening avents
.\update_local_listening_events.ps1(Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned) ; (& c:\Users\Luis\spoty\.venv\Scripts\Activate.ps1)


## Register listening events

`publish_listening_events.py` fetches the latest 50 Spotify plays and sends flat
event rows to the `spoty-data` Action. It does not import code from `old/`.
No intermediate CSV is needed. The capture also supplies tracks, artists and
albums from the same Spotify response, without extra API requests.

Only the necessary legacy pieces have been recovered: `spotifyapi/spotifyclient.py`
for refresh-token authentication and recent plays, `spotifyapi/timestamps.py`
for UTC timestamp parsing, and `config.py` for loading the root `.env`.
The client is trimmed to this operation; database registration, playback,
Streamlit, OAuth browser login and other legacy features are not included.

Configure the root `.env` or environment with `SPOTIFY_CLIENT_ID`,
`SPOTIFY_CLIENT_SECRET`, `SPOTIFY_REFRESH_TOKEN`, and `SPOTY_DATA_TOKEN`.
The GitHub token needs Contents write access to `spoty-data`.

Run from this repository root:

```powershell
.\venv\Scripts\python.exe publish_listening_events.py
```

The script requires `requests`. A successful dispatch means GitHub accepted
the request; check the `spoty-data` Action for the final CSV update.

## Manual or external cron registration

The `Publish listening events` Action has no built-in schedule. Run it manually
through `Run workflow`, or have an external cron trigger its `workflow_dispatch`
event. The Python script also runs once and exits; it has no internal timer.

For example, an authenticated GitHub CLI session with Actions write permission
on `spoty` can trigger the workflow from a cron job or manually:

```powershell
gh workflow run registration.yml --repo luiscaballeroramos/spoty --ref main
```

Use the default branch instead of `main` if it differs. Alternatively, the cron
can call GitHub's `POST /repos/luiscaballeroramos/spoty/actions/workflows/registration.yml/dispatches`
API with a JSON body containing `{"ref":"main"}` and a token with Actions write
permission. The external cron owns the capture frequency.

Add the four credentials above as Actions secrets in `spoty`. The refresh token
must authorize `user-read-recently-played`. Use a fine-grained GitHub token with
Contents write permission only for `spoty-data`; the built-in `GITHUB_TOKEN`
cannot dispatch to another repository.

Publish both repositories' workflows to their default branches and enable
Actions. Each run fetches the latest 50 plays and sends them to `spoty-data`,
whose Action appends new rows and commits the CSV. An accepted dispatch is not
confirmation of a completed save: check both Actions for failures.

Only `spoty-data` owns the CSV schema and writes CSV. `register/listeningevent.py`
only validates and serializes events for dispatch. There is no local CSV buffer.
More than 50 plays between successful captures can leave gaps; the chosen capture
frequency does not guarantee complete history.

## Common data entry

`publish_data.py` sends a source label and any combination of `events`, `tracks`,
`artists` and `albums`. Future source adapters can use this entry independently
of listening capture. IDs must be Spotify IDs.

`register/spotify_data.py` extracts the catalog using the original database
columns. Lists are JSON arrays, missing values are null, and album `tracks`
contains only known songs.

Large payloads are split into bounded dispatches, each committed separately.
If delivery fails, retry the same data: merging is idempotent. Acceptance by
GitHub is not confirmation that the data Action completed.

## Local read-only Streamlit dashboard

The initial dashboard reads `listening_events.csv`, `tracks.csv`, `artists.csv`
and `albums.csv` from one immutable commit of `spoty-data`. It resolves the
configured branch once, verifies each downloaded Git blob, validates CSV
headers and duplicate keys, and caches the result for five minutes. It does not
write to `spoty-data` or use Spotify credentials.

The summary shows registered plays and catalog totals for songs, albums and
artists, with a single song counter.

The dashboard uses the responsive layout API developed in the adjacent
`streamlit-template` project, included locally in [layouts.py](layouts.py) so
the app does not depend on a sibling folder at runtime. In landscape, metrics
and the growth chart use the full width, the calendar and top songs share a
row, and recent plays use the full width. In portrait, those regions stack
automatically. The layout is fluid so the full dashboard scrolls as a page.

The combined chart shows daily registered plays as bars and cumulative distinct
listened songs, artists and albums as lines, with independent y-axes. The bar
scale stays fixed from zero to the highest daily count in the full history.
The line scale spans the minimum and maximum across all three series in the
selected period, with labeled major marks at multiples of 1000 and thinner,
fainter unlabeled grid lines every 250 within those limits, regardless of the
selected period. Horizontal grid lines follow only the right axis, not the
daily-play scale. If all values are equal, its upper limit is
extended by one to keep a visible scale. The lines
count entities from their first recorded listen across the full history, even
when a shorter period is selected. Missing track metadata prevents the related
artists and albums from being counted; catalog totals above may therefore differ.
The period selector uses UTC dates through today, calendar months for the
one-, six- and twelve-month windows, and seven days including today for the
last-seven-days window. Each bar spans its full UTC day.
Days without saved plays are shown as zero, not proof of no actual listening.

The horizontal top-10 chart counts plays across the entire saved history,
shows its UTC date range, and labels each song with its artists and play count.
Hover over a bar to read the full song and artist names.

The monthly listening calendar opens on the latest month with saved plays.
Use the arrows beside the month/year heading or the separate **Año** and
**Mes (UTC)** selectors to browse the history, including months with no records.
Navigation stops at the first and last months in the saved history.
Each cell represents a UTC day. Colors range from green for one registered
play to red for the highest daily count in the entire saved history. All months
and years share this scale and legend, including empty months; when the
historical maximum is one, it stays green.
Hover over a day for its date and exact count. Black cells mean
zero saved records, not necessarily zero actual listening: capture gaps may exist.

Install the dashboard dependencies and start it from the repository root:

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements-dashboard.txt
.\venv\Scripts\python.exe -m streamlit run streamlit_app.py
```

For a public `spoty-data` repository, the token can be omitted. For a private
repository, use a fine-grained token limited to **Contents: read** on
`spoty-data`; do not use the write token from the capture workflow. Locally,
save it in the ignored `.streamlit/secrets.toml` file:

```toml
SPOTY_DATA_READ_TOKEN = "your read-only GitHub token"
```

Streamlit Community Cloud can use the same key in the app's Secrets settings.
Keep this personal-data dashboard local or restrict access to it before deploying.
Use **Actualizar datos** to bypass the cache and load the latest commit on
`main`. The UI displays the short commit ID used for its metrics.

Run the reader tests without network access:

```powershell
.\venv\Scripts\python.exe -m unittest tests.test_spoty_data_reader -v
```

## Archived data and Git LFS

The five archived metadata CSVs larger than 50 MB are tracked with Git LFS,
as configured in `.gitattributes`. Install Git LFS before cloning or updating
this repository, then run:

```powershell
git lfs install
git lfs pull
```

Keep these files in LFS when updating them. Pushes upload their contents to
GitHub LFS and are subject to the repository owner's LFS storage and bandwidth
limits.
