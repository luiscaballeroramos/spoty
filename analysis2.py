from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.patches import Patch

from analysis import load_database_dataframes, prepare_listening_events


TOP_LIMIT = 50


def prepare_top_plays(events: pd.DataFrame, limit: int = TOP_LIMIT) -> dict[str, pd.Series]:
	"""Build top-played rankings for tracks, artists, and albums."""
	if events.empty:
		raise ValueError("No hay listening events para representar.")

	return {
		"Canciones": events["track_name"].value_counts().head(limit).sort_values(),
		"Artistas": (
			events.explode("artist_names")["artist_names"]
			.value_counts()
			.head(limit)
			.sort_values()
		),
		"Albumes": events["album_name"].value_counts().head(limit).sort_values(),
	}


def prepare_rank_change(events: pd.DataFrame) -> dict[str, tuple[pd.Series, pd.Series]]:
	"""Return cumulative ranks for the latest day and seven days earlier."""
	last_day = events["played_at"].max().normalize()

	def rank_until(day: pd.Timestamp) -> dict[str, pd.Series]:
		period_events = events[events["played_at"] < day + pd.Timedelta(days=1)]
		return {
			"Canciones": period_events["track_name"].value_counts().rank(
				method="min", ascending=False
			),
			"Artistas": period_events.explode("artist_names")["artist_names"].value_counts().rank(
				method="min", ascending=False
			),
			"Albumes": period_events["album_name"].value_counts().rank(
				method="min", ascending=False
			),
		}

	current = rank_until(last_day)
	previous = rank_until(last_day - pd.Timedelta(days=7))
	return {
		entity: (current[entity], previous[entity])
		for entity in current
	}


def prepare_recent_plays(events: pd.DataFrame, days: int) -> dict[str, pd.Series]:
	"""Count plays during the latest period for each entity type."""
	last_day = events["played_at"].max().normalize()
	recent_events = events[events["played_at"] >= last_day - pd.Timedelta(days=days - 1)]
	return {
		"Canciones": recent_events["track_name"].value_counts(),
		"Artistas": recent_events.explode("artist_names")["artist_names"].value_counts(),
		"Albumes": recent_events["album_name"].value_counts(),
	}


def create_top_100_charts(
	events: pd.DataFrame,
	output_path: str = "top_100_listening_summary.png",
) -> Path:
	"""Create and save top charts for tracks, artists, and albums."""
	top_plays = prepare_top_plays(events)
	rank_changes = prepare_rank_change(events)
	last_week_plays = prepare_recent_plays(events, days=7)
	last_month_plays = prepare_recent_plays(events, days=30)
	output = Path(output_path)
	output.parent.mkdir(parents=True, exist_ok=True)

	plt.style.use("seaborn-v0_8-whitegrid")
	figure, axes = plt.subplots(3, 1, figsize=(18, 42), constrained_layout=True)
	figure.patch.set_facecolor("#f7f4ed")

	colors = {
		"Canciones": "#2563a6",
		"Artistas": "#c2415d",
		"Albumes": "#b7791f",
	}

	for axis, (entity, ranking) in zip(axes, top_plays.items()):
		axis.set_facecolor("#f7f4ed")
		axis.spines[["top", "right"]].set_visible(False)
		last_week = last_week_plays[entity].reindex(ranking.index, fill_value=0)
		last_month = last_month_plays[entity].reindex(ranking.index, fill_value=0)
		axis.barh(
			ranking.index,
			ranking,
			color=colors[entity],
			alpha=0.25,
			label="Total",
		)
		axis.barh(
			ranking.index,
			last_month,
			color=colors[entity],
			alpha=0.55,
			label="Ultimos 30 dias",
		)
		axis.barh(
			ranking.index,
			last_week,
			color=colors[entity],
			label="Ultimos 7 dias",
		)
		axis.legend(
			handles=[
				Patch(facecolor=colors[entity], label="Ultimos 7 dias"),
				Patch(facecolor=colors[entity], alpha=0.55, label="Ultimos 30 dias"),
				Patch(facecolor=colors[entity], alpha=0.25, label="Total"),
			],
			loc="lower right",
			frameon=False,
		)
		current_ranks, previous_ranks = rank_changes[entity]
		labels = []
		for name in ranking.index:
			current_rank = current_ranks.get(name)
			previous_rank = previous_ranks.get(name)
			if pd.isna(current_rank):
				change_label = "—"
			elif pd.isna(previous_rank):
				change_label = "▲ nuevo"
			else:
				change = int(previous_rank - current_rank)
				change_label = (
					f"▲ {change}" if change > 0
					else f"▼ {abs(change)}" if change < 0
					else "—"
				)
			labels.append(f"{name}  {change_label}")
		axis.set_yticks(range(len(labels)), labels=labels)
		for label, name in zip(axis.get_yticklabels(), ranking.index):
			current_rank = current_ranks.get(name)
			previous_rank = previous_ranks.get(name)
			if pd.isna(current_rank):
				label.set_color("#6b7280")
			elif pd.isna(previous_rank) or previous_rank > current_rank:
				label.set_color("#16803c")
			elif previous_rank < current_rank:
				label.set_color("#c62828")
			else:
				label.set_color("#6b7280")
		axis.set_title(f"{len(ranking)} {entity.lower()} mas escuchados", fontsize=22)
		axis.set_xlabel("Reproducciones", fontsize=15)
		axis.tick_params(axis="y", labelsize=11)
		axis.tick_params(axis="x", labelsize=12)

	date_range = (events["played_at"].max() - events["played_at"].min()).days + 1
	total_hours = events["hours_listened"].sum()
	figure.suptitle(
		f"Top {TOP_LIMIT} de escuchas\n"
		f"{len(events):,} reproducciones  |  {total_hours:,.1f} horas  |  "
		f"{len(events) / date_range:,.1f} eventos/dia",
		fontsize=28,
		fontweight="bold",
		color="#202124",
	)
	figure.savefig(output, dpi=180, bbox_inches="tight", facecolor=figure.get_facecolor())
	plt.close(figure)
	return output


if __name__ == "__main__":
	dataframes = load_database_dataframes()
	listening_events = prepare_listening_events(dataframes)
	chart_path = create_top_100_charts(listening_events)
	print(f"Grafica creada: {chart_path.resolve()}")
