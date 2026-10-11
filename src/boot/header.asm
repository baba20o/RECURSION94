; Standard 256-byte Sega Genesis / Mega Drive cartridge header.

CartridgeHeader:
HeaderConsole:
        dc.b    "SEGA MEGA DRIVE "
        assert  *-HeaderConsole=16,"console field must be 16 bytes"

HeaderCopyright:
        dc.b    "(C)R94 2026.JUL "
        assert  *-HeaderCopyright=16,"copyright field must be 16 bytes"

HeaderDomestic:
        dc.b    "RECURSION 94: THE LAST CARTRIDGE"
        dcb.b   48-(*-HeaderDomestic),$20
        assert  *-HeaderDomestic=48,"domestic title field must be 48 bytes"

HeaderOverseas:
        dc.b    "RECURSION 94: THE LAST CARTRIDGE"
        dcb.b   48-(*-HeaderOverseas),$20
        assert  *-HeaderOverseas=48,"overseas title field must be 48 bytes"

HeaderSerial:
        dc.b    "GM R94M1000-00"
        assert  *-HeaderSerial=14,"serial field must be 14 bytes"

HeaderChecksum:
        dc.w    $0000

HeaderIoSupport:
        dc.b    "J"
        dcb.b   15,$20

        dc.l    $00000000
        dc.l    $000FFFFF
        dc.l    $00FF0000
        dc.l    $00FFFFFF

; RA, $F820 declares byte-wide backup RAM on odd addresses.
        dc.b    "RA"
        dc.w    $F820
        dc.l    $00200001
        dc.l    $00203FFF

HeaderModem:
        dcb.b   12,$20

HeaderMemo:
        dc.b    "ORIGINAL PROGRAMMATIC ART AND CODE"
        dcb.b   40-(*-HeaderMemo),$20

HeaderRegions:
        dc.b    "JUE"
        dcb.b   13,$20

CartridgeHeaderEnd:

        assert  CartridgeHeader=$000100,"cartridge header must start at ROM offset $000100"
        assert  HeaderChecksum=$00018E,"checksum word must be at ROM offset $00018E"
        assert  CartridgeHeaderEnd=$000200,"cartridge header must end at ROM offset $000200"

