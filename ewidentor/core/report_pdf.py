from __future__ import annotations

from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from ewidentor.core.comp_ledger import CompEntry, format_comp_detail_line
from ewidentor.core.display_tz import DEFAULT_ZONE, DisplayZone, zone_label
from ewidentor.core.milo import MiloResult, MiloStay
from ewidentor.core.milo_rates import OP_LABELS_PL
from ewidentor.core.models import Attachment, MonthCard, PayrollBreakdown
from ewidentor.core.month_card import (
    format_clock_local,
    format_minutes,
    sum_field,
    week_slices,
)
from ewidentor.core.paths import driver_reports_dir, safe_filename_part
from ewidentor.core.payroll import compute_payroll, compute_payroll_detailed
from ewidentor.core.virtual_diet import VirtualDietResult

_FONT_REGISTERED = False
_FONT = "Helvetica"
_FONT_BOLD = "Helvetica-Bold"

_HEADER_BG = colors.HexColor("#D1D5DB")
_ALT_BG = colors.HexColor("#F3F4F6")
_GRID = colors.HexColor("#9CA3AF")
_TITLE_BG = colors.HexColor("#1F2937")

_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
_PDF_SUFFIXES = {".pdf"}


def _ensure_fonts() -> None:
    global _FONT_REGISTERED, _FONT, _FONT_BOLD
    if _FONT_REGISTERED:
        return
    _FONT_REGISTERED = True
    candidates = [
        (Path(r"C:\Windows\Fonts\arial.ttf"), Path(r"C:\Windows\Fonts\arialbd.ttf")),
        (
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ),
    ]
    for regular, bold in candidates:
        if regular.is_file() and bold.is_file():
            pdfmetrics.registerFont(TTFont("EwiSans", str(regular)))
            pdfmetrics.registerFont(TTFont("EwiSans-Bold", str(bold)))
            _FONT = "EwiSans"
            _FONT_BOLD = "EwiSans-Bold"
            return


def default_report_path(card: MonthCard) -> Path:
    """raporty/<kierowca>/Ewidencja_YYYY-MM.pdf"""
    return (
        driver_reports_dir(card.driver.full_name)
        / f"Ewidencja_{card.year:04d}-{card.month:02d}.pdf"
    )


def _money(value: float) -> str:
    return f"{value:,.2f} zł".replace(",", " ").replace(".", ",")


def _cell(text: str, size: int = 6) -> Paragraph:
    style = ParagraphStyle(
        "cell",
        fontName=_FONT,
        fontSize=size,
        leading=size + 1,
        alignment=1,
    )
    return Paragraph(text.replace("\n", "<br/>"), style)


def _label(text: str, size: int = 6) -> Paragraph:
    style = ParagraphStyle(
        "lab",
        fontName=_FONT,
        fontSize=size,
        leading=size + 1,
        alignment=0,
    )
    return Paragraph(text, style)


def _build_component_table(
    card: MonthCard,
    rows_spec: list[tuple[str, object]],
) -> Table:
    """Siatka: wiersz składnika × kolumny dni 1..N + Razem (styl miesięcznej karty)."""
    days = card.days
    n = len(days)
    header = [_label("<b>Składnik</b>", 7)]
    for d in days:
        header.append(_cell(f"<b>{d.day.day}</b>", 6))
    header.append(_cell("<b>Razem</b>", 6))

    data: list[list] = [header]
    for label, getter in rows_spec:
        row = [_label(label, 6)]
        total = 0
        for d in days:
            val = getter(d)
            if isinstance(val, int):
                total += val
                row.append(_cell(format_minutes(val) if val else "", 5))
            else:
                row.append(_cell(str(val) if val else "", 5))
        # razem tylko dla minut
        if all(isinstance(getter(d), int) for d in days):
            row.append(_cell(format_minutes(total) if total else "", 5))
        else:
            row.append(_cell("", 5))
        data.append(row)

    page_w = landscape(A4)[0] - 16 * mm
    label_w = 42 * mm
    rest = page_w - label_w
    day_w = rest / (n + 1)
    col_widths = [label_w] + [day_w] * (n + 1)

    table = Table(data, colWidths=col_widths, repeatRows=1)
    style_cmds = [
        ("FONTNAME", (0, 0), (-1, -1), _FONT),
        ("BACKGROUND", (0, 0), (-1, 0), _HEADER_BG),
        ("GRID", (0, 0), (-1, -1), 0.25, _GRID),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 1),
        ("RIGHTPADDING", (0, 0), (-1, -1), 1),
        ("TOPPADDING", (0, 0), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
        ("BACKGROUND", (-1, 0), (-1, -1), colors.HexColor("#E5E7EB")),
    ]
    for i in range(1, len(data)):
        if i % 2 == 0:
            style_cmds.append(("BACKGROUND", (0, i), (-2, i), _ALT_BG))
    table.setStyle(TableStyle(style_cmds))
    return table


def _header_block(card: MonthCard, zone: DisplayZone = DEFAULT_ZONE) -> list:
    _ensure_fonts()
    title_style = ParagraphStyle(
        "title",
        fontName=_FONT_BOLD,
        fontSize=12,
        textColor=colors.white,
        alignment=1,
        spaceAfter=2,
    )
    meta_style = ParagraphStyle(
        "meta",
        fontName=_FONT,
        fontSize=8,
        leading=11,
    )
    employer = card.employer_name or card.driver.employer_name or "—"
    etat = "pełny" if card.driver.employment_fraction >= 0.99 else f"{card.driver.employment_fraction:.0%}"
    lines = [
        Table(
            [[Paragraph("Miesięczna karta pracy pracownika – Ewidencja czasu pracy", title_style)]],
            colWidths=[landscape(A4)[0] - 16 * mm],
        ),
    ]
    lines[0].setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), _TITLE_BG),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    meta = (
        f"<b>Pracownik:</b> {card.driver.full_name} &nbsp;&nbsp; "
        f"<b>Okres:</b> {card.period_label} &nbsp;&nbsp; "
        f"<b>System:</b> {card.driver.work_system} &nbsp;&nbsp; "
        f"<b>Etat:</b> {etat}<br/>"
        f"<b>Pracodawca:</b> {employer} &nbsp;&nbsp; "
        f"<b>Karta:</b> {card.driver.card_number or '—'} &nbsp;&nbsp; "
        f"<b>Strefa:</b> {zone_label(zone)} &nbsp;&nbsp; "
        f"<b>Wygenerowano:</b> {datetime.now().strftime('%d.%m.%Y %H:%M')}"
    )
    return [*lines, Spacer(1, 3 * mm), Paragraph(meta, meta_style), Spacer(1, 3 * mm)]


def _footer_signatures() -> Table:
    style = ParagraphStyle("sig", fontName=_FONT, fontSize=7, leading=9)
    data = [[
        Paragraph("Sporządził: ________________", style),
        Paragraph("Sprawdził: ________________", style),
        Paragraph("Zatwierdził: ________________", style),
        Paragraph("Podpis pracownika: ________________", style),
    ]]
    t = Table(data, colWidths=[70 * mm, 70 * mm, 70 * mm, 70 * mm])
    t.setStyle(TableStyle([("TOPPADDING", (0, 0), (-1, -1), 8)]))
    return t


def export_month_pdf(
    card: MonthCard,
    path: Path | None = None,
    *,
    attachments: list[Attachment] | None = None,
    payroll: PayrollBreakdown | None = None,
    comp_entries: list[CompEntry] | None = None,
    sunday_unreturned_min: int | None = None,
    milo_stays: list[MiloStay] | None = None,
    milo_result: MiloResult | None = None,
    milo_topup_pln: float = 0.0,
    virtual_diet: VirtualDietResult | None = None,
    zone: DisplayZone = DEFAULT_ZONE,
) -> Path:
    """PDF w stylu miesięcznej karty (składniki × dni + przeliczenie + tygodnie)."""
    _ensure_fonts()
    out = path or default_report_path(card)
    out.parent.mkdir(parents=True, exist_ok=True)
    payroll = payroll or compute_payroll(
        card,
        sunday_unreturned_min=sunday_unreturned_min,
        milo_topup_pln=milo_topup_pln,
    )
    detailed = compute_payroll_detailed(
        card,
        sunday_unreturned_min=sunday_unreturned_min,
        milo_topup_pln=milo_topup_pln,
    )
    attachments = attachments or []
    comp_entries = comp_entries or []
    milo_stays = milo_stays or []

    doc = SimpleDocTemplate(
        str(out),
        pagesize=landscape(A4),
        leftMargin=8 * mm,
        rightMargin=8 * mm,
        topMargin=8 * mm,
        bottomMargin=8 * mm,
        title=f"Ewidencja {card.driver.full_name} {card.period_label}",
    )
    story: list = []
    story.extend(_header_block(card, zone=zone))

    # --- Strona 1: składniki rzeczywiste ---
    h = ParagraphStyle("h", fontName=_FONT_BOLD, fontSize=9, spaceBefore=2, spaceAfter=4)
    story.append(Paragraph("1. Składniki rzeczywiste (czas pracy / aktywności)", h))
    story.append(
        _build_component_table(
            card,
            [
                ("Wymiar / plan", lambda d: d.plan_min),
                (
                    "Godz. rozpoczęcia",
                    lambda d: format_clock_local(d.day, d.start_min, zone),
                ),
                (
                    "Godz. zakończenia",
                    lambda d: format_clock_local(d.day, d.end_min, zone),
                ),
                ("Czas pracy", lambda d: d.work_min),
                ("Jazda", lambda d: d.drive_min),
                ("Inna praca", lambda d: d.other_work_min),
                ("Dyżur 50%", lambda d: d.duty_50_min),
                ("Dyżur 100%", lambda d: d.duty_100_min),
                ("Dyspozycja", lambda d: d.availability_min),
                ("Odpoczynek dobowy", lambda d: d.daily_rest_min),
                ("Nieobecność", lambda d: d.absence_label),
            ],
        )
    )
    story.append(Spacer(1, 4 * mm))
    story.append(
        Paragraph(
            f"Suma: jazda {format_minutes(sum_field(card.days, 'drive_min'))}, "
            f"czas pracy {format_minutes(sum_field(card.days, 'work_min'))}, "
            f"wymiar {format_minutes(card.norma_min)}, "
            f"nadgodziny (dzień) {format_minutes(sum_field(card.days, 'overtime_min'))}.",
            ParagraphStyle("sum", fontName=_FONT, fontSize=8),
        )
    )

    # --- Strona 2: składniki do wynagrodzenia ---
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph("2. Składniki do wynagrodzenia", h))
    night_label = (
        f"Noc {card.driver.pay.night_start.strftime('%H:%M')}–"
        f"{card.driver.pay.night_end.strftime('%H:%M')}"
    )
    story.append(
        _build_component_table(
            card,
            [
                ("Czas płatny", lambda d: d.paid_min),
                (night_label, lambda d: d.night_min),
                ("Niedziele / święta", lambda d: d.sunday_holiday_min),
                ("Nadgodziny", lambda d: d.overtime_min),
                ("Dyżur 50%", lambda d: d.duty_50_min),
                ("Dyżur 100%", lambda d: d.duty_100_min),
            ],
        )
    )

    # --- Podsumowanie tygodni ---
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph("3. Podsumowanie tygodni (Pn–Nd)", h))
    week_header = ["Tydzień", "Od", "Do", "Czas pracy", "Jazda", "Noc", "Dyżur 50%", "Nadgodziny"]
    week_data = [week_header]
    by_day = {d.day: d for d in card.days}
    for w_start, w_end in week_slices(card.year, card.month):
        slice_days = [
            by_day[d]
            for d in (w_start + timedelta(days=i) for i in range(7))
            if d in by_day
        ]
        if not slice_days:
            continue
        week_data.append(
            [
                f"{w_start.isocalendar()[1]}",
                w_start.strftime("%d.%m"),
                w_end.strftime("%d.%m"),
                format_minutes(sum_field(slice_days, "work_min")),
                format_minutes(sum_field(slice_days, "drive_min")),
                format_minutes(sum_field(slice_days, "night_min")),
                format_minutes(sum_field(slice_days, "duty_50_min")),
                format_minutes(sum_field(slice_days, "overtime_min")),
            ]
        )
    wt = Table(week_data, colWidths=[25 * mm, 25 * mm, 25 * mm, 30 * mm, 30 * mm, 30 * mm, 30 * mm, 30 * mm])
    wt.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, 0), _FONT_BOLD),
                ("FONTNAME", (0, 1), (-1, -1), _FONT),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("BACKGROUND", (0, 0), (-1, 0), _HEADER_BG),
                ("GRID", (0, 0), (-1, -1), 0.4, _GRID),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ]
        )
    )
    story.append(wt)

    # --- Przeliczenie ---
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph("4. Przeliczenie miesięcznej karty", h))
    story.append(
        Paragraph(
            f"Normatywny czas pracy: {detailed.norma_formula}<br/>"
            f"Płaca miesięczna: {_money(detailed.monthly_salary)} &nbsp; "
            f"Stawka godzinowa: {_money(detailed.hourly_rate)} &nbsp; "
            f"Dodatek nocny: {_money(detailed.night_rate)}/h",
            ParagraphStyle("meta2", fontName=_FONT, fontSize=8, leading=11),
        )
    )
    story.append(Spacer(1, 2 * mm))

    pay_rows = [["Składnik", "Godziny", "Stawka", "Kwota"]]
    section_labels = {
        "A": "A. Wynagrodzenie zasadnicze",
        "B": "B. Dodatkowe składniki wynagrodzenia",
        "C": "C. Dodatkowe składniki z okresu rozliczeniowego",
        "D": "D. Wyrównanie MiLo (top-up)",
    }
    current_sec = None
    for line in detailed.lines:
        if line.section != current_sec:
            current_sec = line.section
            pay_rows.append(
                [section_labels.get(current_sec, current_sec), "", "", ""]
            )
        hrs = format_minutes(line.minutes) if line.minutes else ""
        rate = _money(line.rate) if line.rate else ""
        pay_rows.append([line.label, hrs, rate, _money(line.amount)])
    pay_rows.append(["Razem A", "", "", _money(detailed.section_a)])
    pay_rows.append(["Razem B", "", "", _money(detailed.section_b)])
    pay_rows.append(["Razem C", "", "", _money(detailed.section_c)])
    if detailed.milo_topup_pln:
        pay_rows.append(["Razem D (MiLo)", "", "", _money(detailed.milo_topup_pln)])
    pay_rows.append(["RAZEM BRUTTO", "", "", _money(detailed.gross_total)])
    pay_rows.append(
        ["Szacunek NETTO (uproszczone PL)", "", "", _money(detailed.net_estimate)]
    )

    pt = Table(pay_rows, colWidths=[110 * mm, 25 * mm, 30 * mm, 30 * mm])
    pt.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, 0), _FONT_BOLD),
                ("FONTNAME", (0, 1), (-1, -1), _FONT),
                ("FONTNAME", (0, -2), (-1, -1), _FONT_BOLD),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("BACKGROUND", (0, 0), (-1, 0), _HEADER_BG),
                ("BACKGROUND", (0, -2), (-1, -2), colors.HexColor("#FEF3C7")),
                ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#DCFCE7")),
                ("GRID", (0, 0), (-1, -1), 0.4, _GRID),
                ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    story.append(pt)
    story.append(Spacer(1, 3 * mm))
    story.append(
        Paragraph(
            "Uwaga: netto jest szacunkiem (ZUS + zdrowotna + PIT uproszczone). "
            "Urlop bezpłatny obcina zasadnicze i wymiar. "
            "Nadgodziny: stawka normalna + dodatek vs plan dnia, plus ponad wymiar. "
            "MiLo: kabotaż/cross-trade albo cały czas w kraju (ustawienie w zakładce MiLo).",
            ParagraphStyle("note", fontName=_FONT, fontSize=7, textColor=colors.HexColor("#4B5563")),
        )
    )

    # --- MiLo kraje ---
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph("5. MiLo — czas per kraj i top-up", h))
    if milo_stays:
        milo_data = [["Dzień", "Kraj", "Typ", "Czas"]]
        for s in milo_stays:
            milo_data.append(
                [
                    s.day.strftime("%d.%m.%Y"),
                    s.country_code,
                    OP_LABELS_PL.get(s.op_type, s.op_type.value),
                    format_minutes(s.minutes),
                ]
            )
        mt = Table(milo_data, colWidths=[30 * mm, 25 * mm, 40 * mm, 25 * mm])
        mt.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, 0), _FONT_BOLD),
                    ("FONTNAME", (0, 1), (-1, -1), _FONT),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("BACKGROUND", (0, 0), (-1, 0), _HEADER_BG),
                    ("GRID", (0, 0), (-1, -1), 0.4, _GRID),
                ]
            )
        )
        story.append(mt)
        story.append(Spacer(1, 2 * mm))
    else:
        story.append(
            Paragraph(
                "Brak wpisów krajów (auto z Brudnopis: GNSS / border_crossings / places lub ręcznie).",
                ParagraphStyle("milo0", fontName=_FONT, fontSize=8),
            )
        )
    if milo_result and milo_result.lines:
        top_data = [["Kraj", "Typ", "Godziny", "Min./h", "PL w walucie", "Top-up PLN"]]
        for line in milo_result.lines:
            top_data.append(
                [
                    f"{line.country_code} {line.country_name}",
                    OP_LABELS_PL.get(line.op_type, line.op_type.value),
                    format_minutes(line.minutes),
                    f"{line.min_hourly:g} {line.currency}",
                    f"{line.pl_hourly_in_currency:g}",
                    _money(line.topup_pln),
                ]
            )
        top_data.append(
            ["Razem top-up", "", "", "", "", _money(milo_result.topup_total_pln)]
        )
        tt = Table(
            top_data, colWidths=[45 * mm, 30 * mm, 25 * mm, 30 * mm, 30 * mm, 30 * mm]
        )
        tt.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, 0), _FONT_BOLD),
                    ("FONTNAME", (0, 1), (-1, -1), _FONT),
                    ("FONTNAME", (0, -1), (-1, -1), _FONT_BOLD),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("BACKGROUND", (0, 0), (-1, 0), _HEADER_BG),
                    ("GRID", (0, 0), (-1, -1), 0.4, _GRID),
                    ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
                ]
            )
        )
        story.append(Spacer(1, 2 * mm))
        story.append(tt)

    # --- Wirtualna dieta (informacyjnie) ---
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph("6. Inne — wirtualna dieta (nie do wypłaty)", h))
    if virtual_diet and virtual_diet.days:
        story.append(
            Paragraph(
                virtual_diet.note
                + f" Kurs EUR: {virtual_diet.eur_pln:g} PLN. "
                f"Przeciętne wynagr. (kontekst ZUS): {_money(virtual_diet.avg_wage_pln)}.",
                ParagraphStyle("vdnote", fontName=_FONT, fontSize=7, leading=10,
                               textColor=colors.HexColor("#4B5563")),
            )
        )
        story.append(Spacer(1, 2 * mm))
        vd_data = [["Dzień", "Czas za granicą", "Ułamek", "ZUS €", "PIT €"]]
        for d in virtual_diet.days:
            frac_txt = (
                "1"
                if d.fraction >= 0.999
                else ("½" if abs(d.fraction - 0.5) < 0.01 else "⅓")
            )
            vd_data.append(
                [
                    d.day.strftime("%d.%m.%Y"),
                    format_minutes(d.abroad_minutes),
                    frac_txt,
                    f"{d.zus_eur:g}",
                    f"{d.pit_eur:g}",
                ]
            )
        vd_data.append(
            [
                "Razem",
                "",
                f"{virtual_diet.fraction_total:g}",
                f"{virtual_diet.zus_eur:g}",
                f"{virtual_diet.pit_eur:g}",
            ]
        )
        vdt = Table(vd_data, colWidths=[30 * mm, 35 * mm, 25 * mm, 25 * mm, 25 * mm])
        vdt.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, 0), _FONT_BOLD),
                    ("FONTNAME", (0, 1), (-1, -1), _FONT),
                    ("FONTNAME", (0, -1), (-1, -1), _FONT_BOLD),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("BACKGROUND", (0, 0), (-1, 0), _HEADER_BG),
                    ("GRID", (0, 0), (-1, -1), 0.4, _GRID),
                    ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                ]
            )
        )
        story.append(vdt)
        story.append(Spacer(1, 2 * mm))
        story.append(
            Paragraph(
                f"ZUS łącznie: {virtual_diet.zus_eur:g} € ≈ {_money(virtual_diet.zus_pln)} · "
                f"PIT łącznie: {virtual_diet.pit_eur:g} € ≈ {_money(virtual_diet.pit_pln)}. "
                "Nie doliczane do brutto MiLo / pensji.",
                ParagraphStyle("vdsum", fontName=_FONT, fontSize=8, leading=11),
            )
        )
    else:
        story.append(
            Paragraph(
                "Brak dni zagranicznych (wpisy MiLo) — brak wirtualnej diety.",
                ParagraphStyle("vd0", fontName=_FONT, fontSize=8),
            )
        )

    # --- Informacje szczegółowe (Wn / Xb) ---
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph("7. Informacje szczegółowe (oddawanie Wn / Xb)", h))
    if comp_entries:
        detail_style = ParagraphStyle("comp", fontName=_FONT, fontSize=8, leading=11)
        for i, entry in enumerate(comp_entries, 1):
            story.append(
                Paragraph(f"{i}. {format_comp_detail_line(entry)}", detail_style)
            )
    else:
        story.append(
            Paragraph(
                "Brak wpisów oddawania w tym miesiącu.",
                ParagraphStyle("comp0", fontName=_FONT, fontSize=8),
            )
        )

    # --- Załączniki ---
    story.append(Spacer(1, 5 * mm))
    story.append(Paragraph("8. Załączniki / źródła", h))
    if attachments:
        story.append(
            Paragraph(
                "Treść plików (JPG/PNG = 1 strona; PDF = wszystkie strony) dołączona na końcu raportu.",
                ParagraphStyle("attNote", fontName=_FONT, fontSize=8),
            )
        )
        att_data = [["Lp.", "Plik", "Uwagi"]]
        for i, a in enumerate(attachments, 1):
            att_data.append([str(i), a.filename, a.note])
        at = Table(att_data, colWidths=[15 * mm, 120 * mm, 80 * mm])
        at.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, 0), _FONT_BOLD),
                    ("FONTNAME", (0, 1), (-1, -1), _FONT),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("BACKGROUND", (0, 0), (-1, 0), _HEADER_BG),
                    ("GRID", (0, 0), (-1, -1), 0.4, _GRID),
                ]
            )
        )
        story.append(at)
    else:
        story.append(
            Paragraph(
                "Brak załączników. Podstawa: pliki DDD / rejestry z Brudnopisu OT.",
                ParagraphStyle("att", fontName=_FONT, fontSize=8),
            )
        )

    story.append(Spacer(1, 8 * mm))
    story.append(_footer_signatures())
    doc.build(story)
    if attachments:
        _append_attachment_pages(out, attachments)
    return out


def _image_attachment_pdf_bytes(path: Path, caption: str) -> bytes:
    """Jedna strona A4 z obrazem dopasowanym do marginesów."""
    from PIL import Image as PILImage

    _ensure_fonts()
    page_w, page_h = A4
    margin = 12 * mm
    caption_h = 10 * mm
    max_w = page_w - 2 * margin
    max_h = page_h - 2 * margin - caption_h

    with PILImage.open(path) as img:
        iw, ih = img.size
    if iw <= 0 or ih <= 0:
        raise ValueError(f"Pusty obraz: {path}")
    scale = min(max_w / iw, max_h / ih)
    draw_w = iw * scale
    draw_h = ih * scale

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=margin,
        rightMargin=margin,
        topMargin=margin,
        bottomMargin=margin,
    )
    style = ParagraphStyle(
        "attCap",
        fontName=_FONT,
        fontSize=9,
        leading=11,
        spaceAfter=4,
    )
    story = [
        Paragraph(caption.replace("&", "&amp;").replace("<", "&lt;"), style),
        Image(str(path), width=draw_w, height=draw_h),
    ]
    doc.build(story)
    return buf.getvalue()


def _append_attachment_pages(pdf_path: Path, attachments: list[Attachment]) -> None:
    """Doklej na końcu: JPG/PNG → 1 strona; PDF → wszystkie strony."""
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    base = PdfReader(str(pdf_path))
    for page in base.pages:
        writer.add_page(page)

    for att in attachments:
        src = Path(att.stored_path)
        if not src.is_file():
            continue
        suffix = src.suffix.lower()
        caption = att.filename or src.name
        if att.note:
            caption = f"{caption} — {att.note}"
        try:
            if suffix in _IMAGE_SUFFIXES:
                raw = _image_attachment_pdf_bytes(src, caption)
                part = PdfReader(BytesIO(raw))
                for page in part.pages:
                    writer.add_page(page)
            elif suffix in _PDF_SUFFIXES:
                part = PdfReader(str(src))
                for page in part.pages:
                    writer.add_page(page)
        except Exception:
            # Uszkodzony / zaszyfrowany plik — raport główny zostaje, ten załącznik pomijamy.
            continue

    tmp = pdf_path.with_suffix(".tmp.pdf")
    with tmp.open("wb") as fh:
        writer.write(fh)
    tmp.replace(pdf_path)
