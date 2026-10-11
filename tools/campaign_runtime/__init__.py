"""Generated cartridge-runtime adapter for validated campaign content."""

from .compiler import (
    OUTPUT_ASSEMBLY,
    OUTPUT_REPORT,
    RuntimeCompileError,
    RuntimeArtifacts,
    build_runtime_artifacts,
    check_generated,
    compile_runtime,
)

__all__ = [
    "OUTPUT_ASSEMBLY",
    "OUTPUT_REPORT",
    "RuntimeArtifacts",
    "RuntimeCompileError",
    "build_runtime_artifacts",
    "check_generated",
    "compile_runtime",
]
