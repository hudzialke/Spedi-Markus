# Connect Spedi to Spedi Android App

[Polski](android-connection.pl.md)

The Android source is in `mobile/`. Its display name is **Spedi Android App**
and its application ID is **`pl.spedi.android`**. This installs separately from
older private MarkusDispatcher APKs. No old APK, signing key or Firebase
configuration is distributed.

## 1. Create your Firebase project

Create your own project in [Firebase Console](https://console.firebase.google.com/).
Enable **Authentication → Sign-in method → Email/Password**. Under **Users**,
create the account you will use on your phone and copy its **User UID**.
This is not the project ID or email address.

Create a **Cloud Firestore** database and select its location. Publish
`firebase/firestore.rules` in the Rules tab, or use a signed-in Firebase CLI:

```powershell
.\firebase\deploy.ps1 -ProjectId YOUR_PROJECT_ID
```

The rules isolate one account's data from other accounts. An account can read
all drivers and orders under its UID. This is not per-driver authorization;
only share that account with people allowed to access the whole dataset.

## 2. Build Android

Register an Android application with package name **`pl.spedi.android`** in
your Firebase project. Download its `google-services.json` to
`mobile/app/google-services.json`. Keep this project configuration out of Git.

Open `mobile/` in Android Studio with JDK 17 and Android SDK 35. Sync Gradle,
run the tests and build the APK. With `JAVA_HOME` and the SDK already configured:

```powershell
cd mobile
.\build.ps1
```

The debug APK is `mobile/app/build/outputs/apk/debug/app-debug.apk`. Install it
on Android 8 or later. Use your own signing key for releases and keep the key
and passwords outside the repository.

## 3. Configure the desktop

Install synchronization dependencies:

```powershell
python -m pip install -e ".[sync]"
```

In Firebase **Project settings → Service accounts**, generate a key for your
project. This is a private administrative key. Keep it only on your trusted
desktop; never bundle it in an APK or commit it.

In Spedi, open **Dispatcher → Sync Firebase → Open credentials folder**.
Save the key in that folder as **`service-account.json`**. Enter the **User UID
from step 1**, then select **Save UID**. Spedi writes a local `config.json`.
The default Windows path, determined by `platformdirs`, is
`%LOCALAPPDATA%/Spedi/MarkusDispatcher/firebase/`; the folder button opens the
actual location. The phone configuration and service account must belong to
the same project.

## 4. Publish rest deadlines and reminders

Add your own driver profiles and rests in Brudnopis, then save changes.
Select **Sync reminders** on Spedi's top bar. Spedi publishes the **Modified**
view's deadlines to `users/{UID}/driver_schedules/{profileId}` and checks them
automatically while running once configured. This publication excludes the
full DDD timeline and freight amounts.

Sign in on the phone using the account from step 1. Open **Monity** (Reminders),
check a driver and deadline, and allow notifications and exact alarms when
needed. Select 72/48/24 h reminders. Downloaded alarms run locally; Android
background and battery restrictions can delay refreshes.

Deadline data flows **Brudnopis → Firestore → phone**. Phone plan edits,
hidden profiles and reminder preferences stay on the phone. They do not
modify Brudnopis. A manually edited phone date does not recalculate rest
compensation. Reminders need Firestore, but no Cloud Functions or Cloud
Storage. Check your own project's current quotas and pricing.

## 5. Dispatcher orders

Select **Dispatcher → Sync Firebase → Sync now** to synchronize orders
separately. The phone reads `users/{UID}/jobs` and `users/{UID}/drivers`.
Desktop synchronization for these collections is bidirectional; deadline
publication remains one-way. Phone order hiding/archiving is local.

Dispatcher document synchronization can additionally use Cloud Storage.
Configure your own bucket and check billing requirements if you enable it.
The optional `storageBucket` field in local `config.json` specifies your bucket.
It is not required for reminders. Google Drive/Sheets is independent of the
Android connection.

## Troubleshooting

For empty lists, verify project, UID, saved profiles and the desktop sync result.
For permission errors, verify deployed rules and the signed-in account.
For missed alarms, check notification/exact alarm permissions, battery settings
and whether the deadline is in the future. For Android build configuration
errors, verify the package name and `google-services.json`.

Never include service account keys, tokens or passwords in issue reports or
screenshots. Spedi is MIT-licensed, without warranty. Independently verify
deadlines and results.

Setup references: [Android/Firebase](https://firebase.google.com/docs/android/setup),
[Email/Password](https://firebase.google.com/docs/auth/android/password-auth),
[Admin SDK](https://firebase.google.com/docs/admin/setup).
