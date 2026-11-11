# spoty

## Register listening events

`publish_listening_events.py` fetches the latest 50 Spotify plays and sends flat
event rows to the `spoty-data` Action. It does not import code from `old/`.
No intermediate CSV is needed.

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

## Hourly registration

The `Publish listening events` Action is scheduled each hour at minute 00 (UTC),
or manually through `Run workflow`. GitHub schedules can run late or be skipped;
this is not an exact hourly timer.

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
More than 50 plays between successful captures can leave gaps; hourly capture
does not guarantee complete history.

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
