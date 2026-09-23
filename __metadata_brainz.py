import json
from pathlib import Path

import requests

from spotifyapi.spotifyclient import SpotifyClient


# ============================================================
# 1. SPOTIFY
# ============================================================

client = SpotifyClient()

spotify_track_id = "3n3Ppam7vgaVa1iaRUc9Lp" # Mr. Brightside - The Killers
# spotify_track_id = '3urvSWprIfDCrIgNFrBslY' # Soleá del amor - Son de la frontera
output_dir = Path(__file__).resolve().parent / "outputs" / spotify_track_id
output_dir.mkdir(parents=True, exist_ok=True)

track = client.sp.track(spotify_track_id)

title_spotify = track["name"]
artist_spotify = track["artists"][0]["name"]

isrc = track.get("external_ids", {}).get("isrc")

print(f"Spotify:")
print(f"  Title : {title_spotify}")
print(f"  Artist: {artist_spotify}")
print(f"  ISRC  : {isrc}")

if not isrc:
    raise RuntimeError("El track de Spotify no tiene ISRC.")


# ============================================================
# 2. MUSICBRAINZ
# ============================================================

musicbrainz_url = "https://musicbrainz.org/ws/2/recording"

params = {
    "query": f"isrc:{isrc}",
    "fmt": "json",
    "limit": 10,
}

headers = {
    "User-Agent": "Spoty/1.0 (tu-email@example.com)",
}

response = requests.get(
    musicbrainz_url,
    params=params,
    headers=headers,
    timeout=10,
)

response.raise_for_status()

mb_data = response.json()

with open(
    output_dir / "musicbrainz_recordings.json",
    "w",
    encoding="utf-8",
) as f:
    json.dump(mb_data, f, indent=4, ensure_ascii=False)

recordings = mb_data.get("recordings", [])

if not recordings:
    raise RuntimeError(
        f"No se encontró ningún recording en MusicBrainz para ISRC {isrc}"
    )

print(f"\nMusicBrainz encontró {len(recordings)} recordings.")


# ============================================================
# 3. BUSCAR UN MBID QUE TENGA ACOUSTICBRAINZ
# ============================================================

def get_acousticbrainz(mbid: str):
    """
    Devuelve los datos de AcousticBrainz para un MBID.

    Retorna:
        {
            "low-level": dict | None,
            "high-level": dict | None
        }
    """

    result = {
        "low-level": None,
        "high-level": None,
    }

    for level in ("low-level", "high-level"):

        url = f"https://acousticbrainz.org/api/v1/{mbid}/{level}"

        try:
            response = requests.get(
                url,
                timeout=10,
            )
        except requests.RequestException as e:
            print(f"Error consultando {level}: {e}")
            continue

        print(f"AcousticBrainz {level}: HTTP {response.status_code}")

        if response.status_code == 200:
            try:
                result[level] = response.json()
            except ValueError:
                print(f"Respuesta no JSON para {mbid} / {level}")

    return result


selected_recording = None
acousticbrainz_data = None


for recording in recordings:

    candidate_mbid = recording["id"]

    print(
        f"\nProbando MBID: {candidate_mbid} "
        f"| {recording.get('title')}"
    )

    candidate_data = get_acousticbrainz(candidate_mbid)

    if (
        candidate_data["low-level"] is not None
        or candidate_data["high-level"] is not None
    ):
        selected_recording = recording
        acousticbrainz_data = candidate_data
        break


# ============================================================
# 4. RESULTADO
# ============================================================

if selected_recording is None:

    print("\nNo hay datos de AcousticBrainz para ninguno")
    print("de los recordings encontrados por ese ISRC.")

    print("\nRecordings encontrados:")

    for recording in recordings:
        print(
            f"  MBID: {recording['id']} "
            f"| Title: {recording.get('title')} "
            f"| Length: {recording.get('length')}"
        )

else:

    mbid = selected_recording["id"]
    mb_title = selected_recording.get("title")
    mb_length = selected_recording.get("length")

    print("\n====================================")
    print("MATCH EN ACOUSTICBRAINZ")
    print("====================================")

    print(f"MBID  : {mbid}")
    print(f"Title : {mb_title}")
    print(f"Length: {mb_length}")

    low = acousticbrainz_data["low-level"]
    high = acousticbrainz_data["high-level"]

    print("\nLow-level:")
    print("Disponible:", low is not None)

    print("\nHigh-level:")
    print("Disponible:", high is not None)

    # --------------------------------------------------------
    # Guardar JSON
    # --------------------------------------------------------

    if low is not None:
        with open(
            output_dir / "acousticbrainz_low_level.json",
            "w",
            encoding="utf-8",
        ) as f:
            json.dump(
                low,
                f,
                indent=4,
                ensure_ascii=False,
            )

    if high is not None:
        with open(
            output_dir / "acousticbrainz_high_level.json",
            "w",
            encoding="utf-8",
        ) as f:
            json.dump(
                high,
                f,
                indent=4,
                ensure_ascii=False,
            )

    print("\nJSON guardados correctamente.")

with open(
    output_dir / "musicbrainz_selected_recording.json",
    "w",
    encoding="utf-8",
) as f:
    json.dump(
        selected_recording,
        f,
        indent=4,
        ensure_ascii=False,
    )


# ============================================================
# 5. GENRES / TAGS DE MUSICBRAINZ
# ============================================================

# OJO:
# genres/tags no están necesariamente dentro de "recording".
# Depende de los datos que devuelva MusicBrainz.

for recording in recordings:

    print("\n--------------------------------")
    print(f"Recording: {recording.get('title')}")
    print(f"MBID: {recording.get('id')}")

    genres = [
        genre["name"]
        for genre in recording.get("genres", [])
    ]

    tags = [
        tag["name"]
        for tag in recording.get("tags", [])
    ]

    print(f"Genres: {genres}")
    print(f"Tags  : {tags}")
