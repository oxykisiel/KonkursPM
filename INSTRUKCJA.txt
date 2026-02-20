================================================================================
  AGENT KONKURSÓW PORTALMEDIALNY.PL — INSTRUKCJA OBSŁUGI
  Wersja: 2026-02-08
================================================================================

SPIS TREŚCI
────────────
  1. Opis projektu
  2. Wymagania systemowe
  3. Instalacja krok po kroku (Windows)
  4. Konfiguracja (config.json)
  5. Uruchamianie
  6. Parametry wiersza poleceń
  7. Integracja z LLM (Gemini / OpenAI)
  8. Raport HTML
  9. Struktura plików
 10. Rozwiązywanie problemów
 11. Uwagi i ograniczenia


================================================================================
  1. OPIS PROJEKTU
================================================================================

Agent automatycznie bierze udział w konkursach na stronie
https://portalmedialny.pl/konkursy/

Co robi skrypt:
  1. Skanuje listę konkursów z paginacją (wiele stron)
  2. Zbiera pary ARTYKUŁ ↔ FORMULARZ KONKURSOWY
  3. Odczytuje pytanie konkursowe i generuje odpowiedź:
     - pytania „Ile lat temu…?" → oblicza różnicę lat z mapy znanych dat
     - pytania „Podpowiedź na poprzedniej stronie" → wyciąga 3 kroki
       z artykułu za pomocą heurystyk (listy, akapity, nagłówki)
     - fallback: szablony kroków (projektowanie/testowanie/wdrażanie)
     - fallback: zapytanie LLM (Gemini lub OpenAI) — opcjonalne
  4. Wypełnia formularz (Imię, Nazwisko, Email, Miasto)
  5. Zaznacza zgody RODO
  6. Obsługuje CAPTCHA (pauza / oczekiwanie / pominięcie)
  7. Klika „Wyślij"
  8. Loguje wyniki do CSV i generuje raport HTML

Mechanizmy anti-detekcji:
  - Losowy User-Agent (Chrome, Firefox, Edge, Safari)
  - Losowa rozdzielczość ekranu
  - Stealth JS (ukrywanie webdriver)
  - Symulacja zachowania człowieka (scroll, ruch myszy)
  - Wpisywanie znak po znaku z losowym opóźnieniem
  - Losowe pauzy 15-45s między konkursami

Zabezpieczenia:
  - Limit dzienny wysyłek (domyślnie 10)
  - Pomijanie już wysłanych konkursów
  - Wykrywanie wygasłych konkursów
  - Retry przy błędach sieciowych


================================================================================
  2. WYMAGANIA SYSTEMOWE
================================================================================

  - Windows 10 lub 11 (64-bit)
  - Python 3.12 lub nowszy (testowane na 3.14)
  - Dostęp do internetu
  - ok. 500 MB wolnego miejsca (na przeglądarki Playwright)


================================================================================
  3. INSTALACJA KROK PO KROKU (WINDOWS)
================================================================================

KROK 1: Zainstaluj Pythona
──────────────────────────
  Jeśli nie masz Pythona:
  1. Wejdź na https://www.python.org/downloads/
  2. Pobierz najnowszą wersję (np. Python 3.14)
  3. Uruchom instalator
  4. WAŻNE: Zaznacz checkbox "Add Python to PATH"
  5. Kliknij "Install Now"

  Sprawdź czy działa — otwórz PowerShell i wpisz:

    py --version

  Powinieneś zobaczyć np. "Python 3.14.2"


KROK 2: Otwórz folder projektu
───────────────────────────────
  Otwórz PowerShell (kliknij prawym w Menu Start → Windows PowerShell)
  albo Terminal Windows i przejdź do folderu projektu:

    cd D:\Programy\KonkursPM


KROK 3: Utwórz środowisko wirtualne (jednorazowo)
──────────────────────────────────────────────────
  py -m venv .venv

  Aktywuj je:

    .\.venv\Scripts\Activate.ps1

  Jeśli pojawi się błąd o polityce wykonywania skryptów:

    Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned

  i spróbuj ponownie aktywować.

  Po aktywacji powinieneś widzieć (.venv) na początku linii:

    (.venv) PS D:\Programy\KonkursPM>


KROK 4: Zainstaluj wymagane pakiety
────────────────────────────────────
  pip install playwright tzdata

  Następnie zainstaluj przeglądarki Playwright:

  python -m playwright install

  To pobierze Chromium, Firefox i WebKit (~500 MB).


KROK 5 (opcjonalnie): Zainstaluj pakiet LLM
─────────────────────────────────────────────
  Jeśli chcesz używać Gemini:

    pip install google-generativeai

  Jeśli chcesz używać OpenAI:

    pip install openai


KROK 6: Skonfiguruj dane w config.json
───────────────────────────────────────
  Otwórz plik config.json w edytorze tekstu i uzupełnij swoje dane.
  Szczegóły w rozdziale 4.


KROK 7: Testowe uruchomienie
─────────────────────────────
  Najpierw uruchom w trybie testowym (bez wysyłania):

    python pm_agent_multi.py --dry-run true

  Jeśli wszystko działa, uruchom normalnie:

    python pm_agent_multi.py


================================================================================
  4. KONFIGURACJA (config.json)
================================================================================

Plik config.json zawiera wszystkie ustawienia. Edytuj go dowolnym edytorem
tekstowym (Notatnik, VS Code, Notepad++).

Struktura pliku:

  {
      "uczestnik": {
          "imie": "Twoje imię",
          "nazwisko": "Twoje nazwisko",
          "email": "twoj@email.pl",
          "miasto": "Twoje miasto"
      },
      "ustawienia": {
          "max_pages": 3,         ← ile stron listy konkursów skanować
          "max_contests": 10,     ← ile konkursów przetwarzać na raz
          "max_daily": 10,        ← limit wysyłek dziennie
          "headless": false,      ← true = bez okna przeglądarki
          "interactive": false,   ← true = pauza przy ręcznych pytaniach
          "captcha_mode": "wait", ← "wait", "pause" lub "skip"
          "save_artifacts": false,← true = zapis screenshotów/HTML
          "dry_run": false        ← true = wypełnia bez wysyłania
      },
      "llm": {
          "provider": "gemini",   ← "gemini" lub "openai"
          "api_key": "",          ← wklej tu klucz API
          "model": "gemini-2.0-flash",
          "enabled": false        ← true = włącz LLM
      },
      "urls": {
          "list_url": "https://portalmedialny.pl/konkursy/",
          "seed_forms": [...]     ← zapasowe URL-e konkursów
      }
  }


================================================================================
  5. URUCHAMIANIE
================================================================================

Zawsze najpierw aktywuj środowisko wirtualne:

  cd D:\Programy\KonkursPM
  .\.venv\Scripts\Activate.ps1

Podstawowe uruchomienie:

  python pm_agent_multi.py

Tryb testowy (wypełnia formularz, ale nie wysyła):

  python pm_agent_multi.py --dry-run true

Tylko raport HTML (bez uruchamiania agenta):

  python pm_agent_multi.py --report

Uruchomienie z większą liczbą konkursów:

  python pm_agent_multi.py --max-contests 15 --max-daily 15

Tryb bez okna przeglądarki (cichy):

  python pm_agent_multi.py --headless true

Pełne uruchomienie z diagnostyką:

  python pm_agent_multi.py --max-contests 10 --save-artifacts true --interactive true


================================================================================
  6. PARAMETRY WIERSZA POLECEŃ
================================================================================

  Parametr            Domyślnie    Opis
  ─────────────────── ──────────── ──────────────────────────────────────
  --max-pages          3           Ile stron listy konkursów skanować
  --max-contests       5           Ile konkursów przetwarzać na raz
  --max-daily          10          Limit wysyłek dziennie
  --headless           false       Tryb bez okna przeglądarki
  --interactive        false       Pauza przy ręcznych pytaniach/CAPTCHA
  --captcha-mode       wait        Strategia CAPTCHA: wait/pause/skip
  --save-artifacts     false       Zapis screenshotów i HTML
  --dry-run            false       Tryb testowy (bez wysyłania)
  --report             (flag)      Tylko generuj raport HTML

  Parametry CLI nadpisują ustawienia z config.json.


================================================================================
  7. INTEGRACJA Z LLM (GEMINI / OPENAI)
================================================================================

LLM jest używany jako ostatni fallback — gdy skrypt nie potrafi sam
odpowiedzieć na pytanie konkursowe.

Konfiguracja Gemini:
  1. Wejdź na https://aistudio.google.com/apikey
  2. Utwórz klucz API
  3. W config.json ustaw:
     "llm": {
         "provider": "gemini",
         "api_key": "TUTAJ_WKLEJ_KLUCZ",
         "model": "gemini-2.0-flash",
         "enabled": true
     }
  4. Zainstaluj pakiet:  pip install google-generativeai

Konfiguracja OpenAI:
  1. Wejdź na https://platform.openai.com/api-keys
  2. Utwórz klucz API
  3. W config.json ustaw:
     "llm": {
         "provider": "openai",
         "api_key": "sk-TUTAJ_WKLEJ_KLUCZ",
         "model": "gpt-4o-mini",
         "enabled": true
     }
  4. Zainstaluj pakiet:  pip install openai


================================================================================
  8. RAPORT HTML
================================================================================

Po każdym uruchomieniu agenta automatycznie generuje się plik raport.html
w folderze projektu.

Raport zawiera:
  - Statystyki: ile wysłanych, błędów, wygasłych
  - Tabelę wszystkich zgłoszeń z datą, pytaniem, odpowiedzią i statusem
  - Kolorowe statusy (zielony = wysłane, czerwony = błąd, żółty = pominięte)

Aby wygenerować raport bez uruchamiania agenta:

  python pm_agent_multi.py --report

Otwórz raport.html w przeglądarce (np. dwuklik na plik).


================================================================================
  9. STRUKTURA PLIKÓW
================================================================================

  D:\Programy\KonkursPM\
  │
  ├── pm_agent_multi.py    ← główny skrypt agenta
  ├── config.json          ← konfiguracja (dane, ustawienia, LLM)
  ├── INSTRUKCJA.txt       ← ten plik
  ├── Vol1.docx            ← archiwum konwersacji o projekcie
  │
  ├── pm_agent_log.csv     ← log wszystkich operacji (tworzony automatycznie)
  ├── raport.html          ← raport HTML (tworzony automatycznie)
  │
  ├── artifacts/           ← screenshoty/HTML diagnostyczne (opcjonalne)
  │
  └── .venv/               ← środowisko wirtualne Pythona


================================================================================
  10. ROZWIĄZYWANIE PROBLEMÓW
================================================================================

PROBLEM: "No module named 'playwright'"
ROZWIĄZANIE:
  Aktywuj środowisko wirtualne i zainstaluj:
    .\.venv\Scripts\Activate.ps1
    pip install playwright
    python -m playwright install

PROBLEM: "ZoneInfoNotFoundError: No time zone found with key Europe/Warsaw"
ROZWIĄZANIE:
    pip install tzdata

PROBLEM: "Nie można uruchomić skryptu — polityka wykonywania"
ROZWIĄZANIE:
    Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned

PROBLEM: Agent nie znajduje żadnych konkursów
ROZWIĄZANIE:
  - Sprawdź czy strona https://portalmedialny.pl/konkursy/ działa
  - Uruchom z --max-pages 5 żeby skanować więcej stron
  - Sprawdź log pm_agent_log.csv — może wszystkie już wysłane

PROBLEM: CAPTCHA blokuje wysyłanie
ROZWIĄZANIE:
  Uruchom w trybie interaktywnym:
    python pm_agent_multi.py --interactive true --captcha-mode pause
  Skrypt się zatrzyma na CAPTCHA — rozwiąż ręcznie i zamknij okno pause.

PROBLEM: "No module named 'google.generativeai'" / "No module named 'openai'"
ROZWIĄZANIE:
    pip install google-generativeai    (dla Gemini)
    pip install openai                 (dla OpenAI)

PROBLEM: Skrypt mówi "[LIMIT] Dzisiejszy limit osiągnięty"
ROZWIĄZANIE:
  Zwiększ limit:
    python pm_agent_multi.py --max-daily 20
  lub ustaw w config.json → ustawienia → max_daily


================================================================================
  11. UWAGI I OGRANICZENIA
================================================================================

  - Skrypt działa z konkursami na portalmedialny.pl — inne strony nie
    są wspierane.
  - CAPTCHA wymaga ręcznego rozwiązania (lub pominięcia).
  - LLM wymaga klucza API i może generować koszty (Gemini ma darmowy
    limit, OpenAI jest płatne).
  - Skrypt jest narzędziem wspomagającym — odpowiedzi mogą wymagać
    weryfikacji.
  - Log CSV (pm_agent_log.csv) rośnie z czasem — możesz go wyczyścić
    lub usunąć jeśli chcesz zacząć od nowa.
  - Aby wyczyścić historię wysłanych i zacząć od nowa, usuń plik
    pm_agent_log.csv.


================================================================================
  SZYBKI START (kopiuj-wklej do PowerShell)
================================================================================

  # Jednorazowa instalacja:
  cd D:\Programy\KonkursPM
  py -m venv .venv
  .\.venv\Scripts\Activate.ps1
  pip install playwright tzdata
  python -m playwright install

  # Codzienne uruchomienie:
  cd D:\Programy\KonkursPM
  .\.venv\Scripts\Activate.ps1
  python pm_agent_multi.py

================================================================================
