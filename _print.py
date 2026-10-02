from typing import Any
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from config import DBNAME
from register.db import SimpleDB
from spotifyapi.spotifyclient import SpotifyClient


def _print_as_tree(
    obj: Any, indent: int = 0, prefix: str = "", is_last: bool = True
) -> None:
    branch = "└── " if is_last else "├── "
    if isinstance(obj, dict):
        for i, (key, value) in enumerate(obj.items()):
            is_last_item = i == len(obj) - 1
            print(f"{prefix}{branch}{key}")
            extension = "    " if is_last_item else "│   "
            _print_as_tree(value, indent + 2, prefix + extension, is_last_item)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            is_last_item = i == len(obj) - 1
            print(f"{prefix}{branch}[{i}]")
            extension = "    " if is_last_item else "│   "
            _print_as_tree(item, indent + 2, prefix + extension, is_last_item)
    else:
        print(f"{prefix}{branch}{obj}")


def _get_output_prefix() -> str:
    if DBNAME.endswith(".db"):
        return Path(DBNAME).stem
    return "spotify"


def print_listening_events(limit: int = None):
    output_file = f"{_get_output_prefix()}_listening_events.txt"
    db_ = SimpleDB(DBNAME)
    db_.print_table(
        "listening_events", order_desc="played_at", output_file=output_file, limit=limit
    )


def print_liked_tracks(limit: int = None):
    output_file = f"{_get_output_prefix()}_liked_tracks.txt"
    db_ = SimpleDB(DBNAME)
    db_.print_table(
        "liked_tracks", order_desc="id", output_file=output_file, limit=limit
    )


def print_tracks(limit: int = None):
    output_file = f"{_get_output_prefix()}_tracks.txt"
    db_ = SimpleDB(DBNAME)
    db_.print_table(
        "tracks",
        order_desc="popularity",
        output_file=output_file,
        limit=limit,
        print_columns=[
            "id",
            "name",
            "duration_ms",
            "album_id",
            "album_track",
            "artists_ids",
        ],
    )


def print_albums(limit: int = None):
    output_file = f"{_get_output_prefix()}_albums.txt"
    db_ = SimpleDB(DBNAME)
    db_.print_table(
        "albums",
        order_desc="release_year",
        output_file=output_file,
        limit=limit,
        print_columns=["id", "name", "artists", "release_year", "popularity"],
    )


def print_artists(limit: int = None):
    output_file = f"{_get_output_prefix()}_artists.txt"
    db_ = SimpleDB(DBNAME)
    db_.print_table(
        "artists",
        order_desc="followers",
        output_file=output_file,
        limit=limit,
        print_columns=["id", "name"],
    )


def print_tops_spotify(limit: int = 10, time_range: str = "medium_term") -> None:
    """Print top tracks and artists from Spotify.

    Args:
        limit (int): Number of top items to retrieve (1-50).
        time_range (str): Time range for top items. One of:
            'short_term' => Últimas 4 semanas,
            'medium_term' => Últimas 6 meses,
             'long_term' => Desde siempre.
    """
    if not 1 <= limit <= 50:
        raise ValueError("limit debe estar entre 1 y 50")
    if time_range not in {"short_term", "medium_term", "long_term"}:
        raise ValueError(
            "time_range debe ser 'short_term', 'medium_term' o 'long_term'"
        )

    client = SpotifyClient()
    top_tracks = client.get_top_tracks(limit=limit, time_range=time_range) or {}
    top_artists = client.get_top_artists(limit=limit, time_range=time_range) or {}

    print(f"TOP {limit} CANCIONES ({time_range})")
    tracks = top_tracks.get("items") or []
    if not tracks:
        print("No se encontraron canciones.")
    for position, track in enumerate(tracks, start=1):
        artists = ", ".join(
            artist.get("name", "") for artist in track.get("artists", [])
        )
        print(f"{position}. {track.get('name', 'Sin título')} - {artists}")

    print(f"\nTOP {limit} ARTISTAS ({time_range})")
    artists = top_artists.get("items") or []
    if not artists:
        print("No se encontraron artistas.")
    for position, artist in enumerate(artists, start=1):
        print(f"{position}. {artist.get('name', 'Sin nombre')}")


def print_tops_db(limit: int = 10, time_range: str = "long_term") -> None:
    """Print top tracks, albums, and artists from stored listening events."""
    if limit < 1:
        raise ValueError("limit debe ser mayor que cero")

    lookback_seconds = {
        "short_term": 28 * 24 * 60 * 60,
        "medium_term": 180 * 24 * 60 * 60,
        "long_term": None,
    }
    if time_range not in lookback_seconds:
        raise ValueError(
            "time_range debe ser 'short_term', 'medium_term' o 'long_term'"
        )

    with psycopg.connect(DBNAME, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT MAX(played_at) AS latest_played_at FROM listening_events")
            latest_played_at = cursor.fetchone()["latest_played_at"]
            if latest_played_at is None:
                print("No hay eventos de escucha almacenados en la base de datos.")
                return

            period_filter = sql.SQL("")
            parameters = []
            lookback = lookback_seconds[time_range]
            if lookback is not None:
                period_filter = sql.SQL("WHERE played_at >= %s")
                parameters.append(latest_played_at - lookback)
            parameters.append(limit)

            query = sql.SQL(
                """
                WITH selected_events AS (
                    SELECT track_id, played_at
                    FROM listening_events
                    {period_filter}
                ),
                plays AS (
                    SELECT 'Canciones' AS tipo, tracks.id AS entity_id,
                           tracks.name AS nombre
                    FROM selected_events
                    JOIN tracks ON tracks.id = selected_events.track_id

                    UNION ALL

                    SELECT 'Albumes' AS tipo, albums.id AS entity_id,
                           albums.name AS nombre
                    FROM selected_events
                    JOIN tracks ON tracks.id = selected_events.track_id
                    JOIN albums ON albums.id = tracks.album_id

                    UNION ALL

                    SELECT 'Artistas' AS tipo, artists.id AS entity_id,
                           artists.name AS nombre
                    FROM selected_events
                    JOIN tracks ON tracks.id = selected_events.track_id
                    CROSS JOIN LATERAL jsonb_array_elements_text(
                        tracks.artists_ids::jsonb
                    ) AS track_artist(artist_id)
                    JOIN artists ON artists.id = track_artist.artist_id
                ),
                play_counts AS (
                    SELECT tipo, entity_id, nombre, COUNT(*) AS reproducciones
                    FROM plays
                    GROUP BY tipo, entity_id, nombre
                ),
                ranked_plays AS (
                    SELECT tipo, nombre, reproducciones,
                           ROW_NUMBER() OVER (
                               PARTITION BY tipo
                               ORDER BY reproducciones DESC, nombre ASC
                           ) AS puesto
                    FROM play_counts
                )
                SELECT tipo, nombre, reproducciones, puesto
                FROM ranked_plays
                WHERE puesto <= %s
                ORDER BY CASE tipo
                    WHEN 'Canciones' THEN 1
                    WHEN 'Albumes' THEN 2
                    ELSE 3
                END, puesto
                """
            ).format(period_filter=period_filter)
            cursor.execute(query, parameters)
            rows = cursor.fetchall()

    for category in ("Canciones", "Albumes", "Artistas"):
        print(f"\nTOP {limit} {category.upper()} EN BASE DE DATOS ({time_range})")
        category_rows = [row for row in rows if row["tipo"] == category]
        if not category_rows:
            print("No se encontraron reproducciones.")
            continue
        for row in category_rows:
            print(
                f"{row['puesto']}. {row['nombre']} - "
                f"{row['reproducciones']} reproducciones"
            )


if __name__ == "__main__":
    client = SpotifyClient()

    # # Print currently playing track
    # now=client.get_currently_playing()
    # print("CURRENTLY PLAYING")
    # _print_as_tree(now)

    # # Print recently played tracks one track
    # recently_played = client.get_recently_played(limit=1)
    # print("RECENTLY PLAYED")
    # _print_as_tree(recently_played)

    # # Print last 10 listening events
    # print_listening_events(limit=10)

    # # Print top 10 liked tracks
    # print_liked_tracks(limit=None)

    # # Print top 10 tracks
    # print_tracks(limit=10)

    # # Print top 10 albums
    # print_albums(limit=10)

    # # Print top 10 artists
    # print_artists(limit=20)

    # # Replace with any valid track ID/URI/URL
    # print('TRACK')
    # track = client.sp.track("3n3Ppam7vgaVa1iaRUc9Lp")
    # _print_as_tree(track)

    # # Fetch and print artist details
    # print('ARTIST')
    # artist = client.sp.artist("0OdUWJ0sBjDrqHygGUXeCF")
    # _print_as_tree(artist)

    # # Fetch and print album details
    # print('ALBUM')
    # album = client.sp.album("11lYdxQdsgkvKfDjX0nTHa")
    # _print_as_tree(album)

    # Print top tracks from Spotify
    print_tops_spotify(limit=10, time_range="short_term")
    # Print top tracks from the database
    print_tops_db(limit=10, time_range="short_term")
