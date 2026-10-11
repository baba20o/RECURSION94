# RECURSION 94 content binary format, version 1

All integers are unsigned and big-endian. Every fixed record and section starts
on an even address. Offsets in the file header and directory are relative to
the start of the file; offsets in an encounter header and its records are
relative to the start of that encounter chunk.

## File header (`32` bytes)

| Offset | Width | Field |
|---:|---:|---|
| 0 | 4 | ASCII `R94C` |
| 4 | 2 | format version |
| 6 | 2 | file header size |
| 8 | 2 | encounter count |
| 10 | 2 | directory record size |
| 12 | 4 | directory offset |
| 16 | 4 | encounter data offset |
| 20 | 4 | total file size |
| 24 | 4 | CRC-32 of bytes from the directory through EOF |
| 28 | 4 | format flags; bit 0 means collision bit 1 is solid |

The header is followed by twenty `32`-byte directory records in campaign
order. Each record contains the four-byte NUL-padded encounter ID, numeric ID,
order, sector, kind, flags, entity count, chunk offset/size/CRC-32, objective
count, role count, phase count, and deterministic-response-policy count.

## Encounter chunk

Each chunk begins with a `144`-byte `ENCT` header. It carries the source
CRC-32; room dimensions; spawn and exit; loop, clear-window, echo, tempo, and
string references; record counts; section offsets; all thirteen frozen budget
values; fixed record sizes; response-policy metadata; and the variable-detail
offset.

Sections appear in this order:

1. MSB-first row-major 20-by-14 collision bitset (`35` bytes);
2. fixed `32`-byte entity records;
3. fixed `16`-byte objective records;
4. fixed `16`-byte canonical-role records;
5. fixed `20`-byte boss-phase records;
6. fixed `8`-byte deterministic-response records;
7. variable state, parameter, dependency, action, attack, and frame data;
8. a NUL-prefixed, NUL-terminated local UTF-8 string table.

Entity records identify a stable logical ID, a collision-checked 16-bit entity
type code, category, state table and initial index, 320-by-224 coordinates,
reset policy, typed anchor, and a bounded parameter map. Action targets are
entity-record indices. Objective dependencies are objective-record indices.

Parameter maps sort keys by UTF-8 bytes. Values are recursively tagged as
unsigned 32-bit integer, false, true, local string offset, or bounded list.
The generated assembly include exports all tags, record sizes, header offsets,
entity type codes, and per-encounter directory indices/offsets/sizes.

Both the file payload and each encounter chunk carry CRC-32 checks. The
generated content report additionally records canonical source SHA-256,
binary/assembly SHA-256, chunk sizes/checksums, composition, and budgets.
Replay IDs remain source declarations: no replay input bytes or claimed
outcomes are compiled.

## Structured runtime metadata

B01, B02, and B03 additionally author strict `stage_completion` and
`dependency_policy` objects. A completion state names one of
`state`/`timer`/`param`/`health` plus a nonzero mask and a value contained by
that mask. Dependency groups name a consumer, an explicit `ALL` or `ANY`
policy, one or more directed provider conditions, a result state, and a
bounded authoritative-frame duration.

These structures are validated and included in the canonical source
SHA-256 and per-chunk source checksum. Format-v1 encounter records do not
duplicate them; the campaign-runtime v3 adapter projects them into fixed
semantic, group, edge, and profile records while retaining the authoritative
chunk pointer for all authored parameters.
