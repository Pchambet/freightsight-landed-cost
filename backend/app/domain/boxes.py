"""What a container is, read from what people write about it: its number, its size-type, its line.

Spreadsheets say "40HC", "1x40HQ", "40' HC" or "20DV"; forwarders' ledgers say "FACT 0412 DOS 5678
MSKU1234565"; carriers are "CMA CGM" or "Hapag-Lloyd". The product needs the ISO 6346 size-type (the
audit compares boxes of the same length), a container number it can trust, and the four-letter SCAC
the database has room for. Pure functions; nothing here guesses what it cannot check.
"""

from __future__ import annotations

import re

_NUMBER = re.compile(r"\b([A-Z]{3}[UJZ])\s?(\d{6})\s?(\d)\b")
_ISO_SIZE_TYPE = re.compile(r"^[24L][0-9A-Z][A-Z][0-9A-Z]$")


def check_digit(owner_and_serial: str) -> int:
    """ISO 6346: letters count from A = 10, skipping the multiples of 11; each character is weighted
    by 2 to the power of its position; the sum modulo 11, and 10 reads as 0."""
    total = 0
    for position, char in enumerate(owner_and_serial):
        if char.isdigit():
            value = int(char)
        else:
            value = ord(char) - ord("A") + 10
            value += (value - 1) // 10  # A = 10, B = 12 ... K = 21, L = 23 ... U = 32, V = 34
        total += value * 2**position
    return total % 11 % 10


def is_container_number(text: str) -> bool:
    """A number whose check digit is right: a typo in any of its eleven characters shows."""
    number = text.strip().upper().replace(" ", "")
    match = _NUMBER.fullmatch(number)
    return match is not None and check_digit(match.group(1) + match.group(2)) == int(match.group(3))


def container_numbers_in(text: str) -> list[str]:
    """Every valid container number written in a free text, such as a ledger line's label. The check
    digit is what makes this safe: eleven characters that happen to look like a number almost never
    carry the right one."""
    found = []
    for owner, serial, digit in _NUMBER.findall(text.upper()):
        if check_digit(owner + serial) == int(digit) and owner + serial + digit not in found:
            found.append(owner + serial + digit)
    return found


#: What a spreadsheet writes, as it compares (no spaces, quotes, apostrophes or "1x"), and the ISO 6346
#: size-type it means. "45G1" is ISO for a 40' high cube; "45HC" in shorthand is a 45-footer.
_SHORTHAND = {
    **dict.fromkeys(("20", "20GP", "20DV", "20DC", "20ST", "20STD", "20DRY", "20BOX"), "22G1"),
    **dict.fromkeys(("40", "40GP", "40DV", "40DC", "40ST", "40STD", "40DRY", "40BOX"), "42G1"),
    **dict.fromkeys(("40HC", "40HQ", "40HCDV", "40HDV", "40HIGHCUBE", "40DVHC"), "45G1"),
    **dict.fromkeys(("45", "45HC", "45HQ", "45HCDV", "45HIGHCUBE"), "L5G1"),
    **dict.fromkeys(("20RF", "20RH", "20RE", "20REEFER"), "22R1"),
    **dict.fromkeys(("40RF", "40RH", "40RE", "40RQ", "40HCRF", "40REEFER", "40RHC"), "45R1"),
    **dict.fromkeys(("20OT", "20OPENTOP"), "22U1"),
    **dict.fromkeys(("40OT", "40OPENTOP", "40HCOT"), "42U1"),
    **dict.fromkeys(("20FR", "20FLATRACK", "20FL"), "22P1"),
    **dict.fromkeys(("40FR", "40FLATRACK", "40FL"), "42P1"),
}
#: The length of a box, from the first character of its ISO size-type.
LENGTHS = {"2": "20", "4": "40", "L": "45"}


def iso_size_type(raw: str | None) -> str | None:
    """The ISO 6346 size-type a spreadsheet cell means, or None when it cannot be told."""
    if not raw:
        return None
    text = re.sub(r"^\d+\s*[xX\u00d7]\s*", "", raw.strip())  # "1x40HQ": one box of it
    compact = re.sub(r"[\s'\u2019\"`\u00b4.\-_/]+", "", text).upper().replace("PIEDS", "").replace("FT", "")
    if compact in _SHORTHAND:
        return _SHORTHAND[compact]
    return compact if _ISO_SIZE_TYPE.fullmatch(compact) else None


def length_of(iso_type: str | None) -> str:
    """The length of a box, "20", "40" or "45"; "" when it is not known."""
    return LENGTHS.get((iso_type or "").strip().upper()[:1], "")


#: The lines most French importers ship with, by the names their paperwork gives, and the SCAC the
#: database stores (four characters). Anything else is left for a person to type.
_CARRIERS = {
    "msc": "MSCU", "mediterraneanshippingcompany": "MSCU", "mediterraneanshipping": "MSCU",
    "maersk": "MAEU", "maerskline": "MAEU", "apmollermaersk": "MAEU", "sealand": "SEAU",
    "cmacgm": "CMDU", "cma": "CMDU", "anl": "ANNU", "apl": "APLU",
    "hapaglloyd": "HLCU", "hapag": "HLCU",
    "cosco": "COSU", "coscoshipping": "COSU", "oocl": "OOLU",
    "evergreen": "EGLV", "evergreenline": "EGLV",
    "one": "ONEY", "oceannetworkexpress": "ONEY",
    "yangming": "YMLU", "hmm": "HDMU", "hyundaimerchantmarine": "HDMU",
    "zim": "ZIMU", "pil": "PCIU", "pacificinternationallines": "PCIU", "wanhai": "WHLC",
    "tslines": "TSLU", "sitc": "SITC", "arkas": "ARKU", "unifeeder": "UNFE", "xpressfeeders": "XPRF",
}  # fmt: skip
_SCAC = re.compile(r"^[A-Z]{4}$")


def scac_of(raw: str | None) -> str | None:
    """The four-letter code of a line named in a cell: the code itself, or a known name."""
    if not raw:
        return None
    text = raw.strip()
    if _SCAC.fullmatch(text.upper()) and text.upper() in _CARRIERS.values():
        return text.upper()
    key = re.sub(r"[^a-z]", "", text.casefold())
    return _CARRIERS.get(key) or (text.upper() if _SCAC.fullmatch(text.upper()) else None)
