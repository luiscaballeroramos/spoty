from __future__ import annotations

from pathlib import Path

import lameenc
import numpy as np
import soundcard as sc
import sounddevice as sd


def _record_system_audio(frame_count: int, sample_rate: int) -> np.ndarray:
    """Capture system output audio via loopback using available backend.

    Workflow:
    1) Try soundcard loopback from the default speaker.
    2) If unavailable, try sounddevice with any loopback-like input device.
    3) Raise a clear error if neither backend can capture system audio.
    """
    # First choice: soundcard loopback, usually the most direct system-output capture.
    speaker = sc.default_speaker()
    if speaker is not None:
        loopback_mic = sc.get_microphone(str(speaker.name), include_loopback=True)
        if loopback_mic is not None:
            # Record exactly frame_count samples from system output.
            with loopback_mic.recorder(samplerate=sample_rate) as rec:
                return rec.record(numframes=frame_count)

    # Fallback: scan input devices and use one that exposes loopback capture.
    devices = sd.query_devices()
    for index, device in enumerate(devices):
        name = str(device.get("name", "")).lower()
        if device.get("max_input_channels", 0) > 0 and "loopback" in name:
            # Record float32 samples from the selected loopback-capable device.
            samples = sd.rec(
                frame_count,
                samplerate=sample_rate,
                channels=2,
                dtype="float32",
                device=index,
            )
            # Block until capture completes so callers receive a full buffer.
            sd.wait()
            return samples

    raise RuntimeError(
        "No loopback capture device found. Enable Stereo Mix or install a virtual audio cable."
    )


def record_system_audio_to_mp3(
    output_path: str = "output.mp3",
    duration_seconds: int = 10,
    sample_rate: int = 48000,
    bitrate_kbps: int = 192,
) -> Path:
    """Record system audio and save it as an MP3 file.

    End-to-end workflow:
    1) Validate input parameters.
    2) Capture raw audio frames from system output (loopback).
    3) Convert float audio to 16-bit PCM bytes.
    4) Encode PCM bytes to MP3 with lameenc.
    5) Write the MP3 bytes to disk and return the output path.
    """
    # Basic validation avoids silent failures and hard-to-debug behavior.
    if duration_seconds <= 0:
        raise ValueError("duration_seconds must be > 0")

    if sample_rate <= 0:
        raise ValueError("sample_rate must be > 0")

    output = Path(output_path)
    # Ensure destination directory exists before writing the MP3 file.
    output.parent.mkdir(parents=True, exist_ok=True)

    # Convert duration in seconds to number of frames at the chosen sample rate.
    frame_count = sample_rate * duration_seconds
    samples = _record_system_audio(frame_count=frame_count, sample_rate=sample_rate)

    # Convert float samples in [-1.0, 1.0] to 16-bit PCM bytes for MP3 encoding.
    samples = np.clip(samples, -1.0, 1.0)
    pcm_int16 = (samples * 32767.0).astype(np.int16)

    # Configure MP3 encoder to match captured audio stream properties.
    channels = 1 if pcm_int16.ndim == 1 else pcm_int16.shape[1]
    encoder = lameenc.Encoder()
    encoder.set_bit_rate(bitrate_kbps)
    encoder.set_in_sample_rate(sample_rate)
    encoder.set_channels(channels)
    encoder.set_quality(2)

    # Encode full PCM buffer, then flush delayed encoder frames.
    mp3_bytes = encoder.encode(pcm_int16.tobytes())
    mp3_bytes += encoder.flush()

    # Persist final MP3 bytes on disk.
    output.write_bytes(mp3_bytes)

    return output


if __name__ == "__main__":
    # Simple script entrypoint for manual testing.
    saved_file = record_system_audio_to_mp3(output_path="recording.mp3", duration_seconds=10)
    print(f"Saved MP3: {saved_file}")
