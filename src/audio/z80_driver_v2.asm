; RECURSION 94 proposed cartridge audio ABI v2.
;
; This file is deliberately not included by the current ROM build. It is a
; standalone sjasmplus image for the post-M4 integration campaign.
;
; Hardware V-int through IM 1 is the sole sequencer tick. Every loop has a
; static bound: four commands per V-int, at most two PPQN ticks per V-int,
; seven score layers, sixteen pattern rows, and bounded YM busy waits.

        org     $0000

YM2612_ADDRESS_0                equ     $4000
YM2612_DATA_0                   equ     $4001
YM2612_ADDRESS_1                equ     $4002
YM2612_DATA_1                   equ     $4003
PSG_WRITE_PORT                  equ     $7F11

DRIVER_STATE_BASE               equ     $1000
STATE_REGION                    equ     DRIVER_STATE_BASE+$00
STATE_COMMAND_BUDGET            equ     DRIVER_STATE_BASE+$01
STATE_COMMAND_OPCODE            equ     DRIVER_STATE_BASE+$02
STATE_COMMAND_ARG_0             equ     DRIVER_STATE_BASE+$03
STATE_COMMAND_ARG_1             equ     DRIVER_STATE_BASE+$04
STATE_COMMAND_ARG_2             equ     DRIVER_STATE_BASE+$05
STATE_PENDING                   equ     DRIVER_STATE_BASE+$06
STATE_PATTERN_ROW               equ     DRIVER_STATE_BASE+$07
STATE_ROW_TICK                  equ     DRIVER_STATE_BASE+$08
STATE_TEMPO_INCREMENT           equ     DRIVER_STATE_BASE+$09
STATE_TEMPO_ACCUMULATOR         equ     DRIVER_STATE_BASE+$0B
STATE_PPQN_BUDGET               equ     DRIVER_STATE_BASE+$0D
STATE_CAP_MASK                  equ     DRIVER_STATE_BASE+$0E
STATE_BANK_VALID                equ     DRIVER_STATE_BASE+$0F
STATE_CURRENT_BANK              equ     DRIVER_STATE_BASE+$10
STATE_SFX_TICKS                 equ     DRIVER_STATE_BASE+$11
STATE_SFX_PRIORITY              equ     DRIVER_STATE_BASE+$12
STATE_SFX_ID                    equ     DRIVER_STATE_BASE+$13
STATE_YM_PORT                   equ     DRIVER_STATE_BASE+$14
STATE_YM_NOTE                   equ     DRIVER_STATE_BASE+$15
STATE_YM_LOW_REGISTER           equ     DRIVER_STATE_BASE+$16
STATE_YM_HIGH_REGISTER          equ     DRIVER_STATE_BASE+$17
STATE_YM_KEY_CODE               equ     DRIVER_STATE_BASE+$18
STATE_YM_BLOCK                  equ     DRIVER_STATE_BASE+$19
STATE_REMOVED_MASK              equ     DRIVER_STATE_BASE+$1A
STATE_SFX_COMMAND_BUDGET        equ     DRIVER_STATE_BASE+$1B
STATE_REQUESTED_WIRE_MASK       equ     DRIVER_STATE_BASE+$1C
STATE_SAFETY_COMMAND            equ     DRIVER_STATE_BASE+$1D

ActiveBankStart                 equ     $1200
ACTIVE_BANK_MAGIC_0             equ     ActiveBankStart+$00
ACTIVE_BANK_MAGIC_1             equ     ActiveBankStart+$01
ACTIVE_BANK_MAGIC_2             equ     ActiveBankStart+$02
ACTIVE_BANK_MAGIC_3             equ     ActiveBankStart+$03
ACTIVE_BANK_VERSION             equ     ActiveBankStart+$04
ACTIVE_BANK_ID                  equ     ActiveBankStart+$05
ACTIVE_BANK_TEMPO               equ     ActiveBankStart+$06
ACTIVE_BANK_ECHO_CAP            equ     ActiveBankStart+$07
ACTIVE_BANK_ROWS                equ     ActiveBankStart+$08
ACTIVE_BANK_TICKS_PER_ROW       equ     ActiveBankStart+$09
ACTIVE_BANK_LAYER_COUNT         equ     ActiveBankStart+$0A
ACTIVE_BANK_FLAGS               equ     ActiveBankStart+$0B
ACTIVE_BANK_CRC32               equ     ActiveBankStart+$0C
ActiveBankPattern               equ     ActiveBankStart+$10
ActiveBankEnd                   equ     ActiveBankStart+$80

CommandRingStart                equ     $1F00
CommandRingDataEnd              equ     $1F40
CommandRingRegionEnd            equ     $1FE0
ExtendedStatusStart             equ     $1F40
ExtendedStatusEnd               equ     $1F80
DriverStatusStart               equ     $1FE0
DriverStatusEnd                 equ     $1FF0
UrgentMailboxStart              equ     $1FF0
UrgentMailboxEnd                equ     $2000

EXT_SIGNATURE                   equ     ExtendedStatusStart+$00
EXT_ABI_VERSION                 equ     ExtendedStatusStart+$01
EXT_REQUESTED_SCENE             equ     ExtendedStatusStart+$02
EXT_REQUESTED_MASK              equ     ExtendedStatusStart+$03
EXT_REQUESTED_GENERATION        equ     ExtendedStatusStart+$04
EXT_APPLIED_SCENE               equ     ExtendedStatusStart+$05
EXT_APPLIED_MASK                equ     ExtendedStatusStart+$06
EXT_APPLIED_GENERATION          equ     ExtendedStatusStart+$07
EXT_PPQN_COUNTER                equ     ExtendedStatusStart+$08
EXT_PATTERN_ROW                 equ     ExtendedStatusStart+$0A
EXT_ACTIVE_BANK                 equ     ExtendedStatusStart+$0B
EXT_BANK_FLAGS                  equ     ExtendedStatusStart+$0C
EXT_SFX_PRIORITY                equ     ExtendedStatusStart+$0D
EXT_SFX_DROP_COUNT              equ     ExtendedStatusStart+$0E
EXT_STATE_FLAGS                 equ     ExtendedStatusStart+$0F
EXT_TEMPO_INCREMENT             equ     ExtendedStatusStart+$10
EXT_TEMPO_ACCUMULATOR           equ     ExtendedStatusStart+$12

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
STATUS_PPQN_LOW                 equ     DriverStatusStart+$0D
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

DRIVER_SIGNATURE_VALUE          equ     $94
DRIVER_ABI_VERSION_VALUE        equ     $02

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
STATUS_FLAG_BANK_INVALID        equ     $40
STATUS_FLAG_MUTED               equ     $80

EXT_FLAG_BANK_VALID             equ     $01
EXT_FLAG_PENDING                equ     $02
EXT_FLAG_MUTED                  equ     $04
EXT_FLAG_STALE_IGNORED          equ     $08
EXT_FLAG_GENERATION_COLLISION   equ     $10

COMMAND_NOP                     equ     $00
COMMAND_SFX                     equ     $03
COMMAND_PAUSE                   equ     $04
COMMAND_RESUME                  equ     $05
COMMAND_STOP                    equ     $06
COMMAND_RESET                   equ     $07
COMMAND_SET_REGION              equ     $08
COMMAND_SYNC_STATE              equ     $09

ERROR_QUEUE_CORRUPT             equ     $E1
ERROR_YM_TIMEOUT                equ     $E2
ERROR_UNKNOWN_COMMAND           equ     $E3
ERROR_BANK_INVALID              equ     $E4
ERROR_SCENE_INVALID             equ     $E5
ERROR_GENERATION_COLLISION      equ     $E6

COMMANDS_PER_VINT               equ     4
QUEUE_CAPACITY                  equ     16
YM_BUSY_WAIT_LIMIT              equ     $40
MAX_PPQN_TICKS_PER_VINT         equ     2
NTSC_PPQN_THRESHOLD             equ     3600
PAL_PPQN_THRESHOLD              equ     3000
PATTERN_ROWS                    equ     16
TICKS_PER_ROW                   equ     6
LAYER_COUNT                     equ     7
MAX_SCENE_ID                    equ     39
MAX_SFX_ID                      equ     15

YM_REGISTER_KEY_ON              equ     $28
PSG_MUTE_TONE_0                 equ     $9F
PSG_MUTE_TONE_1                 equ     $BF
PSG_MUTE_TONE_2                 equ     $DF
PSG_MUTE_NOISE                  equ     $FF
PSG_NOISE_SHORT                 equ     $E4
PSG_PERCUSSION_VOLUME           equ     $F8
PSG_TEXTURE_VOLUME              equ     $D8

DriverV2ResetVector:
        jp      DriverV2Init

        defs    $0038-$,$00

DriverV2Im1Vector:
        jp      DriverV2VInt

        defs    $0066-$,$00

DriverV2NmiVector:
        retn

DriverV2Init:
        di
        ld      sp,$1F00

; Clear driver state, but preserve the independently uploaded bank at $1200.
        xor     a
        ld      (DRIVER_STATE_BASE),a
        ld      hl,DRIVER_STATE_BASE
        ld      de,DRIVER_STATE_BASE+1
        ld      bc,$01FF
        ldir

; Extended/core status is driver-owned. The producer mailbox remains intact.
        xor     a
        ld      (ExtendedStatusStart),a
        ld      hl,ExtendedStatusStart
        ld      de,ExtendedStatusStart+1
        ld      bc,$00AF
        ldir

        ld      a,DRIVER_SIGNATURE_VALUE
        ld      (EXT_SIGNATURE),a
        ld      (STATUS_SIGNATURE),a
        ld      (MAILBOX_SIGNATURE),a
        ld      a,DRIVER_ABI_VERSION_VALUE
        ld      (EXT_ABI_VERSION),a
        ld      (STATUS_ABI_VERSION),a
        ld      (MAILBOX_ABI_VERSION),a
; A driver restart never replays bytes published to an earlier instance.
        ld      a,(MAILBOX_QUEUE_WRITE_SEQUENCE)
        ld      (STATUS_QUEUE_READ_SEQUENCE),a
        ld      a,(MAILBOX_URGENT_SEQUENCE)
        ld      (MAILBOX_URGENT_ACK),a
        ld      a,AUDIO_STATE_BOOT
        ld      (STATUS_STATE),a
        ld      a,$0F
        ld      (STATE_PATTERN_ROW),a

        call    InitialiseYm2612V2
        call    InitialisePsgV2
        call    AllNotesOffV2

        ld      a,(STATUS_FLAGS)
        and     STATUS_FLAG_YM_TIMEOUT
        jr      nz,.hardware_error
        ld      a,STATUS_FLAG_READY|STATUS_FLAG_YM_ACTIVE|STATUS_FLAG_PSG_ACTIVE
        ld      (STATUS_FLAGS),a
        ld      a,AUDIO_STATE_READY
        ld      (STATUS_STATE),a
        jr      .start
.hardware_error:
        ld      a,AUDIO_STATE_ERROR
        ld      (STATUS_STATE),a
.start:
        im      1
        ei
.idle:
        halt
        jr      .idle

DriverV2VInt:
        push    af
        push    bc
        push    de
        push    hl
        push    ix
        push    iy

; At most two ranked SFX may touch YM channel 5 per V-int.
        xor     a
        ld      (STATE_SAFETY_COMMAND),a
        ld      a,$02
        ld      (STATE_SFX_COMMAND_BUDGET),a
        call    IncrementHeartbeatV2
        call    ServiceUrgentMailboxV2
        ld      a,(STATE_SAFETY_COMMAND)
        or      a
        jr      nz,.work_done
        call    ServiceCommandQueueV2
        ld      a,(STATE_SAFETY_COMMAND)
        or      a
        jr      nz,.work_done
        call    TickSfxV2
        call    TickSequencerV2

.work_done:
        pop     iy
        pop     ix
        pop     hl
        pop     de
        pop     bc
        pop     af
        ei
        reti

IncrementHeartbeatV2:
        ld      hl,(STATUS_HEARTBEAT)
        inc     hl
        ld      (STATUS_HEARTBEAT),hl
        ret

ServiceUrgentMailboxV2:
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
        call    DispatchCommandV2
        call    IncrementCommandCountV2
        ld      a,(MAILBOX_URGENT_SEQUENCE)
        ld      (MAILBOX_URGENT_ACK),a
        ret

ServiceCommandQueueV2:
        call    MeasureQueueDepthV2
        ret     z
        cp      QUEUE_CAPACITY+1
        jr      c,.depth_valid
        call    RecordQueueFaultV2
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

        call    DispatchCommandV2
        call    IncrementCommandCountV2
        ld      a,(STATUS_QUEUE_READ_SEQUENCE)
        inc     a
        ld      (STATUS_QUEUE_READ_SEQUENCE),a
        ld      a,(STATE_SAFETY_COMMAND)
        or      a
        ret     nz
        ld      a,(STATE_COMMAND_BUDGET)
        dec     a
        ld      (STATE_COMMAND_BUDGET),a
        jr      nz,.next

        call    MeasureQueueDepthV2
        ret     z
        ld      hl,STATUS_LATE_TICK_COUNT
        call    SaturatingIncrementByteV2
        ret

MeasureQueueDepthV2:
        ld      a,(STATUS_QUEUE_READ_SEQUENCE)
        ld      b,a
        ld      a,(MAILBOX_QUEUE_WRITE_SEQUENCE)
        sub     b
        ret

DispatchCommandV2:
        ld      a,(STATE_COMMAND_OPCODE)
        ld      (STATUS_LAST_ERROR_COMMAND),a
        or      a
        ret     z
        cp      COMMAND_SFX
        jp      z,CommandSfxV2
        cp      COMMAND_PAUSE
        jp      z,CommandPauseV2
        cp      COMMAND_RESUME
        jp      z,CommandResumeV2
        cp      COMMAND_STOP
        jp      z,CommandStopV2
        cp      COMMAND_RESET
        jp      z,CommandResetV2
        cp      COMMAND_SET_REGION
        jp      z,CommandSetRegionV2
        cp      COMMAND_SYNC_STATE
        jp      z,CommandSyncStateV2
        ld      a,ERROR_UNKNOWN_COMMAND
        ld      (STATUS_LAST_ERROR_COMMAND),a
        ret

CommandSyncStateV2:
        ld      a,(STATE_COMMAND_ARG_0)
        cp      MAX_SCENE_ID+1
        jp      nc,.scene_invalid

; One generation names exactly one state. Delta 1..127 is newer; 128..255 is
; stale. The one-in-flight 68000 contract makes this comparison unambiguous.
        ld      a,(STATE_COMMAND_ARG_2)
        ld      b,a
        ld      a,(EXT_REQUESTED_GENERATION)
        cp      b
        jr      z,.duplicate
        ld      c,a
        ld      a,b
        sub     c
        bit     7,a
        jr      nz,.stale
        jr      .accept
.duplicate:
        ld      a,(STATE_COMMAND_ARG_0)
        ld      b,a
        ld      a,(EXT_REQUESTED_SCENE)
        cp      b
        jr      nz,.generation_collision
        ld      a,(STATE_COMMAND_ARG_1)
        ld      b,a
        ld      a,(STATE_REQUESTED_WIRE_MASK)
        cp      b
        ret     z
.generation_collision:
        ld      a,(EXT_STATE_FLAGS)
        or      EXT_FLAG_GENERATION_COLLISION
        ld      (EXT_STATE_FLAGS),a
        ld      a,ERROR_GENERATION_COLLISION
        ld      (STATUS_LAST_ERROR_COMMAND),a
        ret
.stale:
        ld      a,(EXT_STATE_FLAGS)
        or      EXT_FLAG_STALE_IGNORED
        ld      (EXT_STATE_FLAGS),a
        ret
.accept:
        ld      a,(STATE_COMMAND_ARG_0)
        ld      (EXT_REQUESTED_SCENE),a
        ld      a,(STATE_COMMAND_ARG_1)
        ld      (STATE_REQUESTED_WIRE_MASK),a
        ld      a,(STATE_COMMAND_ARG_2)
        ld      (EXT_REQUESTED_GENERATION),a
        ld      a,(STATE_COMMAND_ARG_0)
        or      a
        jp      z,ApplyStoppedSceneV2

        call    ValidateActiveBankV2
        jp      c,.bank_invalid

; Clamp the authoritative snapshot to the scene/bank cap without making it
; cumulative. Bit 7 remains the global mute request.
        ld      a,(STATE_COMMAND_ARG_1)
        ld      b,a
        and     $7F
        ld      c,a
        ld      a,(STATE_CAP_MASK)
        and     c
        ld      c,a
        ld      a,b
        and     $80
        or      c
        ld      (EXT_REQUESTED_MASK),a

; Removed voices stop immediately. Logical additions wait for the next row.
        ld      a,(EXT_APPLIED_MASK)
        and     $7F
        ld      b,a
        ld      a,(EXT_REQUESTED_MASK)
        and     $7F
        cpl
        and     b
        ld      (STATE_REMOVED_MASK),a
        call    KeyOffRemovedMaskV2

        ld      a,(EXT_APPLIED_MASK)
        and     $80
        ld      b,a
        ld      a,(EXT_REQUESTED_MASK)
        and     $7F
        ld      c,a
        ld      a,(EXT_APPLIED_MASK)
        and     $7F
        and     c
        or      b
        ld      (EXT_APPLIED_MASK),a
        and     $7F
        ld      (STATUS_ACTIVE_LAYERS),a

; Mute is a one-shot safety transition. Sequencing continues silently.
        ld      a,(EXT_REQUESTED_MASK)
        bit     7,a
        jr      z,.pending
        ld      a,(EXT_APPLIED_MASK)
        bit     7,a
        jr      nz,.pending
        call    AllNotesOffV2
        ld      a,(EXT_APPLIED_MASK)
        or      $80
        ld      (EXT_APPLIED_MASK),a
        ld      a,(EXT_STATE_FLAGS)
        or      EXT_FLAG_MUTED
        ld      (EXT_STATE_FLAGS),a
        ld      a,(STATUS_FLAGS)
        and     $F7
        or      STATUS_FLAG_MUTED
        ld      (STATUS_FLAGS),a
.pending:
        ld      a,$01
        ld      (STATE_PENDING),a
        ld      a,(EXT_STATE_FLAGS)
        or      EXT_FLAG_PENDING
        ld      (EXT_STATE_FLAGS),a
        ld      a,(STATUS_FLAGS)
        and     $F7
        ld      (STATUS_FLAGS),a
        ld      a,AUDIO_STATE_PLAYING
        ld      (STATUS_STATE),a
        ret
.bank_invalid:
; A bad or absent bank becomes muted-but-playable and is acknowledged so the
; main CPU cannot deadlock progression by retrying an impossible audio load.
        ld      a,(STATE_COMMAND_ARG_1)
        and     $80
        or      $80
        ld      (EXT_REQUESTED_MASK),a
        call    AllNotesOffIfAudibleV2
        ld      a,(EXT_REQUESTED_SCENE)
        ld      (EXT_APPLIED_SCENE),a
        ld      a,(EXT_REQUESTED_MASK)
        ld      (EXT_APPLIED_MASK),a
        ld      a,(EXT_REQUESTED_GENERATION)
        ld      (EXT_APPLIED_GENERATION),a
        xor     a
        ld      (STATE_PENDING),a
        ld      (STATUS_ACTIVE_LAYERS),a
        ld      a,(EXT_STATE_FLAGS)
        and     $FD
        or      EXT_FLAG_MUTED
        ld      (EXT_STATE_FLAGS),a
        ld      a,(STATUS_FLAGS)
        or      STATUS_FLAG_BANK_INVALID|STATUS_FLAG_MUTED
        ld      (STATUS_FLAGS),a
        ld      a,AUDIO_STATE_ERROR
        ld      (STATUS_STATE),a
        ret
.scene_invalid:
        ld      a,ERROR_SCENE_INVALID
        ld      (STATUS_LAST_ERROR_COMMAND),a
        ret

ApplyStoppedSceneV2:
        call    AllNotesOffIfAudibleV2
        xor     a
        ld      (EXT_REQUESTED_MASK),a
        ld      (EXT_APPLIED_SCENE),a
        ld      (EXT_APPLIED_MASK),a
        ld      (STATUS_ACTIVE_LAYERS),a
        ld      (STATE_PENDING),a
        ld      (STATE_BANK_VALID),a
        ld      (EXT_ACTIVE_BANK),a
        ld      (EXT_BANK_FLAGS),a
        ld      a,(EXT_STATE_FLAGS)
        and     $F8
        ld      (EXT_STATE_FLAGS),a
        ld      a,(STATUS_FLAGS)
        and     $37
        ld      (STATUS_FLAGS),a
        ld      a,(EXT_REQUESTED_GENERATION)
        ld      (EXT_APPLIED_GENERATION),a
        ld      a,AUDIO_STATE_STOPPED
        ld      (STATUS_STATE),a
        ret

CommandSfxV2:
        ld      a,(STATE_COMMAND_ARG_0)
        or      a
        ret     z
        cp      MAX_SFX_ID+1
        ret     nc
        ld      (STATE_SFX_ID),a
        ld      a,(EXT_APPLIED_MASK)
        bit     7,a
        ret     nz
        ld      a,(STATE_SFX_COMMAND_BUDGET)
        or      a
        jr      z,.drop
        dec     a
        ld      (STATE_SFX_COMMAND_BUDGET),a

        ld      a,(STATE_COMMAND_ARG_1)
        and     $30
        rrca
        rrca
        rrca
        rrca
        ld      c,a
        ld      a,(STATE_SFX_TICKS)
        or      a
        jr      z,.play
        ld      a,(STATE_SFX_PRIORITY)
        cp      c
        jr      c,.play
        jr      z,.play
        ld      hl,EXT_SFX_DROP_COUNT
        call    SaturatingIncrementByteV2
        ret
.drop:
        ld      hl,EXT_SFX_DROP_COUNT
        call    SaturatingIncrementByteV2
        ret
.play:
        ld      a,c
        ld      (STATE_SFX_PRIORITY),a
        ld      (EXT_SFX_PRIORITY),a
        ld      a,(STATE_SFX_ID)
        ld      l,a
        ld      h,$00
        ld      de,SfxNoteTableV2
        add     hl,de
        ld      a,(hl)
        call    PlayYmChannel5V2
        ld      a,(STATE_SFX_PRIORITY)
        ld      l,a
        ld      h,$00
        ld      de,SfxDurationTableV2
        add     hl,de
        ld      a,(hl)
        ld      (STATE_SFX_TICKS),a
        ret

CommandPauseV2:
        call    AllNotesOffIfAudibleV2
        xor     a
        ld      (STATE_SFX_TICKS),a
        inc     a
        ld      (STATE_SAFETY_COMMAND),a
        ld      a,(STATUS_FLAGS)
        or      STATUS_FLAG_PAUSED
        ld      (STATUS_FLAGS),a
        ld      a,AUDIO_STATE_PAUSED
        ld      (STATUS_STATE),a
        ret

CommandResumeV2:
        ld      a,(STATUS_FLAGS)
        and     $F7
        ld      (STATUS_FLAGS),a
        ld      a,AUDIO_STATE_PLAYING
        ld      (STATUS_STATE),a
        ret

CommandStopV2:
        call    AllNotesOffIfAudibleV2
        xor     a
        ld      (STATE_PENDING),a
        ld      (STATE_SFX_TICKS),a
        ld      (STATUS_ACTIVE_LAYERS),a
        inc     a
        ld      (STATE_SAFETY_COMMAND),a
        ld      a,AUDIO_STATE_STOPPED
        ld      (STATUS_STATE),a
        ret

CommandResetV2:
        call    AllNotesOffIfAudibleV2
        xor     a
        ld      (STATE_SFX_TICKS),a
        ld      (STATE_TEMPO_ACCUMULATOR),a
        ld      (STATE_TEMPO_ACCUMULATOR+1),a
        ld      (STATE_ROW_TICK),a
        ld      (EXT_PPQN_COUNTER),a
        ld      (EXT_PPQN_COUNTER+1),a
        inc     a
        ld      (STATE_SAFETY_COMMAND),a
        ld      a,$0F
        ld      (STATE_PATTERN_ROW),a
        ld      (EXT_PATTERN_ROW),a
        ld      a,AUDIO_STATE_PLAYING
        ld      (STATUS_STATE),a
        ret

CommandSetRegionV2:
        ld      a,(STATE_COMMAND_ARG_0)
        and     $01
        ld      (STATE_REGION),a
        ld      (MAILBOX_REGION),a
        ret

TickSfxV2:
        ld      a,(STATE_SFX_TICKS)
        or      a
        ret     z
        dec     a
        ld      (STATE_SFX_TICKS),a
        ret     nz
        ld      a,(EXT_APPLIED_MASK)
        bit     7,a
        ret     nz
        ld      a,$06
        jp      KeyOffYmChannelV2

TickSequencerV2:
        ld      a,(STATUS_STATE)
        cp      AUDIO_STATE_PLAYING
        ret     nz
        ld      hl,(STATE_TEMPO_ACCUMULATOR)
        ld      de,(STATE_TEMPO_INCREMENT)
        add     hl,de
        ld      (STATE_TEMPO_ACCUMULATOR),hl
        ld      a,MAX_PPQN_TICKS_PER_VINT
        ld      (STATE_PPQN_BUDGET),a
.tick_loop:
        ld      hl,(STATE_TEMPO_ACCUMULATOR)
        ld      a,(STATE_REGION)
        or      a
        ld      de,NTSC_PPQN_THRESHOLD
        jr      z,.threshold_ready
        ld      de,PAL_PPQN_THRESHOLD
.threshold_ready:
        or      a
        sbc     hl,de
        jr      c,.below_threshold
        ld      (STATE_TEMPO_ACCUMULATOR),hl
        ld      (EXT_TEMPO_ACCUMULATOR),hl
        call    TickPpqnV2
        ld      a,(STATE_PPQN_BUDGET)
        dec     a
        ld      (STATE_PPQN_BUDGET),a
        jr      nz,.tick_loop
        ret
.below_threshold:
        add     hl,de
        ld      (STATE_TEMPO_ACCUMULATOR),hl
        ld      (EXT_TEMPO_ACCUMULATOR),hl
        ret

TickPpqnV2:
        ld      hl,(EXT_PPQN_COUNTER)
        inc     hl
        ld      (EXT_PPQN_COUNTER),hl
        ld      a,l
        ld      (STATUS_PPQN_LOW),a
        ld      a,(STATE_ROW_TICK)
        inc     a
        cp      TICKS_PER_ROW
        jr      c,.store
        xor     a
        ld      (STATE_ROW_TICK),a
        jp      TickPatternRowV2
.store:
        ld      (STATE_ROW_TICK),a
        ret

TickPatternRowV2:
        ld      a,(STATE_PENDING)
        or      a
        jr      z,.advance_row
        xor     a
        ld      (STATE_PENDING),a
        ld      a,(EXT_STATE_FLAGS)
        and     $FD
        ld      (EXT_STATE_FLAGS),a
        ld      a,(EXT_REQUESTED_SCENE)
        ld      b,a
        ld      a,(EXT_APPLIED_SCENE)
        cp      b
        jr      z,.same_scene
        ld      a,b
        ld      (EXT_APPLIED_SCENE),a
        ld      l,a
        ld      h,$00
        ld      de,SceneStartRowTableV2
        add     hl,de
        ld      a,(hl)
        ld      (STATE_PATTERN_ROW),a
        ld      (EXT_PATTERN_ROW),a
        jr      .copy_state
.same_scene:
        call    AdvancePatternRowV2
.copy_state:
        ld      a,(EXT_REQUESTED_MASK)
        ld      (EXT_APPLIED_MASK),a
        and     $7F
        ld      (STATUS_ACTIVE_LAYERS),a
        ld      a,(EXT_REQUESTED_GENERATION)
        ld      (EXT_APPLIED_GENERATION),a
        ld      a,(EXT_APPLIED_MASK)
        bit     7,a
        jr      z,.unmuted
        ld      a,(EXT_STATE_FLAGS)
        or      EXT_FLAG_MUTED
        ld      (EXT_STATE_FLAGS),a
        ld      a,(STATUS_FLAGS)
        or      STATUS_FLAG_MUTED
        ld      (STATUS_FLAGS),a
        ret
.unmuted:
        ld      a,(EXT_STATE_FLAGS)
        and     $FB
        ld      (EXT_STATE_FLAGS),a
        ld      a,(STATUS_FLAGS)
        and     $7F
        ld      (STATUS_FLAGS),a
        jp      PlayPatternRowV2
.advance_row:
        call    AdvancePatternRowV2
        jp      PlayPatternRowV2

AdvancePatternRowV2:
        ld      a,(STATE_PATTERN_ROW)
        inc     a
        and     $0F
        ld      (STATE_PATTERN_ROW),a
        ld      (EXT_PATTERN_ROW),a
        ret

PlayPatternRowV2:
        ld      a,(EXT_APPLIED_MASK)
        bit     7,a
        ret     nz
        call    KeyOffMusicRowV2
        ld      a,(STATE_PATTERN_ROW)
        add     a,a
        add     a,a
        add     a,a
        ld      b,a
        ld      a,(STATE_PATTERN_ROW)
        sub     b
        neg
        ld      l,a
        ld      h,$00
        ld      de,ActiveBankPattern
        add     hl,de
        push    hl
        pop     ix

        ld      a,(EXT_APPLIED_MASK)
        bit     0,a
        jr      z,.layer_1
        ld      a,(ix+0)
        or      a
        call    nz,PlayYmChannel0V2
.layer_1:
        ld      a,(EXT_APPLIED_MASK)
        bit     1,a
        jr      z,.layer_2
        ld      a,(ix+1)
        or      a
        call    nz,PlayPsgPercussionV2
.layer_2:
        ld      a,(EXT_APPLIED_MASK)
        bit     2,a
        jr      z,.layer_3
        ld      a,(ix+2)
        or      a
        call    nz,PlayYmChannel1V2
.layer_3:
        ld      a,(EXT_APPLIED_MASK)
        bit     3,a
        jr      z,.layer_4
        ld      a,(ix+3)
        or      a
        call    nz,PlayYmChannel2V2
.layer_4:
        ld      a,(EXT_APPLIED_MASK)
        bit     4,a
        jr      z,.layer_5
        ld      a,(ix+4)
        or      a
        call    nz,PlayYmChannel3V2
.layer_5:
        ld      a,(EXT_APPLIED_MASK)
        bit     5,a
        jr      z,.layer_6
        ld      a,(ix+5)
        or      a
        call    nz,PlayPsgTextureV2
.layer_6:
        ld      a,(EXT_APPLIED_MASK)
        bit     6,a
        ret     z
        ld      a,(ix+6)
        or      a
        call    nz,PlayYmChannel4V2
        ret

ValidateActiveBankV2:
        ld      a,(STATE_COMMAND_ARG_0)
        ld      c,a
        ld      l,a
        ld      h,$00
        ld      de,SceneBankTableV2
        add     hl,de
        ld      b,(hl)
        ld      a,(ACTIVE_BANK_MAGIC_0)
        cp      "R"
        jp      nz,.invalid
        ld      a,(ACTIVE_BANK_MAGIC_1)
        cp      "9"
        jp      nz,.invalid
        ld      a,(ACTIVE_BANK_MAGIC_2)
        cp      "4"
        jp      nz,.invalid
        ld      a,(ACTIVE_BANK_MAGIC_3)
        cp      "A"
        jp      nz,.invalid
        ld      a,(ACTIVE_BANK_VERSION)
        cp      $01
        jp      nz,.invalid
        ld      a,(ACTIVE_BANK_ID)
        cp      b
        jp      nz,.invalid
        ld      a,(ACTIVE_BANK_ROWS)
        cp      PATTERN_ROWS
        jp      nz,.invalid
        ld      a,(ACTIVE_BANK_TICKS_PER_ROW)
        cp      TICKS_PER_ROW
        jp      nz,.invalid
        ld      a,(ACTIVE_BANK_LAYER_COUNT)
        cp      LAYER_COUNT
        jp      nz,.invalid
        ld      a,c
        ld      l,a
        ld      h,$00
        ld      de,SceneCapTableV2
        add     hl,de
        ld      b,(hl)
        ld      a,(ACTIVE_BANK_ECHO_CAP)
        cp      b
        jp      nz,.invalid
        ld      l,a
        ld      h,$00
        ld      de,CapMaskTableV2
        add     hl,de
        ld      a,(hl)
        ld      (STATE_CAP_MASK),a
        ld      a,(ACTIVE_BANK_TEMPO)
        or      a
        jp      z,.invalid
        cp      161
        jp      nc,.invalid
        call    ComputeTempoIncrementV2
        ld      a,(ACTIVE_BANK_ID)
        ld      (STATE_CURRENT_BANK),a
        ld      (EXT_ACTIVE_BANK),a
        ld      a,(ACTIVE_BANK_FLAGS)
        ld      (EXT_BANK_FLAGS),a
        ld      a,$01
        ld      (STATE_BANK_VALID),a
        ld      a,(EXT_STATE_FLAGS)
        or      EXT_FLAG_BANK_VALID
        ld      (EXT_STATE_FLAGS),a
        ld      a,(STATUS_FLAGS)
        and     $BF
        ld      (STATUS_FLAGS),a
        or      a
        ret
.invalid:
        xor     a
        ld      (STATE_BANK_VALID),a
        ld      (EXT_ACTIVE_BANK),a
        ld      (EXT_BANK_FLAGS),a
        ld      a,(EXT_STATE_FLAGS)
        and     $FE
        ld      (EXT_STATE_FLAGS),a
        ld      a,(STATUS_FLAGS)
        or      STATUS_FLAG_BANK_INVALID
        ld      (STATUS_FLAGS),a
        ld      a,ERROR_BANK_INVALID
        ld      (STATUS_LAST_ERROR_COMMAND),a
        scf
        ret

ComputeTempoIncrementV2:
; A=BPM. Compute BPM*24 as BPM*8 + BPM*16.
        ld      l,a
        ld      h,$00
        add     hl,hl
        add     hl,hl
        add     hl,hl
        push    hl
        pop     de
        add     hl,hl
        add     hl,de
        ld      (STATE_TEMPO_INCREMENT),hl
        ld      (EXT_TEMPO_INCREMENT),hl
        ret

KeyOffRemovedMaskV2:
        ld      a,(EXT_APPLIED_MASK)
        bit     7,a
        ret     nz
        ld      a,(STATE_REMOVED_MASK)
        bit     0,a
        jr      z,.layer_1
        xor     a
        call    KeyOffYmChannelV2
.layer_1:
        ld      a,(STATE_REMOVED_MASK)
        bit     1,a
        jr      z,.layer_2
        ld      a,PSG_MUTE_NOISE
        call    WritePsgV2
.layer_2:
        ld      a,(STATE_REMOVED_MASK)
        bit     2,a
        jr      z,.layer_3
        ld      a,$01
        call    KeyOffYmChannelV2
.layer_3:
        ld      a,(STATE_REMOVED_MASK)
        bit     3,a
        jr      z,.layer_4
        ld      a,$02
        call    KeyOffYmChannelV2
.layer_4:
        ld      a,(STATE_REMOVED_MASK)
        bit     4,a
        jr      z,.layer_5
        ld      a,$04
        call    KeyOffYmChannelV2
.layer_5:
        ld      a,(STATE_REMOVED_MASK)
        bit     5,a
        jr      z,.layer_6
        ld      a,PSG_MUTE_TONE_2
        call    WritePsgV2
.layer_6:
        ld      a,(STATE_REMOVED_MASK)
        bit     6,a
        ret     z
        ld      a,$05
        jp      KeyOffYmChannelV2

; Voices removed by the immediately applied state snapshot were already keyed
; off earlier in this V-int. Skip only those redundant row-boundary writes,
; then clear the one-row mask. This keeps transition + two-SFX YM writes below
; the fixed 32-write V-int budget without weakening immediate removals.
KeyOffMusicRowV2:
        ld      a,(STATE_REMOVED_MASK)
        bit     0,a
        jr      nz,.layer_1
        xor     a
        call    KeyOffYmChannelV2
.layer_1:
        ld      a,(STATE_REMOVED_MASK)
        bit     2,a
        jr      nz,.layer_2
        ld      a,$01
        call    KeyOffYmChannelV2
.layer_2:
        ld      a,(STATE_REMOVED_MASK)
        bit     3,a
        jr      nz,.layer_3
        ld      a,$02
        call    KeyOffYmChannelV2
.layer_3:
        ld      a,(STATE_REMOVED_MASK)
        bit     4,a
        jr      nz,.layer_4
        ld      a,$04
        call    KeyOffYmChannelV2
.layer_4:
        ld      a,(STATE_REMOVED_MASK)
        bit     6,a
        jr      nz,.noise
        ld      a,$05
        call    KeyOffYmChannelV2
.noise:
        ld      a,(STATE_REMOVED_MASK)
        bit     1,a
        jr      nz,.texture
        ld      a,PSG_MUTE_NOISE
        call    WritePsgV2
.texture:
        ld      a,(STATE_REMOVED_MASK)
        bit     5,a
        jr      nz,.done
        ld      a,PSG_MUTE_TONE_2
        call    WritePsgV2
.done:
        xor     a
        ld      (STATE_REMOVED_MASK),a
        ret

KeyOffMusicV2:
        xor     a
        call    KeyOffYmChannelV2
        ld      a,$01
        call    KeyOffYmChannelV2
        ld      a,$02
        call    KeyOffYmChannelV2
        ld      a,$04
        call    KeyOffYmChannelV2
        ld      a,$05
        call    KeyOffYmChannelV2
        ld      a,PSG_MUTE_NOISE
        call    WritePsgV2
        ld      a,PSG_MUTE_TONE_2
        jp      WritePsgV2

AllNotesOffV2:
        call    KeyOffMusicV2
        ld      a,$06
        call    KeyOffYmChannelV2
        ld      a,PSG_MUTE_TONE_0
        call    WritePsgV2
        ld      a,PSG_MUTE_TONE_1
        call    WritePsgV2
        ld      a,PSG_MUTE_TONE_2
        call    WritePsgV2
        ld      a,PSG_MUTE_NOISE
        jp      WritePsgV2

AllNotesOffIfAudibleV2:
        ld      a,(EXT_APPLIED_MASK)
        bit     7,a
        ret     nz
        jp      AllNotesOffV2

KeyOffYmChannelV2:
        ld      e,a
        ld      d,YM_REGISTER_KEY_ON
        jp      WriteYm0V2

PlayYmChannel0V2:
        ld      (STATE_YM_NOTE),a
        xor     a
        ld      (STATE_YM_PORT),a
        ld      a,$A0
        ld      (STATE_YM_LOW_REGISTER),a
        ld      a,$A4
        ld      (STATE_YM_HIGH_REGISTER),a
        xor     a
        ld      (STATE_YM_KEY_CODE),a
        ld      a,$20
        ld      (STATE_YM_BLOCK),a
        jp      PlayStoredYmNoteV2

PlayYmChannel1V2:
        ld      (STATE_YM_NOTE),a
        xor     a
        ld      (STATE_YM_PORT),a
        ld      a,$A1
        ld      (STATE_YM_LOW_REGISTER),a
        ld      a,$A5
        ld      (STATE_YM_HIGH_REGISTER),a
        ld      a,$01
        ld      (STATE_YM_KEY_CODE),a
        ld      a,$10
        ld      (STATE_YM_BLOCK),a
        jp      PlayStoredYmNoteV2

PlayYmChannel2V2:
        ld      (STATE_YM_NOTE),a
        xor     a
        ld      (STATE_YM_PORT),a
        ld      a,$A2
        ld      (STATE_YM_LOW_REGISTER),a
        ld      a,$A6
        ld      (STATE_YM_HIGH_REGISTER),a
        ld      a,$02
        ld      (STATE_YM_KEY_CODE),a
        ld      a,$20
        ld      (STATE_YM_BLOCK),a
        jp      PlayStoredYmNoteV2

PlayYmChannel3V2:
        ld      (STATE_YM_NOTE),a
        ld      a,$01
        ld      (STATE_YM_PORT),a
        ld      a,$A0
        ld      (STATE_YM_LOW_REGISTER),a
        ld      a,$A4
        ld      (STATE_YM_HIGH_REGISTER),a
        ld      a,$04
        ld      (STATE_YM_KEY_CODE),a
        ld      a,$28
        ld      (STATE_YM_BLOCK),a
        jp      PlayStoredYmNoteV2

PlayYmChannel4V2:
        ld      (STATE_YM_NOTE),a
        ld      a,$01
        ld      (STATE_YM_PORT),a
        ld      a,$A1
        ld      (STATE_YM_LOW_REGISTER),a
        ld      a,$A5
        ld      (STATE_YM_HIGH_REGISTER),a
        ld      a,$05
        ld      (STATE_YM_KEY_CODE),a
        ld      a,$28
        ld      (STATE_YM_BLOCK),a
        jp      PlayStoredYmNoteV2

PlayYmChannel5V2:
        ld      (STATE_YM_NOTE),a
        ld      a,$01
        ld      (STATE_YM_PORT),a
        ld      a,$A2
        ld      (STATE_YM_LOW_REGISTER),a
        ld      a,$A6
        ld      (STATE_YM_HIGH_REGISTER),a
        ld      a,$06
        ld      (STATE_YM_KEY_CODE),a
        ld      a,$28
        ld      (STATE_YM_BLOCK),a
        ld      a,$06
        call    KeyOffYmChannelV2
        jp      PlayStoredYmNoteV2

PlayStoredYmNoteV2:
        ld      a,(STATE_YM_NOTE)
        dec     a
        ld      l,a
        ld      h,$00
        ld      de,YmFrequencyLowTableV2
        add     hl,de
        ld      e,(hl)
        ld      a,(STATE_YM_LOW_REGISTER)
        ld      d,a
        call    WriteYmSelectedV2
        ld      a,(STATE_YM_NOTE)
        dec     a
        ld      l,a
        ld      h,$00
        ld      de,YmFrequencyHighTableV2
        add     hl,de
        ld      a,(hl)
        ld      b,a
        ld      a,(STATE_YM_BLOCK)
        or      b
        ld      e,a
        ld      a,(STATE_YM_HIGH_REGISTER)
        ld      d,a
        call    WriteYmSelectedV2
        ld      a,(STATE_YM_KEY_CODE)
        or      $F0
        ld      e,a
        ld      d,YM_REGISTER_KEY_ON
        jp      WriteYm0V2

PlayPsgPercussionV2:
        ld      a,PSG_NOISE_SHORT
        call    WritePsgV2
        ld      a,PSG_PERCUSSION_VOLUME
        jp      WritePsgV2

PlayPsgTextureV2:
        dec     a
        ld      l,a
        ld      h,$00
        ld      de,PsgToneLatchTableV2
        add     hl,de
        ld      a,(hl)
        call    WritePsgV2
        ld      a,(ix+5)
        dec     a
        ld      l,a
        ld      h,$00
        ld      de,PsgToneDataTableV2
        add     hl,de
        ld      a,(hl)
        call    WritePsgV2
        ld      a,PSG_TEXTURE_VOLUME
        jp      WritePsgV2

InitialiseYm2612V2:
        xor     a
        ld      (STATE_YM_PORT),a
        ld      d,$22
        ld      e,$00
        call    WriteYm0V2
        ld      d,$2B
        ld      e,$00
        call    WriteYm0V2

        xor     a
        ld      (STATE_YM_PORT),a
        ld      c,$00
        call    InitialiseYmChannelV2
        ld      c,$01
        call    InitialiseYmChannelV2
        ld      c,$02
        call    InitialiseYmChannelV2
        ld      a,$01
        ld      (STATE_YM_PORT),a
        ld      c,$00
        call    InitialiseYmChannelV2
        ld      c,$01
        call    InitialiseYmChannelV2
        ld      c,$02
        call    InitialiseYmChannelV2
        ret

InitialiseYmChannelV2:
; C=port-local channel 0..2. Algorithm 7 makes four quiet carriers.
        ld      b,$30
        ld      e,$01
        call    InitialiseOperatorGroupV2
        ld      b,$40
        ld      e,$50
        call    InitialiseOperatorGroupV2
        ld      b,$50
        ld      e,$1F
        call    InitialiseOperatorGroupV2
        ld      b,$80
        ld      e,$0F
        call    InitialiseOperatorGroupV2
        ld      a,$B0
        add     a,c
        ld      d,a
        ld      e,$07
        call    WriteYmSelectedV2
        ld      a,$B4
        add     a,c
        ld      d,a
        ld      e,$C0
        jp      WriteYmSelectedV2

InitialiseOperatorGroupV2:
; B=register base, C=channel, E=value. Exactly four operators.
        ld      a,b
        add     a,c
        ld      d,a
        call    WriteYmSelectedV2
        ld      a,d
        add     a,$04
        ld      d,a
        call    WriteYmSelectedV2
        ld      a,d
        add     a,$04
        ld      d,a
        call    WriteYmSelectedV2
        ld      a,d
        add     a,$04
        ld      d,a
        jp      WriteYmSelectedV2

InitialisePsgV2:
        ld      a,PSG_MUTE_TONE_0
        call    WritePsgV2
        ld      a,PSG_MUTE_TONE_1
        call    WritePsgV2
        ld      a,PSG_MUTE_TONE_2
        call    WritePsgV2
        ld      a,PSG_MUTE_NOISE
        jp      WritePsgV2

WriteYmSelectedV2:
        ld      a,(STATE_YM_PORT)
        or      a
        jp      z,WriteYm0V2
        jp      WriteYm1V2

WriteYm0V2:
        push    af
        push    bc
        call    WaitYmReadyV2
        jr      c,.done
        ld      a,d
        ld      (YM2612_ADDRESS_0),a
        call    WaitYmReadyV2
        jr      c,.done
        ld      a,e
        ld      (YM2612_DATA_0),a
        ld      hl,STATUS_YM_WRITE_COUNT
        call    SaturatingIncrementByteV2
.done:
        pop     bc
        pop     af
        ret

WriteYm1V2:
        push    af
        push    bc
        call    WaitYmReadyV2
        jr      c,.done
        ld      a,d
        ld      (YM2612_ADDRESS_1),a
        call    WaitYmReadyV2
        jr      c,.done
        ld      a,e
        ld      (YM2612_DATA_1),a
        ld      hl,STATUS_YM_WRITE_COUNT
        call    SaturatingIncrementByteV2
.done:
        pop     bc
        pop     af
        ret

WaitYmReadyV2:
        ld      b,YM_BUSY_WAIT_LIMIT
.wait:
        ld      a,(YM2612_ADDRESS_0)
        and     $80
        ret     z
        djnz    .wait
        call    RecordYmTimeoutV2
        scf
        ret

RecordYmTimeoutV2:
        ld      a,(STATUS_FLAGS)
        or      STATUS_FLAG_YM_TIMEOUT
        ld      (STATUS_FLAGS),a
        ld      a,ERROR_YM_TIMEOUT
        ld      (STATUS_LAST_ERROR_COMMAND),a
        ld      a,AUDIO_STATE_ERROR
        ld      (STATUS_STATE),a
        ret

WritePsgV2:
        ld      (PSG_WRITE_PORT),a
        push    af
        ld      hl,STATUS_PSG_WRITE_COUNT
        call    SaturatingIncrementByteV2
        pop     af
        ret

IncrementCommandCountV2:
        ld      hl,(STATUS_COMMAND_COUNT)
        ld      a,h
        and     l
        cp      $FF
        ret     z
        inc     hl
        ld      (STATUS_COMMAND_COUNT),hl
        ret

RecordQueueFaultV2:
        ld      a,(STATUS_FLAGS)
        or      STATUS_FLAG_QUEUE_FAULT
        ld      (STATUS_FLAGS),a
        ld      a,ERROR_QUEUE_CORRUPT
        ld      (STATUS_LAST_ERROR_COMMAND),a
        ret

SaturatingIncrementByteV2:
        ld      a,(hl)
        cp      $FF
        ret     z
        inc     (hl)
        ret

; Scene-to-active-bank and cap tables freeze the host registry ABI.
SceneBankTableV2:
        db      0
        db      1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18
        db      2,2,2,6,6,6,10,10,10,14,14,14
        db      18,18,18,18
        db      19,19,19,20,20
SceneBankTableV2End:

SceneCapTableV2:
        db      0
        db      2,2,3,3,3,3,4,4,4,4,5,5,5,5,6,6,6,6
        db      2,2,2,3,3,3,4,4,4,5,5,5
        db      6,6,6,6
        db      6,6,6,6,6
SceneCapTableV2End:

SceneStartRowTableV2:
        db      0
        db      0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0
        db      0,5,10,0,5,10,0,5,10,0,5,10
        db      0,4,8,12
        db      0,5,10,0,8
SceneStartRowTableV2End:

CapMaskTableV2:
        db      $01,$03,$07,$0F,$1F,$3F,$7F
CapMaskTableV2End:

; F-number tables are original integer approximations for one octave.
YmFrequencyLowTableV2:
        db      $84,$A9,$D2,$FD,$2A,$5A,$8E,$C4,$FD,$39,$7A,$BE
YmFrequencyLowTableV2End:
YmFrequencyHighTableV2:
        db      $02,$02,$02,$02,$03,$03,$03,$03,$03,$04,$04,$04
YmFrequencyHighTableV2End:

PsgToneLatchTableV2:
        db      $CB,$C3,$CC,$C7,$C3,$C0,$CE,$CD,$CD,$CE,$C0,$C2
PsgToneLatchTableV2End:
PsgToneDataTableV2:
        db      $1A,$19,$17,$16,$15,$14,$12,$11,$10,$0F,$0F,$0E
PsgToneDataTableV2End:

SfxNoteTableV2:
        db      0,8,10,5,3,7,1,2,12,4,9,6,11,2,1,8
SfxNoteTableV2End:
SfxDurationTableV2:
        db      2,3,4,6
SfxDurationTableV2End:

DriverV2ImageEnd:
        assert  DriverV2ImageEnd<=DRIVER_STATE_BASE
        assert  ActiveBankEnd-ActiveBankStart=$80
        assert  SceneBankTableV2End-SceneBankTableV2=40
        assert  SceneCapTableV2End-SceneCapTableV2=40
        assert  SceneStartRowTableV2End-SceneStartRowTableV2=40
        assert  CapMaskTableV2End-CapMaskTableV2=7
        assert  YmFrequencyLowTableV2End-YmFrequencyLowTableV2=12
        assert  YmFrequencyHighTableV2End-YmFrequencyHighTableV2=12
        assert  PsgToneLatchTableV2End-PsgToneLatchTableV2=12
        assert  PsgToneDataTableV2End-PsgToneDataTableV2=12
        assert  SfxNoteTableV2End-SfxNoteTableV2=16
        assert  SfxDurationTableV2End-SfxDurationTableV2=4
