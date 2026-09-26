"""The UN/LOCODE of a port, from what a spreadsheet calls it.

An importer's tracking sheet says "NINGBO", "Le Havre" or "Anvers"; the route the audit compares
boxes on is "CNNGB-FRLEH". Names are looked up first and codes second, because some names are
well-formed codes of somewhere else: "GENOA" reads as the Georgian code GE-NOA. A code is accepted
only with the country prefix of a country ships actually sail from or to here. Anything else is left
for a person to say, never guessed.
"""

from __future__ import annotations

import re
import unicodedata

#: The ports French importers' goods leave from and arrive at, by the names paperwork gives them —
#: letters only, lower case, no accents. Several names may mean one port.
_BY_NAME: dict[str, str] = {
    # China and around
    "shanghai": "CNSHA", "ningbo": "CNNGB", "ningbozhoushan": "CNNGB", "shenzhen": "CNSZX",
    "yantian": "CNYTN", "shekou": "CNSHK", "chiwan": "CNCWN", "qingdao": "CNTAO", "xiamen": "CNXMN",
    "tianjin": "CNTSN", "xingang": "CNTXG", "tianjinxingang": "CNTXG", "dalian": "CNDLC",
    "guangzhou": "CNCAN", "nansha": "CNNSA", "huangpu": "CNHUA", "fuzhou": "CNFOC",
    "lianyungang": "CNLYG", "zhuhai": "CNZUH", "taicang": "CNTAG", "hongkong": "HKHKG",
    "kaohsiung": "TWKHH", "keelung": "TWKEL", "taichung": "TWTXG", "busan": "KRPUS", "pusan": "KRPUS",
    "incheon": "KRINC", "tokyo": "JPTYO", "yokohama": "JPYOK", "kobe": "JPUKB", "osaka": "JPOSA",
    "nagoya": "JPNGO",
    # South-East and South Asia
    "hochiminh": "VNSGN", "hochiminhcity": "VNSGN", "saigon": "VNSGN", "catlai": "VNCLI",
    "haiphong": "VNHPH", "danang": "VNDAD", "caimep": "VNCMT", "laemchabang": "THLCH",
    "bangkok": "THBKK", "portklang": "MYPKG", "klang": "MYPKG", "tanjungpelepas": "MYTPP",
    "penang": "MYPEN", "singapore": "SGSIN", "singapour": "SGSIN", "jakarta": "IDJKT",
    "tanjungpriok": "IDTPP", "surabaya": "IDSUB", "semarang": "IDSRG", "manila": "PHMNL",
    "nhavasheva": "INNSA", "jawaharlalnehru": "INNSA", "jnpt": "INNSA", "mumbai": "INBOM",
    "mundra": "INMUN", "chennai": "INMAA", "kolkata": "INCCU", "tuticorin": "INTUT",
    "pipavav": "INPAV", "cochin": "INCOK", "chittagong": "BDCGP", "chattogram": "BDCGP",
    "colombo": "LKCMB", "karachi": "PKKHI", "portqasim": "PKBQM",
    # Middle East, Africa, Turkey
    "jebelali": "AEJEA", "dubai": "AEDXB", "portsaid": "EGPSD", "alexandria": "EGALY",
    "alexandrie": "EGALY", "tangermed": "MAPTM", "tanger": "MAPTM", "casablanca": "MACAS",
    "rades": "TNRAD", "tunis": "TNTUN", "durban": "ZADUR", "istanbul": "TRIST", "ambarli": "TRAMR",
    "mersin": "TRMER", "izmir": "TRIZM", "gemlik": "TRGEM",
    # France
    "lehavre": "FRLEH", "havre": "FRLEH", "fos": "FRFOS", "fossurmer": "FRFOS", "marseillefos": "FRFOS",
    "marseille": "FRMRS", "dunkerque": "FRDKK", "dunkirk": "FRDKK", "montoir": "FRMTX",
    "montoirdebretagne": "FRMTX", "saintnazaire": "FRMTX", "nantes": "FRNTE", "rouen": "FRURO",
    "bordeaux": "FRBOD", "sete": "FRSET",
    # Rest of Europe
    "antwerp": "BEANR", "anvers": "BEANR", "antwerpen": "BEANR", "zeebrugge": "BEZEE",
    "rotterdam": "NLRTM", "amsterdam": "NLAMS", "hamburg": "DEHAM", "hambourg": "DEHAM",
    "bremerhaven": "DEBRV", "bremen": "DEBRE", "valencia": "ESVLC", "algeciras": "ESALG",
    "algesiras": "ESALG", "barcelona": "ESBCN", "barcelone": "ESBCN", "bilbao": "ESBIO",
    "genoa": "ITGOA", "genova": "ITGOA", "genes": "ITGOA", "laspezia": "ITSPE", "livorno": "ITLIV",
    "livourne": "ITLIV", "trieste": "ITTRS", "salerno": "ITSAL", "naples": "ITNAP",
    "gioiatauro": "ITGIT", "felixstowe": "GBFXT", "southampton": "GBSOU", "londongateway": "GBLGP",
    "liverpool": "GBLIV", "lisbon": "PTLIS", "lisbonne": "PTLIS", "sines": "PTSIE",
    "leixoes": "PTLEI", "gdansk": "PLGDN", "gdynia": "PLGDY", "piraeus": "GRPIR", "piree": "GRPIR",
    "koper": "SIKOP", "rijeka": "HRRJK", "constanta": "ROCND", "gothenburg": "SEGOT",
    "goteborg": "SEGOT", "aarhus": "DKAAR", "dublin": "IEDUB",
    # Americas
    "newyork": "USNYC", "losangeles": "USLAX", "longbeach": "USLGB", "savannah": "USSAV",
    "houston": "USHOU", "miami": "USMIA", "montreal": "CAMTR", "santos": "BRSSZ",
}  # fmt: skip

#: Countries whose codes are taken as they are typed. A well-formed code from anywhere else is more
#: likely a name that happens to have five letters.
_COUNTRIES = frozenset(
    {"CN", "HK", "TW", "KR", "JP", "VN", "TH", "MY", "SG", "ID", "PH", "IN", "BD", "LK", "PK", "AE", "SA",
     "OM", "QA", "EG", "MA", "TN", "DZ", "ZA", "TR", "FR", "BE", "NL", "DE", "ES", "IT", "GB", "PT", "IE",
     "PL", "DK", "SE", "NO", "FI", "GR", "HR", "SI", "RO", "BG", "MT", "CY", "LT", "LV", "EE", "US", "CA",
     "MX", "BR", "AR", "CL"}
)  # fmt: skip
_CODE = re.compile(r"^[A-Z]{2}[A-Z2-9]{3}$")


def _key(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().casefold()
    return re.sub(r"[^a-z]", "", plain)


def unlocode(raw: str | None) -> str | None:
    """The UN/LOCODE a cell means — "Ningbo" → CNNGB, "CN NGB" → CNNGB — or None."""
    if not raw or not raw.strip():
        return None
    by_name = _BY_NAME.get(_key(raw))
    if by_name:
        return by_name
    compact = re.sub(r"[\s\-]", "", raw).upper()
    if _CODE.fullmatch(compact) and compact[:2] in _COUNTRIES:
        return compact
    return None
