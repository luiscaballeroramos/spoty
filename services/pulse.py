"""Build time-weighted audio distributions from listening events."""

import math
from dataclasses import dataclass
from numbers import Real


@dataclass(frozen=True)
class AudioParameter:
    key: str
    label: str
    maximum: float = 1.0


PARAMETERS = (
    AudioParameter("energy", "Energía"),
    AudioParameter("danceability", "Bailabilidad"),
    AudioParameter("valence", "Valencia"),
    AudioParameter("acousticness", "Acústica"),
    AudioParameter("instrumentalness", "Instrumentalidad"),
    AudioParameter("tempo", "Tempo", 240.0),
)
EXTRA_PARAMETERS = (
    AudioParameter("speechiness", "Palabra hablada"),
    AudioParameter("liveness", "Directo"),
    AudioParameter("loudness", "Nivel sonoro", 0.0),
)


def audio_value(data: dict, parameter: AudioParameter) -> float | None:
    value = data.get(parameter.key)
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value):
        return None
    if parameter.key == "loudness":
        return float(value) if value <= 0 else None
    if value < 0 or (parameter.maximum == 1 and value > 1):
        return None
    return float(value)


def build_pulse(rows: list[dict], now: float, hours: int = 2, expanded: bool = False) -> dict:
    current = next((row for row in rows if row.get("kind") == "current"), {})
    history = [
        row for row in rows
        if row.get("kind") == "history"
        and 0 <= now - row["played_at"] <= hours * 3600
    ]
    bands = []
    for parameter in PARAMETERS + (EXTRA_PARAMETERS if expanded else ()):
        minimum = -60.0 if parameter.key == "loudness" else 0.0
        maximum = parameter.maximum
        samples = []
        for row in history:
            value = audio_value(row.get("audio") or {}, parameter)
            if value is not None:
                samples.append({
                    "value": value,
                    "weight": 2 ** (-(now - row["played_at"]) / 1800),
                    "name": row.get("name") or "Sin título",
                    "age_minutes": (now - row["played_at"]) / 60,
                })
        current_value = audio_value(current.get("audio") or {}, parameter)
        if parameter.key == "tempo":
            tempo_values = [sample["value"] for sample in samples]
            if not tempo_values and current_value is not None:
                tempo_values = [current_value]
            if tempo_values:
                minimum, maximum = min(tempo_values), max(tempo_values)
        if parameter.key == "loudness":
            minimum = min(minimum, math.floor(min(
                [sample["value"] for sample in samples] + [current_value or 0]
            ) / 10) * 10)
        def position(value):
            if maximum == minimum:
                return 0.5 if value == minimum else (0.0 if value < minimum else 1.0)
            return (value - minimum) / (maximum - minimum)
        heat = [sum(
            sample["weight"] * math.exp(-0.5 * ((index / 79 - position(sample["value"])) / 0.035) ** 2)
            for sample in samples
        ) for index in range(80)]
        peak = max(heat, default=0)
        bands.append({
            "parameter": parameter,
            "minimum": minimum,
            "maximum": maximum,
            "current": current_value,
            "current_position": min(1.0, max(0.0, position(current_value))) if current_value is not None else None,
            "samples": [{**sample, "position": position(sample["value"])} for sample in samples],
            "heat": [value / peak if peak else 0 for value in heat],
        })
    return {"bands": bands, "history_count": len(history), "current": current}
