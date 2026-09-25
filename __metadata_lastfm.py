import json
from pathlib import Path

import requests

from metadata_paths import get_metadata_output_dir, save_json
from spotifyapi.spotifyclient import SpotifyClient


# ============================================================
# 1. SPOTIFY
# ============================================================

client = SpotifyClient()

# spotify_track_id = "3n3Ppam7vgaVa1iaRUc9Lp"  # Mr. Brightside - The Killers
spotify_track_id = "3urvSWprIfDCrIgNFrBslY"  # Soleá del amor - Son de la frontera

track = client.sp.track(spotify_track_id)

title_spotify = track["name"]
artist_spotify = track["artists"][0]["name"]
album_spotify = track["album"]
album_name_spotify = album_spotify["name"]

artist_data = client.sp.artist(track["artists"][0]["id"])
album_data = client.sp.album(album_spotify["id"])

output_dir = get_metadata_output_dir(
    Path(__file__).resolve().parent,
    artist_spotify,
    album_name_spotify,
)

save_json(output_dir, "spotify_track.json", track)
save_json(output_dir, "spotify_artist.json", artist_data)
save_json(output_dir, "spotify_album.json", album_data)

isrc = track.get("external_ids", {}).get("isrc")

print("Spotify:")
print(f"  Title : {title_spotify}")
print(f"  Artist: {artist_spotify}")
print(f"  ISRC  : {isrc}")


# ============================================================
# 2. LAST.FM
# ============================================================

# Pon aquí tu API key de Last.fm
LASTFM_API_KEY = "TU_API_KEY"

lastfm_url = "https://ws.audioscrobbler.com/2.0/"

params = {
    "method": "track.getInfo",
    "api_key": LASTFM_API_KEY,
    "artist": artist_spotify,
    "track": title_spotify,
    "autocorrect": 1,
    "format": "json",
}

response = requests.get(
    lastfm_url,
    params=params,
    timeout=10,
)

response.raise_for_status()

lastfm_data = response.json()


# ============================================================
# 3. GUARDAR RESPUESTA COMPLETA
# ============================================================

save_json(output_dir, "lastfm_track.json", lastfm_data)


# ============================================================
# 4. COMPROBAR RESULTADO
# ============================================================

if "error" in lastfm_data:

    print("\nLast.fm devolvió un error:")
    print(f"  Code   : {lastfm_data.get('error')}")
    print(f"  Message: {lastfm_data.get('message')}")

else:

    lastfm_track = lastfm_data.get("track", {})

    print("\n====================================")
    print("MATCH EN LAST.FM")
    print("====================================")

    print(f"Title : {lastfm_track.get('name')}")

    artist = lastfm_track.get("artist", {})
    print(f"Artist: {artist.get('name')}")

    album = lastfm_track.get("album", {})
    print(f"Album : {album.get('title')}")

    print(f"MBID  : {lastfm_track.get('mbid')}")
    print(f"URL   : {lastfm_track.get('url')}")


    # ========================================================
    # 5. ESTADÍSTICAS LAST.FM
    # ========================================================

    print("\nEstadísticas:")

    print(
        f"  Listeners: "
        f"{lastfm_track.get('listeners')}"
    )

    print(
        f"  Playcount: "
        f"{lastfm_track.get('playcount')}"
    )


    # ========================================================
    # 6. TAGS
    # ========================================================

    tags_data = lastfm_track.get("toptags", {})

    tags = [
        tag["name"]
        for tag in tags_data.get("tag", [])
    ]

    print("\nTags:")

    for tag in tags:
        print(f"  - {tag}")


    # ========================================================
    # 7. WIKI
    # ========================================================

    wiki = lastfm_track.get("wiki")

    if wiki:

        print("\nWiki:")

        print(
            f"  Published: "
            f"{wiki.get('published')}"
        )

        print(
            f"  Summary available: "
            f"{bool(wiki.get('summary'))}"
        )

        print(
            f"  Content available: "
            f"{bool(wiki.get('content'))}"
        )


    # ========================================================
    # 8. GUARDAR INFORMACIÓN NORMALIZADA
    # ========================================================

    normalized_data = {
        "spotify": {
            "track_id": spotify_track_id,
            "title": title_spotify,
            "artist": artist_spotify,
            "isrc": isrc,
        },
        "lastfm": {
            "title": lastfm_track.get("name"),
            "artist": artist.get("name"),
            "album": album.get("title"),
            "mbid": lastfm_track.get("mbid"),
            "url": lastfm_track.get("url"),
            "listeners": lastfm_track.get("listeners"),
            "playcount": lastfm_track.get("playcount"),
            "tags": tags,
            "wiki": wiki,
        },
    }

    save_json(output_dir, "lastfm_normalized.json", normalized_data)

    print("\nJSON guardados correctamente.")
