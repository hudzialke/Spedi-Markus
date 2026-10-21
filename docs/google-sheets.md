# Optional Google Sheets connection

Google Sheets is independent of the Android/Firebase connection.
Each installation uses its own Google Cloud project and Google account.
No spreadsheet ID, Drive folder, OAuth client, API key or token is provided.

In your own Google Cloud project, enable Google Sheets API, Google Drive API
and Google Picker API. Configure the OAuth consent screen and a Desktop OAuth
client. If the project is in testing mode, add the signing-in account as a
test user. Download the client JSON and keep it outside this repository.

In Dispatcher, open the freight settlement panel and **Google configuration**.
Select your Desktop OAuth JSON, enter your own Google Picker API key and
Google Cloud project number, then save. Use **Connect to Google**, complete
the browser sign-in and select or create a spreadsheet and driver tab.
Assign monthly files using **Monthly spreadsheet…**. Sending/updating a
settlement requires its own action in Dispatcher.

On Windows, connection data is protected using DPAPI in the user's local
Dispatcher data folder. Do not copy the protected connection file into Git,
exports, bug reports or APKs. The application asks for `drive.file` access.
The legacy standalone DDD Analyzer Sheets exporter is not part of Spedi's
interface and requires a caller-supplied destination if used separately.

Konfiguracja po polsku: w rozliczeniu frachtu wybierz **Konfiguracja Google**,
wskaż własny plik OAuth typu „Aplikacja komputerowa”, wpisz klucz Picker
i numer tego samego projektu. Zapisz i kliknij **Połącz z Google**. Następnie
wybierz arkusz miesiąca i zakładkę kierowcy. Żadne dane logowania nie są
częścią repozytorium. Połączenie z Google działa z Windows DPAPI.
