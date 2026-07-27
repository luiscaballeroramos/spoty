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

<!-- run -->
python _registration.py

`config.py` loads .env automatically at startup, so you do not need to export the variables manually in PowerShell.
