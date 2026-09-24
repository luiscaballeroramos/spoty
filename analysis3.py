from typing import Literal

import matplotlib.pyplot as plt
import pandas as pd
import psycopg
from matplotlib.patches import Patch

from config import DATABASE_URL


TOP_LIMIT = 200
ENTITY_TYPE: Literal["artists", "albums", "tracks"] = "artists"


def load_top_plays(
	database_url: str = DATABASE_URL,
	entity_type: Literal["artists", "albums", "tracks"] = ENTITY_TYPE,
	limit: int = TOP_LIMIT,
) -> pd.DataFrame:
	"""Load the most played tracks, artists, or albums using a SQL CTE."""
	if entity_type not in {"artists", "albums", "tracks"}:
		raise ValueError("entity_type debe ser artistas, albumes o canciones.")

	query = """
		WITH plays AS (
			SELECT
				'tracks' AS tipo,
				tracks.id AS entity_id,
				tracks.name AS nombre,
				listening_events.played_at
			FROM listening_events
			JOIN tracks ON tracks.id = listening_events.track_id

			UNION ALL

			SELECT
				'albums' AS tipo,
				albums.id AS entity_id,
				albums.name AS nombre,
				listening_events.played_at
			FROM listening_events
			JOIN tracks ON tracks.id = listening_events.track_id
			JOIN albums ON albums.id = tracks.album_id

			UNION ALL

			SELECT
				'artists' AS tipo,
				artists.id AS entity_id,
				artists.name AS nombre,
				listening_events.played_at
			FROM listening_events
			JOIN tracks ON tracks.id = listening_events.track_id
			CROSS JOIN LATERAL jsonb_array_elements_text(tracks.artists_ids::jsonb)
				AS track_artist(artist_id)
			JOIN artists ON artists.id = track_artist.artist_id
		),
		bounds AS (
			SELECT MAX(played_at) AS latest_played_at
			FROM listening_events
		),
		play_counts AS (
			SELECT
				plays.tipo,
				plays.entity_id,
				plays.nombre,
				COUNT(*) AS reproducciones,
				COUNT(*) FILTER (
					WHERE plays.played_at >= bounds.latest_played_at - 29 * 86400
				) AS ultimos_30_dias,
				COUNT(*) FILTER (
					WHERE plays.played_at >= bounds.latest_played_at - 6 * 86400
				) AS ultimos_7_dias,
				COUNT(*) FILTER (
					WHERE plays.played_at <= bounds.latest_played_at - 7 * 86400
				) AS reproducciones_anteriores
			FROM plays
			CROSS JOIN bounds
			WHERE plays.tipo = %s
			GROUP BY plays.tipo, plays.entity_id, plays.nombre
		),
		ranked_plays AS (
			SELECT
				*,
				RANK() OVER (
					PARTITION BY tipo
					ORDER BY reproducciones DESC, nombre ASC
				) AS puesto_actual,
				CASE WHEN reproducciones_anteriores > 0 THEN RANK() OVER (
					PARTITION BY tipo
					ORDER BY reproducciones_anteriores DESC, nombre ASC
				) END AS puesto_anterior
			FROM play_counts
		)
		SELECT
			nombre,
			reproducciones,
			ultimos_30_dias,
			ultimos_7_dias,
			puesto_actual,
			puesto_anterior
		FROM ranked_plays
		ORDER BY reproducciones DESC, nombre ASC
		LIMIT %s
	"""
	with psycopg.connect(database_url) as connection:
		return pd.read_sql_query(query, connection, params=(entity_type, limit))


def create_top_plays_chart(
	top_plays: pd.DataFrame,
	entity_type: str,
	output_path: str | None = None,
) -> str:
	"""Create a PNG horizontal bar chart for a Top plays ranking."""
	if top_plays.empty:
		raise ValueError("No hay reproducciones para representar.")

	output = output_path or f"top_{len(top_plays)}_{entity_type}.png"
	ranking = top_plays.sort_values("reproducciones")
	colors = {
		"tracks": "#2563a6",
		"artists": "#c2415d",
		"albums": "#b7791f",
	}
	plt.style.use("seaborn-v0_8-whitegrid")
	figure, axis = plt.subplots(figsize=(18, max(16, len(ranking) * 0.28)))
	figure.patch.set_facecolor("#f7f4ed")
	axis.set_facecolor("#f7f4ed")
	axis.spines[["top", "right"]].set_visible(False)
	color = colors[entity_type]
	positions = range(len(ranking))
	axis.barh(
		positions, ranking["reproducciones"], color=color, alpha=0.25, label="Total"
	)
	axis.barh(
		positions, ranking["ultimos_30_dias"], color=color, alpha=0.55,
		label="Ultimos 30 dias"
	)
	axis.barh(
		positions, ranking["ultimos_7_dias"], color=color, label="Ultimos 7 dias"
	)
	axis.legend(
		handles=[
			Patch(facecolor=color, label="Ultimos 7 dias"),
			Patch(facecolor=color, alpha=0.55, label="Ultimos 30 dias"),
			Patch(facecolor=color, alpha=0.25, label="Total"),
		],
		loc="lower right",
		frameon=False,
	)
	labels = []
	for _, row in ranking.iterrows():
		if pd.isna(row["puesto_anterior"]):
			change_label = "▲ nuevo"
		else:
			change = int(row["puesto_anterior"] - row["puesto_actual"])
			change_label = (
				f"▲ {change}" if change > 0
				else f"▼ {abs(change)}" if change < 0
				else "—"
			)
		labels.append(f"{row['nombre']}  {change_label}")
	axis.set_yticks(range(len(labels)), labels=labels)
	for label, (_, row) in zip(axis.get_yticklabels(), ranking.iterrows()):
		if pd.isna(row["puesto_anterior"]):
			label.set_color("#16803c")
		elif row["puesto_anterior"] > row["puesto_actual"]:
			label.set_color("#16803c")
		elif row["puesto_anterior"] < row["puesto_actual"]:
			label.set_color("#c62828")
		else:
			label.set_color("#6b7280")
	axis.set_title(f"Top {len(ranking)} de {entity_type}", fontsize=24, fontweight="bold")
	axis.set_xlabel("Reproducciones", fontsize=14)
	axis.tick_params(axis="y", labelsize=9)
	figure.savefig(output, dpi=180, bbox_inches="tight", facecolor=figure.get_facecolor())
	plt.close(figure)
	return output


if __name__ == "__main__":
	top_plays = load_top_plays()
	print(f"Top {TOP_LIMIT} de {ENTITY_TYPE}:")
	print(top_plays.to_string(index=False))
	chart_path = create_top_plays_chart(top_plays, ENTITY_TYPE)
	print(f"Grafica creada: {chart_path}")
