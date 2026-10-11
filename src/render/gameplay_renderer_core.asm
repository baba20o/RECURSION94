; Milestone 4 B01 renderer initialization, static plane construction, reset
; trace, and bounded main-side DMA publication.

; GameplayRendererInitialize
; Called once while display is off, after GameplayInitialize and DmaInitialize.
; Uploads original palette/pattern data, builds authentic Plane B room art,
; clears Plane A, and writes the initial HUD/SAT directly through the VDP port.
; Out: d0.w=RENDERER_RESULT_OK.
; Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6.
GameplayRendererInitialize:
        movem.l d3-d7/a2-a6,-(a7)

        lea     (RENDERER_STATE_BASE).l,a0
        moveq   #0,d0
        move.w  #(RENDERER_BYTES_VALUE/4)-1,d1
.clear_renderer_ram:
        move.l  d0,(a0)+
        dbf     d1,.clear_renderer_ram

        move.l  #RENDERER_MAGIC_VALUE,(RENDERER_MAGIC).l
        move.w  #RENDERER_VERSION_VALUE,(RENDERER_VERSION).l
        move.w  #RENDERER_BYTES_VALUE,(RENDERER_BYTES).l
        move.w  #RENDERER_FLAG_INITIALIZED,(RENDERER_FLAGS).l
        ori.w   #M2_FLAG_SPRITE_ACTIVE,(DEBUG_M2_FLAGS).l
        move.w  (ENGINE_ATTEMPT).l,d0
        move.w  d0,(RENDERER_LAST_ATTEMPT).l
        move.w  (ENGINE_CHECKPOINT_COUNT).l,d0
        move.w  d0,(RENDERER_LAST_CHECKPOINT_COUNT).l
        move.w  #RENDERER_NO_ROW,(RENDERER_RESET_PREVIOUS_ROW).l
        move.w  #RENDERER_NO_ROW,(RENDERER_OVERLAY_CLEAR_DEST).l
        move.w  #RENDERER_NO_ROW,(RENDERER_OVERLAY_TRACE_DEST).l

; The two bounded overlay rows are shared by the transparent restore sweep and
; the terminal completion banner. Initialization stages the reset form.
        bsr.w   RendererBuildResetOverlayRows

        bsr.w   RendererLoadPaletteAndTiles
        bsr.w   RendererClearPlaneA
        bsr.w   RendererBuildPlaneB
        bsr.w   RendererBuildHud
        bsr.w   RendererBuildSat
        bsr.w   RendererWriteInitialDynamic
        clr.w   (RENDERER_LAST_RESULT).l
        moveq   #RENDERER_RESULT_OK,d0
        movem.l (a7)+,d3-d7/a2-a6
        rts

; GameplayRendererBuildAndQueue
; Main-side producer called once per consumed authoritative token, after
; GameplayStep/GameplayCommitAndReset. It never performs gameplay simulation.
; Out: 0 published, 1 prior frame pending, 2 uninitialized, 3 DMA rejection,
; 4 previous video frame deliberately held for one checkpoint frame.
; Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6 and restores the incoming SR.
GameplayRendererBuildAndQueue:
        movem.l d3-d7/a2-a6,-(a7)
        move.w  sr,d7
        move.w  #$2700,sr

        cmpi.l  #RENDERER_MAGIC_VALUE,(RENDERER_MAGIC).l
        bne.w   .not_initialized
        cmpi.w  #RENDERER_VERSION_VALUE,(RENDERER_VERSION).l
        bne.w   .not_initialized
        move.w  (RENDERER_FLAGS).l,d0
        andi.w  #RENDERER_FLAG_INITIALIZED,d0
        beq.w   .not_initialized

        andi.w  #$FFEB,(RENDERER_FLAGS).l ; clear current backlog/reject flags
        tst.w   (DMA_QUEUE_FRAME_PENDING).l
        bne.w   .backlog

; An attempt ordinal transition is the reset-effect trigger. The renderer does
; not infer reset from wall time or controller input.
        moveq   #0,d1                   ; attempt transition this call
        move.w  (ENGINE_ATTEMPT).l,d0
        cmp.w   (RENDERER_LAST_ATTEMPT).l,d0
        beq.s   .attempt_ready
        move.w  d0,(RENDERER_LAST_ATTEMPT).l
        move.w  #RENDERER_RESET_PHASE_TICKS,(RENDERER_RESET_PHASE).l
        ori.w   #RENDERER_FLAG_RESET_ACTIVE,(RENDERER_FLAGS).l
        moveq   #1,d1
.attempt_ready:
; Projection publication is the measured once-per-second CPU spike. Reuse the
; already-visible VRAM image for exactly one frame whenever its saturating
; checkpoint ordinal advances exactly once, or resets to one with an attempt
; transition. No queue is begun or republished; the following ordinary token
; rebuilds current state, while terminal/reset presentation rebuilds on its
; next frame. Malformed jumps and non-campaign callers render normally.
        move.w  (ENGINE_CHECKPOINT_COUNT).l,d0
        cmp.w   (RENDERER_LAST_CHECKPOINT_COUNT).l,d0
        beq.s   .checkpoint_ready
        move.w  (RENDERER_LAST_CHECKPOINT_COUNT).l,d3
        move.w  d0,(RENDERER_LAST_CHECKPOINT_COUNT).l
        move.w  (VOCAB_CAMPAIGN_FLAGS).l,d2
        andi.w  #VOCAB_CAMPAIGN_FLAG_ACTIVE,d2
        beq.s   .checkpoint_ready
        tst.w   (DMA_QUEUE_PUBLISHED_DEPTH).l
        bne.s   .checkpoint_ready
        tst.w   (DMA_QUEUE_BUILD_DEPTH).l
        bne.s   .checkpoint_ready
        tst.w   d1
        bne.s   .checkpoint_new_attempt
        addq.w  #1,d3
        cmp.w   d3,d0
        bne.s   .checkpoint_ready
        bra.s   .checkpoint_hold
.checkpoint_new_attempt:
        cmpi.w  #1,d0
        bne.s   .checkpoint_ready
.checkpoint_hold:
        move.w  #RENDERER_RESULT_CHECKPOINT_HELD,(RENDERER_LAST_RESULT).l
        moveq   #RENDERER_RESULT_CHECKPOINT_HELD,d0
        bra.w   .restore
.checkpoint_ready:
        bsr.w   RendererBuildHud
        bsr.w   RendererBuildSat
        bsr.w   RendererPrepareResetOverlay

        bsr.w   DmaQueueBeginFrame
        moveq   #0,d6                   ; any required enqueue rejected

        lea     (SAT_SHADOW).l,a0
        move.w  #VRAM_SAT,d0
        move.w  #RENDERER_DMA_SAT_WORDS,d1
        move.w  #RENDERER_DMA_SAT_FLAGS,d2
        bsr.w   DmaQueueEnqueue
        tst.l   d0
        bne.s   .hud_0
        moveq   #1,d6

.hud_0:
        lea     (RENDERER_HUD_ROW_0).l,a0
        move.w  #VRAM_PLANE_A,d0
        move.w  #RENDERER_HUD_ROW_WORDS,d1
        move.w  #RENDERER_DMA_HUD_FLAGS,d2
        bsr.w   DmaQueueEnqueue
        tst.l   d0
        bne.s   .hud_1
        moveq   #1,d6
.hud_1:
        lea     (RENDERER_HUD_ROW_1).l,a0
        move.w  #VRAM_PLANE_A+PLANE_ROW_BYTES,d0
        move.w  #RENDERER_HUD_ROW_WORDS,d1
        move.w  #RENDERER_DMA_HUD_FLAGS,d2
        bsr.w   DmaQueueEnqueue
        tst.l   d0
        bne.s   .hud_2
        moveq   #1,d6
.hud_2:
        lea     (RENDERER_HUD_ROW_2).l,a0
        move.w  #VRAM_PLANE_A+(PLANE_ROW_BYTES*2),d0
        move.w  #RENDERER_HUD_ROW_WORDS,d1
        move.w  #RENDERER_DMA_HUD_FLAGS,d2
        bsr.w   DmaQueueEnqueue
        tst.l   d0
        bne.s   .overlay_clear
        moveq   #1,d6

.overlay_clear:
        move.w  (RENDERER_OVERLAY_CLEAR_DEST).l,d0
        cmpi.w  #RENDERER_NO_ROW,d0
        beq.s   .overlay_trace
        lea     (RENDERER_OVERLAY_CLEAR_ROW).l,a0
        move.w  #RENDERER_HUD_ROW_WORDS,d1
        move.w  #RENDERER_DMA_RESET_FLAGS,d2
        bsr.w   DmaQueueEnqueue
        tst.l   d0
        bne.s   .overlay_trace
        moveq   #1,d6
.overlay_trace:
        move.w  (RENDERER_OVERLAY_TRACE_DEST).l,d0
        cmpi.w  #RENDERER_NO_ROW,d0
        beq.s   .publish
        lea     (RENDERER_OVERLAY_TRACE_ROW).l,a0
        move.w  #RENDERER_HUD_ROW_WORDS,d1
        move.w  #RENDERER_DMA_RESET_FLAGS,d2
        bsr.w   DmaQueueEnqueue
        tst.l   d0
        bne.s   .publish
        moveq   #1,d6

.publish:
        bsr.w   DmaQueuePublish
        tst.w   d6
        bne.s   .dma_rejected
        clr.w   (RENDERER_LAST_RESULT).l
        moveq   #RENDERER_RESULT_OK,d0
        bra.s   .restore

.dma_rejected:
        ori.w   #RENDERER_FLAG_DMA_REJECTED,(RENDERER_FLAGS).l
        cmpi.w  #$FFFF,(RENDERER_DMA_REJECTS).l
        beq.s   .dma_reject_counted
        addq.w  #1,(RENDERER_DMA_REJECTS).l
.dma_reject_counted:
        move.w  #RENDERER_RESULT_DMA_REJECTED,(RENDERER_LAST_RESULT).l
        moveq   #RENDERER_RESULT_DMA_REJECTED,d0
        bra.s   .restore

.backlog:
        ori.w   #RENDERER_FLAG_QUEUE_BACKLOG,(RENDERER_FLAGS).l
        cmpi.w  #$FFFF,(RENDERER_BACKLOG_FRAMES).l
        beq.s   .backlog_counted
        addq.w  #1,(RENDERER_BACKLOG_FRAMES).l
.backlog_counted:
        move.w  #RENDERER_RESULT_BACKLOG,(RENDERER_LAST_RESULT).l
        moveq   #RENDERER_RESULT_BACKLOG,d0
        bra.s   .restore

.not_initialized:
        moveq   #RENDERER_RESULT_NOT_INITIALIZED,d0
.restore:
        move.w  d7,sr
        movem.l (a7)+,d3-d7/a2-a6
        rts

; RendererLoadPaletteAndTiles
; Display-off CPU-port transfer. Clobbers d0-d2/a0-a1.
RendererLoadPaletteAndTiles:
        move.l  #VDP_COMMAND_CRAM_WRITE_0,(VDP_CONTROL).l
        lea     RendererPalette(pc),a0
        moveq   #63,d0
.palette:
        move.w  (a0)+,(VDP_DATA).l
        dbf     d0,.palette

        move.w  #RENDERER_VRAM_TILE_START,d0
        bsr.w   VdpSetVramWriteAddress
        lea     RendererTileData(pc),a0
        move.w  #(RENDERER_TILE_BYTES/4)-1,d0
.tiles:
        move.l  (a0)+,(VDP_DATA).l
        dbf     d0,.tiles
        rts

; RendererClearPlaneA
; Clears the complete 64x32 table, including offscreen cells. Clobbers d0-d2.
RendererClearPlaneA:
        move.w  #VRAM_PLANE_A,d0
        bsr.w   VdpSetVramWriteAddress
        moveq   #0,d0
        move.w  #(PLANE_WIDTH_CELLS*32)-1,d1
.clear:
        move.w  d0,(VDP_DATA).l
        dbf     d1,.clear
        rts

; RendererBuildPlaneB
; Constructs a bounded 64x32 tile plane. Active generated campaigns derive
; their room directly from the validated live bounds and canonical rectangle
; staging; standalone/non-campaign fixtures retain the original B01 plane.
; Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6.
RendererBuildPlaneB:
        move.w  (VOCAB_CAMPAIGN_FLAGS).l,d0
        andi.w  #VOCAB_CAMPAIGN_FLAG_ACTIVE,d0
        bne.w   RendererBuildCampaignPlaneB

; Original B01 presentation path. Keep this independent of the generated
; campaign geometry so the frozen standalone fixture remains reproducible.
        movem.l d3-d7,-(a7)
        move.w  #VRAM_PLANE_B,d0
        bsr.w   VdpSetVramWriteAddress
        moveq   #0,d6                   ; row 0..31
.row:
        moveq   #0,d5                   ; column 0..63
.column:
        moveq   #0,d4                   ; transparent outside 40x28
        cmpi.w  #VISIBLE_HEIGHT_CELLS,d6
        bhs.w   .emit
        cmpi.w  #VISIBLE_WIDTH_CELLS,d5
        bhs.w   .emit

        cmpi.w  #3,d6
        bhs.s   .room
        move.w  #RENDERER_TILE_HUD_DARK,d4
        bra.w   .emit
.room:
        cmpi.w  #3,d6
        beq.w   .wall
        cmpi.w  #VISIBLE_HEIGHT_CELLS-1,d6
        beq.w   .wall
        tst.w   d5
        beq.w   .wall
        cmpi.w  #VISIBLE_WIDTH_CELLS-1,d5
        beq.w   .wall

; A restrained one-cell inner shadow gives the room shell depth without
; reintroducing a noisy full-field grid.
        cmpi.w  #4,d6
        beq.w   .wall_shadow_north
        cmpi.w  #VISIBLE_HEIGHT_CELLS-2,d6
        beq.w   .wall_shadow_south
        cmpi.w  #1,d5
        beq.w   .wall_shadow_west
        cmpi.w  #VISIBLE_WIDTH_CELLS-2,d5
        beq.w   .wall_shadow_east

; Thin guide rails frame the first canonical rectangle without pretending
; that collision remains after its four sprite blocks retract.
        cmpi.w  #6,d6
        blo.s   .second_rect
        cmpi.w  #22,d6
        bhs.s   .second_rect
        cmpi.w  #13,d5
        beq.w   .rail
        cmpi.w  #16,d5
        beq.w   .rail
.second_rect:
        cmpi.w  #10,d6
        blo.s   .switch_pad
        cmpi.w  #12,d6
        bhs.s   .switch_pad
        cmpi.w  #26,d5
        blo.s   .switch_pad
        cmpi.w  #29,d5
        blo.w   .wall
.switch_pad:
        cmpi.w  #13,d6
        blo.s   .terminal_pad
        cmpi.w  #16,d6
        bhs.s   .terminal_pad
        cmpi.w  #9,d5
        blo.s   .terminal_pad
        cmpi.w  #11,d5
        blo.w   .switch_floor
.terminal_pad:
        cmpi.w  #13,d6
        blo.s   .hazard_pad
        cmpi.w  #16,d6
        bhs.s   .hazard_pad
        cmpi.w  #35,d5
        blo.s   .hazard_pad
        cmpi.w  #38,d5
        blo.s   .terminal_floor
.hazard_pad:
        cmpi.w  #21,d6
        blo.s   .floor
        cmpi.w  #24,d6
        bhs.s   .floor
        cmpi.w  #21,d5
        blo.s   .floor
        cmpi.w  #24,d5
        blo.s   .hazard_floor
.floor:
; The readable traversal lane is three subtly seamed rows. Everywhere else,
; one quiet expansion seam appears only every eight columns and four rows.
        move.w  #RENDERER_TILE_FLOOR_A,d4
        cmpi.w  #14,d6
        beq.s   .route_core
        cmpi.w  #13,d6
        blo.s   .sparse_floor
        cmpi.w  #16,d6
        blo.s   .floor_b
.sparse_floor:
        move.w  d5,d0
        andi.w  #$0007,d0
        bne.s   .emit
        move.w  d6,d0
        andi.w  #$0003,d0
        bne.s   .emit
.floor_b:
        move.w  #RENDERER_TILE_FLOOR_B,d4
        bra.s   .emit
.route_core:
        move.w  #RENDERER_TILE_ROUTE_CORE,d4
        bra.s   .emit
.wall:
        move.w  #RENDERER_TILE_WALL,d4
        bra.s   .emit
.wall_shadow_north:
        move.w  #RENDERER_TILE_WALL_SHADOW_H,d4
        bra.s   .emit
.wall_shadow_south:
        move.w  #RENDERER_ATTR_VFLIP+RENDERER_TILE_WALL_SHADOW_H,d4
        bra.s   .emit
.wall_shadow_west:
        move.w  #RENDERER_TILE_WALL_SHADOW_V,d4
        bra.s   .emit
.wall_shadow_east:
        move.w  #RENDERER_ATTR_HFLIP+RENDERER_TILE_WALL_SHADOW_V,d4
        bra.s   .emit
.rail:
        move.w  #RENDERER_TILE_RAIL,d4
        bra.s   .emit
.switch_floor:
        move.w  #RENDERER_TILE_SWITCH_PAD,d4
        bra.s   .emit
.terminal_floor:
        move.w  #RENDERER_ATTR_PALETTE_2+RENDERER_TILE_TERMINAL_PAD,d4
        bra.s   .emit
.hazard_floor:
        move.w  #RENDERER_ATTR_PALETTE_3+RENDERER_TILE_HAZARD_PAD,d4
.emit:
        move.w  d4,(VDP_DATA).l
        addq.w  #1,d5
        cmpi.w  #PLANE_WIDTH_CELLS,d5
        blo.w   .column
        addq.w  #1,d6
        cmpi.w  #32,d6
        blo.w   .row
        movem.l (a7)+,d3-d7
        rts

; RendererBuildCampaignPlaneB
; Builds one immutable campaign room while display is off. Every visible cell
; is classified against the validated half-open room bounds and then against
; at most STATIC_RECT_CAPACITY canonical collision rectangles. A cell is a
; wall when its 8x8 area overlaps a live rectangle; no encounter ID or B01
; coordinate participates in this path.
; Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6.
RendererBuildCampaignPlaneB:
        movem.l d3-d7/a2,-(a7)
        move.w  #VRAM_PLANE_B,d0
        jsr     (VdpSetVramWriteAddress).l
        moveq   #0,d6                   ; row 0..31
.campaign_row:
        moveq   #0,d5                   ; column 0..63
.campaign_column:
        moveq   #0,d4                   ; transparent outside live room
        cmpi.w  #VISIBLE_HEIGHT_CELLS,d6
        bhs.w   .campaign_emit
        cmpi.w  #VISIBLE_WIDTH_CELLS,d5
        bhs.w   .campaign_emit

; Plane A owns the text, but the same quiet three-row backplate prevents live
; room geometry from competing with the HUD.
        cmpi.w  #3,d6
        bhs.s   .campaign_room
        move.w  #RENDERER_TILE_HUD_DARK,d4
        bra.w   .campaign_emit

.campaign_room:
        move.w  d5,d3
        lsl.w   #3,d3                   ; cell left in world pixels
        move.w  d6,d2
        lsl.w   #3,d2                   ; cell top in world pixels

; Half-open cell/room overlap. This also makes non-zero or cropped room bounds
; deterministic without assuming the current 320x224 authored dimensions.
        move.w  d3,d0
        addq.w  #8,d0
        cmp.w   (ENGINE_ROOM_LEFT).l,d0
        ble.w   .campaign_emit
        cmp.w   (ENGINE_ROOM_RIGHT).l,d3
        bge.w   .campaign_emit
        move.w  d2,d0
        addq.w  #8,d0
        cmp.w   (ENGINE_ROOM_TOP).l,d0
        ble.w   .campaign_emit
        cmp.w   (ENGINE_ROOM_BOTTOM).l,d2
        bge.w   .campaign_emit

        move.w  #RENDERER_TILE_FLOOR_A,d4
        move.w  d5,d0
        andi.w  #$0007,d0
        bne.s   .campaign_rectangles
        move.w  d6,d0
        andi.w  #$0003,d0
        bne.s   .campaign_rectangles
        move.w  #RENDERER_TILE_FLOOR_B,d4

.campaign_rectangles:
        moveq   #0,d7
        move.w  (ENGINE_RECT_COUNT).l,d7
        cmpi.w  #STATIC_RECT_CAPACITY,d7
        bls.s   .campaign_rect_count_ready
        moveq   #STATIC_RECT_CAPACITY,d7
        ori.w   #RENDERER_FLAG_INVALID_STATE,(RENDERER_FLAGS).l
.campaign_rect_count_ready:
        tst.w   d7
        beq.s   .campaign_emit
        subq.w  #1,d7
        lea     (CANONICAL_RECTS).l,a2
.campaign_rectangle:
; Half-open 8x8 cell/rectangle overlap in both axes.
        move.w  d3,d0
        addq.w  #8,d0
        cmp.w   (STATIC_RECT_X,a2),d0
        ble.s   .campaign_rectangle_next
        move.w  (STATIC_RECT_X,a2),d0
        add.w   (STATIC_RECT_WIDTH,a2),d0
        cmp.w   d3,d0
        ble.s   .campaign_rectangle_next
        move.w  d2,d0
        addq.w  #8,d0
        cmp.w   (STATIC_RECT_Y,a2),d0
        ble.s   .campaign_rectangle_next
        move.w  (STATIC_RECT_Y,a2),d0
        add.w   (STATIC_RECT_HEIGHT,a2),d0
        cmp.w   d2,d0
        ble.s   .campaign_rectangle_next
        move.w  #RENDERER_TILE_WALL,d4
        bra.s   .campaign_emit
.campaign_rectangle_next:
        adda.w  #STATIC_RECT_BYTES,a2
        dbf     d7,.campaign_rectangle

.campaign_emit:
        move.w  d4,(VDP_DATA).l
        addq.w  #1,d5
        cmpi.w  #PLANE_WIDTH_CELLS,d5
        blo.w   .campaign_column
        addq.w  #1,d6
        cmpi.w  #32,d6
        blo.w   .campaign_row
        movem.l (a7)+,d3-d7/a2
        rts

; RendererWriteInitialDynamic
; Display-off direct transfer of three HUD rows and the complete SAT.
; Clobbers d0-d2/a0-a1.
RendererWriteInitialDynamic:
        lea     (RENDERER_HUD_ROW_0).l,a0
        move.w  #VRAM_PLANE_A,d0
        bsr.w   RendererWriteCpuRow
        lea     (RENDERER_HUD_ROW_1).l,a0
        move.w  #VRAM_PLANE_A+PLANE_ROW_BYTES,d0
        bsr.w   RendererWriteCpuRow
        lea     (RENDERER_HUD_ROW_2).l,a0
        move.w  #VRAM_PLANE_A+(PLANE_ROW_BYTES*2),d0
        bsr.w   RendererWriteCpuRow

        move.w  #VRAM_SAT,d0
        jsr     VdpSetVramWriteAddress
        lea     (SAT_SHADOW).l,a0
        move.w  #SAT_SHADOW_WORDS-1,d1
.sat:
        move.w  (a0)+,(VDP_DATA).l
        dbf     d1,.sat
        rts

; RendererWriteCpuRow
; In: a0=40-word source, d0.w=VRAM destination. Display-off only.
; Clobbers d0-d2/a0-a1.
RendererWriteCpuRow:
        jsr     VdpSetVramWriteAddress
        move.w  #RENDERER_HUD_ROW_WORDS-1,d1
.word:
        move.w  (a0)+,(VDP_DATA).l
        dbf     d1,.word
        rts

; RendererBuildOverlayClearRows
; Clears both bounded shared overlay sources. Clobbers d0-d2/a0-a1.
RendererBuildOverlayClearRows:
        lea     (RENDERER_OVERLAY_CLEAR_ROW).l,a0
        moveq   #0,d0
        move.w  #((RENDERER_DYNAMIC_END-RENDERER_OVERLAY_CLEAR_ROW)/4)-1,d1
.clear:
        move.l  d0,(a0)+
        dbf     d1,.clear
        rts

; RendererBuildResetOverlayRows
; Stages a transparent, non-priority alternating trace over a clear row.
; Clobbers d0-d2/a0-a1.
RendererBuildResetOverlayRows:
        bsr.s   RendererBuildOverlayClearRows
        lea     (RENDERER_OVERLAY_TRACE_ROW).l,a0
        move.w  #(RENDERER_HUD_ROW_WORDS/2)-1,d1
.trace:
        move.w  #RENDERER_RESET_ATTRIBUTE+RENDERER_TILE_TRACE,(a0)+
        move.w  #RENDERER_RESET_ATTRIBUTE+RENDERER_ATTR_HFLIP+RENDERER_TILE_TRACE,(a0)+
        dbf     d1,.trace
        rts

; RendererBuildCompletionRows
; Stages the in-world two-line completion banner in the same bounded sources.
; Clobbers d0-d2/a0-a1.
RendererBuildCompletionRows:
        bsr.s   RendererBuildOverlayClearRows
        move.w  #RENDERER_COMPLETION_ATTRIBUTE+RENDERER_TILE_DASH,(RENDERER_OVERLAY_CLEAR_ROW+20).l
        move.w  #RENDERER_COMPLETION_ATTRIBUTE+RENDERER_TILE_DASH,(RENDERER_OVERLAY_CLEAR_ROW+22).l
        move.w  #RENDERER_COMPLETION_ATTRIBUTE+RENDERER_TILE_DASH,(RENDERER_OVERLAY_CLEAR_ROW+54).l
        move.w  #RENDERER_COMPLETION_ATTRIBUTE+RENDERER_TILE_DASH,(RENDERER_OVERLAY_CLEAR_ROW+56).l
        lea     RendererWorldStableLine0(pc),a0
        lea     (RENDERER_OVERLAY_CLEAR_ROW+26).l,a1
        moveq   #14,d0
        move.w  #RENDERER_COMPLETION_ATTRIBUTE,d1
        bsr.w   RendererHudWriteText

        move.w  #RENDERER_COMPLETION_ATTRIBUTE+RENDERER_TILE_DASH,(RENDERER_OVERLAY_TRACE_ROW+16).l
        move.w  #RENDERER_COMPLETION_ATTRIBUTE+RENDERER_TILE_DASH,(RENDERER_OVERLAY_TRACE_ROW+18).l
        move.w  #RENDERER_COMPLETION_ATTRIBUTE+RENDERER_TILE_DASH,(RENDERER_OVERLAY_TRACE_ROW+60).l
        move.w  #RENDERER_COMPLETION_ATTRIBUTE+RENDERER_TILE_DASH,(RENDERER_OVERLAY_TRACE_ROW+62).l
        lea     RendererWorldStableLine1(pc),a0
        lea     (RENDERER_OVERLAY_TRACE_ROW+22).l,a1
        moveq   #18,d0
        move.w  #RENDERER_COMPLETION_ATTRIBUTE,d1
        bsr.w   RendererHudWriteText
        rts

; RendererPrepareResetOverlay
; Produces exactly two or fewer shared-row destinations. Completion staging
; has first priority. On leaving completion, both banner rows are cleared for
; one frame before replacement/reset/ordinary staging can reuse the sources.
; Otherwise a 12-frame scan advances rows 3,5,...25 and then clears its last
; trace row.
; Clobbers d0-d2/a0-a1.
RendererPrepareResetOverlay:
        move.w  #RENDERER_NO_ROW,(RENDERER_OVERLAY_CLEAR_DEST).l
        move.w  #RENDERER_NO_ROW,(RENDERER_OVERLAY_TRACE_DEST).l

        move.w  (GAMEPLAY_FLAGS).l,d0
        andi.w  #GAMEPLAY_FLAG_PROGRAM_STABLE,d0
        beq.s   .not_completion
        bsr.w   RendererBuildCompletionRows
        move.w  #VRAM_PLANE_A+(PLANE_ROW_BYTES*RENDERER_COMPLETION_FIRST_ROW),(RENDERER_OVERLAY_CLEAR_DEST).l
        move.w  #VRAM_PLANE_A+(PLANE_ROW_BYTES*RENDERER_COMPLETION_SECOND_ROW),(RENDERER_OVERLAY_TRACE_DEST).l
        ori.w   #RENDERER_FLAG_COMPLETION_STAGED,(RENDERER_FLAGS).l
        rts

.not_completion:
        move.w  (RENDERER_FLAGS).l,d0
        andi.w  #RENDERER_FLAG_COMPLETION_STAGED,d0
        beq.s   .reset
        bsr.w   RendererBuildOverlayClearRows
        move.w  #VRAM_PLANE_A+(PLANE_ROW_BYTES*RENDERER_COMPLETION_FIRST_ROW),(RENDERER_OVERLAY_CLEAR_DEST).l
        move.w  #VRAM_PLANE_A+(PLANE_ROW_BYTES*RENDERER_COMPLETION_SECOND_ROW),(RENDERER_OVERLAY_TRACE_DEST).l
        andi.w  #$FEFF,(RENDERER_FLAGS).l
        rts

.reset:
        bsr.w   RendererBuildResetOverlayRows
        move.w  (RENDERER_RESET_PREVIOUS_ROW).l,d0
        cmpi.w  #RENDERER_NO_ROW,d0
        beq.s   .trace
        move.w  d0,d1
        mulu.w  #PLANE_ROW_BYTES,d1
        addi.w  #VRAM_PLANE_A,d1
        move.w  d1,(RENDERER_OVERLAY_CLEAR_DEST).l

.trace:
        moveq   #0,d0
        move.w  (RENDERER_RESET_PHASE).l,d0
        beq.s   .clear_complete
        move.w  #RENDERER_RESET_PHASE_TICKS,d1
        sub.w   d0,d1
        mulu.w  #RENDERER_RESET_ROW_STEP,d1
        addi.w  #RENDERER_RESET_FIRST_ROW,d1
        move.w  d1,(RENDERER_RESET_PREVIOUS_ROW).l
        mulu.w  #PLANE_ROW_BYTES,d1
        addi.w  #VRAM_PLANE_A,d1
        move.w  d1,(RENDERER_OVERLAY_TRACE_DEST).l
        subq.w  #1,(RENDERER_RESET_PHASE).l
        rts

.clear_complete:
        move.w  #RENDERER_NO_ROW,(RENDERER_RESET_PREVIOUS_ROW).l
        andi.w  #$FFFD,(RENDERER_FLAGS).l
        rts
