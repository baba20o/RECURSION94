"""Frozen ABI and proposed vocabulary-layout contract helpers."""

from __future__ import annotations

import ast
import hashlib
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LAYOUT_SOURCES = (
    ROOT / "src/game/engine_layout.asm",
    ROOT / "src/gameplay/gameplay_layout.asm",
    ROOT / "src/gameplay/vocabulary_layout.asm",
)
FROZEN_SOURCE_SHA256 = {
    "src/game/engine_layout.asm": "40e2876cc36a02057a9a2254d4c381e9a00bad4939c9fd5426d890cc70f409be",
    "src/gameplay/gameplay_layout.asm": "41c23b178e425fa723a5889a31e4a6fa6c89477f2b1d94a77f26d471d7fdeaa7",
    "src/game/projection.asm": "f863d0ea4730da3767144c6b5336c7b012754fb56adf4efb0651734f9663ee64",
    "src/gameplay/gameplay.asm": "e8b48b999d924d6162cf0da46e7a051eba72f046efb970c26957bb2e62b49fd9",
    "src/main.asm": "cd986429c4eeaaee61c5b587713ff6e5d0c846a491c4d47089fc69d40d3c92a9",
    "src/boot/startup.asm": "3ca2f70abe74c9b9f05cb7995e3a98ec6bde79f1b4b40e0eb42fcd2e485b1200",
    "tools/campaign_runtime/compiler.py": "74fd9c06d49f24798c063fa37b50306bc7213b1c9e29b97fd82948800a03d58e",
    "tools/content_compiler/compiler.py": "f0edfc50a19de7bcb5cf6f5db80c56c8a78679bf547e67c25b84aebf633a717d",
}
EQU_RE = re.compile(
    r"^\s*([A-Z][A-Z0-9_]*)\s+equ\s+(.+?)\s*$", re.IGNORECASE
)


def _safe_eval(expression: str, symbols: dict[str, int]) -> int:
    normalized = re.sub(r"\$([0-9A-Fa-f]+)", r"0x\1", expression)
    normalized = normalized.replace("/", "//")
    tree = ast.parse(normalized, mode="eval")
    allowed = (
        ast.Expression,
        ast.Constant,
        ast.Name,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.FloorDiv,
        ast.BitAnd,
        ast.BitOr,
        ast.LShift,
        ast.RShift,
        ast.UnaryOp,
        ast.USub,
        ast.UAdd,
        ast.BinOp,
        ast.Load,
    )
    if any(not isinstance(node, allowed) for node in ast.walk(tree)):
        raise ValueError(f"unsupported equ expression: {expression}")
    return int(eval(compile(tree, "<m5-equ>", "eval"), {"__builtins__": {}}, symbols))


def load_constants() -> dict[str, int]:
    pending: list[tuple[str, str]] = []
    for path in LAYOUT_SOURCES:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.split(";", 1)[0].strip()
            match = EQU_RE.match(line)
            if match:
                pending.append((match.group(1), match.group(2).strip()))

    symbols: dict[str, int] = {}
    while pending:
        deferred: list[tuple[str, str]] = []
        progressed = False
        for name, expression in pending:
            try:
                symbols[name] = _safe_eval(expression, symbols)
            except (NameError, ValueError):
                deferred.append((name, expression))
            else:
                progressed = True
        if not progressed:
            unresolved = ", ".join(name for name, _expression in deferred)
            raise ValueError(f"unresolved M5 layout symbols: {unresolved}")
        pending = deferred
    return symbols


def verify_frozen_sources() -> dict[str, str]:
    actual: dict[str, str] = {}
    for relative, expected in FROZEN_SOURCE_SHA256.items():
        digest = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        if digest != expected:
            raise ValueError(
                f"frozen source drift: {relative}: expected {expected}, got {digest}"
            )
        actual[relative] = digest
    return actual
