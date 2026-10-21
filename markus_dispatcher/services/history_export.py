"""Eksport historii zleceń do samodzielnego skoroszytu Excel."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
import sqlite3
from tempfile import NamedTemporaryFile
from xml.etree.ElementTree import Element, SubElement, tostring
from zipfile import ZIP_DEFLATED, ZipFile

from markus_dispatcher import config

EXPORT_FILENAME = "MarkusDispatcher-historia-zlecen.xlsx"
@dataclass(frozen=True)
class ExportResult:
    path: Path
    job_count: int
    verification_count: int

def export_path() -> Path:
    return config.DATA_DIR / "eksporty" / EXPORT_FILENAME

def _read_rows(db_path: Path) -> list[dict[str, object]]:
    if not db_path.exists():
        raise FileNotFoundError(f"Nie znaleziono bazy Markus Dispatcher: {db_path}")
    conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("""SELECT j.id, j.ref_numbers, j.status, j.client_name, j.contact_phone, j.contact_email, j.load_address, j.load_datetime, j.unload_address, j.unload_datetime, j.pallets, j.notes, j.created_at, j.updated_at, COALESCE(c.phone, '') client_phone, COALESCE(c.email, '') client_email FROM jobs j LEFT JOIN clients c ON c.id = j.client_id ORDER BY j.load_datetime ASC, j.id ASC""").fetchall()
    finally:
        conn.close()
    output: list[dict[str, object]] = []
    for raw in rows:
        row = dict(raw)
        row["phone"] = str(row["contact_phone"] or row["client_phone"] or "")
        row["email"] = str(row["contact_email"] or row["client_email"] or "")
        output.append(row)
    return output

def _column_name(number: int) -> str:
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result

def _cell(parent: Element, value: object, style: int | None = None) -> None:
    attrs = {"s": str(style)} if style is not None else {}
    cell = SubElement(parent, "c", attrs)
    if value not in (None, ""):
        cell.set("t", "inlineStr")
        inline = SubElement(cell, "is")
        SubElement(inline, "t").text = str(value)

def _sheet_xml(rows: list[list[object]]) -> bytes:
    ws = Element("worksheet", {"xmlns": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"})
    data = SubElement(ws, "sheetData")
    for number, row in enumerate(rows, 1):
        xml_row = SubElement(data, "row", {"r": str(number)})
        for value in row:
            _cell(xml_row, value, 1 if number == 1 else None)
    if rows and rows[0]:
        SubElement(ws, "autoFilter", {"ref": f"A1:{_column_name(len(rows[0]))}{len(rows)}"})
    return tostring(ws, encoding="utf-8", xml_declaration=True)

def _write_workbook(path: Path, sheets: list[tuple[str, list[list[object]]]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, suffix=".xlsx", delete=False) as temp:
        temporary = Path(temp.name)
    try:
        with ZipFile(temporary, "w", ZIP_DEFLATED) as archive:
            overrides = "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1, len(sheets) + 1))
            archive.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>' + overrides + '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>')
            archive.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
            sheets_xml = "".join(f'<sheet name="{name}" sheetId="{i}" r:id="rId{i}"/>' for i, (name, _) in enumerate(sheets, 1))
            archive.writestr("xl/workbook.xml", '<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>' + sheets_xml + "</sheets></workbook>")
            relations = "".join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1, len(sheets) + 1))
            archive.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + relations + f'<Relationship Id="rId{len(sheets)+1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
            archive.writestr("xl/styles.xml", '<?xml version="1.0" encoding="UTF-8"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font></fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF1F4E78"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf/></cellStyleXfs><cellXfs count="2"><xf fontId="0" fillId="0" borderId="0" xfId="0"/><xf fontId="1" fillId="1" borderId="0" xfId="0" applyFont="1" applyFill="1"/></cellXfs></styleSheet>')
            for i, (_, rows) in enumerate(sheets, 1):
                archive.writestr(f"xl/worksheets/sheet{i}.xml", _sheet_xml(rows))
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)

def export_history(*, db_path: Path | None = None, destination: Path | None = None) -> ExportResult:
    """Tworzy raport z odczytu SQLite w trybie read-only; nigdy nie modyfikuje bazy."""
    rows = _read_rows(db_path or config.DB_PATH)
    jobs = [["ID", "Numery zleceń", "Status", "Klient", "Telefon", "E-mail", "Załadunek", "Data załadunku", "Rozładunek", "Data rozładunku", "Palety", "Notatki", "Utworzono", "Zaktualizowano"]]
    routes: Counter[tuple[str, str]] = Counter()
    companies: defaultdict[tuple[str, str, str], list[dict[str, object]]] = defaultdict(list)
    verification = [["ID", "Numery zleceń", "Brakujące dane", "Klient", "Telefon", "Załadunek", "Rozładunek"]]
    for row in rows:
        load_address = str(row["load_address"] or "")
        unload_address = str(row["unload_address"] or "")
        jobs.append([row["id"], row["ref_numbers"], row["status"], row["client_name"], row["phone"], row["email"], load_address, row["load_datetime"], unload_address, row["unload_datetime"], row["pallets"], row["notes"], row["created_at"], row["updated_at"]])
        routes[(load_address, unload_address)] += 1
        companies[(str(row["client_name"] or ""), str(row["phone"] or ""), str(row["email"] or ""))].append(row)
        check = {"numer zlecenia": row["ref_numbers"], "klient": row["client_name"], "telefon": row["phone"], "załadunek": load_address, "data załadunku": row["load_datetime"], "rozładunek": unload_address, "data rozładunku": row["unload_datetime"]}
        missing = [label for label, value in check.items() if not str(value or "").strip()]
        if missing:
            verification.append([row["id"], row["ref_numbers"], ", ".join(missing), row["client_name"], row["phone"], row["load_address"], row["unload_address"]])
    route_rows = [["Załadunek", "Rozładunek", "Liczba zleceń"]] + [list(key) + [count] for key, count in sorted(routes.items(), key=lambda item: (-item[1], item[0]))]
    company_rows = [["Firma", "Telefon", "E-mail", "Liczba zleceń", "Pierwszy załadunek", "Ostatni załadunek"]]
    for (name, phone, email), records in sorted(companies.items(), key=lambda item: (-len(item[1]), item[0][0].casefold())):
        dates = sorted(str(record["load_datetime"] or "") for record in records if record["load_datetime"])
        company_rows.append([name, phone, email, len(records), dates[0] if dates else "", dates[-1] if dates else ""])
    target = destination or export_path()
    _write_workbook(target, [("Zlecenia", jobs), ("Statystyka tras", route_rows), ("Firmy", company_rows), ("Do weryfikacji", verification)])
    return ExportResult(target, len(rows), len(verification) - 1)
