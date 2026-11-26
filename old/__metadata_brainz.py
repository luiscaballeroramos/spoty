"""Compatibility exports for MusicBrainz and AcousticBrainz metadata access."""

from metadata_sources.acousticbrainz import AcousticBrainzAdapter
from metadata_sources.musicbrainz import MusicBrainzAdapter

__all__ = ["AcousticBrainzAdapter", "MusicBrainzAdapter"]
