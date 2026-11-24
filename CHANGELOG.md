# Changelog

[Polski / Polish release notes](docs/releases/0.4.0.md#polski)

## 0.4.0 — 2026-11-24

### Added

- **All in backups:** portable 7z archives with lossless LZMA2 compression,
  consistent SQLite snapshots and SHA-256 verification of every archived file.
- Choose a local destination folder or save a verified backup to the paired
  Google Drive account. Google uploads retain the local copy.
- Restore from 7z, existing ZIP backups, an extracted backup folder or Google
  Drive. Restore runs before databases open on the next launch and first creates
  a recovery copy of current data.
- Managed DDD and attachment paths are adjusted when restoring on another
  computer. Default scenario and report folders are included in full backups.

### Changed

- Brudnopis commands are grouped into **Edit**, **DDD** and **View** menus.
  Profile, cycle start and time zone controls remain directly accessible.
- Expanded English interface coverage for backups, Google sign-in, edit history,
  updates, the daily overview, document import and POD workflows. Backup and
  Google connection errors now follow the selected interface language.
- Backup lists avoid resolving every destination file during display and cache
  only small metadata summaries. Restore always performs full verification.
- 7z verification hashes the decompression stream without writing all unpacked
  payloads to temporary disk. Compression settings and checksum checks are unchanged.
- Windows packaging removes byte-identical private OCR DLL duplicates while
  retaining shared native dependencies and the full five-language OCR runtime.

### Fixed

- Google Drive errors distinguish disabled APIs, missing permissions, storage
  limits and administrator restrictions. Drive access is checked before backup
  compression starts.
- The Google sign-in callback listener waits for the matching response instead
  of stopping on an unrelated browser request. Backup sign-in allows ten minutes,
  supports cancellation and explains how to restart an expired attempt.

### Publication

- Public exports exclude databases, DDD files, documents, backups and account
  configuration. Content checks also reject SQLite databases and 7z archives
  disguised with a source-file extension.
- Windows downloads are built from public source on a clean GitHub Actions
  runner. Driver data, credentials and local development builds are not uploaded.

## Earlier releases

- [0.3.2](docs/releases/0.3.2.md): corrected compensation handling after a new
  departure and retained overdue-compensation history.
- [0.3.1](docs/releases/0.3.1.md)
- [0.3.0](docs/releases/0.3.0.md)
- [0.2.3](docs/releases/0.2.3.md)
- [0.2.2](docs/releases/0.2.2.md)
- [0.2.1](docs/releases/0.2.1.md)
- [0.2.0](docs/releases/0.2.0.md)
