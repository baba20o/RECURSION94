"""Deterministic M5 reference fixtures."""

from __future__ import annotations

from dataclasses import dataclass

from .model import (
    Actor,
    CARRYABLE_CELL,
    DEVICE_BEAM,
    DEVICE_CONVEYOR,
    DEVICE_DOOR,
    DEVICE_HACK,
    DEVICE_PRESSURE,
    DEVICE_REFLECTOR,
    DEVICE_REPAIR,
    DEVICE_SECURITY,
    DEVICE_SHIELD,
    DEVICE_SOCKET,
    DEVICE_TIMED,
    ENEMY_GUARD,
    ENEMY_PURSUER,
    ENEMY_TURRET,
    Entity,
    HAZARD_CONVEYOR,
    HAZARD_DAMAGE,
    STATE_ACTIVE,
    VocabularyWorld,
)


@dataclass(frozen=True)
class CombinedFixture:
    world: VocabularyWorld
    pressure_door_slot: int
    socket_slot: int
    socket_door_slot: int
    terminal_slot: int
    emitter_slot: int
    reflector_slot: int
    turret_slot: int
    power_cell_slot: int


def combined_multi_echo_fixture() -> CombinedFixture:
    world = VocabularyWorld(stage_id=205, attempt=2)
    world.set_actor(0, Actor(0, "echo_1", 32, 112))
    world.set_actor(1, Actor(1, "echo_2", 96, 112))
    world.set_actor(2, Actor(2, "echo_3", 260, 112))
    world.set_actor(6, Actor(6, "current", 240, 112))
    world.echo_recordings = [
        (0x08,) * 60,
        (0x20,) * 45,
        (0x00,) * 30,
    ]
    world.echo_protected = [False, False, False]

    world.add(
        "devices",
        Entity(10, DEVICE_PRESSURE, 32, 112, link=1),
    )
    pressure_door = world.add(
        "devices",
        Entity(11, DEVICE_DOOR, 64, 112, link=1),
    )
    socket = world.add(
        "devices",
        Entity(20, DEVICE_SOCKET, 128, 112, link=2),
    )
    socket_door = world.add(
        "devices",
        Entity(21, DEVICE_DOOR, 160, 112, link=2),
    )
    terminal = world.add(
        "devices",
        Entity(30, DEVICE_HACK, 240, 112),
    )
    emitter = world.add(
        "devices",
        Entity(40, DEVICE_BEAM, 32, 48, state=STATE_ACTIVE, direction=0),
    )
    reflector = world.add(
        "devices",
        Entity(41, DEVICE_REFLECTOR, 96, 48, direction=0),
    )
    world.add(
        "devices",
        Entity(50, DEVICE_TIMED, 200, 176),
    )
    world.add(
        "devices",
        Entity(51, DEVICE_CONVEYOR, 176, 176, state=STATE_ACTIVE),
    )
    world.add(
        "devices",
        Entity(52, DEVICE_SHIELD, 272, 176, state=STATE_ACTIVE),
    )
    world.add(
        "devices",
        Entity(53, DEVICE_SECURITY, 224, 176, state=STATE_ACTIVE, health=2),
    )
    world.add(
        "devices",
        Entity(54, DEVICE_REPAIR, 240, 176, link=53),
    )

    turret = world.add(
        "enemies",
        Entity(60, ENEMY_TURRET, 280, 112, timer=45, health=3),
    )
    world.add(
        "enemies",
        Entity(61, ENEMY_PURSUER, 280, 176, health=2),
    )
    world.add(
        "enemies",
        Entity(62, ENEMY_GUARD, 208, 176, timer=15, health=2),
    )
    power_cell = world.add(
        "carryables",
        Entity(70, CARRYABLE_CELL, 96, 112),
    )
    world.add(
        "hazards",
        Entity(80, HAZARD_CONVEYOR, 176, 176, radius=16, direction=0),
    )
    world.add(
        "hazards",
        Entity(81, HAZARD_DAMAGE, 304, 176, radius=12, damage=1),
    )
    world.freeze_canonical()

    return CombinedFixture(
        world=world,
        pressure_door_slot=pressure_door,
        socket_slot=socket,
        socket_door_slot=socket_door,
        terminal_slot=terminal,
        emitter_slot=emitter,
        reflector_slot=reflector,
        turret_slot=turret,
        power_cell_slot=power_cell,
    )
