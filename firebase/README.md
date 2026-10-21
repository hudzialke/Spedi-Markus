# Firebase configuration

This directory contains generic Firestore rules and a deployment helper.
There is no preselected Firebase project, account, user UID or service key.

Use your own project and publish `firestore.rules`, either from Firebase Console
or with `./deploy.ps1 -ProjectId YOUR_PROJECT_ID` and an authenticated Firebase
CLI. The script deploys Firestore rules only.

The rules allow an authenticated user to access their own `users/{UID}` subtree.
Client writes to `driver_schedules` are denied; Spedi publishes those documents
through the Admin SDK. Admin credentials bypass client rules and must stay on
the trusted desktop, outside the repository and APK.

Reminders use Firestore and Android alarms. No Cloud Functions are included.
Dispatcher document synchronization can additionally use your own Storage
bucket; review its configuration and billing requirements separately.

Full instructions: [English](../docs/android-connection.md) ·
[Polski](../docs/android-connection.pl.md).
