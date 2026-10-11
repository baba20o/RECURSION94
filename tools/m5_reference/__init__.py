"""Deterministic, host-only Milestone 5 vocabulary reference."""

from .contracts import load_constants, verify_frozen_sources
from .fixtures import combined_multi_echo_fixture
from .model import VocabularyWorld

__all__ = (
    "VocabularyWorld",
    "combined_multi_echo_fixture",
    "load_constants",
    "verify_frozen_sources",
)
