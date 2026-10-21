# Połączenie Spedi z aplikacją Spedi Android App

[English](android-connection.md)

Aplikacja Android znajduje się w `mobile/`. Jej publiczna nazwa to
**Spedi Android App**, a identyfikator aplikacji to `pl.spedi.android`.
Jest to osobna instalacja względem wcześniejszych prywatnych APK
MarkusDispatcher. Nie dołączamy starego APK, jego podpisu ani konfiguracji.

## 1. Własny projekt Firebase

W [Firebase Console](https://console.firebase.google.com/) utwórz własny projekt.
Włącz **Authentication → Sign-in method → Email/Password**. W **Users** utwórz
konto, którym będziesz logować się w telefonie. Skopiuj jego **User UID**.
Nie jest to identyfikator projektu ani adres e-mail.

Utwórz bazę **Cloud Firestore** i ustaw jej lokalizację. Opublikuj reguły
z `firebase/firestore.rules` w zakładce Rules. Możesz też użyć zalogowanego
Firebase CLI i uruchomić z PowerShell:

```powershell
.\firebase\deploy.ps1 -ProjectId TWOJ_ID_PROJEKTU
```

UID izoluje dane jednego konta od innych kont. Konto widzi wszystkie profile
oraz zlecenia zapisane pod jego UID. To nie jest mechanizm udostępnienia każdemu
kierowcy tylko jego własnej karty; nie przekazuj wspólnego konta osobom, które
nie powinny widzieć całego zestawu.

## 2. Zbudowanie aplikacji Android

W ustawieniach projektu Firebase dodaj aplikację Android z nazwą pakietu
**`pl.spedi.android`**. Pobierz jej `google-services.json` i zapisz lokalnie jako
`mobile/app/google-services.json`. To konfiguracja Twojego projektu;
nie dodawaj jej do Git.

Otwórz `mobile/` w Android Studio. Użyj JDK 17 i Android SDK 35. Zsynchronizuj
Gradle, uruchom testy i zbuduj APK. Alternatywnie, przy skonfigurowanym
`JAVA_HOME` i Android SDK:

```powershell
cd mobile
.\build.ps1
```

APK debug będzie w `mobile/app/build/outputs/apk/debug/app-debug.apk`.
Zainstaluj go na Androidzie 8 lub nowszym. Do własnych wydań używaj własnego
klucza podpisującego; klucze i hasła pozostają poza repozytorium.

## 3. Konfiguracja komputera

Zainstaluj opcjonalne zależności synchronizacji:

```powershell
python -m pip install -e ".[sync]"
```

W Firebase **Project settings → Service accounts** wygeneruj klucz konta
serwisowego dla swojego projektu. To prywatny klucz administracyjny.
Zapisz go wyłącznie na zaufanym komputerze, nigdy w repozytorium ani telefonie.
W Spedi wejdź w **Dispatcher → Sync Firebase → Otwórz folder credentials**.
W tym folderze zapisz klucz pod nazwą **`service-account.json`**.
W oknie synchronizacji wpisz **UID konta z kroku 1** i kliknij **Zapisz UID**.
Spedi zapisze lokalny `config.json`. Domyślna ścieżka na Windows wynika
z `platformdirs`: `%LOCALAPPDATA%/Spedi/MarkusDispatcher/firebase/`;
przycisk otwierający folder pokazuje faktycznie używaną lokalizację.

Konfiguracja telefonu i konto serwisowe muszą dotyczyć tego samego projektu.

## 4. Monity i terminy odpoczynków

Dodaj własne profile i odpoczynki w Brudnopisie. Zapisz zmiany. Kliknij
**Sync monity** w górnym pasku Spedi. Program publikuje terminy z widoku
**Zmodyfikowany** do `users/{UID}/driver_schedules/{profileId}`. Po konfiguracji
sprawdza je automatycznie podczas działania. Pełna oś DDD i kwoty frachtu
nie są częścią tej publikacji.

Zaloguj się w telefonie kontem z kroku 1. Otwórz **Monity**, sprawdź profil
i termin. Zezwól na powiadomienia, a w razie potrzeby na dokładne alarmy.
Wybierz przypomnienia 72/48/24 h. Po pobraniu telefon planuje alarmy lokalnie;
system Android i ustawienia baterii mogą opóźniać odświeżanie.

Przepływ terminów: **Brudnopis → Firestore → telefon**. Edycja planu,
ukrywanie profili i wybór przypomnień zapisują się w telefonie. Nie zmieniają
bazy Brudnopisu. Ręczna data w telefonie nie przelicza rekompensat.
Monity korzystają z Firestore; nie wymagają Cloud Functions ani Cloud Storage.
Sprawdzaj aktualne limity i opłaty własnego projektu.

## 5. Zlecenia Dispatchera

Zlecenia synchronizuje osobno **Dispatcher → Sync Firebase → Synchronizuj
teraz**. Telefon czyta `users/{UID}/jobs` i `users/{UID}/drivers`.
Desktopowa synchronizacja tych danych jest dwukierunkowa; nie dotyczy to
jednostronnych monitów. Telefon lokalnie archiwizuje/ukrywa zlecenia.

Dokumenty Dispatchera mogą dodatkowo korzystać z Cloud Storage. Jeśli chcesz
je synchronizować, skonfiguruj własny bucket i sprawdź wymagany plan rozliczeń.
Opcjonalny `storageBucket` w lokalnym `config.json` wskazuje nazwę Twojego
bucketu. Nie jest wymagany do samych monitów. Google Drive/Sheets nie jest
potrzebne do połączenia aplikacji Android.

## Sprawdzenie połączenia

Pusta lista: sprawdź zgodność projektu i UID, zapis profili oraz wynik
**Sync monity**. `Permission denied`: sprawdź opublikowane reguły i zalogowane
konto. Brak alarmu: sprawdź uprawnienia powiadomień, dokładnych alarmów,
ustawienia baterii i przyszłą datę terminu. Błąd konfiguracji Firebase przy
budowaniu: sprawdź nazwę pakietu i plik `google-services.json`.

Pełny klucz serwisowy, tokeny i hasła nigdy nie powinny trafiać do zgłoszeń
błędów ani screenów. Program jest na MIT, bez gwarancji; terminy i wyniki
należy samodzielnie weryfikować.

Źródła konfiguracji: [Android/Firebase](https://firebase.google.com/docs/android/setup),
[Email/Password](https://firebase.google.com/docs/auth/android/password-auth),
[Admin SDK](https://firebase.google.com/docs/admin/setup).
