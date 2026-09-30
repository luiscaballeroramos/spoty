import argparse
from pathlib import Path
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
	if limit < 1:
		raise ValueError("El limite del top debe ser mayor que cero.")

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
					WHERE plays.played_at >= bounds.latest_played_at - 86400
				) AS ultimas_24_horas,
				COUNT(*) FILTER (
					WHERE plays.played_at <= bounds.latest_played_at - 30 * 86400
				) AS reproducciones_anteriores_30_dias,
				COUNT(*) FILTER (
					WHERE plays.played_at <= bounds.latest_played_at - 86400
				) AS reproducciones_anteriores_24_horas,
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
					ORDER BY reproducciones DESC
				) AS puesto_actual,
				CASE WHEN reproducciones_anteriores > 0 THEN RANK() OVER (
					PARTITION BY tipo
					ORDER BY reproducciones_anteriores DESC
				) END AS puesto_anterior,
				CASE WHEN reproducciones_anteriores_24_horas > 0 THEN RANK() OVER (
					PARTITION BY tipo
					ORDER BY reproducciones_anteriores_24_horas DESC
				) END AS puesto_anterior_24_horas,
				CASE WHEN reproducciones_anteriores_30_dias > 0 THEN RANK() OVER (
					PARTITION BY tipo
					ORDER BY reproducciones_anteriores_30_dias DESC
				) END AS puesto_anterior_30_dias
			FROM play_counts
		)
		SELECT
			entity_id,
			nombre,
			reproducciones,
			ultimos_30_dias,
			ultimos_7_dias,
			ultimas_24_horas,
			puesto_actual,
			puesto_anterior,
			puesto_anterior_24_horas,
			puesto_anterior_30_dias
		FROM ranked_plays
		ORDER BY reproducciones DESC, nombre ASC, entity_id ASC
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

	output = Path(output_path) if output_path is not None else (
		Path(__file__).resolve().parent
		/ "outputs"
		/ "chart"
		/ f"top_{len(top_plays)}_{entity_type}.png"
	)
	output.parent.mkdir(parents=True, exist_ok=True)
	ranking = top_plays.sort_values(
		["reproducciones", "nombre", "entity_id"],
		ascending=[True, False, False],
	)
	colors = {
		"tracks": "#2563a6",
		"artists": "#c2415d",
		"albums": "#b7791f",
	}
	plt.style.use("seaborn-v0_8-whitegrid")
	figure = plt.figure(figsize=(18, max(16, len(ranking) * 0.28)))
	grid = figure.add_gridspec(
		1, 7, width_ratios=[4.6, 0.4, 0.7, 0.5, 0.5, 0.5, 8], wspace=0.005
	)
	name_axis = figure.add_subplot(grid[0, 0])
	rank_axis = figure.add_subplot(grid[0, 1], sharey=name_axis)
	times_axis = figure.add_subplot(grid[0, 2], sharey=name_axis)
	daily_axis = figure.add_subplot(grid[0, 3], sharey=name_axis)
	weekly_axis = figure.add_subplot(grid[0, 4], sharey=name_axis)
	monthly_axis = figure.add_subplot(grid[0, 5], sharey=name_axis)
	axis = figure.add_subplot(grid[0, 6], sharey=name_axis)
	figure.patch.set_facecolor("#f7f4ed")
	for label_axis in (
		name_axis, rank_axis, times_axis, daily_axis, weekly_axis, monthly_axis, axis
	):
		label_axis.set_facecolor("#f7f4ed")
	axis.spines[["top", "right"]].set_visible(False)
	for label_axis in (
		name_axis, rank_axis, times_axis, daily_axis, weekly_axis, monthly_axis
	):
		label_axis.set_xlim(0, 1)
		label_axis.set_xticks([])
		label_axis.set_yticks(range(len(ranking)))
		label_axis.tick_params(
			axis="both", left=False, bottom=False,
			labelleft=False, labelbottom=False
		)
		for spine in label_axis.spines.values():
			spine.set_visible(False)
	color = colors[entity_type]
	transparencies = {
		"total": 0.18,
		"30_days": 0.38,
		"7_days": 0.62,
		"24_hours": 1.00,
	}
	positions = range(len(ranking))
	axis.barh(
		positions, ranking["reproducciones"], color=color,
		alpha=transparencies["total"], label="Total"
	)
	axis.barh(
		positions, ranking["ultimos_30_dias"], color=color,
		alpha=transparencies["30_days"],
		label="Ultimos 30 dias"
	)
	axis.barh(
		positions, ranking["ultimos_7_dias"], color=color,
		alpha=transparencies["7_days"],
		label="Ultimos 7 dias"
	)
	axis.barh(
		positions, ranking["ultimas_24_horas"],
		left=ranking["reproducciones"] - ranking["ultimas_24_horas"],
		color=color,
		alpha=transparencies["24_hours"],
		label="Ultimas 24 horas"
	)
	axis.set_yticks(positions)
	axis.tick_params(axis="y", left=False, labelleft=False)
	axis.legend(
		handles=[
			Patch(facecolor=color, alpha=transparencies["24_hours"], label="Ultimas 24 horas"),
			Patch(facecolor=color, alpha=transparencies["7_days"], label="Ultimos 7 dias"),
			Patch(facecolor=color, alpha=transparencies["30_days"], label="Ultimos 30 dias"),
			Patch(facecolor=color, alpha=transparencies["total"], label="Total"),
		],
		loc="lower right",
		frameon=False,
	)
	rank_axis.set_title("#", fontsize=10, pad=10)
	times_axis.set_title("times", fontsize=9, pad=10)
	daily_axis.set_title("24h", fontsize=10, pad=10)
	weekly_axis.set_title("7d", fontsize=10, pad=10)
	monthly_axis.set_title("30d", fontsize=10, pad=10)
	rank_labels = ranking["puesto_actual"].astype(int).astype(str)
	rank_labels.loc[ranking.duplicated("puesto_actual", keep="last")] = "—"
	for position, (index, row) in enumerate(ranking.iterrows()):
		daily_label, daily_color = _rank_change_label(
			row["puesto_anterior_24_horas"], row["puesto_actual"]
		)
		weekly_label, weekly_color = _rank_change_label(
			row["puesto_anterior"], row["puesto_actual"]
		)
		monthly_label, monthly_color = _rank_change_label(
			row["puesto_anterior_30_dias"], row["puesto_actual"]
		)
		name_color = "#374151"
		if daily_color == weekly_color == monthly_color and daily_color in {
			"#16803c", "#c62828"
		}:
			name_color = daily_color
		name_axis.text(
			0.98, position, str(row["nombre"]),
			transform=name_axis.get_yaxis_transform(),
			ha="right", va="center", fontsize=9, color=name_color
		)
		rank_axis.text(
			0.5, position, rank_labels.loc[index],
			transform=rank_axis.get_yaxis_transform(),
			ha="center", va="center", fontsize=9, color="#374151"
		)
		times_axis.text(
			0.5, position, f"{row['reproducciones']:,}",
			transform=times_axis.get_yaxis_transform(),
			ha="center", va="center", fontsize=9, color="#374151"
		)
		daily_axis.text(
			0.5, position, daily_label,
			transform=daily_axis.get_yaxis_transform(),
			ha="center", va="center", fontsize=9, color=daily_color
		)
		weekly_axis.text(
			0.5, position, weekly_label,
			transform=weekly_axis.get_yaxis_transform(),
			ha="center", va="center", fontsize=9, color=weekly_color
		)
		monthly_axis.text(
			0.5, position, monthly_label,
			transform=monthly_axis.get_yaxis_transform(),
			ha="center", va="center", fontsize=9, color=monthly_color
		)
	figure.suptitle(
		f"Top {len(ranking)} de {entity_type}", fontsize=24, fontweight="bold"
	)
	axis.set_xlabel("Reproducciones", fontsize=14)
	figure.subplots_adjust(top=0.94, bottom=0.06, left=0.02, right=0.99)
	figure.savefig(output, dpi=180, bbox_inches="tight", facecolor=figure.get_facecolor())
	plt.close(figure)
	return str(output)


def _rank_change_label(previous_rank: float, current_rank: int) -> tuple[str, str]:
	if pd.isna(previous_rank):
		return "▲ nuevo", "#16803c"
	change = int(previous_rank - current_rank)
	if change > 0:
		return f"▲ {change}", "#16803c"
	if change < 0:
		return f"▼ {abs(change)}", "#c62828"
	return "—", "#6b7280"


def main(
	limit: int = TOP_LIMIT,
	entity_type: Literal["artists", "albums", "tracks"] = ENTITY_TYPE,
	database_url: str = DATABASE_URL,
) -> str:
	top_plays = load_top_plays(
		database_url=database_url,
		entity_type=entity_type,
		limit=limit,
	)
	print(f"Top {limit} de {entity_type}:")
	print(top_plays.drop(columns=["entity_id"]).to_string(index=False))
	chart_path = create_top_plays_chart(top_plays, entity_type)
	print(f"Grafica creada: {chart_path}")
	return chart_path


if __name__ == "__main__":
	parser = argparse.ArgumentParser(description="Genera un ranking de reproducciones.")
	parser.add_argument("--limit", type=int, default=TOP_LIMIT, help="Cantidad de elementos del top.")
	parser.add_argument(
		"--entity",
		choices=("artists", "albums", "tracks"),
		default=ENTITY_TYPE,
		help="Tipo de elemento que se quiere clasificar.",
	)
	arguments = parser.parse_args()
	main(limit=arguments.limit, entity_type=arguments.entity)
