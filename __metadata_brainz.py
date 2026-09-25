import os
import time
from pathlib import Path

import requests

from metadata_paths import get_metadata_output_dir, save_json
from spotifyapi.spotifyclient import SpotifyClient


# ============================================================
# 1. SPOTIFY
# ============================================================

client = SpotifyClient()

spotify_track_id = "3n3Ppam7vgaVa1iaRUc9Lp" # Mr. Brightside - The Killers
# spotify_track_id = '3urvSWprIfDCrIgNFrBslY' # Soleá del amor - Son de la frontera

track = client.sp.track(spotify_track_id)

title_spotify = track["name"]
artists_spotify = track.get("artists", [])
artist_spotify = artists_spotify[0]["name"]
album_spotify = track["album"]
album_name_spotify = album_spotify["name"]

artist_data = client.sp.artist(artists_spotify[0]["id"])
album_data = client.sp.album(album_spotify["id"])

isrc = track.get("external_ids", {}).get("isrc")

output_dir = get_metadata_output_dir(
    Path(__file__).resolve().parent,
    artist_spotify,
    album_name_spotify,
)

save_json(output_dir, "spotify_track.json", track)
save_json(output_dir, "spotify_artist.json", artist_data)
save_json(output_dir, "spotify_album.json", album_data)

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
    "User-Agent": os.getenv("MUSICBRAINZ_USER_AGENT", "Spoty/1.0 (local)"),
}

musicbrainz_last_request = 0.0


def get_musicbrainz_details(
    entity: str,
    mbid: str,
    includes: str,
) -> dict:
    """Obtiene una entidad MusicBrainz con sus relaciones y metadatos."""

    global musicbrainz_last_request

    elapsed = time.monotonic() - musicbrainz_last_request
    if elapsed < 1.1:
        time.sleep(1.1 - elapsed)

    response = requests.get(
        f"https://musicbrainz.org/ws/2/{entity}/{mbid}",
        params={"fmt": "json", "inc": includes},
        headers=headers,
        timeout=20,
    )
    musicbrainz_last_request = time.monotonic()
    response.raise_for_status()
    return response.json()

response = requests.get(
    musicbrainz_url,
    params=params,
    headers=headers,
    timeout=10,
)

response.raise_for_status()

mb_data = response.json()

save_json(output_dir, "musicbrainz_recordings.json", mb_data)

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
        save_json(output_dir, "acousticbrainz_low_level.json", low)

    if high is not None:
        save_json(output_dir, "acousticbrainz_high_level.json", high)

    print("\nJSON guardados correctamente.")

save_json(output_dir, "musicbrainz_selected_recording.json", selected_recording)


# ============================================================
# 5. METADATA DETALLADA DE MUSICBRAINZ
# ============================================================

recording_for_details = selected_recording or recordings[0]
recording_mbid = recording_for_details["id"]

musicbrainz_recording = get_musicbrainz_details(
    "recording",
    recording_mbid,
    "artists+releases+release-groups+isrcs+tags+genres+aliases+url-rels",
)
save_json(output_dir, "musicbrainz_recording.json", musicbrainz_recording)

artist_mbids = {
    artist_credit.get("artist", {}).get("id")
    for artist_credit in musicbrainz_recording.get("artist-credit", [])
    if artist_credit.get("artist", {}).get("id")
}
release_mbids = {
    release.get("id")
    for release in musicbrainz_recording.get("releases", [])
    if release.get("id")
}
release_group_mbids = {
    release.get("release-group", {}).get("id")
    for release in musicbrainz_recording.get("releases", [])
    if release.get("release-group", {}).get("id")
}

musicbrainz_artists = [
    get_musicbrainz_details(
        "artist",
        artist_mbid,
        "aliases+tags+genres+ratings+artist-rels+url-rels",
    )
    for artist_mbid in sorted(artist_mbids)
]
musicbrainz_releases = [
    get_musicbrainz_details(
        "release",
        release_mbid,
        "artists+labels+recordings+release-groups+media+isrcs+tags+genres+aliases+url-rels",
    )
    for release_mbid in sorted(release_mbids)
]
musicbrainz_release_groups = [
    get_musicbrainz_details(
        "release-group",
        release_group_mbid,
        "artists+releases+tags+genres+aliases+url-rels",
    )
    for release_group_mbid in sorted(release_group_mbids)
]

save_json(output_dir, "musicbrainz_artists.json", musicbrainz_artists)
save_json(output_dir, "musicbrainz_releases.json", musicbrainz_releases)
save_json(
    output_dir,
    "musicbrainz_release_groups.json",
    musicbrainz_release_groups,
)
save_json(
    output_dir,
    "musicbrainz_metadata.json",
    {
        "recording": musicbrainz_recording,
        "artists": musicbrainz_artists,
        "releases": musicbrainz_releases,
        "release_groups": musicbrainz_release_groups,
    },
)

print(
    "\nMetadata MusicBrainz guardada: "
    f"{len(musicbrainz_artists)} artista(s), "
    f"{len(musicbrainz_releases)} release(s), "
    f"{len(musicbrainz_release_groups)} release-group(s)."
)


# ============================================================
# 6. GENRES / TAGS DE MUSICBRAINZ
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
