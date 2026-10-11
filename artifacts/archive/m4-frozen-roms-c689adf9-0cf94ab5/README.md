# Milestone 4 frozen ROM baseline

These are the exact Debug and Release ROMs bound by the final Milestone 4
runtime, visual, audio, cycle, and host-test reports. They were copied out of
`build/` immediately before Milestone 5 ABI-v3 integration began overwriting
the live build outputs.

| Artifact | SHA-256 |
| --- | --- |
| `recursion94-debug.bin` | `c689adf93f646c34809649d103cc7759d4b5adf19f5315b3ab99bafe883d2460` |
| `recursion94-release.bin` | `0cf94ab5a303bc077810f3d1fe2d4157070ffe6ca515ba0ae5568d3cb90f9d43` |

This archive is historical evidence only. Milestone 5 and later validation
must bind their reports to newly built ROM hashes.

The committed `artifacts/reports/m4-*.json` reports that carry the Debug hash
above, plus `artifacts/replays/B01.r94r` and their recorded trace/capture
artifacts, are capture-time Milestone 4 evidence. Their internal
`build/recursion94-debug.bin` and symbol-map paths describe the files used
during capture; they are not claims about the current live `build/` outputs.
Offline historical checks must use the archived Debug ROM here and validate
capture-time source/hash bundles internally. Current-runtime gates must use
fresh M5-or-later evidence instead.

The capture-time M4 symbol map was not preserved in this ROM-only archive.
Historical checks may validate its recorded SHA-256 provenance, but must not
substitute the current live symbol map or claim that the unavailable artifact
was rechecked.
