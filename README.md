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

## Opcion 1: ejecutar en GitHub Actions (sin tener el PC encendido)

Este repositorio incluye un workflow programado en `.github/workflows/spotify-cron.yml`.

### 1) Primer login local para generar el token cache

En local, ejecuta una vez:

python _registration.py --once

Eso genera el archivo `.cache` con refresh token.

### 2) Crear secretos en GitHub

En GitHub: Settings > Secrets and variables > Actions, crea:

- SPOTIFY_CLIENT_ID
- SPOTIFY_CLIENT_SECRET
- SPOTIFY_REDIRECT_URI
- SPOTIFY_CACHE_B64

Para `SPOTIFY_CACHE_B64`, codifica tu `.cache` en base64 y pega el resultado como secret.

Ejemplo en PowerShell:

[Convert]::ToBase64String([IO.File]::ReadAllBytes(".cache"))

### 3) Activar workflow

- El workflow corre cada 10 minutos.
- Tambien lo puedes lanzar manualmente con `workflow_dispatch`.

### 4) Persistencia de la base de datos

El workflow hace commit automatico de `spotify.db` si hubo cambios para mantener el estado entre ejecuciones.

Nota: guardar una base SQLite en git funciona para proyectos pequenos, pero a medio plazo es mejor mover la base a Postgres/MySQL gestionado.
