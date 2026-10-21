"""Generuje Instrukcja_Ewidentor.pdf (reportlab). Uruchom: python -m ewidentor.docs.generate_manual"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

_OUT_NAME = "Instrukcja_Ewidentor.pdf"


def _fonts() -> tuple[str, str]:
    regular = Path(r"C:\Windows\Fonts\arial.ttf")
    bold = Path(r"C:\Windows\Fonts\arialbd.ttf")
    if regular.is_file() and bold.is_file():
        pdfmetrics.registerFont(TTFont("ManualSans", str(regular)))
        pdfmetrics.registerFont(TTFont("ManualSans-Bold", str(bold)))
        return "ManualSans", "ManualSans-Bold"
    return "Helvetica", "Helvetica-Bold"


def _styles(font: str, font_bold: str) -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "cover": ParagraphStyle(
            "cover",
            parent=base["Title"],
            fontName=font_bold,
            fontSize=22,
            leading=28,
            alignment=TA_CENTER,
            spaceAfter=8,
        ),
        "subtitle": ParagraphStyle(
            "subtitle",
            parent=base["Normal"],
            fontName=font,
            fontSize=11,
            leading=15,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#4B5563"),
            spaceAfter=24,
        ),
        "h1": ParagraphStyle(
            "h1",
            parent=base["Heading1"],
            fontName=font_bold,
            fontSize=14,
            leading=18,
            spaceBefore=14,
            spaceAfter=8,
            textColor=colors.HexColor("#111827"),
        ),
        "h2": ParagraphStyle(
            "h2",
            parent=base["Heading2"],
            fontName=font_bold,
            fontSize=11,
            leading=14,
            spaceBefore=10,
            spaceAfter=5,
            textColor=colors.HexColor("#1F2937"),
        ),
        "body": ParagraphStyle(
            "body",
            parent=base["Normal"],
            fontName=font,
            fontSize=9.5,
            leading=13,
            alignment=TA_JUSTIFY,
            spaceAfter=6,
        ),
        "bullet": ParagraphStyle(
            "bullet",
            parent=base["Normal"],
            fontName=font,
            fontSize=9.5,
            leading=12.5,
            leftIndent=4,
            spaceAfter=2,
        ),
        "note": ParagraphStyle(
            "note",
            parent=base["Normal"],
            fontName=font,
            fontSize=9,
            leading=12,
            textColor=colors.HexColor("#374151"),
            backColor=colors.HexColor("#F3F4F6"),
            borderPadding=6,
            spaceBefore=4,
            spaceAfter=8,
        ),
        "footer": ParagraphStyle(
            "footer",
            parent=base["Normal"],
            fontName=font,
            fontSize=8,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#6B7280"),
        ),
        "cell": ParagraphStyle(
            "cell",
            parent=base["Normal"],
            fontName=font,
            fontSize=8.5,
            leading=11,
            alignment=TA_LEFT,
        ),
        "cell_b": ParagraphStyle(
            "cell_b",
            parent=base["Normal"],
            fontName=font_bold,
            fontSize=8.5,
            leading=11,
            alignment=TA_LEFT,
        ),
    }


def _bullets(items: list[str], style: ParagraphStyle) -> ListFlowable:
    return ListFlowable(
        [ListItem(Paragraph(t, style), leftIndent=12, value="•") for t in items],
        bulletType="bullet",
        start="•",
        leftIndent=14,
        spaceBefore=2,
        spaceAfter=6,
    )


def _table(rows: list[list[str]], styles: dict, col_widths: list[float]) -> Table:
    data = []
    for i, row in enumerate(rows):
        st = styles["cell_b"] if i == 0 else styles["cell"]
        data.append([Paragraph(c, st) for c in row])
    t = Table(data, colWidths=col_widths, repeatRows=1)
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E5E7EB")),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#9CA3AF")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                (
                    "ROWBACKGROUNDS",
                    (0, 1),
                    (-1, -1),
                    [colors.white, colors.HexColor("#F9FAFB")],
                ),
            ]
        )
    )
    return t


def build_story(styles: dict) -> list:
    s = styles
    story: list = []

    story.append(Spacer(1, 40 * mm))
    story.append(Paragraph("Ewidentor", s["cover"]))
    story.append(
        Paragraph(
            "Instrukcja obsługi — miesięczna ewidencja czasu pracy kierowców",
            s["subtitle"],
        )
    )
    story.append(
        Paragraph(
            "Dokument praktyczny: co robić krok po kroku, gdzie szukać przycisków "
            "i jak rozwiązać typowe problemy. Program współpracuje z Brudnopisem OT.",
            s["body"],
        )
    )
    story.append(Spacer(1, 8 * mm))
    story.append(
        Paragraph(
            "Wersja instrukcji zgodna z funkcjami aplikacji (profil, Brudnopis, "
            "MiLo, załączniki PDF, raporty per kierowca).",
            s["note"],
        )
    )
    story.append(PageBreak())

    # 1
    story.append(Paragraph("1. Do czego służy Ewidentor", s["h1"]))
    story.append(
        Paragraph(
            "Ewidentor buduje <b>miesięczną kartę ewidencji</b> czasu pracy kierowcy "
            "(m.in. pod kątem PIP / u.c.p.k.): czasy z tachografu, kraje, nieobecności, "
            "oddawanie niedziel i świąt, wyliczenie wynagrodzenia (norma, nadgodziny, noc, dyżur, "
            "MiLo) oraz <b>PDF ewidencji</b> z ewentualnymi załącznikami.",
            s["body"],
        )
    )
    story.append(
        Paragraph(
            "Aktywności i kraje bierze z bazy <b>Brudnopis OT</b> "
            "(plik <font face='Courier'>ddd_profiles.sqlite</font>). "
            "Własne dane Ewidentora (profile, stawki, nadpisania dni, załączniki) "
            "są w folderze <font face='Courier'>dane\\</font> obok programu.",
            s["body"],
        )
    )

    # 2
    story.append(Paragraph("2. Pierwsze uruchomienie", s["h1"]))
    story.append(
        _bullets(
            [
                "Uruchom <b>Ewidentor.exe</b> (z folderu portable albo skrótu).",
                "Ustaw <b>Ścieżka Brudnopis DB…</b> na plik "
                "<font face='Courier'>ddd_profiles.sqlite</font> "
                "(w paczce portable: <font face='Courier'>..\\BrudnopisOT\\dane\\</font>).",
                "W Brudnopisie zaimportuj kartę DDD za dany miesiąc (ważne: świeży import "
                "po aktualizacji GNSS / crossings).",
                "W Ewidentorze: <b>Zaciągnij z Brudnopisu</b> albo "
                "<b>Uzupełnij z pliku DDD…</b> / <b>Nowy profil</b>.",
                "W <b>Edytuj profil</b> uzupełnij pensję, % dyżuru, okno nocne, staż AT (MiLo).",
                "Wybierz kierowcę, rok i miesiąc — miesiąc wczytuje się <b>automatycznie</b>.",
            ],
            s["bullet"],
        )
    )

    # 3
    story.append(Paragraph("3. Układ okna", s["h1"]))
    story.append(Paragraph("3.1. Pasek narzędzi (góra)", s["h2"]))
    story.append(
        Paragraph(
            "Po lewej wordmark <b>Ewidentor</b>. Dalej toolbar pogrupowany: "
            "<b>Profil</b> | <b>Dane</b> | <b>Rozliczenie</b> | "
            "<b>PDF / Zamknij</b> (+ strefa czasu).",
            s["body"],
        )
    )
    story.append(
        _table(
            [
                ["Przycisk", "Działanie"],
                ["Nowy / Edytuj / Usuń", "Zarządzanie profilem kierowcy (pensja, AT, Brudnopis)."],
                [
                    "Zaciągnij z Brudnopisu",
                    "Import / aktualizacja profili z bazy Brudnopisu (karty). "
                    "Nie nadpisuje ustawionych stawek pensji.",
                ],
                [
                    "DDD…",
                    "Odczyt tożsamości z pliku C_*.ddd (gdy dostępny ddd-analyzer).",
                ],
                ["Ścieżka DB…", "Wybór pliku ddd_profiles.sqlite."],
                ["MiLo: kursy…", "Kursy walut → PLN oraz stawki godzinowe krajów."],
                ["PDF ewidencji", "Generuje raport PDF miesiąca + dokleja załączniki."],
                [
                    "Zamknij miesiąc…",
                    "Pokazuje checklistę zamknięcia i prowadzi do generowania PDF.",
                ],
                [
                    "Strefa czasu (lista)",
                    "Europe/Warsaw lub UTC — tylko wyświetlanie godzin start/koniec.",
                ],
            ],
            s,
            [42 * mm, 130 * mm],
        )
    )

    story.append(Paragraph("3.2. Kontekst, KPI i checklista", s["h2"]))
    story.append(
        Paragraph(
            "U góry: wybór kierowcy, roku i miesiąca oraz przełącznik "
            "<b>Lista | Kalendarz</b>. Miesiąc ładuje się sam przy zmianie kontekstu "
            "(bez osobnego „Wczytaj”).",
            s["body"],
        )
    )
    story.append(
        Paragraph(
            "<b>Pasek KPI (inline):</b> norma, czas pracy, Wn otwarte, MiLo top-up, "
            "brutto, netto≈ — wartości w jednym rzędzie bez osobnych kart.",
            s["body"],
        )
    )
    story.append(
        Paragraph(
            "<b>Zamknięcie miesiąca</b> — pasek kroków 1→6 (Sync, Dni, Wn, MiLo, Zał., PDF). "
            "Ukończone kroki wyróżnione; klik skacze do sekcji; przycisk "
            "<b>Zamknij miesiąc…</b> otwiera podsumowanie i generowanie PDF.",
            s["body"],
        )
    )

    story.append(Paragraph("3.3. Lista / Kalendarz i inspektor dnia", s["h2"]))
    story.append(
        Paragraph(
            "<b>Lista</b> — tabela dni z ciemnym nagłówkiem, kolumną znaczników oraz "
            "kolorami wierszy. <b>Kalendarz</b> — siatka miesiąca (zielona heat-mapa "
            "czasu pracy; żółty = nieobecność, czerwony = praca nd/św, niebieski = dzień "
            "ręczny). Klik dnia w obu widokach ustawia wspólny wybór.",
            s["body"],
        )
    )
    story.append(
        Paragraph(
            "Kolumny tabeli: Dzień, Znaczniki, Kraje (DDD), Start, Koniec, Czas pracy, "
            "Jazda, Inna praca, Dyżur 50%, Noc, Nadg., Nieobecność.",
            s["body"],
        )
    )
    story.append(
        Paragraph(
            "<b>Inspektor dnia</b> (prawa kolumna, nad tabami): kraje, start/koniec, "
            "skrót czasów i bloków, przycisk <b>Edytuj dzień</b> (także dwuklik wiersza). "
            "Pod inspektorem: taby Nieobecności | Wn/Xb | MiLo | Załączniki.",
            s["note"],
        )
    )
    story.append(
        Paragraph(
            "<b>Kolory wierszy:</b> żółty — nieobecność; czerwony — otwarte Wn; "
            "niebieski — dzień ręcznie nadpisany; szary — praca bez kraju.",
            s["note"],
        )
    )

    story.append(Paragraph("3.4. Prawa strona — zakładki", s["h2"]))
    story.append(
        _bullets(
            [
                "<b>Nieobecności</b> — chorobowe, urlop, wolne itd. (Dodaj / Usuń).",
                "<b>Wn / Xb</b> — dług za pracę w niedzielę/święto: "
                "Oddaj dzień / Wypłać 100% / Usuń wpis.",
                "<b>MiLo</b> — kraje i typ operacji (bilateralny, tranzyt, kabotaż, "
                "cross-trade). Auto-wpisy przy wczytaniu miesiąca. "
                "Zbiorczo: Ustaw typ… / Oznacz wszystkie auto jako… / Zastosuj sugestie… "
                "(podgląd przed zapisem). Edycja: dwuklik lub Edytuj.",
                "<b>Załączniki</b> — PDF / JPG / PNG dołączane na końcu raportu.",
            ],
            s["bullet"],
        )
    )

    story.append(PageBreak())

    # 4
    story.append(Paragraph("4. Typowy przebieg miesiąca", s["h1"]))
    story.append(
        _bullets(
            [
                "W Brudnopisie: import DDD za miesiąc (karta kierowcy).",
                "W Ewidentorze: ścieżka DB → Zaciągnij z Brudnopisu (jeśli nowy kierowca).",
                "Wybór kierowcy + miesiąca (auto-wczytanie).",
                "Sprawdź KPI i checklistę; w razie potrzeby przełącz <b>Kalendarz</b>.",
                "Sprawdź kraje i auto-MiLo; popraw typy (kabotaż / cross-trade) tam, "
                "gdzie należy się top-up.",
                "Uzupełnij nieobecności; rozlicz Wn (oddaj dzień albo wypłać 100%).",
                "W razie potrzeby: <b>Edytuj dzień</b> z inspektora (bloki aktywności).",
                "Dodaj załączniki → <b>Zamknij miesiąc…</b> albo <b>PDF ewidencji</b>.",
            ],
            s["bullet"],
        )
    )
    story.append(
        Paragraph(
            "Raport trafia domyślnie do "
            "<font face='Courier'>raporty\\&lt;Nazwa_Kierowcy&gt;\\Ewidencja_RRRR-MM.pdf</font>.",
            s["body"],
        )
    )

    # 5
    story.append(Paragraph("5. Profil kierowcy", s["h1"]))
    story.append(
        Paragraph(
            "W oknie profilu ustawiasz m.in.: imię i nazwisko, numer karty, "
            "<b>Profil Brudnopis</b> (powiązanie z bazą), pracodawcę, system pracy, "
            "etat, pensję miesięczną, stawkę godzinową (puste = wyliczana z pensji/normy), "
            "% dyżuru, % dodatku nocnego, <b>okno nocne</b> (presety 00–04 / 22–06 / 21–07; "
            "domyślnie 22–06 dla nowych kierowców), opcję liczenia nocy na dyspozycji, "
            "% nadgodzin 50/100, <b>tryb dyżuru</b> (surowy tacho vs heurystyka dyspozycji) "
            "oraz <b>staż AT (a–e)</b> do stawek MiLo.",
            s["body"],
        )
    )
    story.append(
        Paragraph(
            "Bez powiązania z profilem Brudnopisu miesiąc wczyta się bez segmentów "
            "z tachografu (puste dni albo tylko ręczne wpisy).",
            s["note"],
        )
    )

    # 6
    story.append(Paragraph("6. Edycja dnia", s["h1"]))
    story.append(
        Paragraph(
            "Dwuklik wiersza lub przycisk <b>Edytuj dzień</b>. Możesz dodać/usunąć bloki "
            "(Jazda, Praca, Dyspozycja, Odpoczynek) w wybranej strefie czasu. "
            "Między blokami nie może być luk ani nachodzeń — zapis jest blokowany, "
            "aż uzupełnisz brakujące odcinki (np. <b>Dodaj blok na lukę</b>); "
            "nie trzeba pokrywać całego dnia 00:00–24:00. "
            "<b>Zapisz</b> tworzy lokalne nadpisanie w Ewidentorze. "
            "<b>Przywróć z Brudnopisu</b> usuwa nadpisanie i wraca do danych z bazy Brudnopisu. "
            "Zmiany <b>nie</b> są zapisywane z powrotem do Brudnopisu.",
            s["body"],
        )
    )

    # 7
    story.append(Paragraph("7. Nieobecności i oddawanie Wn", s["h1"]))
    story.append(Paragraph("7.1. Nieobecności", s["h2"]))
    story.append(
        Paragraph(
            "Dodaj nieobecność (chorobowe, urlop wypoczynkowy/bezpłatny, wolne, itd.). "
            "Domyślnie obejmuje cały dzień — czasy z tacho w tym dniu są czyszczone "
            "na potrzeby ewidencji.",
            s["body"],
        )
    )
    story.append(Paragraph("7.2. Oddawanie niedziel / świąt (Wn)", s["h2"]))
    story.append(
        Paragraph(
            "Przy wczytaniu miesiąca program może sam dodać otwarte wpisy długu, "
            "gdy była praca w niedzielę lub święto. Potem:",
            s["body"],
        )
    )
    story.append(
        _bullets(
            [
                "<b>Oddaj dzień</b> — wskazujesz dzień oddania (status: oddane + data).",
                "<b>Wypłać 100%</b> — bez oddania dnia wolnego (status: wypłacono 100%).",
                "Nieoddane minuty wpływają na część wynagrodzenia (sekcja C w PDF).",
            ],
            s["bullet"],
        )
    )

    # 8
    story.append(Paragraph("8. MiLo", s["h1"]))
    story.append(
        Paragraph(
            "Przy wczytaniu miesiąca Ewidentor tworzy brakujące wpisy MiLo "
            "na podstawie minut w krajach (z przekroczeń granic / places z Brudnopisu). "
            "Auto-wpisy mają typ <b>bilateralny</b> i <b>nie dają top-upu</b>. "
            "Istniejących wpisów (ten sam dzień + kraj) auto <b>nie nadpisuje</b>.",
            s["body"],
        )
    )
    story.append(
        Paragraph(
            "Top-up MiLo liczony jest dla typów <b>kabotaż</b> i <b>cross-trade</b> "
            "(różnica względem minimum kraju × kurs → PLN). Tranzyt i bilateralny = bez top-upu. "
            "Czas MiLo = jazda + praca + dyspozycja w danym kraju.",
            s["body"],
        )
    )
    story.append(
        Paragraph(
            "Na zakładce MiLo możesz ustawić <b>kurs EUR miesiąca</b> (nadpisanie) "
            "oraz zobaczyć szacunek top-upu „gdyby wszystkie auto były cross-trade” "
            "(dry-run, bez zapisu) — checklista „MiLo typy OK” wymaga braku auto-bilateralnych.",
            s["body"],
        )
    )
    story.append(
        Paragraph(
            "Uwaga w wpisie może brzmieć: „auto z GNSS”, „auto z border_crossings” "
            "lub „auto z places (DDD)” — zależnie od źródła w bazie Brudnopisu. "
            "Po ręcznej zmianie typu dopisywane jest „· ręcznie”.",
            s["note"],
        )
    )
    story.append(
        Paragraph(
            "<b>MiLo: kursy i stawki…</b> — ręcznie utrzymujesz kursy walut (PLN) "
            "oraz tabelę stawek krajowych. To wpływa na wyliczenie top-upu i kontekst diety wirtualnej w PDF.",
            s["body"],
        )
    )

    story.append(PageBreak())

    # 9
    story.append(Paragraph("9. Załączniki i PDF", s["h1"]))
    story.append(Paragraph("9.1. Załączniki", s["h2"]))
    story.append(
        Paragraph(
            "Sekcja <b>Załączniki (PDF/JPG/PNG)</b>: Dodaj plik → opcjonalne uwagi. "
            "Pliki kopiowane są do "
            "<font face='Courier'>dane\\zalaczniki\\&lt;id_kierowcy&gt;\\RRRR-MM\\</font>.",
            s["body"],
        )
    )
    story.append(Paragraph("9.2. PDF ewidencji", s["h2"]))
    story.append(
        Paragraph(
            "Przycisk <b>PDF ewidencji</b> (lub <b>Zamknij miesiąc…</b> z checklistą) "
            "buduje raport (składniki, przeliczenie A–D: pensja + nadgodziny powyżej normy KP, "
            "noc, dyżur, nieoddane nd/święta, MiLo; "
            "tygodnie, Wn, dieta wirtualna, lista załączników). Na końcu PDF "
            "doklejane są załączniki: <b>JPG/PNG = 1 strona</b>, <b>PDF = wszystkie strony</b>. "
            "Nadgodziny: dodatek %50/%100 od stawki za czas pracy powyżej normy — "
            "dyżur nie wchodzi do bazy OT.",
            s["body"],
        )
    )
    story.append(
        Paragraph(
            "Folder docelowy: każdy kierowca ma własny podfolder w "
            "<font face='Courier'>raporty\\</font>.",
            s["body"],
        )
    )

    # 10
    story.append(Paragraph("10. Pojęcia w skrócie", s["h1"]))
    story.append(
        _table(
            [
                ["Pojęcie", "Znaczenie w programie"],
                ["Czas pracy", "Jazda + inna praca."],
                ["Dyżur 50%", "Aktywność dyspozycji / availability; płatność wg % w profilu."],
                ["Noc", "Praca/jazda w oknie nocnym z profilu (np. 00:00–04:00)."],
                ["Norma", "Wymiar KP × etat w miesiącu."],
                ["Nadgodziny", "Praca powyżej planu dnia; w nd/św praca idzie do ewidencji Wn."],
                [
                    "Brutto / Netto≈",
                    "Suma składników A–D. Netto≈ to uproszczenie (nie lista płac).",
                ],
                [
                    "Dieta wirtualna",
                    "Informacyjnie w PDF — nie wchodzi do wypłaty w tym module.",
                ],
            ],
            s,
            [40 * mm, 132 * mm],
        )
    )

    # 11
    story.append(Paragraph("11. Skąd biorą się dane", s["h1"]))
    story.append(
        _table(
            [
                ["Dane", "Źródło"],
                ["Segmenty (jazda/praca/…)", "Brudnopis → tabela segments"],
                ["Kraje / granice", "border_crossings (w tym GNSS) lub places START/END"],
                ["Nadpisanie dnia", "Tylko lokalnie w Ewidentorze"],
                ["Nieobecności, Wn, MiLo ręczne", "Baza Ewidentora (dane\\ewidentor.sqlite)"],
                ["Załączniki", "dane\\zalaczniki\\…"],
                ["PDF", "raporty\\&lt;kierowca&gt;\\…"],
            ],
            s,
            [48 * mm, 124 * mm],
        )
    )

    # 12
    story.append(Paragraph("12. Problemy i rozwiązania", s["h1"]))
    story.append(
        _table(
            [
                ["Objaw", "Co zrobić"],
                [
                    "Nie znaleziono bazy / pusty sync",
                    "Toolbar → Ścieżka Brudnopis DB… → wskaż ddd_profiles.sqlite.",
                ],
                [
                    "Pusty miesiąc (0 godzin)",
                    "Czy profil ma powiązanie Brudnopis? Czy DDD za ten miesiąc "
                    "jest w Brudnopisie?",
                ],
                [
                    "Brak krajów / auto-MiLo",
                    "W Brudnopisie ponownie zaimportuj DDD (crossings/GNSS/places).",
                ],
                [
                    "Godziny „przesunięte”",
                    "Zmień strefę Europe/Warsaw ↔ UTC na pasku narzędzi.",
                ],
                [
                    "Ręczny dzień nie wraca do tacho",
                    "Edytuj dzień → Przywróć z Brudnopisu.",
                ],
                [
                    "Auto-MiLo nie zmienia typu na kabotaż",
                    "Usuń stary wpis (dzień+kraj) albo dodaj ręcznie właściwy typ.",
                ],
                [
                    "Pensja znika po syncu",
                    "Sync nie powinien nadpisywać stawek — ustaw ponownie w Edytuj profil.",
                ],
                [
                    "Brak ddd-analyzer przy DDD",
                    "Użyj Zaciągnij z Brudnopisu albo uruchom pełną paczkę portable.",
                ],
            ],
            s,
            [55 * mm, 117 * mm],
        )
    )

    story.append(Paragraph("13. Foldery obok programu", s["h1"]))
    story.append(
        _bullets(
            [
                "<font face='Courier'>dane\\</font> — baza Ewidentora i załączniki",
                "<font face='Courier'>raporty\\</font> — PDF ewidencji (podfolder per kierowca)",
                "<font face='Courier'>_internal\\</font> — biblioteki portable (nie kasować)",
                "W paczce: Brudnopis OT w sąsiednim folderze "
                "<font face='Courier'>BrudnopisOT\\</font>",
            ],
            s["bullet"],
        )
    )

    story.append(Spacer(1, 10 * mm))
    story.append(
        Paragraph(
            "Koniec instrukcji. W razie zmian w programie wygeneruj plik ponownie "
            "(skrypt generate_manual w repozytorium).",
            s["footer"],
        )
    )
    return story


def default_output_path() -> Path:
    return Path(__file__).resolve().parent / _OUT_NAME


def generate(path: Path | None = None) -> Path:
    out = path or default_output_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    font, font_bold = _fonts()
    styles = _styles(font, font_bold)

    def _on_page(canvas, doc) -> None:
        canvas.saveState()
        canvas.setFont(font, 8)
        canvas.setFillColor(colors.HexColor("#6B7280"))
        canvas.drawCentredString(
            A4[0] / 2,
            10 * mm,
            f"Ewidentor — instrukcja obsługi  |  str. {doc.page}",
        )
        canvas.restoreState()

    doc = SimpleDocTemplate(
        str(out),
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=14 * mm,
        bottomMargin=16 * mm,
        title="Instrukcja obsługi — Ewidentor",
        author="Ewidentor",
    )
    doc.build(build_story(styles), onFirstPage=_on_page, onLaterPages=_on_page)
    return out


def main() -> None:
    out = generate()
    print(out)


if __name__ == "__main__":
    main()
