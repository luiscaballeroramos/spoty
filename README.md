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

Alternative (if you prefer separate PG variables):

PGHOST=127.0.0.1
PGPORT=5432
PGDATABASE=spoty
PGUSER=postgres
PGPASSWORD=postgres
PGSSLMODE=require

<!-- run -->
python _registration.py

`config.py` loads .env automatically at startup, so you do not need to export the variables manually in PowerShell.

`_merge.py` and `_revision.py` are kept as legacy SQLite-only tools for old `.db` maintenance.
