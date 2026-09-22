import json
from pathlib import Path
from typing import Dict, Optional, Sequence

import matplotlib.pyplot as plt
import pandas as pd
import psycopg
from psycopg import sql
from psycopg.connection import Connection

from config import DATABASE_URL, DBSCHEMA

ANALYSIS_TIMEZONE = "Europe/Madrid"


def load_table_dataframe(conn: Connection, table: str) -> pd.DataFrame:
	"""Load one configured database table into a DataFrame."""
	if table not in DBSCHEMA:
		available_tables = ", ".join(DBSCHEMA)
		raise ValueError(
			f"Unknown table '{table}'. Available tables: {available_tables}"
		)

	query = sql.SQL("SELECT * FROM {}").format(sql.Identifier(table))
	with conn.cursor() as cursor:
		cursor.execute(query)
		rows = cursor.fetchall()
		columns = [column.name for column in cursor.description]

	return pd.DataFrame.from_records(rows, columns=columns)


def load_database_dataframes(
	database_url: str = DATABASE_URL,
	tables: Optional[Sequence[str]] = None,
) -> Dict[str, pd.DataFrame]:
	"""Load database tables into a dictionary keyed by table name."""
	selected_tables = list(tables) if tables is not None else list(DBSCHEMA)

	with psycopg.connect(database_url) as conn:
		return {
			table: load_table_dataframe(conn, table)
			for table in selected_tables
		}


def prepare_listening_events(
	dataframes: Dict[str, pd.DataFrame],
	timezone: str = ANALYSIS_TIMEZONE,
) -> pd.DataFrame:
	"""Enrich listening events with local time, track, album, and artist data."""
	events = dataframes["listening_events"].copy()
	tracks = dataframes["tracks"]
	albums = dataframes["albums"]
	artists = dataframes["artists"]

	artist_names = artists.set_index("id")["name"].to_dict()

	def resolve_artists(value: object) -> list[str]:
		if not isinstance(value, str):
			return ["Artista desconocido"]
		try:
			artist_ids = json.loads(value)
		except json.JSONDecodeError:
			return ["Artista desconocido"]
		return [artist_names.get(artist_id, artist_id) for artist_id in artist_ids]

	events["played_at"] = pd.to_datetime(
		events["played_at"], unit="s", utc=True
	).dt.tz_convert(timezone)
	events = events.merge(
		tracks[["id", "name", "duration_ms", "album_id", "artists_ids"]],
		left_on="track_id",
		right_on="id",
		how="left",
	).drop(columns="id")
	events = events.merge(
		albums[["id", "name"]].rename(columns={"name": "album_name"}),
		left_on="album_id",
		right_on="id",
		how="left",
	).drop(columns="id")
	events = events.rename(columns={"name": "track_name"})
	events["artist_names"] = events["artists_ids"].apply(resolve_artists)
	events["artist_name"] = events["artist_names"].str.join(", ")
	events["hours_listened"] = events["duration_ms"] / 3_600_000
	return events


def prepare_catalog_growth(events: pd.DataFrame) -> pd.DataFrame:
	"""Build daily cumulative counts of catalog entities seen in listening events."""
	if events.empty:
		raise ValueError("No hay listening events para representar.")

	date_index = pd.date_range(
		start=events["played_at"].min().normalize(),
		end=events["played_at"].max().normalize(),
		freq="D",
		tz=events["played_at"].dt.tz,
	)

	def cumulative_entities(entity_dates: pd.DataFrame) -> pd.Series:
		return (
			entity_dates.drop_duplicates("entity")
			.groupby("date")
			.size()
			.reindex(date_index, fill_value=0)
			.cumsum()
		)

	track_dates = pd.DataFrame({
		"date": events["played_at"].dt.normalize(),
		"entity": events["track_id"],
	})
	album_dates = pd.DataFrame({
		"date": events["played_at"].dt.normalize(),
		"entity": events["album_id"],
	})
	artist_dates = pd.DataFrame({
		"date": events["played_at"].dt.normalize(),
		"entity": events["artists_ids"].apply(
			lambda value: json.loads(value) if isinstance(value, str) else []
		),
	}).explode("entity")

	catalog_growth = pd.DataFrame({
		"Tracks": cumulative_entities(track_dates),
		"Albumes": cumulative_entities(album_dates),
		"Artistas": cumulative_entities(artist_dates),
	})
	return catalog_growth


def create_listening_summary_charts(
	events: pd.DataFrame,
	output_path: str = "listening_events_summary.png",
) -> Path:
	"""Create and save a dashboard with the main listening-event insights."""
	if events.empty:
		raise ValueError("No hay listening events para representar.")

	output = Path(output_path)
	output.parent.mkdir(parents=True, exist_ok=True)

	daily = events.set_index("played_at").resample("D").size()
	daily_average = daily.rolling(7, min_periods=1).mean()
	catalog_growth = prepare_catalog_growth(events)
	weekday_order = [
		"Lunes", "Martes", "Miercoles", "Jueves", "Viernes", "Sabado", "Domingo"
	]
	activity = pd.crosstab(
		events["played_at"].dt.dayofweek,
		events["played_at"].dt.hour,
	).reindex(index=range(7), columns=range(24), fill_value=0)
	top_artists = (
		events.explode("artist_names")["artist_names"]
		.value_counts()
		.head(20)
		.sort_values()
	)
	top_tracks = events["track_name"].value_counts().head(20).sort_values()
	top_albums = events["album_name"].value_counts().head(20).sort_values()
	track_play_distribution = (
		events.groupby("track_id").size().value_counts().sort_index()
	)
	artist_play_counts = events.explode("artist_names")["artist_names"].value_counts()
	artist_play_distribution = artist_play_counts.value_counts().sort_index()
	album_play_distribution = (
		events.groupby("album_id").size().value_counts().sort_index()
	)

	plt.style.use("seaborn-v0_8-whitegrid")
	figure = plt.figure(figsize=(16, 20), constrained_layout=True)
	grid = figure.add_gridspec(4, 3, height_ratios=[1, 1, 1.15, 1.15])
	axes = [
		[figure.add_subplot(grid[0, 0]), figure.add_subplot(grid[0, 1:])],
		[figure.add_subplot(grid[1, :]), None, None],
		[
			figure.add_subplot(grid[2, 0]),
			figure.add_subplot(grid[2, 1]),
			figure.add_subplot(grid[2, 2]),
		],
		[
			figure.add_subplot(grid[3, 0]),
			figure.add_subplot(grid[3, 1]),
			figure.add_subplot(grid[3, 2]),
		],
	]
	figure.patch.set_facecolor("#f7f4ed")
	for row in axes:
		for axis in row:
			if axis is not None:
				axis.set_facecolor("#f7f4ed")
				axis.spines[["top", "right"]].set_visible(False)

	axes[0][0].plot(daily.index, daily, color="#9aa0a6", alpha=0.45, linewidth=1)
	axes[0][0].plot(
		daily_average.index, daily_average, color="#18794e", linewidth=2.5,
		label="Media movil de 7 dias",
	)
	axes[0][0].fill_between(
		daily_average.index, daily_average, color="#18794e", alpha=0.12
	)
	axes[0][0].set_title("Evolucion diaria")
	axes[0][0].set_ylabel("Reproducciones")
	axes[0][0].legend(frameon=False)

	heatmap = axes[0][1].imshow(activity, cmap="YlGnBu", aspect="auto")
	axes[0][1].set_title("Actividad por dia y hora")
	axes[0][1].set_xlabel("Hora del dia")
	axes[0][1].set_xticks(range(0, 24, 3))
	axes[0][1].set_yticks(range(7), labels=weekday_order)
	figure.colorbar(heatmap, ax=axes[0][1], label="Reproducciones", shrink=0.85)

	for entity, color in zip(
		catalog_growth.columns,
		["#2563a6", "#c2415d", "#b7791f"],
	):
		axes[1][0].plot(
			catalog_growth.index,
			catalog_growth[entity],
			color=color,
			linewidth=2.5,
			label=entity,
		)
	axes[1][0].set_title("Crecimiento acumulado del catalogo")
	axes[1][0].set_ylabel("Elementos guardados")
	axes[1][0].set_ylim(bottom=0)
	axes[1][0].legend(frameon=False)

	axes[2][0].barh(top_tracks.index, top_tracks.values, color="#2563a6")
	axes[2][0].set_title("20 canciones mas escuchadas")
	axes[2][0].set_xlabel("Reproducciones")

	axes[2][1].barh(top_artists.index, top_artists.values, color="#c2415d")
	axes[2][1].set_title("20 artistas mas escuchados")
	axes[2][1].set_xlabel("Reproducciones")

	axes[2][2].barh(top_albums.index, top_albums.values, color="#b7791f")
	axes[2][2].set_title("20 albumes mas escuchados")
	axes[2][2].set_xlabel("Reproducciones")

	axes[3][0].scatter(
		track_play_distribution.index,
		track_play_distribution.values,
		color="#2563a6",
		alpha=0.75,
		s=28,
	)
	axes[3][0].set_title("Distribucion de reproducciones por tema")
	axes[3][0].set_xlabel("Reproducciones del tema")
	axes[3][0].set_ylabel("Numero de temas")
	axes[3][0].set_xscale("log")
	axes[3][0].set_yscale("log")

	axes[3][1].scatter(
		artist_play_distribution.index,
		artist_play_distribution.values,
		color="#c2415d",
		alpha=0.75,
		s=28,
	)
	axes[3][1].set_title("Distribucion de reproducciones por artista")
	axes[3][1].set_xlabel("Reproducciones del artista")
	axes[3][1].set_ylabel("Numero de artistas")
	axes[3][1].set_xscale("log")
	axes[3][1].set_yscale("log")

	axes[3][2].scatter(
		album_play_distribution.index,
		album_play_distribution.values,
		color="#b7791f",
		alpha=0.75,
		s=28,
	)
	axes[3][2].set_title("Distribucion de reproducciones por album")
	axes[3][2].set_xlabel("Reproducciones del album")
	axes[3][2].set_ylabel("Numero de albumes")
	axes[3][2].set_xscale("log")
	axes[3][2].set_yscale("log")

	date_range = (events["played_at"].max() - events["played_at"].min()).days + 1
	total_hours = events["hours_listened"].sum()
	unique_tracks = events["track_id"].nunique()
	figure.suptitle(
		"Resumen de listening events\n"
		f"{len(events):,} reproducciones  |  {total_hours:,.1f} horas  |  "
		f"{unique_tracks:,} canciones  |  {len(events) / date_range:,.1f} eventos/dia",
		fontsize=18,
		fontweight="bold",
		color="#202124",
	)
	figure.savefig(output, dpi=180, bbox_inches="tight", facecolor=figure.get_facecolor())
	plt.close(figure)
	return output


if __name__ == "__main__":
	dataframes = load_database_dataframes()
	listening_events = prepare_listening_events(dataframes)
	chart_path = create_listening_summary_charts(listening_events)
	print(f"Grafica creada: {chart_path.resolve()}")
