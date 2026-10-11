; RECURSION 94 Milestone 2 Z80 sound driver.
;
; The hardware V-int through IM 1 is the sole sequencer clock. The idle path
; only HALTs; it never advances music state. All queues and waits are bounded.

        org     $0000

YM2612_ADDRESS_0                equ     $4000
YM2612_DATA_0                   equ     $4001
PSG_WRITE_PORT                  equ     $7F11

DRIVER_STATE_BASE               equ     $1000
STATE_TICK_DIVIDER              equ     DRIVER_STATE_BASE+$00
STATE_REGION                    equ     DRIVER_STATE_BASE+$01
STATE_COMMAND_BUDGET            equ     DRIVER_STATE_BASE+$02
STATE_COMMAND_OPCODE            equ     DRIVER_STATE_BASE+$03
STATE_COMMAND_ARG_0             equ     DRIVER_STATE_BASE+$04
STATE_COMMAND_ARG_1             equ     DRIVER_STATE_BASE+$05
STATE_COMMAND_ARG_2             equ     DRIVER_STATE_BASE+$06
STATE_SFX_TICKS                 equ     DRIVER_STATE_BASE+$07
STATE_LAYER_PULSE_TICKS         equ     DRIVER_STATE_BASE+$08

CommandRingStart                equ     $1F00
CommandRingDataEnd              equ     $1F40
CommandRingRegionEnd            equ     $1FE0
DriverStatusStart               equ     $1FE0
DriverStatusEnd                 equ     $1FF0
UrgentMailboxStart              equ     $1FF0
UrgentMailboxEnd                equ     $2000

STATUS_SIGNATURE                equ     DriverStatusStart+$00
STATUS_ABI_VERSION              equ     DriverStatusStart+$01
STATUS_STATE                    equ     DriverStatusStart+$02
STATUS_FLAGS                    equ     DriverStatusStart+$03
STATUS_HEARTBEAT                equ     DriverStatusStart+$04
STATUS_QUEUE_READ_SEQUENCE      equ     DriverStatusStart+$06
STATUS_QUEUE_HIGH_WATER         equ     DriverStatusStart+$07
STATUS_COMMAND_COUNT            equ     DriverStatusStart+$08
STATUS_LATE_TICK_COUNT          equ     DriverStatusStart+$0A
STATUS_YM_WRITE_COUNT           equ     DriverStatusStart+$0B
STATUS_PSG_WRITE_COUNT          equ     DriverStatusStart+$0C
STATUS_MOTIF_STEP               equ     DriverStatusStart+$0D
STATUS_ACTIVE_LAYERS            equ     DriverStatusStart+$0E
STATUS_LAST_ERROR_COMMAND       equ     DriverStatusStart+$0F

MAILBOX_SIGNATURE               equ     UrgentMailboxStart+$00
MAILBOX_ABI_VERSION             equ     UrgentMailboxStart+$01
MAILBOX_QUEUE_WRITE_SEQUENCE    equ     UrgentMailboxStart+$02
MAILBOX_PRODUCER_FLAGS          equ     UrgentMailboxStart+$03
MAILBOX_URGENT_SEQUENCE         equ     UrgentMailboxStart+$04
MAILBOX_URGENT_ACK              equ     UrgentMailboxStart+$05
MAILBOX_URGENT_OPCODE           equ     UrgentMailboxStart+$06
MAILBOX_URGENT_ARG_0            equ     UrgentMailboxStart+$07
MAILBOX_URGENT_ARG_1            equ     UrgentMailboxStart+$08
MAILBOX_URGENT_ARG_2            equ     UrgentMailboxStart+$09
MAILBOX_REGION                  equ     UrgentMailboxStart+$0A
MAILBOX_REQUESTED_LAYERS        equ     UrgentMailboxStart+$0B
MAILBOX_MUSIC_STATE             equ     UrgentMailboxStart+$0C
MAILBOX_CONTROL_FLAGS           equ     UrgentMailboxStart+$0D

DRIVER_SIGNATURE_VALUE          equ     $94
DRIVER_ABI_VERSION_VALUE        equ     $01

AUDIO_STATE_BOOT                equ     $00
AUDIO_STATE_READY               equ     $01
AUDIO_STATE_PLAYING             equ     $02
AUDIO_STATE_PAUSED              equ     $03
AUDIO_STATE_STOPPED             equ     $04
AUDIO_STATE_ERROR               equ     $80

STATUS_FLAG_READY               equ     $01
STATUS_FLAG_YM_ACTIVE           equ     $02
STATUS_FLAG_PSG_ACTIVE          equ     $04
STATUS_FLAG_PAUSED              equ     $08
STATUS_FLAG_QUEUE_FAULT         equ     $10
STATUS_FLAG_YM_TIMEOUT          equ     $20

COMMAND_NOP                     equ     $00
COMMAND_PLAY                    equ     $01
COMMAND_SET_LAYERS              equ     $02
COMMAND_SFX                     equ     $03
COMMAND_PAUSE                   equ     $04
COMMAND_RESUME                  equ     $05
COMMAND_STOP                    equ     $06
COMMAND_RESET                   equ     $07
COMMAND_SET_REGION              equ     $08

ERROR_QUEUE_CORRUPT             equ     $E1
ERROR_YM_TIMEOUT                equ     $E2
ERROR_UNKNOWN_COMMAND           equ     $E3

COMMANDS_PER_VINT               equ     4
QUEUE_CAPACITY                  equ     16
YM_BUSY_WAIT_LIMIT              equ     $40
NTSC_STEP_VINTS                 equ     15
PAL_STEP_VINTS                  equ     12
SFX_DURATION_VINTS              equ     4
LAYER_PULSE_VINTS               equ     2

YM_REGISTER_KEY_ON              equ     $28
YM_REGISTER_FREQUENCY_LOW       equ     $A0
YM_REGISTER_FREQUENCY_HIGH      equ     $A4
YM_KEY_OFF_CHANNEL_0            equ     $00
YM_KEY_ON_CHANNEL_0             equ     $F0
YM_NOTE_C_HIGH                  equ     $22
YM_NOTE_C_LOW                   equ     $84
YM_NOTE_E_HIGH                  equ     $22
YM_NOTE_E_LOW                   equ     $D3

PSG_TONE_C_LATCH                equ     $86
PSG_TONE_C_DATA                 equ     $0D
PSG_TONE_E_LATCH                equ     $8A
PSG_TONE_E_DATA                 equ     $0A
PSG_TONE_QUIET                  equ     $9A
PSG_NOISE_SHORT                 equ     $E4
PSG_NOISE_QUIET                 equ     $FA
PSG_LAYER_PERCUSSION_VOLUME     equ     $F8
PSG_MUTE_TONE_0                 equ     $9F
PSG_MUTE_TONE_1                 equ     $BF
PSG_MUTE_TONE_2                 equ     $DF
PSG_MUTE_NOISE                  equ     $FF

DriverResetVector:
        jp      DriverInit

        defs    $0038-$,$00

DriverIm1Vector:
        jp      DriverVInt

        defs    $0066-$,$00

DriverNmiVector:
        retn

DriverInit:
        di
        ld      sp,$1F00

; The 68000 clears all 8 KiB before upload. Clear the writable half again so a
; direct driver reset also reconstructs every queue/status/mailbox byte.
        xor     a
        ld      (DRIVER_STATE_BASE),a
        ld      hl,DRIVER_STATE_BASE
        ld      de,DRIVER_STATE_BASE+1
        ld      bc,$0FFF
        ldir

        ld      a,DRIVER_SIGNATURE_VALUE
        ld      (STATUS_SIGNATURE),a
        ld      (MAILBOX_SIGNATURE),a
        ld      a,DRIVER_ABI_VERSION_VALUE
        ld      (STATUS_ABI_VERSION),a
        ld      (MAILBOX_ABI_VERSION),a
        ld      a,AUDIO_STATE_BOOT
        ld      (STATUS_STATE),a
        ld      a,$01
        ld      (STATUS_ACTIVE_LAYERS),a
        ld      (MAILBOX_REQUESTED_LAYERS),a

        call    InitialiseYm2612
        call    InitialisePsg
        call    ReloadTickDivider
        xor     a
        ld      (STATUS_MOTIF_STEP),a
        call    PlayCurrentMotifNote

        ld      a,(STATUS_FLAGS)
        and     STATUS_FLAG_YM_TIMEOUT
        jr      nz,.hardware_error
        ld      a,STATUS_FLAG_READY|STATUS_FLAG_YM_ACTIVE|STATUS_FLAG_PSG_ACTIVE
        ld      (STATUS_FLAGS),a
        ld      a,AUDIO_STATE_PLAYING
        ld      (STATUS_STATE),a
        ld      (MAILBOX_MUSIC_STATE),a
        jr      .start
.hardware_error:
        ld      a,AUDIO_STATE_ERROR
        ld      (STATUS_STATE),a
        ld      (MAILBOX_MUSIC_STATE),a
.start:
        im      1
        ei
.idle:
        halt
        jr      .idle

DriverVInt:
        push    af
        push    bc
        push    de
        push    hl
        push    ix
        push    iy

        call    IncrementHeartbeat
        call    ServiceUrgentMailbox
        call    ServiceCommandQueue
        call    TickSfx
        call    TickLayerPulse
        call    TickSequencer

        pop     iy
        pop     ix
        pop     hl
        pop     de
        pop     bc
        pop     af
        ei
        reti

IncrementHeartbeat:
        ld      hl,(STATUS_HEARTBEAT)
        inc     hl
        ld      (STATUS_HEARTBEAT),hl
        ret

ServiceUrgentMailbox:
        ld      a,(MAILBOX_URGENT_SEQUENCE)
        ld      b,a
        ld      a,(MAILBOX_URGENT_ACK)
        cp      b
        ret     z

        ld      a,(MAILBOX_URGENT_OPCODE)
        ld      (STATE_COMMAND_OPCODE),a
        ld      a,(MAILBOX_URGENT_ARG_0)
        ld      (STATE_COMMAND_ARG_0),a
        ld      a,(MAILBOX_URGENT_ARG_1)
        ld      (STATE_COMMAND_ARG_1),a
        ld      a,(MAILBOX_URGENT_ARG_2)
        ld      (STATE_COMMAND_ARG_2),a
        call    DispatchCommand
        call    IncrementCommandCount
; Dispatch is free to use every driver-owned register. Reload the request
; sequence before acknowledging so the mailbox does not rely on B surviving.
        ld      a,(MAILBOX_URGENT_SEQUENCE)
        ld      (MAILBOX_URGENT_ACK),a
        ret

ServiceCommandQueue:
        call    MeasureQueueDepth
        ret     z
        cp      QUEUE_CAPACITY+1
        jr      c,.depth_valid
        call    RecordQueueFault
        ld      a,(MAILBOX_QUEUE_WRITE_SEQUENCE)
        ld      (STATUS_QUEUE_READ_SEQUENCE),a
        ret
.depth_valid:
        ld      b,a
        ld      a,(STATUS_QUEUE_HIGH_WATER)
        cp      b
        jr      nc,.high_water_done
        ld      a,b
        ld      (STATUS_QUEUE_HIGH_WATER),a
.high_water_done:
        ld      a,COMMANDS_PER_VINT
        ld      (STATE_COMMAND_BUDGET),a
.next:
        ld      a,(MAILBOX_QUEUE_WRITE_SEQUENCE)
        ld      b,a
        ld      a,(STATUS_QUEUE_READ_SEQUENCE)
        cp      b
        ret     z

        and     $0F
        add     a,a
        add     a,a
        ld      l,a
        ld      h,$00
        ld      de,CommandRingStart
        add     hl,de
        ld      a,(hl)
        ld      (STATE_COMMAND_OPCODE),a
        inc     hl
        ld      a,(hl)
        ld      (STATE_COMMAND_ARG_0),a
        inc     hl
        ld      a,(hl)
        ld      (STATE_COMMAND_ARG_1),a
        inc     hl
        ld      a,(hl)
        ld      (STATE_COMMAND_ARG_2),a

        call    DispatchCommand
        call    IncrementCommandCount
        ld      a,(STATUS_QUEUE_READ_SEQUENCE)
        inc     a
        ld      (STATUS_QUEUE_READ_SEQUENCE),a

        ld      a,(STATE_COMMAND_BUDGET)
        dec     a
        ld      (STATE_COMMAND_BUDGET),a
        jr      nz,.next

; A nonempty queue after the fixed service budget is observable, never an
; unbounded extension of interrupt work.
        call    MeasureQueueDepth
        ret     z
        ld      hl,STATUS_LATE_TICK_COUNT
        call    SaturatingIncrementByte
        ret

MeasureQueueDepth:
        ld      a,(STATUS_QUEUE_READ_SEQUENCE)
        ld      b,a
        ld      a,(MAILBOX_QUEUE_WRITE_SEQUENCE)
        sub     b
        ret

DispatchCommand:
        ld      a,(STATE_COMMAND_OPCODE)
        ld      (STATUS_LAST_ERROR_COMMAND),a
        or      a
        ret     z
        cp      COMMAND_PLAY
        jp      z,CommandPlay
        cp      COMMAND_SET_LAYERS
        jp      z,CommandSetLayers
        cp      COMMAND_SFX
        jp      z,CommandSfx
        cp      COMMAND_PAUSE
        jp      z,CommandPause
        cp      COMMAND_RESUME
        jp      z,CommandResume
        cp      COMMAND_STOP
        jp      z,CommandStop
        cp      COMMAND_RESET
        jp      z,CommandReset
        cp      COMMAND_SET_REGION
        jp      z,CommandSetRegion
        ld      a,ERROR_UNKNOWN_COMMAND
        ld      (STATUS_LAST_ERROR_COMMAND),a
        ret

CommandPlay:
        ld      a,(STATE_COMMAND_ARG_0)
        ld      (STATUS_ACTIVE_LAYERS),a
        ld      (MAILBOX_REQUESTED_LAYERS),a
        ld      a,(STATE_COMMAND_ARG_2)
        and     $01
        ld      (STATE_REGION),a
        ld      (MAILBOX_REGION),a
        xor     a
        ld      (STATUS_MOTIF_STEP),a
        call    ReloadTickDivider
        call    PlayCurrentMotifNote
        xor     a
        ld      (STATE_SFX_TICKS),a
        ld      a,(STATUS_FLAGS)
        and     $F7
        ld      (STATUS_FLAGS),a
        ld      a,AUDIO_STATE_PLAYING
        ld      (STATUS_STATE),a
        ld      (MAILBOX_MUSIC_STATE),a
        ret

CommandSetLayers:
        ld      a,(STATE_COMMAND_ARG_0)
        and     $7F
        or      $01
        ld      (STATUS_ACTIVE_LAYERS),a
        ld      (MAILBOX_REQUESTED_LAYERS),a
; Echo 1 owns a short PSG-noise percussion pulse. Removing that layer mutes it
; immediately unless a higher-priority sound effect is using the noise voice.
        bit     1,a
        ret     nz
        xor     a
        ld      (STATE_LAYER_PULSE_TICKS),a
        ld      a,(STATE_SFX_TICKS)
        or      a
        ret     nz
        ld      a,PSG_MUTE_NOISE
        call    WritePsg
        ret

CommandSfx:
        xor     a
        ld      (STATE_LAYER_PULSE_TICKS),a
        ld      a,PSG_NOISE_SHORT
        call    WritePsg
        ld      a,PSG_NOISE_QUIET
        call    WritePsg
        ld      a,SFX_DURATION_VINTS
        ld      (STATE_SFX_TICKS),a
        ret

CommandPause:
        call    AllNotesOff
        xor     a
        ld      (STATE_SFX_TICKS),a
        ld      a,(STATUS_FLAGS)
        or      STATUS_FLAG_PAUSED
        ld      (STATUS_FLAGS),a
        ld      a,AUDIO_STATE_PAUSED
        ld      (STATUS_STATE),a
        ld      (MAILBOX_MUSIC_STATE),a
        ret

CommandResume:
        ld      a,(STATUS_FLAGS)
        and     $F7
        ld      (STATUS_FLAGS),a
        call    ReloadTickDivider
        call    PlayCurrentMotifNote
        ld      a,AUDIO_STATE_PLAYING
        ld      (STATUS_STATE),a
        ld      (MAILBOX_MUSIC_STATE),a
        ret

CommandStop:
        call    AllNotesOff
        xor     a
        ld      (STATE_SFX_TICKS),a
        ld      a,(STATUS_FLAGS)
        and     $F7
        ld      (STATUS_FLAGS),a
        ld      a,AUDIO_STATE_STOPPED
        ld      (STATUS_STATE),a
        ld      (MAILBOX_MUSIC_STATE),a
        ret

CommandReset:
        call    AllNotesOff
        xor     a
        ld      (STATUS_MOTIF_STEP),a
        ld      (STATE_SFX_TICKS),a
        ld      a,(STATE_COMMAND_ARG_0)
        ld      (STATUS_ACTIVE_LAYERS),a
        ld      (MAILBOX_REQUESTED_LAYERS),a
        call    ReloadTickDivider
        call    PlayCurrentMotifNote
        ld      a,(STATUS_FLAGS)
        and     $F7
        ld      (STATUS_FLAGS),a
        ld      a,AUDIO_STATE_PLAYING
        ld      (STATUS_STATE),a
        ld      (MAILBOX_MUSIC_STATE),a
        ret

CommandSetRegion:
        ld      a,(STATE_COMMAND_ARG_0)
        and     $01
        ld      (STATE_REGION),a
        ld      (MAILBOX_REGION),a
        call    ReloadTickDivider
        ret

TickSfx:
        ld      a,(STATE_SFX_TICKS)
        or      a
        ret     z
        dec     a
        ld      (STATE_SFX_TICKS),a
        ret     nz
        ld      a,PSG_MUTE_NOISE
        call    WritePsg
        ret

TickLayerPulse:
        ld      a,(STATE_LAYER_PULSE_TICKS)
        or      a
        ret     z
        dec     a
        ld      (STATE_LAYER_PULSE_TICKS),a
        ret     nz
        ld      a,(STATE_SFX_TICKS)
        or      a
        ret     nz
        ld      a,PSG_MUTE_NOISE
        call    WritePsg
        ret

TickSequencer:
        ld      a,(STATUS_STATE)
        cp      AUDIO_STATE_PLAYING
        ret     nz
        ld      a,(STATE_TICK_DIVIDER)
        dec     a
        ld      (STATE_TICK_DIVIDER),a
        ret     nz
        call    ReloadTickDivider
        ld      a,(STATUS_MOTIF_STEP)
        inc     a
        and     $01
        ld      (STATUS_MOTIF_STEP),a
        call    PlayCurrentMotifNote
        ret

ReloadTickDivider:
        ld      a,(STATE_REGION)
        or      a
        ld      a,NTSC_STEP_VINTS
        jr      z,.store
        ld      a,PAL_STEP_VINTS
.store:
        ld      (STATE_TICK_DIVIDER),a
        ret

PlayCurrentMotifNote:
; Every transition explicitly keys off before changing frequency and keying on.
        ld      d,YM_REGISTER_KEY_ON
        ld      e,YM_KEY_OFF_CHANNEL_0
        call    WriteYm0
        ld      a,(STATUS_MOTIF_STEP)
        and     $01
        jr      nz,.note_e
.note_c:
        ld      d,YM_REGISTER_FREQUENCY_HIGH
        ld      e,YM_NOTE_C_HIGH
        call    WriteYm0
        ld      d,YM_REGISTER_FREQUENCY_LOW
        ld      e,YM_NOTE_C_LOW
        call    WriteYm0
        ld      a,PSG_TONE_C_LATCH
        call    WritePsg
        ld      a,PSG_TONE_C_DATA
        call    WritePsg
        jr      .key_on
.note_e:
        ld      d,YM_REGISTER_FREQUENCY_HIGH
        ld      e,YM_NOTE_E_HIGH
        call    WriteYm0
        ld      d,YM_REGISTER_FREQUENCY_LOW
        ld      e,YM_NOTE_E_LOW
        call    WriteYm0
        ld      a,PSG_TONE_E_LATCH
        call    WritePsg
        ld      a,PSG_TONE_E_DATA
        call    WritePsg
.key_on:
        ld      d,YM_REGISTER_KEY_ON
        ld      e,YM_KEY_ON_CHANNEL_0
        call    WriteYm0
        ld      a,PSG_TONE_QUIET
        call    WritePsg
; The first echo adds a distinct two-V-int percussion transient on every
; quarter-second motif boundary. Sound effects retain priority on PSG noise.
        ld      a,(STATUS_ACTIVE_LAYERS)
        bit     1,a
        ret     z
        ld      a,(STATE_SFX_TICKS)
        or      a
        ret     nz
        ld      a,PSG_NOISE_SHORT
        call    WritePsg
        ld      a,PSG_LAYER_PERCUSSION_VOLUME
        call    WritePsg
        ld      a,LAYER_PULSE_VINTS
        ld      (STATE_LAYER_PULSE_TICKS),a
        ret

InitialiseYm2612:
; LFO and DAC off.
        ld      d,$22
        ld      e,$00
        call    WriteYm0
        ld      d,$2B
        ld      e,$00
        call    WriteYm0

; Key off every FM channel, including the three channels selected by bit 2.
        ld      d,YM_REGISTER_KEY_ON
        ld      e,$00
        call    WriteYm0
        ld      e,$01
        call    WriteYm0
        ld      e,$02
        call    WriteYm0
        ld      e,$04
        call    WriteYm0
        ld      e,$05
        call    WriteYm0
        ld      e,$06
        call    WriteYm0

; Channel 1, algorithm 7: four quiet carriers with fast attack and finite
; release. The register set is intentionally small and original to this ROM.
        ld      d,$30
        ld      e,$01
        call    WriteYm0
        ld      d,$34
        ld      e,$01
        call    WriteYm0
        ld      d,$38
        ld      e,$01
        call    WriteYm0
        ld      d,$3C
        ld      e,$01
        call    WriteYm0

        ld      d,$40
        ld      e,$48
        call    WriteYm0
        ld      d,$44
        ld      e,$48
        call    WriteYm0
        ld      d,$48
        ld      e,$48
        call    WriteYm0
        ld      d,$4C
        ld      e,$48
        call    WriteYm0

        ld      d,$50
        ld      e,$1F
        call    WriteYm0
        ld      d,$54
        ld      e,$1F
        call    WriteYm0
        ld      d,$58
        ld      e,$1F
        call    WriteYm0
        ld      d,$5C
        ld      e,$1F
        call    WriteYm0

        ld      d,$60
        ld      e,$08
        call    WriteYm0
        ld      d,$64
        ld      e,$08
        call    WriteYm0
        ld      d,$68
        ld      e,$08
        call    WriteYm0
        ld      d,$6C
        ld      e,$08
        call    WriteYm0

        ld      d,$70
        ld      e,$04
        call    WriteYm0
        ld      d,$74
        ld      e,$04
        call    WriteYm0
        ld      d,$78
        ld      e,$04
        call    WriteYm0
        ld      d,$7C
        ld      e,$04
        call    WriteYm0

        ld      d,$80
        ld      e,$4F
        call    WriteYm0
        ld      d,$84
        ld      e,$4F
        call    WriteYm0
        ld      d,$88
        ld      e,$4F
        call    WriteYm0
        ld      d,$8C
        ld      e,$4F
        call    WriteYm0

        ld      d,$B0
        ld      e,$07
        call    WriteYm0
        ld      d,$B4
        ld      e,$C0
        call    WriteYm0
        ret

InitialisePsg:
        ld      a,PSG_MUTE_TONE_0
        call    WritePsg
        ld      a,PSG_MUTE_TONE_1
        call    WritePsg
        ld      a,PSG_MUTE_TONE_2
        call    WritePsg
        ld      a,PSG_MUTE_NOISE
        call    WritePsg
        ret

AllNotesOff:
        ld      d,YM_REGISTER_KEY_ON
        ld      e,$00
        call    WriteYm0
        ld      e,$01
        call    WriteYm0
        ld      e,$02
        call    WriteYm0
        ld      e,$04
        call    WriteYm0
        ld      e,$05
        call    WriteYm0
        ld      e,$06
        call    WriteYm0
        ld      a,PSG_MUTE_TONE_0
        call    WritePsg
        ld      a,PSG_MUTE_TONE_1
        call    WritePsg
        ld      a,PSG_MUTE_TONE_2
        call    WritePsg
        ld      a,PSG_MUTE_NOISE
        call    WritePsg
        ret

WriteYm0:
; In: D=register, E=data. Busy checks before both port writes are bounded.
        push    af
        push    bc
        call    WaitYm0Ready
        jr      c,.done
        ld      a,d
        ld      (YM2612_ADDRESS_0),a
        call    WaitYm0Ready
        jr      c,.done
        ld      a,e
        ld      (YM2612_DATA_0),a
        ld      hl,STATUS_YM_WRITE_COUNT
        call    SaturatingIncrementByte
.done:
        pop     bc
        pop     af
        ret

WaitYm0Ready:
        ld      b,YM_BUSY_WAIT_LIMIT
.wait:
        ld      a,(YM2612_ADDRESS_0)
        and     $80
        ret     z
        djnz    .wait
        call    RecordYmTimeout
        scf
        ret

RecordYmTimeout:
        ld      a,(STATUS_FLAGS)
        or      STATUS_FLAG_YM_TIMEOUT
        ld      (STATUS_FLAGS),a
        ld      a,ERROR_YM_TIMEOUT
        ld      (STATUS_LAST_ERROR_COMMAND),a
        ld      a,AUDIO_STATE_ERROR
        ld      (STATUS_STATE),a
        ret

WritePsg:
        ld      (PSG_WRITE_PORT),a
        push    af
        ld      hl,STATUS_PSG_WRITE_COUNT
        call    SaturatingIncrementByte
        pop     af
        ret

IncrementCommandCount:
        ld      hl,(STATUS_COMMAND_COUNT)
        ld      a,h
        and     l
        cp      $FF
        ret     z
        inc     hl
        ld      (STATUS_COMMAND_COUNT),hl
        ret

RecordQueueFault:
        ld      a,(STATUS_FLAGS)
        or      STATUS_FLAG_QUEUE_FAULT
        ld      (STATUS_FLAGS),a
        ld      a,ERROR_QUEUE_CORRUPT
        ld      (STATUS_LAST_ERROR_COMMAND),a
        ret

SaturatingIncrementByte:
; In: HL=counter address.
        ld      a,(hl)
        cp      $FF
        ret     z
        inc     (hl)
        ret

DriverImageEnd:
        assert  DriverImageEnd<=DRIVER_STATE_BASE
