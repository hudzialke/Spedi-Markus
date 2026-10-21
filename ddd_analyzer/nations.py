from __future__ import annotations

# Annex 1B nation codes → ISO-3166-1 alpha-2 (jak w stawkach MiLo).
ANNEX_TO_ISO2: dict[str, str] = {
    "A": "AT",
    "B": "BE",
    "BG": "BG",
    "BIH": "BA",
    "BY": "BY",
    "CH": "CH",
    "CY": "CY",
    "CZ": "CZ",
    "D": "DE",
    "DK": "DK",
    "E": "ES",
    "EST": "EE",
    "F": "FR",
    "FIN": "FI",
    "FL": "LI",
    "GB": "GB",
    "GR": "GR",
    "H": "HU",
    "HR": "HR",
    "I": "IT",
    "IRL": "IE",
    "IS": "IS",
    "L": "LU",
    "LT": "LT",
    "LV": "LV",
    "M": "MT",
    "MC": "MC",
    "MD": "MD",
    "MK": "MK",
    "N": "NO",
    "NL": "NL",
    "P": "PT",
    "PL": "PL",
    "RO": "RO",
    "RUS": "RU",
    "S": "SE",
    "SK": "SK",
    "SLO": "SI",
    "SM": "SM",
    "SRB": "RS",
    "TR": "TR",
    "UA": "UA",
    "V": "VA",
}


def to_iso2_country(code: str | None) -> str:
    """Znormalizuj kod narodu z tacho do ISO-2 (DE, IT, …)."""
    raw = (code or "").strip().upper()
    if not raw or raw in {"N/A", "—", "-", "NONE", "UNK", "UNKNOWN"}:
        return ""
    if raw in ANNEX_TO_ISO2:
        return ANNEX_TO_ISO2[raw]
    # Już ISO-2 / dłuższy kod znany
    if len(raw) == 2 and raw.isalpha():
        return raw
    return ANNEX_TO_ISO2.get(raw, raw)
