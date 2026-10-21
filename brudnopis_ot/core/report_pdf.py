from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from brudnopis_ot.core.daily_summary import build_daily_totals, format_minutes
from brudnopis_ot.core.display_tz import DEFAULT_ZONE, DisplayZone, zone_label
from brudnopis_ot.core.models import (
    ACTIVITY_LABELS_PL,
    ActivityKind,
    Block,
    Scenario,
    ValidationResult,
)
from brudnopis_ot.core.paths import reports_dir, safe_filename_part
from brudnopis_ot.core.validator import validate_scenario

_FONT_REGISTERED = False
_FONT_NAME = "Helvetica"
_FONT_BOLD = "Helvetica-Bold"


def _ensure_fonts() -> None:
    global _FONT_REGISTERED, _FONT_NAME, _FONT_BOLD
    if _FONT_REGISTERED:
        return
    _FONT_REGISTERED = True
    candidates = [
        (Path(r"C:\Windows\Fonts\arial.ttf"), Path(r"C:\Windows\Fonts\arialbd.ttf")),
        (Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
         Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")),
    ]
    for regular, bold in candidates:
        if regular.is_file() and bold.is_file():
            pdfmetrics.registerFont(TTFont("BrudnopisSans", str(regular)))
            pdfmetrics.registerFont(TTFont("BrudnopisSans-Bold", str(bold)))
            _FONT_NAME = "BrudnopisSans"
            _FONT_BOLD = "BrudnopisSans-Bold"
            return


def _fmt_dt(value: datetime) -> str:
    return value.strftime("%d.%m.%Y %H:%M")


def _fmt_date(value: date) -> str:
    return value.strftime("%d.%m.%Y")


def _fmt_hours(hours: float) -> str:
    total_min = int(round(hours * 60))
    return format_minutes(total_min)


def _range_bounds(range_start: date, range_end: date) -> tuple[datetime, datetime]:
    start = datetime.combine(range_start, datetime.min.time())
    end = datetime.combine(range_end + timedelta(days=1), datetime.min.time())
    return start, end


def _overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return a_start < b_end and a_end > b_start


def _clip_block(block: Block, range_start: datetime, range_end: datetime) -> tuple[datetime, datetime] | None:
    start = max(block.start, range_start)
    end = min(block.end, range_end)
    if end <= start:
        return None
    return start, end


def _severity_pl(severity: str) -> str:
    return {
        "violation": "Naruszenie",
        "warning": "Ostrzeżenie",
        "info": "Info",
        "ok": "OK",
    }.get(severity, severity)


def _table(data: list[list[str]], *, col_widths: list[float] | None = None) -> Table:
    style = TableStyle(
        [
            ("FONTNAME", (0, 0), (-1, 0), _FONT_BOLD),
            ("FONTNAME", (0, 1), (-1, -1), _FONT_NAME),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E5E7EB")),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#9CA3AF")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]
    )
    table = Table(data, colWidths=col_widths, repeatRows=1)
    table.setStyle(style)
    return table


def default_report_path(
    *,
    profile_label: str,
    range_start: date,
    range_end: date,
    when: datetime | None = None,
) -> Path:
    when = when or datetime.now()
    name = (
        f"raport_{safe_filename_part(profile_label)}_"
        f"{range_start.strftime('%Y%m%d')}-{range_end.strftime('%Y%m%d')}_"
        f"{when.strftime('%H%M%S')}.pdf"
    )
    return reports_dir() / name


def write_profile_report(
    scenario: Scenario,
    *,
    range_start: date,
    range_end: date,
    output_path: str | Path | None = None,
    profile_label: str | None = None,
    zone: DisplayZone = DEFAULT_ZONE,
    validation: ValidationResult | None = None,
) -> Path:
    """Zapisuje tabelaryczny raport PDF aktywnego profilu za wybrany okres."""
    if range_end < range_start:
        raise ValueError("Data końcowa nie może być wcześniejsza niż początkowa.")

    _ensure_fonts()
    label = (profile_label or scenario.title or "profil").strip() or "profil"
    out = Path(output_path) if output_path else default_report_path(
        profile_label=label,
        range_start=range_start,
        range_end=range_end,
    )
    out.parent.mkdir(parents=True, exist_ok=True)

    result = validation or validate_scenario(scenario, zone=zone)
    win_start, win_end = _range_bounds(range_start, range_end)

    clipped: list[tuple[Block, datetime, datetime]] = []
    for block in sorted(scenario.blocks, key=lambda b: b.start):
        clipped_span = _clip_block(block, win_start, win_end)
        if clipped_span is not None:
            clipped.append((block, clipped_span[0], clipped_span[1]))

    rests = [
        wr
        for wr in result.weekly_rests
        if wr.is_weekly and _overlaps(wr.start, wr.end, win_start, win_end)
    ]
    days = build_daily_totals(
        scenario.blocks,
        range_start=range_start,
        range_end=range_end,
    )
    # Markery do czytelniejszego PDF
    drive_10h_days = {d.day for d in days if (d.drive_min / 60.0) >= 10.0 - 1e-6}
    daily_9h_days = {
        wr.start.date()
        for wr in result.weekly_rests
        if (
            wr.classification == "too_short"
            and wr.duration_hours >= 9.0 - 1e-6
            and wr.duration_hours < 11.0 - 1e-6
            and range_start <= wr.start.date() <= range_end
        )
    }
    weekly_class_by_block: dict[str, str] = {}
    daily_9h_block_ids: set[str] = set()
    for wr in result.weekly_rests:
        if not _overlaps(wr.start, wr.end, win_start, win_end):
            continue
        if wr.is_weekly:
            for bid in wr.block_ids:
                weekly_class_by_block[bid] = wr.classification
        elif (
            wr.classification == "too_short"
            and wr.duration_hours >= 9.0 - 1e-6
            and wr.duration_hours < 11.0 - 1e-6
        ):
            daily_9h_block_ids.update(wr.block_ids)

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "ReportTitle",
        parent=styles["Heading1"],
        fontName=_FONT_BOLD,
        fontSize=14,
        spaceAfter=6,
    )
    heading_style = ParagraphStyle(
        "ReportHeading",
        parent=styles["Heading2"],
        fontName=_FONT_BOLD,
        fontSize=11,
        spaceBefore=10,
        spaceAfter=4,
    )
    body_style = ParagraphStyle(
        "ReportBody",
        parent=styles["Normal"],
        fontName=_FONT_NAME,
        fontSize=9,
        leading=12,
    )

    story: list = []
    story.append(Paragraph("Raport Brudnopis OT", title_style))
    story.append(
        Paragraph(
            f"<b>Profil:</b> {label}<br/>"
            f"<b>Scenariusz:</b> {scenario.title or '—'}<br/>"
            f"<b>Strefa:</b> {zone_label(zone)}<br/>"
            f"<b>Okres raportu:</b> {_fmt_date(range_start)} – {_fmt_date(range_end)}<br/>"
            f"<b>Data generowania:</b> {_fmt_dt(datetime.now())}",
            body_style,
        )
    )
    story.append(Spacer(1, 4 * mm))

    if not clipped:
        story.append(
            Paragraph(
                "<b>Brak aktywności w wybranym okresie.</b> "
                "Poniżej pozostają wyniki walidacji całego scenariusza.",
                body_style,
            )
        )

    # Zgodność OT
    story.append(Paragraph("Zgodność OT", heading_style))
    findings_rows = [["Poziom", "Kod", "Komunikat"]]
    if result.findings:
        for finding in result.findings:
            findings_rows.append(
                [
                    _severity_pl(finding.severity),
                    finding.code,
                    finding.message_pl,
                ]
            )
    else:
        findings_rows.append(["—", "—", "Brak naruszeń i ostrzeżeń."])
    story.append(_table(findings_rows, col_widths=[28 * mm, 28 * mm, 120 * mm]))

    # Podpowiedzi
    story.append(Paragraph("Podpowiedzi (rekompensata / 6×24 h)", heading_style))
    hints = list(result.compensation_hints)
    if result.six_day_windows:
        for window in result.six_day_windows:
            hints.append(
                f"Okno Max. 6×24 h: {_fmt_dt(window.start)} → {_fmt_dt(window.end)}"
            )
    if hints:
        for hint in hints:
            # Rekompensaty i ich terminy wyróżniamy kolorem.
            if any(
                key in hint
                for key in (
                    "Oddano rekompensatę",
                    "Do oddania rekompensata",
                    "Brak rekompensaty",
                    "Rekompensata PM",
                )
            ):
                story.append(
                    Paragraph(
                        f"• <font color=\"#16A34A\"><b>{hint}</b></font>",
                        body_style,
                    )
                )
            else:
                story.append(Paragraph(f"• {hint}", body_style))
    else:
        story.append(Paragraph("Brak podpowiedzi.", body_style))

    # Odpoczynki tygodniowe
    story.append(Paragraph("Odpoczynki tygodniowe (w okresie)", heading_style))
    ot_rows = [["Typ OT", "Etykieta", "Od", "Do", "Czas", "Dług", "Rek."]]
    if rests:
        for wr in rests:
            typ = "regularny" if wr.classification == "regular" else "skrócony"
            debt_str = _fmt_hours(wr.debt_hours)
            rek_str = _fmt_hours(wr.compensation_hours)
            if wr.debt_hours > 1e-6:
                debt_str = f"★ {debt_str}"
            if wr.compensation_hours > 1e-6:
                rek_str = f"★ {rek_str}"
            ot_rows.append(
                [
                    typ,
                    wr.label,
                    _fmt_dt(wr.start),
                    _fmt_dt(wr.end),
                    _fmt_hours(wr.duration_hours),
                    debt_str,
                    rek_str,
                ]
            )
    else:
        ot_rows.append(["—", "—", "—", "—", "—", "—", "Brak OT w okresie"])
    story.append(
        _table(
            ot_rows,
            col_widths=[24 * mm, 38 * mm, 30 * mm, 30 * mm, 18 * mm, 18 * mm, 18 * mm],
        )
    )

    # Podsumowanie dni
    story.append(Paragraph("Podsumowanie dni", heading_style))
    day_rows = [["Dzień", "Jazda", "Praca", "Dysp.", "Odpoczynek", "Razem", "Ozn."]]
    if days:
        for day in days:
            markers = []
            if day.day in drive_10h_days:
                markers.append("10h jazda")
            if day.day in daily_9h_days:
                markers.append("9h OD")
            marker_str = ", ".join(markers) if markers else "—"
            day_rows.append(
                [
                    _fmt_date(day.day),
                    format_minutes(day.drive_min),
                    format_minutes(day.work_min),
                    format_minutes(day.availability_min),
                    format_minutes(day.rest_min),
                    format_minutes(day.total_min),
                    marker_str,
                ]
            )
    else:
        day_rows.append(["—", "—", "—", "—", "—", "—", "Brak danych"])
    story.append(
        _table(
            day_rows,
            col_widths=[22 * mm, 22 * mm, 22 * mm, 22 * mm, 28 * mm, 20 * mm, 36 * mm],
        )
    )

    # Bloki aktywności
    story.append(Paragraph("Bloki aktywności (przycięte do okresu)", heading_style))
    block_rows = [["Rodzaj", "Od", "Do", "Czas", "Ozn."]]
    if clipped:
        for block, start, end in clipped:
            hours = (end - start).total_seconds() / 3600.0
            marker = "—"
            if block.kind == ActivityKind.DRIVE and start.date() in drive_10h_days:
                marker = "10h dzień"
            elif block.kind == ActivityKind.REST:
                if block.id in weekly_class_by_block:
                    marker = (
                        "OT regularny"
                        if weekly_class_by_block[block.id] == "regular"
                        else "OT skrócony"
                    )
                elif block.id in daily_9h_block_ids:
                    marker = "OD 9h"
            block_rows.append(
                [
                    ACTIVITY_LABELS_PL.get(block.kind, block.kind.value),
                    _fmt_dt(start),
                    _fmt_dt(end),
                    _fmt_hours(hours),
                    marker,
                ]
            )
    else:
        block_rows.append(["—", "—", "—", "—", "Brak aktywności w okresie"])
    story.append(
        _table(block_rows, col_widths=[45 * mm, 36 * mm, 36 * mm, 18 * mm, 45 * mm])
    )

    doc = SimpleDocTemplate(
        str(out),
        pagesize=A4,
        leftMargin=14 * mm,
        rightMargin=14 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title=f"Raport OT — {label}",
        author="Brudnopis OT",
    )
    doc.build(story)
    return out
