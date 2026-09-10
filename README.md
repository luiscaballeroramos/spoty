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

<!-- config streamlit iphone visualization -->
streamlit
F12
Toogle device toolba (ctrl+shift+M)
375x765(812-47-34) there is a white band on top of 47 pixels, and a space lost on the bottom of 34 pixels
