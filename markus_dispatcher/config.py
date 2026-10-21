from __future__ import annotations

from pathlib import Path
import os

from platformdirs import user_data_dir

APP_NAME = "MarkusDispatcher"
APP_AUTHOR = "Spedi"

DATA_DIR = Path(os.environ.get("SPEDI_DISPATCHER_DATA_DIR") or user_data_dir(APP_NAME, APP_AUTHOR))
DB_PATH = DATA_DIR / "markus_dispatcher.db"
FOOTER_PATH = DATA_DIR / "footer.txt"
# Ewidencja: ewidencja/<kierowca>/<chronologiczne_zlecenie>/dokumenty/
ARCHIVE_ROOT = DATA_DIR / "ewidencja"

GEOCODE_USER_AGENT = "MarkusDispatcher/0.1 (Spedi; lokalna aplikacja dyspozytorni)"
GEOCODE_TIMEOUT_S = 10.0
GEOCODE_DEBOUNCE_MS = 1200
GEOCODE_MIN_INTERVAL_S = 1.15
GEOCODE_MAX_REQUESTS = 5

# Opcjonalny import starej bazy Rozpiski przy pierwszym starcie.
ROZPISKA_DB_CANDIDATE = Path(user_data_dir("Rozpiska", APP_AUTHOR)) / "rozpiska.db"

# E-mail: gmail | outlook | mailto
# gmail/outlook = https compose w Opera GX (zakładka w działającej sesji).
EMAIL_COMPOSE_MODE = "gmail"

# Firebase (poza repo — %LOCALAPPDATA%\MarkusDispatcher\firebase\)
FIREBASE_DIR = DATA_DIR / "firebase"
FIREBASE_SERVICE_ACCOUNT = FIREBASE_DIR / "service-account.json"
FIREBASE_CONFIG = FIREBASE_DIR / "config.json"
