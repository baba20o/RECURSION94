"""Deterministic host-side audio scene and bank contract compiler."""

from .compiler import (
    AUDIO_BANKS_OUTPUT,
    AUDIO_CHECKSUMS_OUTPUT,
    AUDIO_REGISTRY_OUTPUT,
    AudioArtifactBundle,
    AudioCompileError,
    AudioValidationError,
    build_artifacts,
    build_bank_binaries,
    check_generated,
    compile_audio,
    load_and_validate,
)

__all__ = [
    "AUDIO_BANKS_OUTPUT",
    "AUDIO_CHECKSUMS_OUTPUT",
    "AUDIO_REGISTRY_OUTPUT",
    "AudioArtifactBundle",
    "AudioCompileError",
    "AudioValidationError",
    "build_artifacts",
    "build_bank_binaries",
    "check_generated",
    "compile_audio",
    "load_and_validate",
]
