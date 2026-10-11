# Campaign runtime adapter, version 3

All integers are unsigned and big-endian. The authoritative content compiler
preserves every authored parameter in `campaign_content.bin`; each v3 profile
retains a pointer to that encounter chunk. This adapter adds fixed,
assembly-native semantic and dependency tables. It does not embed replay input
or precompute a completion outcome.

For all twenty encounters the generated assembly emits:

- one 32-byte engine descriptor;
- at most 32 merged, 8-byte collision rectangles;
- one 12-byte canonical object record per authored entity;
- one 4-byte parallel metadata record per canonical object;
- one 16-byte semantic record per canonical object;
- zero or more 16-byte directed dependency groups;
- zero or more 8-byte directed provider-condition edges;
- one 64-byte encounter profile.

The source 20×14 visual-metatile map is merged into maximal horizontal solid
runs, extending identical runs vertically. Generation rejects out-of-room
geometry and rooms above the 32-rectangle staging capacity.

## Semantic and reference registries

The formatting-independent adapter contract contains exactly 58 stable
semantic tokens: 18 object kinds, six object-state bits, six actor-state bits,
three projectile-state bits, five anchor types, four ranks, and sixteen result
codes. Token IDs are contiguous 1–58. Equal numeric values in different
domains remain distinct tokens.

Every currently authored parameter key containing an encounter-local stable
reference is classified in the 20-entry reference registry. Each contract
declares source-to-target or target-to-source direction and an ALL/ANY default;
a new reference-bearing key fails generation until classified. These authored
references are preserved for diagnostics and do not become undirected
connected components.

Dependency and completion records address four explicit object fields:

| ID | Field | Width | Object source |
|---:|---|---:|---|
| 1 | state | 2 | `OBJECT_STATE` |
| 2 | timer | 2 | `OBJECT_TIMER` |
| 3 | param | 2 | `OBJECT_PARAM` |
| 4 | health | 1 | high byte of `OBJECT_STATE` |

## Canonical object translation

Entity categories map to the bounded engine pools:

| Manifest category | Engine pool |
|---|---:|
| enemies | 1 |
| devices | 3 |
| carryables | 4 |
| hazards | 5 |
| boss components | 6 |

`OBJECT_STATE` is an explicit named-state bitfield, never a source-list
ordinal. Enemy health and authored breakable-device health occupy its high
byte. Thus B03's three-health shielded node starts at `$0300`; ACTIVE in the
low byte is its exposed/damageable gate.

`OBJECT_PARAM` keeps the vocabulary kind in its high byte. Its low byte is the
explicit dependency group for ordinary devices, the canonical logical target
ID for repair terminals, the bounded radius for hazards, and zero otherwise.
M01's repair terminal targets its own canonical logical ID because no distinct
repair target is authored.

The 4-byte parallel metadata record is:

| Offset | Width | Field |
|---:|---:|---|
| 0 | 2 | original collision-checked entity type code |
| 2 | 1 | owner: 1 vocabulary / 2 future boss controller |
| 3 | 1 | explicit dependency group, or zero |

Boss components remain owned by `future_boss_phase_controller` with vocabulary
kind and state zero until that controller is implemented.

## Object semantic record (`16` bytes)

| Offset | Width | Field |
|---:|---:|---|
| 0 | 1 | canonical object index |
| 1 | 1 | 58-token semantic kind ID; zero for deferred boss objects |
| 2 | 2 | original entity type code |
| 4 | 2 | initial object state |
| 6 | 2 | initial object timer |
| 8 | 2 | initial object parameter |
| 10 | 1 | authored parameter count |
| 11 | 1 | metadata owner |
| 12 | 2 | primary authored duration, or zero |
| 14 | 2 | semantic flags and optional rectangular hazard geometry |

The primary duration is a direct projection of the first applicable typed
duration parameter. In B03, the timed switch record carries 180 frames and the
door record carries 150 frames. The authoritative content chunk remains the
lossless source for every parameter, including strings and lists.

Semantic flag bits 5–10 encode an optional hazard width in eight-pixel units;
bits 11–15 encode its height in eight-pixel units. Both fields are zero for
point/radius hazards. This fits the full 320×224 authored room while retaining
the frozen 16-byte record and leaves `OBJECT_TIMER` available for live timing.

## Directed dependency group (`16` bytes)

| Offset | Width | Field |
|---:|---:|---|
| 0 | 1 | consumer object index |
| 1 | 1 | policy: 1 ALL / 2 ANY |
| 2 | 1 | first encounter-local edge index |
| 3 | 1 | edge count |
| 4 | 1 | result field ID |
| 5 | 1 | reserved zero |
| 6 | 2 | result mask |
| 8 | 2 | result value |
| 10 | 2 | result duration in authoritative frames |
| 12 | 2 | flags |
| 14 | 2 | reserved zero |

## Directed dependency edge (`8` bytes)

| Offset | Width | Field |
|---:|---:|---|
| 0 | 1 | provider object index |
| 1 | 1 | comparison field ID |
| 2 | 2 | comparison mask |
| 4 | 2 | comparison value |
| 6 | 2 | flags |

An edge is true when the selected provider field, masked by `mask`, equals
`value`. A group evaluates only its listed directed provider edges. No
union-find or connected-component equivalence is part of format v3.

B03 freezes two groups:

1. switch B ACTIVE provides node exposure;
2. ALL of switch A ACTIVE and node health zero provides the door OPEN result
   for 150 frames.

B01 also freezes plate A ACTIVE → door A OPEN as a combinational ALL group.
B02 freezes rear-node health zero → exit ACTIVE. These explicit groups retain
the playable dependency path while avoiding v2's undirected equivalence.

## Encounter profile (`64` bytes)

| Offset | Width | Field |
|---:|---:|---|
| 0 | 1 | directory index, 0–19 |
| 1 | 1 | sector code, 1–5 |
| 2 | 1 | kind, 0 primary / 1 boss |
| 3 | 1 | echo cap |
| 4 | 2 | authored numeric ID |
| 6 | 2 | tempo BPM |
| 8 | 2 | exit X |
| 10 | 2 | exit Y |
| 12 | 1 | objective count |
| 13 | 1 | canonical role count |
| 14 | 1 | boss phase count |
| 15 | 1 | encounter flags |
| 16 | 4 | authoritative encounter chunk pointer |
| 20 | 2 | semantic record count |
| 22 | 2 | dependency group count |
| 24 | 2 | dependency edge count |
| 26 | 1 | completion object, `$FF` when absent |
| 27 | 1 | completion field ID |
| 28 | 2 | completion mask |
| 30 | 2 | completion value |
| 32 | 4 | semantic table pointer |
| 36 | 4 | dependency group table pointer |
| 40 | 4 | dependency edge table pointer |
| 44 | 4 | global semantic token table pointer |
| 48 | 2 | semantic token count, exactly 58 |
| 50 | 2 | reference field count, exactly 4 |
| 52 | 2 | completion flags; bit 0 means present |
| 54 | 10 | reserved zero |

B01, B02, and B03 explicitly author stage completion. B03 freezes object 4,
field state, mask `$0005`, and value `$0005`.

Generated files live only under `assets/generated/campaign_runtime/`.
`--check` rejects missing, changed, or unexpected files. The schema-v3 report
binds the output to the canonical content hash and registry contract hash,
includes encoded records for decoder tests, and reports every directed
reference, dependency, semantic, and completion value.
