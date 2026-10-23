# Publishing source without private data

Use `python tools/public_export.py --output PATH_TO_A_NEW_FOLDER` to create
a source-only copy. The destination must not already exist. The exporter uses
explicit source directories, file extensions and root files. It does not copy
the working directory wholesale or reuse any previous Git history.

Excluded: databases and journals, imported DDD files, driver archives, reports,
documents, local settings, service accounts, OAuth clients, tokens, API keys,
Firebase Android configuration, Android signing keys, SDK caches, build outputs,
private research notes and development artifacts.

Run `python tools/public_export.py --check PATH_TO_EXPORT` before committing.
The check rejects sensitive filenames and common credential formats, including
private keys and Google/GitHub/AWS tokens. It reports paths rather than matched
secret values. Automated patterns do not prove the absence of every possible
secret or personal detail; review new files before future publications.

Tests use synthetic data. Public ERCA verification keys and public country
boundaries are retained. These are application assets, not account credentials.
Original third-party notices are preserved.

For a fresh Git repository, initialize Git only inside the exported directory,
inspect `git status` and the staged file list, then commit and push. Do not
publish existing installation folders or old configured APK/EXE packages.

Po polsku: publikuj wyłącznie czysty eksport utworzony powyższym narzędziem.
Nie dodawaj katalogów `dane`, `ddd_files`, `ewidencja`, `raporty`, starych
instalatorów, APK ani lokalnej konfiguracji. Klucze API, konta serwisowe,
tokeny i bazy pozostają prywatne. `.gitignore` jest dodatkowym zabezpieczeniem,
a nie zamiennikiem kontroli plików i historii Git.

## Synthetic showcase assets

The PNG/GIF files in `docs/showcase/assets/` were captured from real application
widgets populated only with generated fictional fixtures. The capture process
uses isolated data/settings directories and blocks network connections.
The exporter accepts only the explicitly reviewed asset paths and SHA-256
digests in `SHOWCASE_ASSETS`. Other screenshots are excluded; changing an
approved image requires another review and a digest update. Demo databases,
intermediate source PDFs and working files are not published.
