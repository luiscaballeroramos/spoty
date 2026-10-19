"""Build time-weighted audio distributions from listening events."""

import heapq
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
MAX_PULSE_SAMPLES = 250


def audio_value(data: dict, parameter: AudioParameter) -> float | None:
    value = data.get(parameter.key)
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value):
        return None
    if parameter.key == "loudness":
        return float(value) if value <= 0 else None
    if value < 0 or (parameter.maximum == 1 and value > 1):
        return None
    return float(value)


def build_pulse(rows: list[dict], now: float, hours: int | None = 2, expanded: bool = False) -> dict:
    current = next((row for row in rows if row.get("kind") == "current"), {})
    history = [
        row for row in rows
        if row.get("kind") == "history"
        and 0 <= now - row["played_at"]
        and (hours is None or now - row["played_at"] <= hours * 3600)
    ]
    bands = []
    for parameter in PARAMETERS + (EXTRA_PARAMETERS if expanded else ()):
        minimum = -60.0 if parameter.key == "loudness" else 0.0
        maximum = parameter.maximum
        current_value = audio_value(current.get("audio") or {}, parameter)
        if parameter.key == "tempo":
            minimum = min(
                (
                    value for row in history
                    if (value := audio_value(row.get("audio") or {}, parameter)) is not None
                ),
                default=current_value if current_value is not None else parameter.maximum,
            )
            maximum = max(
                (
                    value for row in history
                    if (value := audio_value(row.get("audio") or {}, parameter)) is not None
                ),
                default=current_value if current_value is not None else parameter.maximum,
            )
        if parameter.key == "loudness":
            lowest = min(
                (
                    value for row in history
                    if (value := audio_value(row.get("audio") or {}, parameter)) is not None
                ),
                default=0,
            )
            minimum = min(minimum, math.floor(min(lowest, current_value or 0) / 10) * 10)
        def position(value):
            if maximum == minimum:
                return 0.5 if value == minimum else (0.0 if value < minimum else 1.0)
            return (value - minimum) / (maximum - minimum)
        histogram = [0.0] * 80
        sample_heap = []
        sample_count = 0
        for index, row in enumerate(history):
            value = audio_value(row.get("audio") or {}, parameter)
            if value is None:
                continue
            age_minutes = (now - row["played_at"]) / 60
            weight = 2 ** (-age_minutes / 30)
            sample_position = position(value)
            location = min(79.0, max(0.0, sample_position * 79))
            left = int(location)
            fraction = location - left
            histogram[left] += weight * (1 - fraction)
            if left < 79:
                histogram[left + 1] += weight * fraction
            sample_count += 1
            sample = {
                "value": value,
                "weight": weight,
                "name": row.get("name") or "Sin título",
                "age_minutes": age_minutes,
                "position": sample_position,
            }
            candidate = (-age_minutes, index, sample)
            if len(sample_heap) < MAX_PULSE_SAMPLES:
                heapq.heappush(sample_heap, candidate)
            elif candidate > sample_heap[0]:
                heapq.heapreplace(sample_heap, candidate)
        heat = [
            sum(
                weight * math.exp(-0.5 * (((index - center) / 79) / 0.035) ** 2)
                for center, weight in enumerate(histogram)
            )
            for index in range(80)
        ]
        peak = max(heat, default=0)
        samples = sorted(
            (entry[2] for entry in sample_heap),
            key=lambda sample: sample["age_minutes"],
        )
        bands.append({
            "parameter": parameter,
            "minimum": minimum,
            "maximum": maximum,
            "current": current_value,
            "current_position": min(1.0, max(0.0, position(current_value))) if current_value is not None else None,
            "samples": samples,
            "sample_count": sample_count,
            "heat": [value / peak if peak else 0 for value in heat],
        })
    return {"bands": bands, "history_count": len(history), "current": current}
