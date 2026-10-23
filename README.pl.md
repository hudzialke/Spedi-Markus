# Spedi

[English](README.md) · **Polski**

## Pobierz i zainstaluj (Windows 64-bit)

[**Pobierz instalator Spedi**](https://github.com/hudzialke/Spedi-Markus/releases/latest/download/Spedi-Setup.exe)

Uruchom pobrany `Spedi-Setup.exe`, przejdź przez instalację, a następnie otwórz
Spedi ze skrótu na pulpicie. Python, CMD i PowerShell nie są potrzebne.
Instalator zawiera lokalny OCR: polski, angielski, niemiecki, francuski i włoski.
OCR ma limit pamięci 2 GiB i przetwarza jedno zaznaczenie naraz.
[Wszystkie wydania i wersja przenośna ZIP](https://github.com/hudzialke/Spedi-Markus/releases).
Przy aktualizacji zamknij program i uruchom nowy instalator. Bazy i dokumenty
użytkownika są przechowywane osobno od plików aplikacji.

Program desktopowy łączący Brudnopis OT, Ewidentor, DDD Analyzer i Dispatcher:
planowanie odpoczynków, analizę danych tachografu, ewidencję czasu pracy,
zlecenia oraz rozliczenie frachtu. W `mobile/` znajduje się **Spedi Android App**
(wcześniej MarkusDispatcher): zlecenia, terminy i lokalne monity w telefonie.

**Autor: Patryk Prośniak (hudzialke).** Program powstał przy wykorzystaniu różnych
modeli sztucznej inteligencji (AI). Autorstwo komponentów zewnętrznych opisuje
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Zobacz, jak działa Spedi

Import dokumentu obok formularza, zapis wymagań POD, lista OT,
analiza aktywności DDD, ewidencja miesiąca i rozliczenie frachtu.
Wszystkie przykłady wykorzystują całkowicie fikcyjne dane.

![Import dokumentu do zlecenia w Spedi](docs/showcase/assets/07-import.png)

[**Zobacz pokaz funkcji i animację importu →**](docs/showcase/README.pl.md)

## Licencja i odpowiedzialność

Projekt udostępniono na [licencji MIT](LICENSE). Można go używać, kopiować,
modyfikować i rozpowszechniać, również komercyjnie, zachowując informację
o autorstwie i treść licencji.

**Program jest udostępniany bez gwarancji. Autor nie ponosi odpowiedzialności
za jego działanie, błędy, utratę danych ani skutki wykorzystania, w zakresie
dopuszczonym przez prawo.** Korzystasz z programu na własną odpowiedzialność.
Sprawdzaj wyniki i wykonuj kopie swoich danych. Program nie zastępuje porady
prawnej ani samodzielnej kontroli zgodności z przepisami. Zmiana języka
interfejsu nie zmienia stosowanych reguł ani strefy czasu danych tachografu.

## Uruchomienie

Python 3.12 lub nowszy. Wszystkie moduły i parser są w tym repozytorium.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,sync]"
python -m spedi
```

W systemach Linux/macOS aktywuj środowisko przez `source .venv/bin/activate`.
Głównym środowiskiem programu jest Windows; połączenie Google korzysta
z zabezpieczeń Windows DPAPI.

## Język

W prawym górnym rogu wybierz **Polski** lub **English**, zapisz swoją pracę
i uruchom Spedi ponownie. Ustawienie jest zapamiętywane lokalnie. Interfejs
desktopowy ma katalog tłumaczeń `spedi/locales/en.json`; teksty raportów,
szczegółowe uzasadnienia obliczeń i aplikacja Android mogą nadal zawierać
polskie komunikaty. Treść wpisana przez użytkownika nie jest tłumaczona.
Informacje o autorze, AI i licencji są też w oknie **O programie**.

## Dane i integracje

Repozytorium zawiera źródła i syntetyczne testy. Nie zawiera baz kierowców,
DDD, dokumentów przewozowych, raportów, haseł, tokenów, prywatnych kluczy,
konfiguracji Firebase ani wskazań prywatnych arkuszy lub folderów Google Drive.

Dane Brudnopisu/Ewidentora trafiają do katalogu użytkownika Spedi, a dane
Dispatchera do katalogu MarkusDispatcher. Ścieżki wynikają z `platformdirs`;
do testów można wskazać `SPEDI_DATA_DIR` i `SPEDI_DISPATCHER_DATA_DIR`.
Stare bazy znalezione lokalnie mogą zostać zaimportowane przy pierwszym starcie.

Integracje wymagają własnej konfiguracji. Konfiguracja, logowanie i dane
pozostają poza repozytorium. Google Sheets jest opcjonalne i niezależne od
połączenia telefonu. **Nie umieszczaj konta serwisowego Firebase w APK.**

- [Połączenie Spedi z Androidem — instrukcja krok po kroku](docs/android-connection.pl.md)
- [Spedi Android App](mobile/README.md)
- [Konfiguracja Google Sheets](docs/google-sheets.md)
- [Lokalny import dokumentów i wymagań POD do Dispatchera](docs/document-import.pl.md)
- [Zasady publikacji bez danych](docs/publication.md)

## Testy i budowanie

```powershell
python -m pytest -q
python tools/public_export.py --check .
.\build.ps1
```

Wynik budowania Windows: `dist/Spedi/Spedi.exe`. Instalator:
`installer/Spedi.iss` (Inno Setup). Android buduje się osobno, po skonfigurowaniu
własnego Firebase. Gotowe binaria z wcześniejszej prywatnej konfiguracji
nie są częścią tego repozytorium.
