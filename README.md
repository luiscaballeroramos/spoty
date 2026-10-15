# spoty
<!-- create env -->
python -m venv venv

<!-- activate env -->
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
venv\Scripts\activate

<!-- deactivate env -->
deactivate

<!-- install requirements -->
pip install -r requirements.txt

<!-- config environmental variables -->
Create a .env file in the project root with:

SPOTIFY_CLIENT_ID=tu_client_id
SPOTIFY_CLIENT_SECRET=tu_client_secret
SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888/
DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/spoty
SPOTY_DISPLAY_TIMEZONE=Etc/GMT-4

`SPOTY_DISPLAY_TIMEZONE` accepts an IANA timezone name and defaults to
`Etc/GMT-4` (UTC+04:00), preserving the current summary display. For example,
set it to `Europe/Madrid` to use that region's daylight-saving rules.

<!-- config streamlit iphone visualization -->
streamlit
F12
Toogle device toolba (ctrl+shift+M)
375x765(812-47-34) there is a white band on top of 47 pixels, and a space lost on the bottom of 34 pixels

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
