from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

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


def create_top_100_charts(
	events: pd.DataFrame,
	output_path: str = "top_100_listening_summary.png",
) -> Path:
	"""Create and save top charts for tracks, artists, and albums."""
	top_plays = prepare_top_plays(events)
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
		axis.barh(ranking.index, ranking.values, color=colors[entity])
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
