# Visual QA

Required deterministic scenes: boot diagnostic, title, stage select, BOOT
gameplay, two-echo composition, four-echo composition, VIDEO parallax, SOUND
layered machinery, MEMORY anchor, INTERRUPT raster scene, each of five bosses,
Program Stable, and final transmission.

The M2 boot diagnostic was captured at
`artifacts/screenshots/m2-diagnostic.png` from Debug ROM `2e66b738...` and
inspected by the mission controller. The image shows the cyan-on-black
diagnostic, hardware-ready labels, grid, pulse, and sprite without corruption;
its report passes full PNG decoding, freshness, palette, composition, portable
emulator isolation, and ROM-hash checks. The separate fatal fixture at
`artifacts/screenshots/m2-fatal.png` visibly shows white `FATAL EXCEPTION /
CODE 0001` text on a red field and passes its fail-closed pixel gate.

These are bring-up inspections, not the independent 4.2/5 release rubric.
The formal scene-by-scene critic loop remains pending.

The passing rubric is at least 4/5 in composition, silhouettes, palette,
sprites, backgrounds, depth, motion, echo identity, hazard legibility, UI,
hardware authenticity, and finish; average at least 4.2; no critical
readability defect or severe dropout. Each review records the exact ROM hash,
replay/state, screenshot, critic, scores, defects, repair, and comparison.
