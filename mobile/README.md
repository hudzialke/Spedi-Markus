# Spedi Android App

Formerly MarkusDispatcher. Author: **Patryk Prośniak (hudzialke)**.
Developed with assistance from various AI models. Distributed under the
repository's [MIT License](../LICENSE), without warranty or liability to the
extent permitted by law.

Android 8+ companion for Spedi: orders, driver rest/return deadlines,
offline phone plans, hidden profiles, and 72/48/24 h local reminders.
The current Android interface contains Polish text.

- [Connection and build instructions — English](../docs/android-connection.md)
- [Instrukcja połączenia i budowania — Polski](../docs/android-connection.pl.md)

Display name: **Spedi Android App**. Public application ID: `pl.spedi.android`.
The Kotlin namespace is retained for source compatibility. Public builds
install separately from older private APKs; they do not reuse the author's
Firebase project or signing key.

Before building, register your own Firebase Android app, put its configuration
in `app/google-services.json`, configure JDK 17 / Android SDK 35 and run
`./build.ps1` or use Android Studio. Your configuration, `local.properties`,
keystores and build outputs are ignored and excluded from publication.

The private Admin SDK service-account key belongs only on the trusted desktop.
The phone signs in with Firebase Email/Password. All profiles under the signed-in
UID are visible to that account; the app does not provide per-driver access
control. Deadline flow is desktop → Firestore → phone. Phone plan changes stay
local and do not recalculate desktop compensation.
