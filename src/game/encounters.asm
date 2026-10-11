; Minimal original immutable encounter fixtures for the deterministic core.
; Campaign content can extend this pointer table without changing engine code.

        even

EncounterDescriptorTable:
        dc.l    EncounterDescriptor0
        dc.l    EncounterDescriptor1
EncounterDescriptorTableEnd:
ENCOUNTER_DESCRIPTOR_COUNT      equ     (EncounterDescriptorTableEnd-EncounterDescriptorTable)/4

EncounterDescriptor0:
        dc.w    $0000                   ; encounter ID
        dc.w    $0000                   ; flags
        dc.w    ENCOUNTER_DESCRIPTOR_VERSION_VALUE
        dc.w    ENCOUNTER_DESCRIPTOR_SIZE
        dc.w    0,0,320,224             ; half-open room bounds
        dc.w    32,112                  ; spawn center in pixels
        dc.w    (Encounter0RectsEnd-Encounter0Rects)/STATIC_RECT_BYTES
        dc.w    (Encounter0ObjectsEnd-Encounter0Objects)/OBJECT_INIT_BYTES
        dc.l    Encounter0Rects
        dc.l    Encounter0Objects
EncounterDescriptor0End:

Encounter0Rects:
        dc.w    112,48,16,128
        dc.w    208,80,24,16
Encounter0RectsEnd:

Encounter0Objects:
        dc.b    POOL_TYPE_ENEMY,$00
        dc.w    $0001,160,64,0,3
        dc.b    POOL_TYPE_DEVICE,$00
        dc.w    $0001,80,112,0,0
        dc.b    POOL_TYPE_CARRYABLE,$00
        dc.w    $0001,48,152,0,0
        dc.b    POOL_TYPE_HAZARD,$00
        dc.w    $0001,176,176,0,1
Encounter0ObjectsEnd:

        even

EncounterDescriptor1:
        dc.w    $0001
        dc.w    $0000
        dc.w    ENCOUNTER_DESCRIPTOR_VERSION_VALUE
        dc.w    ENCOUNTER_DESCRIPTOR_SIZE
        dc.w    0,0,320,224
        dc.w    288,112
        dc.w    (Encounter1RectsEnd-Encounter1Rects)/STATIC_RECT_BYTES
        dc.w    (Encounter1ObjectsEnd-Encounter1Objects)/OBJECT_INIT_BYTES
        dc.l    Encounter1Rects
        dc.l    Encounter1Objects
EncounterDescriptor1End:

Encounter1Rects:
        dc.w    64,96,80,16
        dc.w    176,48,16,112
        dc.w    240,144,40,16
Encounter1RectsEnd:

Encounter1Objects:
        dc.b    POOL_TYPE_ENEMY,$00
        dc.w    $0001,224,64,0,4
        dc.b    POOL_TYPE_ENEMY,$01
        dc.w    $0001,256,96,0,4
        dc.b    POOL_TYPE_ENEMY,$02
        dc.w    $0001,224,176,0,4
        dc.b    POOL_TYPE_DEVICE,$00
        dc.w    $0002,48,48,0,0
        dc.b    POOL_TYPE_DEVICE,$01
        dc.w    $0002,288,176,0,0
        dc.b    POOL_TYPE_HAZARD,$00
        dc.w    $0001,120,72,0,2
        dc.b    POOL_TYPE_HAZARD,$01
        dc.w    $0001,208,136,0,2
        dc.b    POOL_TYPE_BOSS,$00
        dc.w    $0001,272,48,0,6
Encounter1ObjectsEnd:

        even

        assert  EncounterDescriptor0End-EncounterDescriptor0=ENCOUNTER_DESCRIPTOR_SIZE,"encounter 0 descriptor size mismatch"
        assert  EncounterDescriptor1End-EncounterDescriptor1=ENCOUNTER_DESCRIPTOR_SIZE,"encounter 1 descriptor size mismatch"
        assert  Encounter0RectsEnd-Encounter0Rects<=STATIC_RECT_CAPACITY*STATIC_RECT_BYTES,"encounter 0 rectangle overflow"
        assert  Encounter1RectsEnd-Encounter1Rects<=STATIC_RECT_CAPACITY*STATIC_RECT_BYTES,"encounter 1 rectangle overflow"
        assert  Encounter0ObjectsEnd-Encounter0Objects<=TOTAL_POOL_CAPACITY*OBJECT_INIT_BYTES,"encounter 0 object overflow"
        assert  Encounter1ObjectsEnd-Encounter1Objects<=TOTAL_POOL_CAPACITY*OBJECT_INIT_BYTES,"encounter 1 object overflow"
