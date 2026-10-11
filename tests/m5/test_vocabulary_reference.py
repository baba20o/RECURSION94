from __future__ import annotations

import copy
import hashlib
import unittest

from tools.m5_reference.fixtures import combined_multi_echo_fixture
from tools.m5_reference.model import (
    ACTOR_CAPACITY,
    ACTOR_PHASED,
    ANCHOR_SOCKETED_CELL,
    CARRYABLE_CAPACITY,
    DEVICE_CAPACITY,
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
    ENEMY_CAPACITY,
    ENEMY_GUARD,
    ENEMY_PURSUER,
    ENEMY_TURRET,
    Entity,
    HAZARD_CAPACITY,
    HAZARD_CONVEYOR,
    HAZARD_DAMAGE,
    INPUT_B,
    INPUT_C,
    INPUT_LEFT,
    INPUT_RIGHT,
    PROJECTILE_CAPACITY,
    PROJECTILE_ENEMY,
    PROJECTILE_PLAYER,
    RANK_OPTIMIZED,
    RANK_STABLE,
    RANK_TRANSCENDENT,
    RESULT_ANCHOR_FULL,
    RESULT_ACTIVATED,
    RESULT_COOLDOWN,
    RESULT_DASHED,
    RESULT_HACKED,
    RESULT_INVALID,
    RESULT_NO_TARGET,
    RESULT_OK,
    RESULT_PICKED_UP,
    RESULT_REPAIRED,
    RESULT_SOCKETED,
    STATE_ACTIVE,
    STATE_ANCHORED,
    STATE_HACKED,
    STATE_OCCUPIED,
    STATE_OPEN,
    UPDATE_UNIT_LIMIT,
    Actor,
    TimelineUiReference,
    VocabularyWorld,
)


class PrimitiveReferenceTests(unittest.TestCase):
    def test_c_dash_phase_has_fixed_duration_and_cooldown(self) -> None:
        world = VocabularyWorld()
        world.set_actor(6, Actor(6, "current", 40, 40))
        actor = world.actors[6]
        result = world.apply_dash_phase(
            6, INPUT_RIGHT | INPUT_C, INPUT_C
        )
        self.assertEqual(result, RESULT_DASHED)
        self.assertTrue(actor.flags & ACTOR_PHASED)
        self.assertEqual((actor.vx, actor.vy), (6, 0))
        self.assertEqual(
            world.apply_dash_phase(6, INPUT_C, INPUT_C), RESULT_COOLDOWN
        )
        for _ in range(6):
            world._tick_actor_extensions()
        self.assertFalse(actor.flags & ACTOR_PHASED)
        self.assertEqual(actor.dash_cooldown, 18)

    def test_projectiles_apply_damage_and_phase_rejects_damage(self) -> None:
        world = VocabularyWorld()
        world.set_actor(6, Actor(6, "current", 100, 100, health=3))
        enemy_slot = world.add(
            "enemies", Entity(1, ENEMY_TURRET, 108, 100, health=2)
        )
        world.add(
            "projectiles",
            Entity(0, PROJECTILE_PLAYER, 100, 100, timer=5, vx=0, vy=0),
        )
        world.update_projectiles()
        self.assertEqual(world.enemies[enemy_slot].health, 1)

        world.add(
            "projectiles",
            Entity(1, PROJECTILE_ENEMY, 100, 100, timer=5, vx=0, vy=0),
        )
        world.update_projectiles()
        self.assertEqual(world.actors[6].health, 2)
        world.actors[6].flags |= ACTOR_PHASED
        self.assertFalse(world.damage_actor(6))
        self.assertEqual(world.actors[6].health, 2)

    def test_b_priority_pickup_socket_terminal_switch_and_repair(self) -> None:
        world = VocabularyWorld()
        world.set_actor(6, Actor(6, "current", 64, 64))
        cell = world.add("carryables", Entity(9, 0x0100, 64, 64))
        terminal = world.add("devices", Entity(10, DEVICE_HACK, 64, 64))
        self.assertEqual(world.devices[terminal].duration, 1)
        self.assertEqual(world.resolve_interaction(6), RESULT_PICKED_UP)
        self.assertFalse(world.devices[terminal].state & STATE_HACKED)
        socket = world.add("devices", Entity(11, DEVICE_SOCKET, 80, 64))
        world.actors[6].x = 80
        self.assertEqual(world.resolve_interaction(6), RESULT_SOCKETED)
        self.assertTrue(world.devices[socket].state & STATE_OCCUPIED)
        self.assertEqual(world.carryables[cell].owner, 11)

        world.actors[6].x = 64
        self.assertEqual(world.resolve_interaction(6), RESULT_HACKED)
        self.assertTrue(world.devices[terminal].state & STATE_HACKED)

        security = world.add(
            "devices",
            Entity(12, DEVICE_SECURITY, 160, 64, state=STATE_ACTIVE | 0x0008),
        )
        repair = world.add(
            "devices", Entity(13, DEVICE_REPAIR, 104, 64, link=12)
        )
        world.actors[6].x = 104
        self.assertEqual(world.resolve_interaction(6), RESULT_REPAIRED)
        self.assertTrue(world.devices[repair].state & STATE_ACTIVE)
        self.assertFalse(world.devices[security].state & 0x0008)

    def test_terminal_hold_completes_on_exact_duration_step(self) -> None:
        world = VocabularyWorld()
        world.set_actor(6, Actor(6, "current", 64, 64))
        terminal_slot = world.add(
            "devices", Entity(10, DEVICE_HACK, 64, 64, duration=3)
        )
        terminal = world.devices[terminal_slot]
        assert terminal is not None

        world.step({6: INPUT_B})
        self.assertEqual(terminal.timer, 1)
        self.assertEqual(terminal.owner, 6)
        self.assertEqual(world.actors[6].last_action, RESULT_ACTIVATED)
        self.assertTrue(terminal.state & STATE_ACTIVE)
        self.assertFalse(terminal.state & STATE_HACKED)

        world.set_actor(
            0, Actor(0, "echo_1", 64, 64, held=INPUT_B)
        )
        self.assertEqual(world.sustain_terminal_hold(0), RESULT_NO_TARGET)
        self.assertEqual(terminal.timer, 1)
        self.assertEqual(terminal.owner, 6)

        world.step({6: INPUT_B})
        self.assertEqual(terminal.timer, 2)
        self.assertFalse(terminal.state & STATE_HACKED)

        world.step({6: INPUT_B})
        self.assertEqual(terminal.timer, 3)
        self.assertEqual(world.actors[6].last_action, RESULT_HACKED)
        self.assertTrue(terminal.state & STATE_HACKED)

        world.set_actor(0, Actor(0, "echo_1", 64, 64))
        self.assertEqual(world.resolve_interaction(0), RESULT_NO_TARGET)
        self.assertEqual(terminal.timer, 3)
        self.assertEqual(terminal.owner, 6)

    def test_terminal_hold_interruptions_reset_and_require_a_new_edge(
        self,
    ) -> None:
        world = VocabularyWorld()
        world.set_actor(6, Actor(6, "current", 64, 64, health=1))
        terminal_slot = world.add(
            "devices", Entity(10, DEVICE_HACK, 64, 64, duration=3)
        )
        terminal = world.devices[terminal_slot]
        assert terminal is not None

        world.step({6: INPUT_B})
        world.actors[6].x = 128
        world.step({6: INPUT_B})
        self.assertEqual(terminal.timer, 0)
        self.assertIsNone(terminal.owner)
        self.assertFalse(terminal.state & (STATE_ACTIVE | STATE_HACKED))

        world.actors[6].x = 64
        world.step({6: INPUT_B})
        self.assertEqual(terminal.timer, 0)

        world.step({6: 0})
        world.step({6: INPUT_B})
        self.assertEqual(terminal.timer, 1)
        world.step({6: 0})
        self.assertEqual(terminal.timer, 0)
        self.assertIsNone(terminal.owner)

        world.step({6: INPUT_B})
        self.assertEqual(terminal.timer, 1)
        self.assertTrue(world.damage_actor(6))
        self.assertFalse(world.actors[6].active)
        self.assertEqual(terminal.timer, 0)
        self.assertIsNone(terminal.owner)
        self.assertFalse(terminal.state & STATE_ACTIVE)

    def test_security_node_is_instant_and_keeps_device_priority(self) -> None:
        world = VocabularyWorld()
        world.set_actor(6, Actor(6, "current", 64, 64))
        timed_slot = world.add(
            "devices", Entity(10, DEVICE_TIMED, 64, 64)
        )
        security_slot = world.add(
            "devices", Entity(11, DEVICE_SECURITY, 64, 64, duration=3)
        )

        self.assertEqual(world.resolve_interaction(6), RESULT_HACKED)
        self.assertTrue(world.devices[security_slot].state & STATE_HACKED)
        self.assertFalse(world.devices[timed_slot].state & STATE_ACTIVE)

    def test_pressure_and_timed_doors_are_deterministic(self) -> None:
        world = VocabularyWorld()
        world.set_actor(0, Actor(0, "echo_1", 32, 32))
        pressure = world.add(
            "devices", Entity(1, DEVICE_PRESSURE, 32, 32, link=4)
        )
        door = world.add("devices", Entity(2, DEVICE_DOOR, 64, 32, link=4))
        timed = world.add(
            "devices",
            Entity(3, DEVICE_TIMED, 100, 32, state=STATE_ACTIVE, timer=2, link=5),
        )
        timed_door = world.add(
            "devices", Entity(4, DEVICE_DOOR, 120, 32, link=5)
        )
        world.update_devices()
        self.assertTrue(world.devices[pressure].state & STATE_ACTIVE)
        self.assertTrue(world.devices[door].state & STATE_OPEN)
        self.assertTrue(world.devices[timed_door].state & STATE_OPEN)
        world.update_devices()
        world.update_devices()
        self.assertFalse(world.devices[timed].state & STATE_ACTIVE)
        self.assertFalse(world.devices[timed_door].state & STATE_OPEN)

    def test_device_and_hazard_conveyors_move_on_exact_axes(self) -> None:
        world = VocabularyWorld()
        world.set_actor(0, Actor(0, "echo_1", 32, 32))
        conveyor = world.add(
            "devices",
            Entity(
                1,
                DEVICE_CONVEYOR,
                32,
                32,
                state=STATE_ACTIVE,
                direction=0,
            ),
        )
        world.add(
            "hazards",
            Entity(2, HAZARD_CONVEYOR, 33, 32, direction=1),
        )

        world.update_devices()
        self.assertEqual((world.actors[0].x, world.actors[0].y), (33, 32))
        world.update_hazards()
        self.assertEqual((world.actors[0].x, world.actors[0].y), (33, 33))
        self.assertEqual(world.resolve_interaction(0), RESULT_ACTIVATED)
        self.assertEqual(world.devices[conveyor].direction, 1)
        world.update_devices()
        self.assertEqual((world.actors[0].x, world.actors[0].y), (33, 34))

    def test_beam_reflection_and_lowest_target_tie_break(self) -> None:
        fixture = combined_multi_echo_fixture()
        world = fixture.world
        world.build_beams()
        self.assertEqual(len(world.beams), 2)
        self.assertTrue(all(segment.reflected for segment in world.beams))
        self.assertEqual(world.beams[0].length, 64)
        world.update_enemies()
        self.assertEqual(world.enemy_targets[fixture.turret_slot], 2)

        tie = VocabularyWorld()
        tie.set_actor(0, Actor(0, "echo_1", 40, 50))
        tie.set_actor(1, Actor(1, "echo_2", 60, 50))
        enemy = Entity(1, ENEMY_GUARD, 50, 50)
        self.assertEqual(tie.select_target(enemy), 0)

    def test_turret_guard_and_pursuer_behavior_is_deterministic(self) -> None:
        def run_fixture() -> tuple[object, ...]:
            world = VocabularyWorld()
            world.set_actor(0, Actor(0, "echo_1", 100, 100))
            world.set_actor(1, Actor(1, "echo_2", 240, 100))
            turret = world.add(
                "enemies",
                Entity(10, ENEMY_TURRET, 120, 100, timer=0),
            )
            pursuer = world.add(
                "enemies",
                Entity(11, ENEMY_PURSUER, 130, 100),
            )
            guard = world.add(
                "enemies",
                Entity(12, ENEMY_GUARD, 130, 100, timer=0),
            )
            world.update_enemies()
            projectiles = tuple(
                (
                    item.owner,
                    item.x,
                    item.y,
                    item.vx,
                    item.vy,
                    item.timer,
                )
                for item in world.projectiles
                if item is not None and item.active
            )
            return (
                tuple(world.enemy_targets[:3]),
                (
                    world.enemies[turret].x,
                    world.enemies[turret].y,
                    world.enemies[turret].timer,
                ),
                (
                    world.enemies[pursuer].x,
                    world.enemies[pursuer].y,
                    world.enemies[pursuer].vx,
                    world.enemies[pursuer].vy,
                ),
                (
                    world.enemies[guard].x,
                    world.enemies[guard].y,
                    world.enemies[guard].vx,
                    world.enemies[guard].vy,
                    world.enemies[guard].timer,
                ),
                projectiles,
            )

        first = run_fixture()
        self.assertEqual(first, run_fixture())
        self.assertEqual(first[0], (0, 0, 0))
        self.assertEqual(first[1], (120, 100, 45))
        self.assertEqual(first[2], (129, 100, -1, 0))
        self.assertEqual(first[3], (129, 100, -1, 0, 45))
        self.assertEqual(
            first[4],
            (
                (10, 120, 100, -2, 0, 60),
                (12, 129, 100, -2, 0, 60),
            ),
        )

    def test_shield_precedes_hazard_damage(self) -> None:
        world = VocabularyWorld()
        world.set_actor(6, Actor(6, "current", 40, 40, health=3))
        world.add(
            "devices",
            Entity(1, DEVICE_SHIELD, 40, 40, state=STATE_ACTIVE),
        )
        hazard = world.add(
            "hazards", Entity(2, HAZARD_DAMAGE, 40, 40, damage=1)
        )
        world.update_hazards()
        self.assertEqual(world.actors[6].health, 3)
        world.devices[0].state = 0
        world.update_hazards()
        self.assertEqual(world.actors[6].health, 2)
        self.assertTrue(world.hazards[hazard].active)


class PersistenceAndResultTests(unittest.TestCase):
    def test_reset_restores_ordinary_state_and_reapplies_only_anchor(self) -> None:
        fixture = combined_multi_echo_fixture()
        world = fixture.world
        self.assertEqual(world.resolve_interaction(1), RESULT_PICKED_UP)
        world.actors[1].x = 128
        self.assertEqual(world.resolve_interaction(1), RESULT_SOCKETED)
        self.assertEqual(
            world.set_anchor(ANCHOR_SOCKETED_CELL, 70, 20), RESULT_OK
        )
        pressure = world.devices[0]
        pressure.state |= STATE_ACTIVE
        world.projectiles[0] = Entity(99, PROJECTILE_ENEMY, 10, 10)
        recordings = copy.deepcopy(world.echo_recordings)
        world.reset_attempt()
        self.assertEqual(world.echo_recordings, recordings)
        self.assertIsNone(world.projectiles[0])
        self.assertFalse(world.devices[0].state & STATE_ACTIVE)
        self.assertTrue(
            world.devices[fixture.socket_slot].state
            & (STATE_OCCUPIED | STATE_ANCHORED)
        )
        self.assertEqual(world.carryables[fixture.power_cell_slot].owner, 20)

    def test_anchor_capacity_and_echo_replacement_are_explicit(self) -> None:
        world = VocabularyWorld()
        self.assertEqual(world.set_anchor(1, 1, 1), RESULT_OK)
        self.assertEqual(world.set_anchor(1, 2, 1), RESULT_ANCHOR_FULL)
        world.echo_recordings = [(index,) for index in range(6)]
        world.echo_protected = [True, False, False, False, False, False]
        self.assertEqual(world.request_replacement(0), RESULT_INVALID)
        self.assertEqual(world.request_replacement(1), RESULT_OK)
        self.assertEqual(world.commit_echo((0xFF, INPUT_B)), RESULT_OK)
        self.assertEqual(len(world.echo_recordings), 6)
        self.assertEqual(world.echo_recordings[-1], (0x70, INPUT_B))

    def test_timeline_ui_selection_replacement_fixture_is_deterministic(
        self,
    ) -> None:
        def run_fixture() -> tuple[object, ...]:
            world = VocabularyWorld()
            world.echo_recordings = [(index,) for index in range(6)]
            world.echo_protected = [True, False, False, False, False, False]
            timeline = TimelineUiReference()
            results = []

            results.append(
                timeline.step(
                    world, start_held=True, held=0, pressed=0
                )
            )
            results.append(
                timeline.step(
                    world, start_held=False, held=0, pressed=0
                )
            )
            results.append(
                timeline.step(
                    world,
                    start_held=False,
                    held=INPUT_LEFT,
                    pressed=INPUT_LEFT,
                )
            )
            left_wrap = timeline.cursor
            results.append(
                timeline.step(
                    world,
                    start_held=False,
                    held=INPUT_RIGHT,
                    pressed=INPUT_RIGHT,
                )
            )
            right_wrap = timeline.cursor
            results.append(
                timeline.step(
                    world,
                    start_held=False,
                    held=INPUT_C,
                    pressed=INPUT_C,
                    recording=(INPUT_B,),
                )
            )
            protected_open = timeline.open
            results.append(
                timeline.step(
                    world, start_held=False, held=0, pressed=0
                )
            )
            results.append(
                timeline.step(
                    world,
                    start_held=False,
                    held=INPUT_RIGHT,
                    pressed=INPUT_RIGHT,
                )
            )
            selected = timeline.cursor
            results.append(
                timeline.step(
                    world, start_held=False, held=0, pressed=0
                )
            )
            results.append(
                timeline.step(
                    world,
                    start_held=False,
                    held=INPUT_C,
                    pressed=INPUT_C,
                    recording=(0xFF, INPUT_B),
                )
            )
            committed_wait_release = timeline.wait_release
            recordings_after_commit = tuple(world.echo_recordings)
            results.append(
                timeline.step(
                    world, start_held=False, held=INPUT_C, pressed=0
                )
            )
            still_waiting = timeline.wait_release
            results.append(
                timeline.step(
                    world, start_held=False, held=0, pressed=0
                )
            )
            released = not timeline.wait_release
            results.append(
                timeline.step(
                    world, start_held=False, held=0, pressed=0
                )
            )
            return (
                tuple(results),
                left_wrap,
                right_wrap,
                protected_open,
                selected,
                committed_wait_release,
                recordings_after_commit,
                still_waiting,
                released,
                tuple(world.echo_protected),
                world.replacement_ordinal,
                tuple(world.action_log),
            )

        first = run_fixture()
        self.assertEqual(first, run_fixture())
        self.assertEqual(first[0][4], (True, RESULT_INVALID))
        self.assertEqual(first[0][8], (True, RESULT_OK))
        self.assertEqual(first[1:5], (5, 0, True, 1))
        self.assertTrue(first[5])
        self.assertEqual(
            first[6],
            ((0,), (2,), (3,), (4,), (5,), (0x70, INPUT_B)),
        )
        self.assertTrue(first[7])
        self.assertTrue(first[8])
        self.assertEqual(first[9], (True, False, False, False, False, False))
        self.assertIsNone(first[10])
        self.assertEqual(first[0][-1], (False, None))

    def test_stage_ranks_use_attempt_echo_and_remaining_time(self) -> None:
        transcendent = VocabularyWorld(stage_id=1, attempt=2, tick=400)
        transcendent.echo_recordings = [(0,)]
        self.assertEqual(transcendent.finalize_stage().rank, RANK_TRANSCENDENT)
        optimized = VocabularyWorld(stage_id=2, attempt=3, tick=600)
        optimized.echo_recordings = [(0,), (0,)]
        self.assertEqual(optimized.finalize_stage().rank, RANK_OPTIMIZED)
        stable = VocabularyWorld(stage_id=3, attempt=4, tick=100)
        stable.echo_recordings = [(0,), (0,), (0,)]
        self.assertEqual(stable.finalize_stage().rank, RANK_STABLE)

    def test_projection_tail_is_fixed_big_endian_and_state_sensitive(self) -> None:
        world = combined_multi_echo_fixture().world
        first = world.projection_tail()
        second = world.projection_tail()
        self.assertEqual(first, second)
        self.assertEqual(len(first), 200)
        self.assertEqual(first[:4], b"R95V")
        self.assertEqual(first[8:10], b"\x00\xc8")
        initial_hash = hashlib.sha256(first).hexdigest()
        world.apply_dash_phase(6, INPUT_C | INPUT_RIGHT, INPUT_C)
        changed = world.projection_tail()
        self.assertNotEqual(hashlib.sha256(changed).hexdigest(), initial_hash)


class CombinedAndBudgetTests(unittest.TestCase):
    def test_combined_multi_echo_room_executes_required_roles(self) -> None:
        fixture = combined_multi_echo_fixture()
        world = fixture.world
        self.assertEqual(world.resolve_interaction(1), RESULT_PICKED_UP)
        world.actors[1].x = 128
        self.assertEqual(world.resolve_interaction(1), RESULT_SOCKETED)
        world.update_devices()
        self.assertTrue(
            world.devices[fixture.pressure_door_slot].state & STATE_OPEN
        )
        self.assertTrue(
            world.devices[fixture.socket_door_slot].state & STATE_OPEN
        )
        self.assertEqual(world.resolve_interaction(6), RESULT_HACKED)
        self.assertTrue(world.devices[fixture.terminal_slot].state & STATE_HACKED)
        world.build_beams()
        self.assertEqual(len(world.beams), 2)
        world.update_enemies()
        self.assertEqual(world.enemy_targets[fixture.turret_slot], 2)
        self.assertIn("actor:1:pickup:70", world.action_log)
        self.assertIn("actor:1:socket:70:20", world.action_log)

    def test_full_pool_stress_remains_bounded(self) -> None:
        world = VocabularyWorld()
        for slot in range(ACTOR_CAPACITY):
            world.set_actor(slot, Actor(slot, f"actor_{slot}", slot * 20, 16))
        for index in range(ENEMY_CAPACITY):
            world.enemies[index] = Entity(
                index, ENEMY_TURRET, 1000 + index * 20, 1000, timer=45
            )
        for index in range(PROJECTILE_CAPACITY):
            world.projectiles[index] = Entity(
                index, PROJECTILE_PLAYER, 2000 + index * 20, 2000, timer=60
            )
        for index in range(DEVICE_CAPACITY):
            if index < 4:
                kind = 0x0600
                state = STATE_ACTIVE
            elif index < 12:
                kind = DEVICE_REFLECTOR
                state = 0
            else:
                kind = DEVICE_PRESSURE
                state = 0
            world.devices[index] = Entity(
                index, kind, 3000 + index * 20, 3000, state=state
            )
        for index in range(CARRYABLE_CAPACITY):
            world.carryables[index] = Entity(
                index, 0x0100, 4000 + index * 20, 4000
            )
        for index in range(HAZARD_CAPACITY):
            world.hazards[index] = Entity(
                index, HAZARD_DAMAGE, 5000 + index * 20, 5000
            )
        world.step({})
        self.assertLessEqual(world.update_units, UPDATE_UNIT_LIMIT)
        self.assertEqual(len(world.enemies), ENEMY_CAPACITY)
        self.assertEqual(len(world.projectiles), PROJECTILE_CAPACITY)
        self.assertEqual(len(world.devices), DEVICE_CAPACITY)
        self.assertEqual(len(world.carryables), CARRYABLE_CAPACITY)
        self.assertEqual(len(world.hazards), HAZARD_CAPACITY)
        self.assertLessEqual(len(world.beams), 8)


if __name__ == "__main__":
    unittest.main()
