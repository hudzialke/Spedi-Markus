# Optymalizacja Spedi — 29 października 2026

Zmiany ograniczają czas odczytu historii, ponowne obliczenia, koszt rysowania oraz
pracę wykonywaną w wątku interfejsu. Przygotowana wersja nie została zainstalowana
ani uruchomiona na danych użytkownika. Testy korzystały z wygenerowanych dokumentów
i osobnych baz SQLite, a okna Qt działały przez platformę `offscreen`.

## Co zmieniono

- Lista kierowców pobiera same metadane. Pełna historia jest odczytywana dopiero
  po wyborze profilu. Miesiąc jest wybierany zapytaniem zakresowym z zachowaniem
  potwierdzonych aktywności przechodzących przez granicę miesiąca.
- Analiza jazdy i pracy nocnej korzysta z indeksów przedziałów oraz współdzielonych
  sum dobowych. Nakładanie korekt omija nieprzecinające się fragmenty historii.
  Kopiowanie scenariuszy zachowuje niezależność edytowanych bloków.
- Oś czasu przechowuje wyniki przeliczenia współrzędnych i grupuje elementy według
  tygodnia. Malowanie fragmentu okna pomija niewidoczne wiersze. Klucze pamięci
  podręcznej rozróżniają obie godziny podczas jesiennej zmiany czasu.
- Importy, duże odczyty i zapisy profili oraz eksporty PDF/Excel działają w tle.
  Anulowanie importu następuje między zakończonymi plikami. Zamykanie aplikacji
  czeka na trwające zapisy bez blokowania pętli interfejsu.
- Ewidentor i Dispatcher powstają przy pierwszym wejściu do danego modułu.
  Kolejne wejścia wykorzystują już istniejący widok.
- Automatyczne wpisy MiLo są zapisywane w jednej transakcji; przyporządkowanie
  krajów jest liczone raz. Połączenia SQLite są jawnie zamykane.
- Podgląd CSV/XLS/XLSX dzieli duże tabele na strony. Wszystkie komórki pozostają
  dostępne w wyborze stron, a scalone pola otrzymują kontynuację na granicach.
  Ograniczenie rozmiaru pojedynczej tabeli usuwa wielosekundowy koszt jej układania.
- Zakończone zadania OCR/PDF, okna pomocnicze i zadania geokodowania są zwalniane.
  Geokodowanie łączy powtarzane żądania i ignoruje nieaktualne odpowiedzi.
- Zamknięte silniki PDF trafiają do puli maksymalnie czterech obiektów. Próba
  izolowana wykazała wzrost natywnej pamięci przy ciągłym tworzeniu silników Qt
  6.11.2; ponowne użycie usuwało ten trend. Każdy silnik ma wyłącznego właściciela
  i wraca z wątku roboczego przed jego zakończeniem. Odczyt przez bufor o limicie
  50 MB zamyka uchwyt pliku od razu; bufor jest opróżniany po zakończeniu podglądu.
- Nieudane wczytanie kolejnego profilu zachowuje poprzednią tożsamość i edycje,
  zamiast pozostawiać poprzednią oś czasu przypisaną do nowego kierowcy.
- Pakowanie Qt korzysta ze standardowych hooków PyInstallera, zamiast dołączać
  cały PySide6. Same składniki Qt zmalały z 662 688 707 do 96 412 640 bajtów.

## Wyniki pomiarów

Pomiary dotyczą tej maszyny, danych syntetycznych i wskazanych operacji, nie całego
czasu pracy programu. Python 3.14.2, PySide6 6.11.2. Pomiar osi czasu używa Segoe UI.
Medianę liczy narzędzie `tools/benchmark_performance.py`; próbki i ich maksimum
są zapisane w JSON. Wyniki końcowego powtórzenia znajdują się poniżej.

- Odczyt miesiąca z historii 105 120 segmentów: **567,6 → 11,9 ms** (około 48×).
- Wybór profilu: wcześniejsze pobieranie pełnej historii wszystkich profili
  298,7 ms; nowa ścieżka pobierająca metadane 0,9 ms. Pełne `list_profiles()`
  zachowuje swoje znaczenie i koszt: około 304 ms.
- Analiza pracy nocnej za rok: **1075,6 → 123,1 ms** (około 8,7×).
- Walidacja realistycznego rocznego grafiku: **568,8 → 44,7 ms** (około 13×).
- Walidacja gęstego scenariusza 16 tygodni: **3294,3 → 94,7 ms** (około 35×).
- Rysowanie całej osi czasu: **2532,9 → 176,5 ms** (około 14×).
- Rysowanie widocznego fragmentu osi: **2462,7 → 40,6 ms** (około 61×).
- Pierwszy podgląd CSV zawierającego 100 000 komórek: **14 283 → 218 ms**
  (około 66×, pojedyncza próba porównawcza). Dane są dostępne na 100 stronach.

Dodatkowe, wcześniej wykonane próby:

- Wczytanie skorygowanej historii 17 520 segmentów i 1095 korekt: 4594 → 402 ms.
- Wybór miesiąca z 12 000 zleceń Dispatchera: 522 → 40 ms.
- Miesiąc Ewidentora z 2976 segmentami i 186 pobytami MiLo: pierwszy odczyt
  1632 → 193 ms, ponowny odczyt 917 → 118 ms. Te pomiary zawierają narzut profilera.
- Import, konstrukcja głównego okna i pierwszy rysunek po utworzeniu QApplication:
  mediana 1447 → 532 ms w osobnych procesach. Koszt pierwszego wejścia do Ewidentora
  i Dispatchera został odroczony (około 196 i 485 ms); kolejne wejścia około 6–7 ms.
  Ten pomiar nie obejmuje całego zimnego startu EXE.

## Weryfikacja

- Pełny zestaw: **308 testów zaliczonych**, 26,81 s. Dodatkowe testy obejmują
  zakresy miesięcy, kolejność sumowania, zagnieżdżone przedziały, niezależne kopie,
  częściowy repaint, zmianę czasu, anulowanie, awarie odczytu i cykle życia wątków.
- **400 wyników porównawczych zgodnych z pierwotnym kodem**: walidacja, praca
  nocna, nakładanie korekt i podział czasu między kraje. SHA256 obu zbiorów:
  `5a14184c1990701d128c89db03d92cc518fe8f26ac5937b6c4bab949a0506486`.
- Cztery obrazy osi czasu (wiosna/jesień, PL/UTC) są identyczne bajt po bajcie
  względem wersji pierwotnej przy tym samym kroju pisma.
- Testy puli PDF sprawdzają wyłączność, powrót do właściwego wątku, anulowanie,
  błędny dokument, ponowne użycie okna, limit puli oraz możliwość zmiany nazwy
  i usunięcia pliku po odczycie.

- Nowa kompilacja EXE przeszła **10 kontroli pakietu**, obejmujących podgląd
  PDF i zwolnienie uchwytów pliku, oba czytniki Excela, strony dużego arkusza,
  lokalny OCR z pięcioma językami i limitem procesu, odczyt DDD, moduły ładowane
  na żądanie, operacje w tle i oba generatory raportów PDF.
- **1004 cykle** otwierania dwóch różnych PDF, zmiany stron, zaznaczania tekstu,
  Watchlisty i nawigacji: bez awarii i bez pozostałych wątków roboczych.
  Prywatna pamięć procesu: 62,69 → 64,54 MiB; od 700. do 1000. cyklu 64,54 MiB.
  W porównywalnej wcześniejszej próbie było 60,86 → 80,39 MiB po 1000 cykli.
  Pula pozostawała ograniczona, a zamknięte bufory PDF miały zerowy rozmiar.
- W długiej próbie proces miał obniżony priorytet i współdzielił komputer
  z kompilacją/testami: p95 dodatkowego opóźnienia zegara wynosiło 26,88 ms,
  maksimum 1182,81 ms. To pomiar pętli zdarzeń, nie czasu pojedynczej klatki;
  nadal wystąpiły sporadyczne opóźnienia pod obciążeniem.

- Dodatkowe 102 cykle bez równoległej kompilacji i benchmarku agenta również
  przeszły; pamięć 62,48 → 62,68 MiB. Zegar testowy nadal zarejestrował maksimum
  2503,92 ms (p95 26,43 ms). Nie jest to dowód zatrzymania obliczeń aplikacji:
  test sam oczekuje przez `QThread.msleep(1)` i działa z obniżonym priorytetem
  podczas pracy użytkownika. W osobnej, profilowanej próbie 103 cykli największy
  skok 628,22 ms odpowiadał 0 ms czasu CPU procesu, a oczekiwanie `msleep`
  zajęło 23,3 z 31,3 s. Ocena płynności rzeczywistego okna wymaga odrębnego
  pomiaru podczas jego używania; nie przejmowano w tym celu ekranu użytkownika.

Reguły rozliczeniowe nie zostały zmienione. Próby porównawcze stanowią kontrolę
regresji i nie zastępują sprawdzenia na każdym możliwym rzeczywistym pliku DDD.
Pozostałe opóźnienia mogą zależeć od rozmiaru danych, dysku, OCR, usług sieciowych
i obciążenia komputera. Nie ma podstaw do obietnicy całkowitego braku przycięć.

## Powtarzanie pomiarów

W katalogu projektu, z użyciem jego środowiska Python:

```powershell
$env:TEMP = Join-Path $PWD 'work/optimization-20261029'
$env:TMP = $env:TEMP
$env:QT_QPA_PLATFORM = 'offscreen'
$env:SPEDI_DATA_DIR = Join-Path $PWD 'work/optimization-20261029/recheck-data'
$env:SPEDI_DISPATCHER_DATA_DIR = Join-Path $PWD 'work/optimization-20261029/recheck-dispatcher'
.venv/Scripts/python.exe -m pytest tests -q
.venv/Scripts/python.exe tools/benchmark_performance.py --output work/recheck.json
```

Materiały robocze w `work/optimization-20261029/` zawierają pierwotny snapshot
źródeł, plik zmian, logi testów, porównania JSON, obrazy oraz oddzielną kompilację.
Oryginalne katalogi dystrybucji i uruchomiona aplikacja nie zostały podmienione.
