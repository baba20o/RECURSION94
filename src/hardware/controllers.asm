; Standard three-button controller port 1 support. The bounded VBlank handler
; calls ControllerPoll exactly once after acknowledging the VDP.

CONTROLLER_TH_OUTPUT            equ     $40
CONTROLLER_PHASE_HIGH           equ     $40
CONTROLLER_PHASE_LOW            equ     $00

INPUT_BIT_UP                    equ     $01
INPUT_BIT_DOWN                  equ     $02
INPUT_BIT_LEFT                  equ     $04
INPUT_BIT_RIGHT                 equ     $08
INPUT_BIT_A                     equ     $10
INPUT_BIT_B                     equ     $20
INPUT_BIT_C                     equ     $40

ControllerInitialize:
        move.b  #CONTROLLER_TH_OUTPUT,(IO_PORT1_CONTROL).l
        move.b  #CONTROLLER_PHASE_HIGH,(IO_PORT1_DATA).l
        clr.b   (INPUT_HELD).l
        clr.b   (INPUT_PRESSED).l
        clr.b   (INPUT_RELEASED).l
        clr.b   (INPUT_RAW_HIGH).l
        clr.b   (INPUT_RAW_LOW).l
        clr.b   (INPUT_CONNECTED).l
        clr.b   (INPUT_START_HELD).l
        clr.b   (INPUT_PREVIOUS).l
        clr.l   (INPUT_SAMPLE_COUNT).l
        ori.w   #M2_FLAG_INPUT_ACTIVE,(DEBUG_M2_FLAGS).l
        rts

; VBlank-only hardware routine. Clobbers d0-d2. TH is returned high after
; every bounded two-phase sample.
ControllerPoll:
        move.b  #CONTROLLER_PHASE_HIGH,(IO_PORT1_DATA).l
        nop
        nop
        nop
        nop
        moveq   #0,d0
        move.b  (IO_PORT1_DATA).l,d0
        move.b  d0,(INPUT_RAW_HIGH).l

        move.b  #CONTROLLER_PHASE_LOW,(IO_PORT1_DATA).l
        nop
        nop
        nop
        nop
        moveq   #0,d1
        move.b  (IO_PORT1_DATA).l,d1
        move.b  d1,(INPUT_RAW_LOW).l

        move.b  #CONTROLLER_PHASE_HIGH,(IO_PORT1_DATA).l
        nop
        nop
        nop
        nop

; During the low-TH phase a three-button pad drives left/right low. Both lines
; floating high is the disconnected-port signature.
        andi.b  #$0C,d1
        bne.s   .disconnected
        move.b  #$01,(INPUT_CONNECTED).l

; U/D/L/R and B/C arrive in the high-TH phase, all active low.
        not.b   d0
        andi.b  #$3F,d0
        move.b  d0,d2
        andi.b  #$0F,d2
        btst    #4,d0
        beq.s   .no_b
        ori.b   #INPUT_BIT_B,d2
.no_b:
        btst    #5,d0
        beq.s   .no_c
        ori.b   #INPUT_BIT_C,d2
.no_c:

; A and Start arrive in bits 4 and 5 of the low-TH phase.
        moveq   #0,d1
        move.b  (INPUT_RAW_LOW).l,d1
        not.b   d1
        btst    #4,d1
        beq.s   .no_a
        ori.b   #INPUT_BIT_A,d2
.no_a:
        andi.b  #$20,d1
        lsr.b   #5,d1
        move.b  d1,(INPUT_START_HELD).l
        bra.s   .sanitize_axes

.disconnected:
        clr.b   (INPUT_CONNECTED).l
        clr.b   (INPUT_START_HELD).l
        moveq   #0,d2

; Opposite directions are deterministic neutral, never an arbitrary winner.
.sanitize_axes:
        move.b  d2,d0
        andi.b  #INPUT_BIT_UP+INPUT_BIT_DOWN,d0
        cmpi.b  #INPUT_BIT_UP+INPUT_BIT_DOWN,d0
        bne.s   .horizontal
        andi.b  #$FC,d2
.horizontal:
        move.b  d2,d0
        andi.b  #INPUT_BIT_LEFT+INPUT_BIT_RIGHT,d0
        cmpi.b  #INPUT_BIT_LEFT+INPUT_BIT_RIGHT,d0
        bne.s   .derive_edges
        andi.b  #$F3,d2

.derive_edges:
        moveq   #0,d0
        move.b  (INPUT_PREVIOUS).l,d0

        move.b  d0,d1
        not.b   d1
        and.b   d2,d1
        move.b  d1,(INPUT_PRESSED).l

        move.b  d2,d1
        not.b   d1
        and.b   d0,d1
        move.b  d1,(INPUT_RELEASED).l

        move.b  d2,(INPUT_HELD).l
        move.b  d2,(INPUT_PREVIOUS).l
        addq.l  #1,(INPUT_SAMPLE_COUNT).l

; Mirror the live input ABI for emulator automation.
        move.b  d2,(DEBUG_INPUT_HELD).l
        move.b  (INPUT_PRESSED).l,(DEBUG_INPUT_PRESSED).l
        move.b  (INPUT_RELEASED).l,(DEBUG_INPUT_RELEASED).l
        move.b  (INPUT_CONNECTED).l,(DEBUG_INPUT_CONNECTED).l
        move.b  (INPUT_START_HELD).l,(DEBUG_INPUT_START_HELD).l
        move.l  (INPUT_SAMPLE_COUNT).l,(DEBUG_CONTROLLER_SAMPLES).l
        rts
