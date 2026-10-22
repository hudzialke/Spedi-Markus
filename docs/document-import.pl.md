# Lokalny import dokumentu do Dispatchera

Wybierz kierowcę we flocie, następnie **Import z dokumentu** w edytorze zlecenia.
Wariant A pokazuje dokument obok formularza. Program odczytuje wskazane
fragmenty i pozwala je poprawić; nie interpretuje automatycznie warunków umowy.

1. Wybierz PDF, DOCX, DOC, XLSX, XLS, XLSM, CSV lub obraz. Możesz też wkleić
   obraz przyciskiem **Wklej obraz**. Ctrl+V działa jako wklejenie obrazu wtedy,
   gdy fokus jest w panelu podglądu; w polach tekstowych wkleja zwykły tekst.
2. Wybierz pole po prawej. W PDF zaznacz prostokąt wokół jednego bloku;
   dla Worda i Excela zaznacz tekst w podglądzie. Zaznaczanie prostokąta
   ogranicza odczyt do tego obszaru, niezależnie od kolejności tekstu w PDF.
3. Popraw treść zaznaczenia w dolnym polu. Kliknij **Przypisz i przejdź dalej**.
   Możesz też wpisać wartości bezpośrednio w pola albo pominąć pole.
4. Przy obrazach i skanach zaznacz obszar i wybierz lokalny OCR. Wynik wymaga
   sprawdzenia. Bez silnika OCR można wpisać treść ręcznie.
5. Wymagania POD zaznacz w całości: dokumenty, format, kanał / adres / portal,
   termin i sposób jego liczenia oraz ewentualną karę. Pole przechowuje treść;
   w tej wersji nie wylicza terminu i nie wysyła POD.
6. Kliknij **Przenieś do zlecenia**, rozwiń szczegóły i zatwierdź.
   Jeśli edytujesz istniejące zlecenie, program osobno pyta o zastąpienie pól.
7. W edytorze sprawdź kierowcę, daty i rozliczenie, a następnie zapisz zlecenie.
   Dokument źródłowy zostanie dołączony do lokalnej ewidencji. Braki są oznaczone
   trójkątem i opisane w pasku nad edytorem; zlecenia bez terminu rozładunku
   pozostają dostępne w historii kierowcy niezależnie od filtra miesiąca.

Numer klienta jest wpisywany w istniejące pole referencji. Oznaczenie kierowcy
i numer miesięczny z dokumentu trafiają do uwag, aby nie przypisywać kierowcy
na podstawie niejednoznacznego nazwiska. Stawka trafia do lokalnego rozliczenia
frachtu w EUR. Godzina nie jest zgadywana; daty bez godziny i okna godzinowe
wymagają ręcznego doprecyzowania. Pełne okno czasowe zachowaj w uwagach.

## Dostępność formatów

- PDF z tekstem: wbudowane Qt PDF, bez OCR i dodatkowego silnika.
- DOCX: lokalny podgląd treści i tabel, także nagłówków i stopek. Nie jest to
  wierna kopia wydruku; przy nietypowych polach i obiektach użyj PDF.
- XLSX/XLSM: openpyxl. XLS: xlrd. Zachowywane są granice komórek; w XLSX także
  komórki scalone. Makra i formuły nie są wykonywane. Formuły mają wyłącznie
  wyniki wcześniej zapisane w pliku; ich brak może dać pustą komórkę.
- Stary DOC: wymaga lokalnego LibreOffice (`soffice` w PATH albo standardowym
  katalogu Program Files). Konwersja działa w osobnym profilu z zablokowanymi
  makrami, aktualizacją łączy i odnośnikami spoza zaufanych lokalizacji.
  Bez LibreOffice zapisz plik lokalnie jako PDF lub DOCX.
- Obrazy i skany: lokalny Tesseract z danymi pol, eng, deu, fra i ita
  (polski, angielski, niemiecki, francuski, włoski). Prywatny runtime projektu
  znajduje się w `work/local-tools/tesseract/`; wydanie EXE 0.2.0 zawiera go
  w swoim katalogu `_internal/tesseract/`. Program najpierw sprawdza prywatny
  runtime, następnie PATH i Program Files/Tesseract-OCR. Pobieranie pakietów
  językowych nie jest wykonywane automatycznie podczas odczytu dokumentu.

OCR ma limit **2 GiB (2048 MiB)** pamięci zadeklarowanej procesu/grupy OCR,
egzekwowany przez Windows Job Object. Proces startuje wstrzymany, otrzymuje
limit i dopiero wtedy jest wznawiany. Jeśli limitu nie można ustawić, OCR nie
uruchamia się bez ograniczenia. Jeden odczyt naraz zapobiega sumowaniu limitów
w kilku oknach. Limit nie obejmuje całego Spedi, podglądu dokumentu ani cache
plików Windows i nie oznacza pomiaru całkowitego fizycznego RAM systemu.

OCR używa maksymalnie dwóch wątków i ma limit 45 sekund na zaznaczenie
(sprawdzenie dostępnych języków: maksymalnie 10 sekund). Obraz do OCR jest
ograniczany do 2600 pikseli na najdłuższym boku. Konwersja DOC ma limit 60 sekund.
Pliki podglądu mają limit 50 MB; obrazy 40 megapikseli.

Prywatną wersję Tesseract 5.5.3.20260724 i pięć szybkich modeli językowych
przygotowuje `python tools/setup_tesseract.py` (wymaga lokalnego 7-Zip).
Skrypt pobiera wyłącznie oprogramowanie i modele z repozytoriów Tesseract,
sprawdza SHA-256 instalatora i modeli, a także zapisuje wersję, rewizję modeli oraz ich sumy
kontrolne w `spedi-runtime.json`. Nie uruchamia instalatora, nie zmienia PATH
i nie przekazuje dokumentów. Po przygotowaniu OCR działa bez internetu.

## Prywatność i zapis

Importer nie korzysta z chmurowego OCR ani modeli AI i nie wysyła dokumentów,
zaznaczeń ani wyników OCR do OpenAI. Nie otwieraj rzeczywistych dokumentów
w podglądzie agenta ani nie wklejaj wyników do czatu.

Po przeniesieniu danych zlecenie otrzymuje trwałą flagę **tylko lokalnie**.
Firebase pomija zlecenie i jego załączniki. Wysyłka do Google Sheets,
geokodowanie, Google Maps oraz wiadomości e-mail/WhatsApp z tego zlecenia
są zablokowane. Import nie tworzy ani nie aktualizuje wspólnego klienta.
Istniejąca flaga pozostaje zachowana również przy kolejnych zapisach;
zdalna synchronizacja nie może nadpisać takiego zlecenia.

Wymagania POD oraz źródła przypisań są zapisywane w lokalnej SQLite. Przy
zapisie ewidencji powstają również `warunki_pod.txt` i `zrodla_importu.json`.
Źródła wskazują plik, stronę / arkusz i obszar zaznaczenia, gdy jest dostępny.
Ręczne poprawienie pola w importerze zachowuje odniesienie i oznacza korektę.
Późniejsze zmiany w edytorze zlecenia nie są historią wersji dokumentu.

Anulowanie importu nie zapisuje danych w bazie i nie dodaje dokumentu do
ewidencji. Dane przeniesione do edytora wymagają zwykłego zapisu zlecenia.
Przy problemie z dołączeniem źródła program zachowuje dane i prosi o ponowny
zapis; nie oznacza tego jako zakończonego importu załącznika.

## Uruchomienie ze źródeł i testy

Po instalacji zależności z pyproject.toml uruchom `python -m spedi`.
Przycisk nie pojawi się w dawnym EXE bez przebudowania programu.
Nie trzeba instalować PaddleOCR, Suryi ani lokalnego modelu językowego.

Testy `tests/test_document_import.py` korzystają wyłącznie z fikcyjnych danych.
`python tools/preview_document_import.py` tworzy fikcyjny PDF i podglądy
interfejsu w `work/document-import-preview/`, bez otwierania bazy użytkownika.
