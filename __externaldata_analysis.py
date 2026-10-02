import sqlite3
from pathlib import Path
from typing import Optional

import pandas as pd


DATABASE_PATH = Path(__file__).resolve().parent / "external data" / "last_fm.db"
TABLE_NAME = "TRAINING_DB_2"


def read_training_data(limit: Optional[int] = None) -> pd.DataFrame:
	"""Read TRAINING_DB_2 from the local Last.fm SQLite database."""
	if not DATABASE_PATH.is_file():
		raise FileNotFoundError(f"No se encontró la base de datos: {DATABASE_PATH}")
	if limit is not None and limit < 1:
		raise ValueError("limit debe ser un entero mayor que cero")

	query = f'SELECT * FROM "{TABLE_NAME}"'
	parameters = ()
	if limit is not None:
		query += " LIMIT ?"
		parameters = (limit,)

	connection = sqlite3.connect(DATABASE_PATH)
	try:
		return pd.read_sql_query(query, connection, params=parameters)
	finally:
		connection.close()


def count_songs_by_genre() -> list[tuple[str, int]]:
	"""Return each genre and its unique song count, ordered by count descending."""
	if not DATABASE_PATH.is_file():
		raise FileNotFoundError(f"No se encontró la base de datos: {DATABASE_PATH}")

	query = f'''
		SELECT genre_name, COUNT(DISTINCT track_id) AS song_count
		FROM "{TABLE_NAME}"
		GROUP BY genre_name
		ORDER BY song_count DESC, genre_name ASC
	'''
	with sqlite3.connect(DATABASE_PATH) as connection:
		return connection.execute(query).fetchall()


def main() -> None:
	songs=set()
	genres=set()
	for genre, song_count in count_songs_by_genre():
		print(f"{genre}: {song_count} canciones")
		genres.add(genre)
		songs.add(song_count)
	# Print the total number of unique genres and songs
	print(f'numero de generos: {len(genres)}')
	print(f'numero de canciones: {len(songs)}')


if __name__ == "__main__":
	main()
