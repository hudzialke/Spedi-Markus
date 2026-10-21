from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from ddd_analyzer.models import (
    ACTIVITY_LABELS_PL,
    ActivitySegment,
    ClassifiedRest,
    DddAnalysis,
    DriverInfo,
    RestAnalysisSummary,
)
from ddd_analyzer.night_work import NightWorkAnalysis
from ddd_analyzer.reg561 import significant_rests
from ddd_analyzer.summarize import format_minutes, segment_time_label

# Legacy optional exporter: callers supply their own destination.
DEFAULT_DRIVE_FOLDER_ID = ""
DEFAULT_DRIVE_FOLDER_URL = ""

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]

_INVALID_TITLE_CHARS = re.compile(r'[\\/:*?"<>|]')


def app_data_dir() -> Path:
    base = Path.home() / "AppData" / "Local" / "DDDAnalyzer"
    base.mkdir(parents=True, exist_ok=True)
    return base


def credentials_path() -> Path:
    return app_data_dir() / "credentials.json"


def token_path() -> Path:
    return app_data_dir() / "token.json"


def has_credentials() -> bool:
    return credentials_path().is_file()


def has_token() -> bool:
    return token_path().is_file()


def credentials_status_text() -> str:
    cred = credentials_path()
    if not cred.is_file():
        return f"Brak credentials.json — zapisz OAuth Desktop w:\n{cred}"
    if has_token():
        return f"Google OAuth: OK ({cred.parent})"
    return f"Credentials OK — przy eksporcie zalogujesz sie w przegladarce ({cred.parent})"


def install_credentials_from(source: str | Path) -> Path:
    src = Path(source).resolve()
    if not src.is_file():
        raise FileNotFoundError(f"Plik nie istnieje: {src}")
    dest = credentials_path()
    dest.write_bytes(src.read_bytes())
    return dest


def clear_token() -> None:
    path = token_path()
    if path.exists():
        path.unlink()


def build_spreadsheet_title(read_at: datetime, driver: DriverInfo) -> str:
    """Format: YYYY-MM-DD Imie Nazwisko (data odczytu pliku przez analyzer)."""
    date_part = read_at.strftime("%Y-%m-%d")
    name = driver.display_name.strip() or "Nieznany kierowca"
    title = f"{date_part} {name}"
    return _INVALID_TITLE_CHARS.sub("", title).strip()[:100]


def _get_credentials():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds_file = credentials_path()
    if not creds_file.is_file():
        raise FileNotFoundError(
            f"Brak pliku credentials.json.\n"
            f"Utworz klienta OAuth (Desktop) w Google Cloud Console i zapisz plik jako:\n"
            f"{creds_file}"
        )

    creds = None
    token_file = token_path()
    if token_file.is_file():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_file), SCOPES)
            creds = flow.run_local_server(port=0)
        token_file.write_text(creds.to_json(), encoding="utf-8")

    return creds


def _fmt_dt(value) -> str:
    return value.strftime("%d.%m.%Y %H:%M")


def _activity_rows(segments: list[ActivitySegment]) -> list[list[str]]:
    rows: list[list[str]] = [["Od", "Do", "Aktywnosc", "Czas trwania"]]
    ordered = sorted(segments, key=lambda s: (s.day, s.start_minute, s.end_minute))
    for segment in ordered:
        start, end, duration = segment_time_label(segment)
        day = segment.day.strftime("%d.%m.%Y")
        rows.append(
            [
                f"{day} {start}",
                f"{day} {end}",
                ACTIVITY_LABELS_PL[segment.activity],
                duration,
            ]
        )
    return rows


def _rest_rows(classified: list[ClassifiedRest]) -> list[list[str]]:
    rows: list[list[str]] = [
        [
            "Od",
            "Do",
            "Czas",
            "Typ",
            "Kompensacja doklejona",
            "Dlug utworzony",
            "Dlug po",
            "Termin kompensacji",
            "Status",
            "Uwagi",
        ]
    ]
    for item in significant_rests(classified):
        rows.append(
            [
                _fmt_dt(item.start),
                _fmt_dt(item.end),
                format_minutes(item.duration_min),
                item.kind_label_pl,
                format_minutes(item.compensation_min),
                format_minutes(item.debt_created_min),
                format_minutes(item.debt_after_min),
                item.deadline.strftime("%d.%m.%Y") if item.deadline else "",
                item.status,
                item.note,
            ]
        )
    return rows


def _summary_rows(
    analysis: DddAnalysis,
    rest_summary: RestAnalysisSummary,
    night_analysis: NightWorkAnalysis | None,
) -> list[list[str]]:
    start, end = analysis.date_range
    read_at = analysis.read_at or datetime.now()
    rows: list[list[str]] = [
        ["Pole", "Wartosc"],
        ["Kierowca", analysis.driver.display_name],
        ["Nr karty", analysis.driver.card_number],
        ["Plik", analysis.filename],
        ["Data odczytu przez analyzer", read_at.strftime("%d.%m.%Y %H:%M")],
        ["Zakres danych od", start.strftime("%d.%m.%Y") if start else ""],
        ["Zakres danych do", end.strftime("%d.%m.%Y") if end else ""],
        ["Dobowych skroconych", str(rest_summary.daily_reduced_count)],
        ["Dobowych regularnych", str(rest_summary.daily_regular_count)],
        ["Tygodniowych skroconych (24–45h)", str(rest_summary.weekly_reduced_count)],
        ["Tygodniowych regularnych (>=45h)", str(rest_summary.weekly_regular_count)],
        ["Niesplacona kompensacja", format_minutes(rest_summary.unpaid_compensation_min)],
    ]
    if night_analysis:
        rows.extend(
            [
                [
                    "Okresy z praca w porze nocnej",
                    str(night_analysis.night_limited_count),
                ],
                [
                    "Naruszenia limitu 10 h (art. 21)",
                    str(len(night_analysis.violations)),
                ],
                ["Pora nocna (domyslnie)", night_analysis.night_window.label],
            ]
        )
        for idx, violation in enumerate(night_analysis.violations, start=1):
            rows.append(
                [
                    f"Naruszenie {idx}",
                    (
                        f"{violation.start.strftime('%d.%m.%Y %H:%M')} – "
                        f"{violation.end.strftime('%d.%m.%Y %H:%M')}: "
                        f"praca {format_minutes(violation.work_min)}, "
                        f"nadwyzka {format_minutes(violation.over_min)}"
                    ),
                ]
            )
    rows.append(
        [
            "Uwaga",
            "Heurystyka 561/2006 + art. 21 ustawy o czasie pracy. Nie zastepuje audytu TachoScan.",
        ]
    )
    return rows


def _create_spreadsheet_in_folder(
    drive,
    sheets,
    title: str,
    folder_id: str,
) -> tuple[str, str]:
    created = (
        drive.files()
        .create(
            body={
                "name": title,
                "mimeType": "application/vnd.google-apps.spreadsheet",
                "parents": [folder_id],
            },
            fields="id, webViewLink",
            supportsAllDrives=True,
        )
        .execute()
    )
    spreadsheet_id = created["id"]
    url = created.get("webViewLink") or f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}"

    sheets.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={
            "requests": [
                {
                    "updateSheetProperties": {
                        "properties": {"sheetId": 0, "title": "Bloki"},
                        "fields": "title",
                    }
                },
                {"addSheet": {"properties": {"title": "Odpoczynki_561"}}},
                {"addSheet": {"properties": {"title": "Podsumowanie"}}},
            ]
        },
    ).execute()
    return spreadsheet_id, url


def export_to_google_sheets(
    analysis: DddAnalysis,
    rest_summary: RestAnalysisSummary,
    night_analysis: NightWorkAnalysis | None = None,
    *,
    folder_id: str = DEFAULT_DRIVE_FOLDER_ID,
) -> str:
    """Tworzy arkusz w folderze Google Drive i zwraca URL."""
    if not folder_id.strip():
        raise ValueError("Provide your own Google Drive folder ID before exporting.")
    from googleapiclient.discovery import build

    creds = _get_credentials()
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    sheets = build("sheets", "v4", credentials=creds, cache_discovery=False)

    read_at = analysis.read_at or datetime.now()
    title = build_spreadsheet_title(read_at, analysis.driver)

    spreadsheet_id, url = _create_spreadsheet_in_folder(drive, sheets, title, folder_id)

    sheets.spreadsheets().values().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={
            "valueInputOption": "RAW",
            "data": [
                {"range": "Bloki!A1", "values": _activity_rows(analysis.segments)},
                {"range": "Odpoczynki_561!A1", "values": _rest_rows(rest_summary.classified)},
                {
                    "range": "Podsumowanie!A1",
                    "values": _summary_rows(analysis, rest_summary, night_analysis),
                },
            ],
        },
    ).execute()

    return url
