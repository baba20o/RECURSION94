#!/usr/bin/env python3
"""Independent bounded reference model for the 68000 VDP DMA queue."""

from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any


_EQU_PATTERN = re.compile(
    r"(?m)^\s*([A-Za-z_][A-Za-z0-9_]*)\s+equ\s+([^;\r\n]+)"
)
_HEX_PATTERN = re.compile(r"\$([0-9A-Fa-f]+)")
_CANARY_BEFORE = 0xA5
_CANARY_AFTER = 0x5A


class DmaContractError(ValueError):
    """The assembly contract could not be resolved safely."""


def _evaluate_equate(
    name: str,
    expressions: dict[str, str],
    resolved: dict[str, int],
    active: set[str],
) -> int:
    if name in resolved:
        return resolved[name]
    if name in active:
        raise DmaContractError(f"cyclic assembly equate involving {name}")
    try:
        expression = expressions[name]
    except KeyError as error:
        raise DmaContractError(f"missing assembly equate {name}") from error
    active.add(name)
    translated = _HEX_PATTERN.sub(r"0x\1", expression.strip())
    try:
        tree = ast.parse(translated, mode="eval")
    except SyntaxError as error:
        raise DmaContractError(
            f"cannot parse assembly equate {name}: {expression!r}"
        ) from error

    def evaluate(node: ast.AST) -> int:
        if isinstance(node, ast.Expression):
            return evaluate(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, int):
            return node.value
        if isinstance(node, ast.Name):
            return _evaluate_equate(
                node.id, expressions, resolved, active
            )
        if isinstance(node, ast.UnaryOp) and isinstance(
            node.op, (ast.UAdd, ast.USub, ast.Invert)
        ):
            value = evaluate(node.operand)
            if isinstance(node.op, ast.UAdd):
                return value
            if isinstance(node.op, ast.USub):
                return -value
            return ~value
        if isinstance(node, ast.BinOp):
            left = evaluate(node.left)
            right = evaluate(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, (ast.Div, ast.FloorDiv)):
                if right == 0:
                    raise DmaContractError(
                        f"zero divisor in assembly equate {name}"
                    )
                return left // right
            if isinstance(node.op, ast.LShift):
                return left << right
            if isinstance(node.op, ast.RShift):
                return left >> right
            if isinstance(node.op, ast.BitOr):
                return left | right
            if isinstance(node.op, ast.BitAnd):
                return left & right
        raise DmaContractError(
            f"unsupported syntax in assembly equate {name}: {expression!r}"
        )

    value = evaluate(tree)
    active.remove(name)
    resolved[name] = value
    return value


def load_equates(paths: list[Path]) -> tuple[dict[str, int], dict[str, str]]:
    expressions: dict[str, str] = {}
    digest = hashlib.sha256()
    for path in paths:
        payload = path.read_bytes()
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(payload)
        for name, expression in _EQU_PATTERN.findall(
            payload.decode("utf-8", errors="strict")
        ):
            if name in expressions:
                raise DmaContractError(f"duplicate assembly equate {name}")
            expressions[name] = expression.strip()
    resolved: dict[str, int] = {}
    for name in expressions:
        try:
            _evaluate_equate(name, expressions, resolved, set())
        except DmaContractError as error:
            if not str(error).startswith("missing assembly equate"):
                raise
    return resolved, {
        "sha256": digest.hexdigest(),
        "files": ",".join(path.as_posix() for path in paths),
    }


@dataclass(frozen=True)
class DmaContract:
    queue_base: int
    entries_base: int
    input_state_base: int
    capacity: int
    entry_bytes: int
    required_flag: int
    processed_flag: int
    priority_mask: int
    priority_levels: int
    word_limit: int
    source_window_bytes: int
    work_ram_base: int
    work_ram_end: int
    vram_ranges: tuple[tuple[int, int], ...]
    assembly_sha256: str

    @property
    def queue_storage_end(self) -> int:
        return self.entries_base + self.capacity * self.entry_bytes


def load_dma_contract(root: Path) -> DmaContract:
    paths = [
        root / "src/hardware/memory_map.asm",
        root / "src/hardware/dma.asm",
    ]
    values, metadata = load_equates(paths)
    required = (
        "DMA_QUEUE_BASE",
        "DMA_QUEUE_ENTRIES",
        "INPUT_STATE_BASE",
        "DMA_QUEUE_CAPACITY",
        "DMA_QUEUE_ENTRY_BYTES",
        "DMA_FLAG_REQUIRED",
        "DMA_FLAG_PROCESSED",
        "DMA_PRIORITY_MASK",
        "DMA_PRIORITY_LEVELS",
        "DMA_ORDINARY_WORD_LIMIT",
        "DMA_SOURCE_WINDOW_BYTES",
        "WORK_RAM_BASE",
        "DMA_VRAM_DYNAMIC_START",
        "DMA_VRAM_DYNAMIC_END",
        "VRAM_PLANE_A",
        "DMA_VRAM_PLANE_A_END",
        "VRAM_WINDOW",
        "DMA_VRAM_WINDOW_END",
        "VRAM_PLANE_B",
        "DMA_VRAM_PLANE_B_END",
        "VRAM_SAT",
        "DMA_VRAM_SAT_END",
        "VRAM_HSCROLL",
        "DMA_VRAM_HSCROLL_END",
    )
    missing = [name for name in required if name not in values]
    if missing:
        raise DmaContractError(f"unresolved DMA assembly equates: {missing}")
    contract = DmaContract(
        queue_base=values["DMA_QUEUE_BASE"],
        entries_base=values["DMA_QUEUE_ENTRIES"],
        input_state_base=values["INPUT_STATE_BASE"],
        capacity=values["DMA_QUEUE_CAPACITY"],
        entry_bytes=values["DMA_QUEUE_ENTRY_BYTES"],
        required_flag=values["DMA_FLAG_REQUIRED"],
        processed_flag=values["DMA_FLAG_PROCESSED"],
        priority_mask=values["DMA_PRIORITY_MASK"],
        priority_levels=values["DMA_PRIORITY_LEVELS"],
        word_limit=values["DMA_ORDINARY_WORD_LIMIT"],
        source_window_bytes=values["DMA_SOURCE_WINDOW_BYTES"],
        work_ram_base=values["WORK_RAM_BASE"],
        work_ram_end=0x01000000,
        vram_ranges=(
            (
                values["DMA_VRAM_DYNAMIC_START"],
                values["DMA_VRAM_DYNAMIC_END"],
            ),
            (values["VRAM_PLANE_A"], values["DMA_VRAM_PLANE_A_END"]),
            (values["VRAM_WINDOW"], values["DMA_VRAM_WINDOW_END"]),
            (values["VRAM_PLANE_B"], values["DMA_VRAM_PLANE_B_END"]),
            (values["VRAM_SAT"], values["DMA_VRAM_SAT_END"]),
            (values["VRAM_HSCROLL"], values["DMA_VRAM_HSCROLL_END"]),
        ),
        assembly_sha256=metadata["sha256"],
    )
    if contract.queue_storage_end > contract.input_state_base:
        raise DmaContractError("DMA queue overlaps the input-state region")
    return contract


@dataclass(frozen=True)
class DmaEntry:
    identifier: str
    source: int
    destination: int
    words: int
    flags: int
    age: int = 0


class DmaReferenceQueue:
    """Bounded queue model whose backing bytes expose adjacent canaries."""

    def __init__(self, contract: DmaContract) -> None:
        self.contract = contract
        self._entries: list[DmaEntry] = []
        self._storage = bytearray(
            [_CANARY_BEFORE]
            + [0] * (contract.capacity * contract.entry_bytes)
            + [_CANARY_AFTER]
        )
        self.overflow_count = 0
        self.invalid_count = 0
        self.deferred_count = 0
        self.required_drop_count = 0
        self.high_water = 0

    @staticmethod
    def _increment(value: int) -> int:
        return min(value + 1, 0xFFFF)

    @property
    def depth(self) -> int:
        return len(self._entries)

    @property
    def entries(self) -> tuple[DmaEntry, ...]:
        return tuple(self._entries)

    @property
    def canaries(self) -> tuple[int, int]:
        return self._storage[0], self._storage[-1]

    def _sync_storage(self) -> None:
        start = 1
        end = start + self.contract.capacity * self.contract.entry_bytes
        self._storage[start:end] = bytes(end - start)
        for index, entry in enumerate(self._entries):
            offset = start + index * self.contract.entry_bytes
            payload = (
                entry.source.to_bytes(4, "big")
                + entry.destination.to_bytes(2, "big")
                + entry.words.to_bytes(2, "big")
                + entry.flags.to_bytes(2, "big")
                + entry.age.to_bytes(2, "big")
            )
            self._storage[offset : offset + self.contract.entry_bytes] = payload

    def _valid_destination(self, destination: int, byte_count: int) -> bool:
        end = destination + byte_count
        if end > 0x10000:
            return False
        return any(
            start <= destination < limit and end <= limit
            for start, limit in self.contract.vram_ranges
        )

    def _valid_source(self, source: int, byte_count: int) -> bool:
        if source & 1:
            return False
        owned = source < 0x00100000 or (
            self.contract.work_ram_base
            <= source
            < self.contract.work_ram_end
        )
        if not owned:
            return False
        offset = source & (self.contract.source_window_bytes - 1)
        return offset + byte_count <= self.contract.source_window_bytes

    def enqueue(
        self,
        identifier: str,
        *,
        source: int,
        destination: int,
        words: int,
        flags: int,
    ) -> bool:
        allowed_flags = (
            self.contract.required_flag | self.contract.priority_mask
        )
        numeric_fields_valid = (
            0 <= source <= 0xFFFFFFFF
            and 0 <= destination <= 0xFFFF
            and 0 <= words <= 0xFFFF
            and 0 <= flags <= 0xFFFF
        )
        byte_count = words * 2
        valid = (
            numeric_fields_valid
            and not (flags & ~allowed_flags)
            and 0 < words <= self.contract.word_limit
            and not (destination & 1)
            and self._valid_source(source, byte_count)
            and self._valid_destination(destination, byte_count)
        )
        if not valid:
            self.invalid_count = self._increment(self.invalid_count)
            if flags & self.contract.required_flag:
                self.required_drop_count = self._increment(
                    self.required_drop_count
                )
            return False
        if self.depth >= self.contract.capacity:
            self.overflow_count = self._increment(self.overflow_count)
            if flags & self.contract.required_flag:
                self.required_drop_count = self._increment(
                    self.required_drop_count
                )
            return False
        self._entries.append(
            DmaEntry(
                identifier=identifier,
                source=source,
                destination=destination,
                words=words,
                flags=flags,
            )
        )
        self.high_water = max(self.high_water, self.depth)
        self._sync_storage()
        return True

    def commit(self) -> dict[str, Any]:
        committed_words = 0
        issued: list[DmaEntry] = []
        processed: set[int] = set()
        entries = list(self._entries)
        for required in (True, False):
            for priority in range(self.contract.priority_levels):
                for index, entry in enumerate(entries):
                    is_required = bool(
                        entry.flags & self.contract.required_flag
                    )
                    if (
                        is_required != required
                        or entry.flags & self.contract.priority_mask
                        != priority
                    ):
                        continue
                    if committed_words + entry.words <= self.contract.word_limit:
                        committed_words += entry.words
                        issued.append(entry)
                        processed.add(index)
                    elif required:
                        processed.add(index)
                        self.required_drop_count = self._increment(
                            self.required_drop_count
                        )
                    else:
                        entries[index] = replace(
                            entry, age=min(entry.age + 1, 0xFFFF)
                        )
                        self.deferred_count = self._increment(
                            self.deferred_count
                        )
        self._entries = [
            entry
            for index, entry in enumerate(entries)
            if index not in processed
        ]
        self._sync_storage()
        return {
            "issued": [entry.identifier for entry in issued],
            "words": committed_words,
            "deferred": [entry.identifier for entry in self._entries],
            "required_drop_count": self.required_drop_count,
        }

