# Spedi w praktyce

[English](README.md) · **Polski**

Brudnopis OT, Ewidentor, analiza DDD i Dispatcher w jednej aplikacji na Windows: odpoczynki, ewidencja czasu pracy, zlecenia transportowe, dokumenty POD i rozliczenia frachtu.

> Prezentacja **Spedi 0.2.1**. Zrzuty przedstawiają rzeczywistą aplikację z całkowicie fikcyjnymi danymi, wygenerowanymi od zera. Nie wykorzystano baz, dokumentów ani danych rzeczywistych kierowców i klientów.

## Import danych z dokumentu

Otwórz PDF, Word, Excel, CSV lub obraz ze schowka. Wybierz pole, zaznacz odpowiedni tekst albo obszar, sprawdź treść i przypisz ją do formularza. Następnie przenieś dane do zlecenia i zapisz.

![Pokaz przypisywania załadunku, rozładunku i wymagań POD z dokumentu](assets/import-demo.gif)

Przykład pokazuje dwa adresy w sąsiednich kolumnach PDF. Zaznaczenie lewego obszaru przenosi adres załadunku; prawego — adres rozładunku. Warunki POD trafiają do osobnego pola. Pozostałe wartości na końcu pokazu uzupełniono ręcznie.

![Dokument i formularz z pełnymi wymaganiami POD](assets/07-import.png)

PDF z warstwą tekstową można odczytać bez OCR. Dla skanów i obrazów dostępny jest lokalny Tesseract z językami polskim, angielskim, niemieckim, francuskim i włoskim. Rozpoznawanie działa pojedynczo, z limitem 2 GiB pamięci dla procesu OCR i dwoma wątkami. Limit dotyczy OCR, nie całej aplikacji.

To import wspomagany przez użytkownika: wartości można sprawdzić i poprawić przed zapisaniem. Dane z lokalnego importu mają zablokowaną wysyłkę i synchronizację.

## Dispatcher i warunki POD

![Zlecenie transportowe: status, daty i oddzielne adresy](assets/05-dispatcher.png)

Przypisz zlecenie do kierowcy, zachowaj klienta, daty, referencje i dokumenty. Historia zleceń jest dostępna według miesiąca rozładunku.

![Pełne warunki POD zapisane przy zleceniu](assets/06-pod.png)

W fikcyjnym zleceniu DEMO/1042/2026 wymagane są podpisane CMR i WZ w jednym PDF, przesłane w ciągu 24 godzin na `pod@example.invalid`. Opisana opłata za opóźnienie wynosi 25 EUR. Pełna treść pozostaje przy zleceniu do sprawdzenia.

**Zapisanie warunków POD nie uruchamia automatycznej wysyłki ani automatycznego przypomnienia wyliczonego z ich treści.** Zdjęcia POD można zapisywać jako PDF w lokalnym archiwum.

## Brudnopis OT

![Oś aktywności, odpoczynki i widoczne naruszenia](assets/01-brudnopis.png)

Planuj i edytuj aktywności na osi wielu tygodni, sprawdzaj odpoczynki, rekompensaty, ostrzeżenia i naruszenia. Dostępne są profile DDD oraz ręczne profile bez pliku źródłowego. Przykładowy scenariusz celowo pokazuje także problemy wymagające oceny.

![Lista OT z terminami dla wybranych kierowców](assets/02-lista-ot.png)

Lista OT zestawia dla wybranych kierowców terminy rozpoczęcia odpoczynku i powrotu do domu lub bazy, wymagane rekompensaty oraz uzasadnienia. Niepełna historia pozostaje oznaczona jako **BRAK DANYCH**.

## Analiza DDD

![Dzienna oś aktywności i podsumowanie dni](assets/03-ddd.png)

Sprawdzaj dzienne aktywności, ich dokładne godziny, podsumowania oraz analizy odpoczynków i pory nocnej. Zrzut przedstawia **wygenerowane aktywności w profilu demonstracyjnym**; nie użyto rzeczywistego pliku DDD.

## Ewidentor

![Kalendarz miesiąca z urlopami i podsumowaniem](assets/04-ewidentor.png)

![Szczegółowa lista dni ewidencji](assets/04b-ewidentor-lista.png)

Przenieś aktywności z Brudnopisu do ewidencji miesiąca, dodaj nieobecności i sprawdź poszczególne dni. Program pokazuje podsumowania czasu pracy i wyliczenia wynagrodzenia oraz pozwala eksportować ewidencję do PDF. Kwoty i kurs w przykładzie są demonstracyjne.

## Rozliczenie frachtu

![Dolot, kilometry ładowne, stawka i koszty](assets/08-rozliczenie.png)

Wprowadź kilometry puste i ładowne, stawkę oraz koszty. Panel oblicza łączny dystans, EUR/km i kwotę po kosztach. Kwoty są prywatne i nie trafiają do instrukcji kierowcy. W przykładzie użyto zapisu lokalnego; niczego nie wysłano do Google Sheets.

## Dodatkowe możliwości

- Polski i angielski interfejs; niektóre raporty i szczegółowe objaśnienia mogą pozostać po polsku.
- Lokalne archiwum dokumentów, raporty PDF i eksport historii zleceń do Excel.
- Opcjonalne Google Sheets oraz połączenie z aplikacją Android po skonfigurowaniu własnych usług. Pokaz nie przedstawia aktywnego połączenia.
- Instalator Windows uruchamiany kliknięciem, bez potrzeby korzystania z terminala.

Wyniki zależą od kompletności i poprawności wprowadzonych danych. Zrzuty służą prezentacji obsługi programu.
