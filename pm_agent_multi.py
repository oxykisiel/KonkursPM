# -*- coding: utf-8 -*-
"""
pm_agent_multi.py — agent konkursów portalmedialny.pl
Wersja: 2026-02-08
- Konfiguracja z config.json (dane osobowe, ustawienia, LLM)
- Wykrywanie wygasłych konkursów
- Integracja z LLM (Gemini/OpenAI) dla nieznanych pytań
- Raport HTML z wyników
- Anti-detekcja (losowy UA, viewport, stealth JS, human delays)
- Obsługa CAPTCHA (pause/wait/skip) i tryb dry-run
Wymagania:
  pip install playwright
  playwright install
  pip install google-generativeai   # opcjonalnie, dla Gemini
  pip install openai                # opcjonalnie, dla OpenAI
"""
from __future__ import annotations
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
import re
import csv
import os
import json
import random
import argparse
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import urljoin

# ===== KONFIGURACJA =====
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

def load_config() -> dict:
    """Załaduj konfigurację z config.json. Przy braku pliku użyj domyślnych."""
    defaults = {
        "uczestnik": {"imie": "Marcin", "nazwisko": "Kisielewski", "email": "oxykisiel@gmail.com", "miasto": "Słupsk"},
        "ustawienia": {"max_pages": 3, "max_contests": 10, "max_daily": 10, "headless": False,
                       "interactive": False, "captcha_mode": "wait", "save_artifacts": False, "dry_run": False},
        "llm": {"provider": "gemini", "api_key": "", "model": "gemini-2.0-flash", "enabled": False},
        "urls": {"list_url": "https://portalmedialny.pl/konkursy/", "seed_forms": []}
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            # Merge z defaults
            for section in defaults:
                if section in cfg:
                    if isinstance(defaults[section], dict):
                        defaults[section].update(cfg[section])
                    else:
                        defaults[section] = cfg[section]
            print(f"[CONFIG] Załadowano z {CONFIG_FILE}")
        except Exception as e:
            print(f"[CONFIG] Błąd odczytu {CONFIG_FILE}: {e} — używam domyślnych")
    return defaults

CFG = load_config()

# ===== DANE UCZESTNIKA (z config) =====
IMIE = CFG["uczestnik"]["imie"]
NAZWISKO = CFG["uczestnik"]["nazwisko"]
EMAIL = CFG["uczestnik"]["email"]
MIASTO = CFG["uczestnik"]["miasto"]

# ===== LISTA KONKURSÓW =====
LIST_URL = CFG["urls"]["list_url"]

# ===== FALLBACK: znane formularze =====
SEED_FORMS = CFG["urls"].get("seed_forms", [])

# ===== LOG CSV =====
LOG_FILE = "pm_agent_log.csv"

def ensure_log():
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow([
                "timestamp", "contest_url", "article_url", "question", "answer", "status", "fill_stats"
            ])

def log_row(contest_url: str, article_url: str | None, q: str, a: str, status: str, fill_stats: str = ""):
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([
            datetime.now(ZoneInfo("Europe/Warsaw")).isoformat(), contest_url, article_url or "", q, a, status, fill_stats
        ])

# ===== LIMIT DZIENNY =====
SENT_STATUSES = {"SENT", "SENT_TEMPLATE", "SENT_EXTRACT", "SENT_YEARS", "SENT_LLM", "SENT_UNCONFIRMED"}

def today_local_iso() -> str:
    """Lokalna data (Europe/Warsaw) dla zliczania limitu dziennego."""
    return datetime.now(ZoneInfo("Europe/Warsaw")).date().isoformat()

def count_today_sent() -> int:
    """Liczy wpisy w LOG_FILE z dzisiejszą datą lokalną i statusem wysłania."""
    if not os.path.exists(LOG_FILE):
        return 0
    today = today_local_iso()
    cnt = 0
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            rdr = csv.reader(f)
            _ = next(rdr, None)
            for row in rdr:
                if not row or len(row) < 6:
                    continue
                timestamp = row[0]
                status = row[5]
                date_part = (timestamp.split("T")[0]).strip()
                if date_part == today and status in SENT_STATUSES:
                    cnt += 1
    except Exception:
        pass
    return cnt

def get_already_sent_urls() -> set[str]:
    """Zwraca zbiór URL-i konkursów, które już zostały wysłane (kiedykolwiek)."""
    urls: set[str] = set()
    if not os.path.exists(LOG_FILE):
        return urls
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            rdr = csv.reader(f)
            _ = next(rdr, None)
            for row in rdr:
                if not row or len(row) < 6:
                    continue
                status = row[5]
                if status in SENT_STATUSES:
                    urls.add(row[1])
    except Exception:
        pass
    return urls

# ===== ANTI-DETEKCJA =====
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Safari/605.1.15",
]

VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1366, "height": 768},
    {"width": 1536, "height": 864},
    {"width": 1440, "height": 900},
    {"width": 1680, "height": 1050},
    {"width": 2560, "height": 1440},
]

STEALTH_JS = """
() => {
    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
    Object.defineProperty(navigator, 'languages', {get: () => ['pl-PL', 'pl', 'en-US', 'en']});
    Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
    window.chrome = {runtime: {}};
}
"""

def human_delay(min_ms: int = 800, max_ms: int = 3000):
    """Losowe opóźnienie symulujące prawdziwego użytkownika."""
    return random.randint(min_ms, max_ms)

def simulate_human_behavior(page):
    """Symuluje zachowanie człowieka: scrollowanie, ruch myszy."""
    try:
        # Losowy scroll
        scroll_y = random.randint(100, 500)
        page.mouse.wheel(0, scroll_y)
        page.wait_for_timeout(human_delay(300, 800))
        # Losowy ruch myszy
        page.mouse.move(random.randint(100, 800), random.randint(100, 500))
        page.wait_for_timeout(human_delay(200, 600))
        # Scroll z powrotem
        page.mouse.wheel(0, -scroll_y // 2)
        page.wait_for_timeout(human_delay(200, 500))
    except Exception:
        pass

# ===== WYKRYWANIE WYGASŁYCH KONKURSÓW =====
EXPIRED_PATTERNS = [
    r"konkurs\s+(zakończony|zamknięty|nieaktywny|wygasł)",
    r"(termin|czas)\s+(minął|upłynął|zakończył)",
    r"formularz\s+(zamknięty|niedostępny|nieaktywny)",
    r"zgłoszenia\s+(zamknięte|zakończone)",
    r"konkurs\s+trwał\s+do",
    r"expired|closed|ended|no longer",
]

def is_contest_expired(scope) -> bool:
    """Sprawdza czy konkurs jest wygasły na podstawie tekstu strony."""
    try:
        txt = get_text(scope) or ""
        for pat in EXPIRED_PATTERNS:
            if re.search(pat, txt, flags=re.IGNORECASE):
                return True
        # Sprawdź datę zakończenia
        m = re.search(r"do\s+(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})", txt)
        if m:
            day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
            try:
                end_date = datetime(year, month, day, tzinfo=ZoneInfo("Europe/Warsaw"))
                if datetime.now(ZoneInfo("Europe/Warsaw")) > end_date:
                    return True
            except ValueError:
                pass
        # Brak formularza = prawdopodobnie wygasły
        try:
            if scope.locator("form").count() == 0:
                if not any(re.search(p, txt, flags=re.IGNORECASE) for p in [r"pytanie", r"odpowiedź", r"wyślij"]):
                    return True
        except Exception:
            pass
    except Exception:
        pass
    return False

# ===== INTEGRACJA Z LLM =====
def ask_llm(question: str, context_text: str = "") -> str | None:
    """Odpowiada na pytanie konkursowe za pomocą LLM (Gemini lub OpenAI)."""
    llm_cfg = CFG.get("llm", {})
    if not llm_cfg.get("enabled") or not llm_cfg.get("api_key"):
        return None

    provider = llm_cfg.get("provider", "gemini")
    api_key = llm_cfg["api_key"]
    model = llm_cfg.get("model", "gemini-2.0-flash")

    prompt = (
        "Jesteś ekspertem od konkursów wiedzy. Odpowiedz krótko i konkretnie na pytanie konkursowe. "
        "Jeśli pytanie dotyczy roku lub daty, podaj dokładną liczbę. "
        "Jeśli pytanie prosi o wymienienie kroków, podaj je numerowane.\n\n"
    )
    if context_text:
        prompt += f"Kontekst z artykułu:\n{context_text[:3000]}\n\n"
    prompt += f"Pytanie: {question}\n\nOdpowiedź:"

    try:
        if provider == "gemini":
            return _ask_gemini(api_key, model, prompt)
        elif provider == "openai":
            return _ask_openai(api_key, model, prompt)
        else:
            print(f"[LLM] Nieznany provider: {provider}")
            return None
    except Exception as e:
        print(f"[LLM] Błąd: {e}")
        return None

def _ask_gemini(api_key: str, model: str, prompt: str) -> str | None:
    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        m = genai.GenerativeModel(model)
        response = m.generate_content(prompt)
        answer = response.text.strip()
        print(f"[LLM/Gemini] Odpowiedź: {answer[:200]}")
        return answer
    except ImportError:
        print("[LLM] Brak pakietu google-generativeai. Zainstaluj: pip install google-generativeai")
        return None

def _ask_openai(api_key: str, model: str, prompt: str) -> str | None:
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
            temperature=0.3,
        )
        answer = response.choices[0].message.content.strip()
        print(f"[LLM/OpenAI] Odpowiedź: {answer[:200]}")
        return answer
    except ImportError:
        print("[LLM] Brak pakietu openai. Zainstaluj: pip install openai")
        return None

# ===== POMOCNICZE =====
COOKIE_NOISE = {
    "cookies", "ciasteczka", "zgoda", "preferencje", "prywatności", "privacy", "rodo",
    "personalized ads", "spersonalizowane reklamy", "pomiar reklam", "badanie odbiorców", "ulepszanie usług"
}
NOISE_WORDS = {
    "informacje", "wywiady", "ludzie", "badania rynku", "wydarzenia branżowe",
    "multimedia", "ogłoszenia o pracę", "zdobywcy eteru", "kontakt", "czytaj także",
    "reklama", "tagi", "udostępnij"
}
ACTION_KEYWORDS = {
    "analiza", "projekt", "architekt", "wdrożenie", "implementacja", "konfiguracja",
    "test", "testy", "plan", "przygotowanie", "publikacja", "prototyp", "makiet",
    "badanie", "weryfikacja", "monitoring", "rollback", "release", "deploy",
}

def compute_years_ago(year: int) -> int:
    return datetime.now(ZoneInfo("Europe/Warsaw")).year - year

def get_text(scope) -> str:
    try:
        return scope.locator("body").inner_text()
    except Exception:
        try:
            return scope.inner_text()
        except Exception:
            return ""

def text_is_noise(t: str) -> bool:
    low = t.lower()
    return any(w in low for w in COOKIE_NOISE) or len(t.strip()) < 5

def get_question(scope) -> str | None:
    t = get_text(scope)
    if not t:
        return None
    m = re.search(r"Pytanie\s+konkursowe:\s*(.+)", t, flags=re.IGNORECASE)
    if m:
        q = m.group(1).strip()
        q = q.splitlines()[0].strip() if q else q
        return q
    m2 = re.search(r"(Podpowiedź\s+na\s+poprzedniej\s+stronie.*)", t, flags=re.IGNORECASE)
    if m2:
        q = m2.group(1).strip()
        q = q.splitlines()[0].strip() if q else q
        return q
    for line in t.splitlines():
        if "?" in line:
            s = line.strip()
            if 5 <= len(s) <= 500:
                return s
    return None

# ===== MAPA PYTAŃ -> ROK =====
def resolve_year(question: str) -> int | None:
    ql = (question or "").lower()
    if "fantomas" in ql or "fantômas" in ql:
        return 1964
    if "discovery channel" in ql:
        return 1985
    if "tanita tikaram" in ql:
        return 1969
    if "groteska" in ql and "teatr" in ql:
        return 1945
    if "new york world" in ql and ("krzyżów" in ql or "crossword" in ql):
        return 1913
    if "nie ma róży bez ognia" in ql:
        return 1974
    if "w labiryncie" in ql and ("pierwszy" in ql or "pierwszego" in ql or "odcinek" in ql):
        return 1988
    m = re.search(r"(18|19|20)\d{2}", ql)
    return int(m.group(0)) if m else None

# ===== Cookies/CMP =====
def dismiss_cookies_any(scope) -> bool:
    """Zamyka CMP/banery cookies w danym scope (page/iframe)."""
    selectors = [
        "button:has-text('Akceptuję')",
        "button:has-text('Zgadzam się')",
        "button:has-text('OK')",
        "button:has-text('Rozumiem')",
        "button:has-text('Accept')",
        "button:has-text('I agree')",
        "button:has-text('Przejdź dalej')",
        "a:has-text('Akceptuję')",
        "a:has-text('Zgadzam się')",
    ]
    closed = False
    for sel in selectors:
        try:
            loc = scope.locator(sel)
            if loc.count() > 0 and loc.first.is_visible():
                loc.first.click(timeout=800)
                closed = True
        except Exception:
            pass
    try:
        scope.locator("[aria-label*='zgoda'], [class*='cookie'], [id*='cookie']").evaluate_all(
            "els => els.forEach(el => el.style.display='none')"
        )
    except Exception:
        pass
    return closed

# ===== Iframe/form detection =====
def find_form_frame(page):
    try:
        if page.locator("form").count() > 0:
            return page
    except Exception:
        pass
    try:
        for fr in page.frames:
            try:
                txt = get_text(fr)
                if any(x in txt for x in [
                    "Imię", "Nazwisko", "Adres e-mail", "Miasto", "Twoja odpowiedź", "Pytanie konkursowe",
                    "first name", "last name", "email", "city", "answer"
                ]):
                    return fr
            except Exception:
                pass
    except Exception:
        pass
    return page

# ===== Potwierdzenie wysyłki =====
def has_submission_confirmation(scope) -> bool:
    try:
        txt = get_text(scope) or ""
        patterns = [
            r"dziękujemy", r"twoje zgłoszenie zostało wysłane", r"zgłoszenie przyjęte",
            r"wysłano", r"formularz został wysłany",
            r"thank you", r"submission received", r"succe(ss|s)", r"submitted"
        ]
        if any(re.search(pat, txt, flags=re.IGNORECASE) for pat in patterns):
            return True
    except Exception:
        pass
    try:
        u = scope.url if hasattr(scope, "url") else ""
        if re.search(r"(sent|success|dziekujemy|wyslane|submitted)", u, flags=re.IGNORECASE):
            return True
    except Exception:
        pass
    return False

# ===== Skany zawartości artykułu =====
CONTENT_SELECTORS = [
    "article", ".article", "main", ".entry-content", ".content", ".post", ".single-article", ".news-entry",
]

def get_main_text(page) -> str:
    for sel in CONTENT_SELECTORS:
        try:
            loc = page.locator(sel)
            if loc.count() > 0:
                txt = loc.first.inner_text()
                if txt and len(txt.strip()) > 50:
                    return txt
        except Exception:
            pass
    try:
        return page.locator("body").inner_text()
    except Exception:
        return ""

# ===== Nawigacja z retry =====
def safe_goto(page, url: str, timeout: int = 20000, retries: int = 2) -> bool:
    """Nawiguje do URL z retry przy błędach sieciowych."""
    for attempt in range(retries + 1):
        try:
            page.goto(url, timeout=timeout)
            page.wait_for_load_state("domcontentloaded")
            return True
        except Exception as e:
            if attempt < retries:
                print(f"[RETRY] Błąd nawigacji ({attempt+1}/{retries}): {e}")
                page.wait_for_timeout(2000)
            else:
                print(f"[ERR] Nie udało się załadować: {url} — {e}")
                return False
    return False

# ===== 1) Zbierz linki ARTYKUŁÓW z listy =====
def collect_article_links(page, max_pages: int = 3) -> list[str]:
    arts: set[str] = set()
    for page_idx in range(max_pages):
        if page_idx == 0:
            if not safe_goto(page, LIST_URL):
                break
        dismiss_cookies_any(page)
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(1200)
        try:
            page.locator("body").evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(800)
        except Exception:
            pass
        try:
            hrefs = page.evaluate("() => Array.from(document.querySelectorAll('a')).map(a => a.getAttribute('href') || '')")
        except Exception:
            hrefs = []
        for href in hrefs:
            if not href:
                continue
            full = urljoin(LIST_URL, href)
            if re.search(r"/art/\d+/.+\.html/?$", full):
                arts.add(full)
        clicked_next = False
        for selector in [
            ("role", "link", re.compile("następna", re.IGNORECASE)),
            ("css", "a:has-text('następna')", None)
        ]:
            try:
                if selector[0] == "role":
                    page.get_by_role(selector[1], name=selector[2]).click(timeout=2000)
                else:
                    page.locator(selector[1]).click(timeout=2000)
                clicked_next = True
                break
            except Exception:
                pass
        if not clicked_next:
            break
    return sorted(list(arts))

# ===== 2) Z artykułu wyciągnij pary (FORM, ART) =====
def extract_form_pairs_from_article(page, article_url: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    if not safe_goto(page, article_url):
        return pairs
    dismiss_cookies_any(page)
    page.wait_for_timeout(800)
    try:
        hrefs = page.evaluate("() => Array.from(document.querySelectorAll('a')).map(a => a.getAttribute('href') || '')")
    except Exception:
        hrefs = []
    for href in hrefs:
        if not href:
            continue
        full = urljoin(article_url, href)
        if re.search(r"/konkursy/\d+/.+\.html/?$", full):
            pairs.append((full, article_url))
    try:
        page.get_by_role("link", name=re.compile("Biorę udział w konkursie", re.IGNORECASE)).click(timeout=1500)
        page.wait_for_load_state("domcontentloaded")
        if "konkursy/" in page.url:
            pairs.append((page.url, article_url))
    except Exception:
        pass
    try:
        if page.locator("form").count() > 0 and "konkursy/" in page.url:
            pairs.append((page.url, article_url))
    except Exception:
        pass
    seen = set()
    unique: list[tuple[str, str]] = []
    for form, art in pairs:
        if form not in seen:
            seen.add(form)
            unique.append((form, art))
    return unique

# ===== Heurystyka: kroki blisko słów kluczowych =====
def extract_steps_near_keywords(page, max_items: int = 3) -> list[str]:
    steps: list[str] = []
    body_text = get_main_text(page)
    lines = [l.strip() for l in body_text.splitlines() if l.strip()]
    lines = [l for l in lines if not any(nw in l.lower() for nw in NOISE_WORDS) and not text_is_noise(l)]
    anchor_idx = None
    keywords = (
        "proces projektowania", "krok", "etap", "step", "jak wygląda proces",
        "proces testowania", "proces wdrażania", "publikacja"
    )
    for i, l in enumerate(lines):
        if any(kw in l.lower() for kw in keywords):
            anchor_idx = i
            break
    if anchor_idx is not None:
        for l in lines[anchor_idx:anchor_idx + 30]:
            if re.match(r"^(\d+[\.)]\s+|[\-•—]\s+)", l):
                candidate = re.sub(r"^(\d+[\.)]\s+|[\-•—]\s+)", "", l).strip()
            else:
                candidate = l
            low = candidate.lower()
            if any(nw in low for nw in NOISE_WORDS) or text_is_noise(candidate):
                continue
            if not any(ak in low for ak in ACTION_KEYWORDS):
                continue
            if 5 <= len(candidate) <= 180:
                steps.append(candidate)
            if len(steps) >= max_items:
                break
    return steps[:max_items]

# ===== 3) Ekstrakcja 3 kroków z artykułu =====
def extract_three_steps_from_article(context, article_url: str) -> list[str]:
    art_page = context.new_page()
    steps: list[str] = []
    try:
        if not safe_goto(art_page, article_url):
            return steps
        dismiss_cookies_any(art_page)
        art_page.wait_for_timeout(800)
        # 1) <ol><li>
        try:
            lis_ol = art_page.locator("ol li")
            for i in range(min(3, lis_ol.count())):
                txt = lis_ol.nth(i).inner_text().strip()
                if txt and len(txt) > 4 and not text_is_noise(txt):
                    steps.append(txt)
        except Exception:
            pass
        if len(steps) >= 3:
            return steps[:3]
        # 2) <ul><li>
        try:
            lis_ul = art_page.locator("ul li")
            idx = 0
            while len(steps) < 3 and idx < lis_ul.count():
                txt = lis_ul.nth(idx).inner_text().strip()
                low = txt.lower()
                if (txt and len(txt) > 4
                    and not any(nw in low for nw in NOISE_WORDS)
                    and not text_is_noise(txt)
                    and any(ak in low for ak in ACTION_KEYWORDS)):
                    steps.append(txt)
                idx += 1
        except Exception:
            pass
        if len(steps) >= 3:
            return steps[:3]
        # 3) akapity/numeracje
        try:
            paras = art_page.locator("p, li, div")
            for i in range(paras.count()):
                txt = paras.nth(i).inner_text().strip()
                low = txt.lower()
                if text_is_noise(txt):
                    continue
                if re.match(r"^(Krok|Etap|Step)\s*\d+[\:\-\.\) ]\s+", txt, flags=re.IGNORECASE):
                    steps.append(txt)
                elif re.match(r"^\d+[\.\)]\s+", txt) and not any(nw in low for nw in NOISE_WORDS):
                    steps.append(txt)
                if len(steps) >= 3:
                    break
        except Exception:
            pass
        if len(steps) >= 3:
            return steps[:3]
        # 4) nagłówki -> sąsiednie
        try:
            heads = art_page.locator("h2, h3, h4")
            for i in range(heads.count()):
                htxt = heads.nth(i).inner_text().strip()
                if re.search(r"(proces|etap|krok|test|wdrażanie|publikacja)", htxt, flags=re.IGNORECASE):
                    nearby = art_page.locator("p, li")
                    for j in range(nearby.count()):
                        t = nearby.nth(j).inner_text().strip()
                        low = t.lower()
                        if t and len(t) > 4 and not any(nw in low for nw in NOISE_WORDS) and not text_is_noise(t):
                            steps.append(t)
                        if len(steps) >= 3:
                            break
                if len(steps) >= 3:
                    break
        except Exception:
            pass
        if len(steps) >= 3:
            return steps[:3]
        # 5) blisko słów kluczowych
        if len(steps) < 3:
            steps_near = extract_steps_near_keywords(art_page, max_items=3)
            if steps_near:
                return steps_near
        # 6) regex fallback
        body_text = get_main_text(art_page)
        if len(steps) < 3 and body_text:
            candidates = re.findall(r"(?:Krok|Etap)\s*\d+[\:\-\.\) ]\s*(.+)", body_text, flags=re.IGNORECASE)
            for c in candidates:
                c = c.strip()
                low = c.lower()
                if c and not any(nw in low for nw in NOISE_WORDS) and not text_is_noise(c):
                    steps.append(c)
                if len(steps) >= 3:
                    break
        return steps[:3]
    finally:
        try:
            art_page.close()
        except Exception:
            pass

# ===== FALLBACK: szablony kroków =====
def resolve_three_steps_fallback(question: str) -> list[str]:
    ql = (question or "").lower()
    if any(kw in ql for kw in [
        "proces projektowania aplikacji", "proces projektowania oprogramowania", "jak wygląda proces projektowania",
        "software design process", "pierwsze trzy kroki projektowania aplikacji"
    ]):
        return [
            "Analiza wymagań (cele biznesowe, użytkownicy, zakres)",
            "Projekt rozwiązania/architektury (technologie, moduły, integracje)",
            "Makiety lub prototyp UX (przepływy, walidacja z interesariuszami)"
        ]
    if any(kw in ql for kw in [
        "proces testowania", "jak wygląda proces testowania", "testowanie aplikacji",
        "pierwsze trzy kroki testowania", "software testing process"
    ]):
        return [
            "Plan testów i przygotowanie przypadków (zakres, kryteria wejścia/wyjścia)",
            "Testy jednostkowe i integracyjne w CI (uruchomienia automatyczne)",
            "Testy systemowe/UAT i raportowanie błędów (triage, priorytety)"
        ]
    if any(kw in ql for kw in [
        "proces wdrażania", "wdrożenie", "deployment", "release process", "pierwsze kroki wdrażania"
    ]):
        return [
            "Przygotowanie środowisk i pipeline CI/CD (dev/stage/prod)",
            "Konfiguracja aplikacji, migracje bazy i zarządzanie sekretami",
            "Rollout i monitoring (canary/blue-green), plan rollbacku"
        ]
    if any(kw in ql for kw in [
        "publikacja w sklepach", "google play", "app store", "jak opublikować aplikację",
        "release w sklepach", "dystrybucja mobilna"
    ]):
        return [
            "Zbudowanie i podpisanie pakietów (Android .aab/.apk, iOS .ipa)",
            "Przygotowanie listingów (opis, grafiki, polityka prywatności)",
            "Wysłanie do review/testów beta (TestFlight/closed track) i konfiguracja wersji"
        ]
    return []

# ===== CAPTCHA =====
def detect_captcha(page) -> str:
    try:
        if page.locator("iframe[title*='reCAPTCHA']").count() > 0 or page.locator(".grecaptcha-badge").count() > 0:
            return "recaptcha"
    except Exception:
        pass
    try:
        if page.locator("iframe[src*='hcaptcha.com']").count() > 0 or page.locator("[data-hcaptcha]").count() > 0:
            return "hcaptcha"
    except Exception:
        pass
    return "none"

def wait_for_captcha_solved(page, timeout_ms: int = 60000) -> bool:
    cap = detect_captcha(page)
    if cap == "none":
        return True
    print(f"[CAPTCHA] Wykryto: {cap}. Czekam do {timeout_ms} ms na rozwiązanie...")
    try:
        page.wait_for_selector("input[name*='h-captcha-response'], input[name*='g-recaptcha-response']", timeout=timeout_ms)
        return True
    except PlaywrightTimeout:
        print("[CAPTCHA] Timeout oczekiwania.")
        return False
    except Exception:
        return False

# ===== Artefakty =====
def dump_artifacts(page, scope, prefix: str):
    try:
        png = f"{prefix}.png"
        html_page = f"{prefix}.page.html"
        html_frame = f"{prefix}.frame.html"
        page.screenshot(path=png, full_page=True)
        with open(html_page, "w", encoding="utf-8") as f:
            f.write(page.content())
        if scope is not None:
            try:
                body_html = scope.locator("body").evaluate("el => el.outerHTML")
            except Exception:
                body_html = ""
            with open(html_frame, "w", encoding="utf-8") as f:
                f.write(body_html or "")
        print(f"[ART] Zapisano: {png}, {html_page}, {html_frame}")
    except Exception as e:
        print(f"[ART] Błąd zapisu artefaktów: {e}")

# ===== Skan pól i wypełnianie (mapping) =====
def scan_fields(scope):
    js = """
    () => {
      const out = {imie:null, nazwisko:null, email:null, miasto:null, odp:null, all:[]};
      const inputs = Array.from(document.querySelectorAll('input, textarea, [contenteditable="true"]'));
      const labelsByFor = new Map();
      Array.from(document.querySelectorAll('label[for]')).forEach(l=>labelsByFor.set(l.getAttribute('for'), (l.textContent||'').trim().toLowerCase()));
      function isVisible(el){
        const s = window.getComputedStyle(el);
        if (s.visibility==='hidden' || s.display==='none') return false;
        const r = el.getBoundingClientRect();
        return r && r.width>2 && r.height>2;
      }
      function labelText(el){
        let txt = '';
        if (el.labels && el.labels.length){ txt = Array.from(el.labels).map(l=>l.textContent.trim().toLowerCase()).join(' \\n '); }
        const id = el.getAttribute('id');
        if (!txt && id && labelsByFor.has(id)) txt = labelsByFor.get(id);
        const prev = el.previousElementSibling;
        if (!txt && prev && prev.tagName.toLowerCase()==='label') txt = (prev.textContent||'').trim().toLowerCase();
        return txt;
      }
      const kw = {
        imie: ['imię','imie','first name','firstname'],
        nazwisko: ['nazwisko','last name','lastname'],
        email: ['email','e-mail','adress e-mail','adres e-mail','mail'],
        miasto: ['miasto','city'],
        odp: ['twoja odpowiedź','odpowiedź','answer','treść odpowiedzi']
      };
      function anyMatch(text, arr){ return !!text && arr.some(k=>text.indexOf(k) !== -1); }
      function buildSelector(el){
        const id = el.getAttribute('id');
        const name = el.getAttribute('name');
        if (id) return '#' + id;
        if (name) return el.tagName.toLowerCase() + '[name="' + name.split('"').join('\\\"') + '" ]';
        const parent = el.parentElement;
        if (parent){
          const siblings = Array.from(parent.querySelectorAll(el.tagName));
          const idx = siblings.indexOf(el)+1;
          return el.tagName.toLowerCase() + ':nth-of-type(' + (idx>0?idx:1) + ')';
        }
        return el.tagName.toLowerCase();
      }
      for (const el of inputs){
        if (!isVisible(el)) continue;
        if (el.matches('input[type="hidden"], input[disabled], textarea[disabled], input[readonly], textarea[readonly]')) continue;
        const tag = el.tagName.toLowerCase();
        const type = (el.getAttribute('type')||'').toLowerCase();
        const name = (el.getAttribute('name')||'').toLowerCase();
        const id = (el.getAttribute('id')||'').toLowerCase();
        const ph = (el.getAttribute('placeholder')||'').toLowerCase();
        const aria = (el.getAttribute('aria-label')||'').toLowerCase();
        const lab = labelText(el);
        const sel = buildSelector(el);
        out.all.push({sel, tag, type, name, id, ph, aria, lab});
        if (!out.email && type==='email') out.email = sel;
        if (!out.odp && (tag==='textarea' || el.getAttribute('contenteditable')==='true')) out.odp = sel;
        if (!out.imie && (anyMatch(name, kw.imie) || anyMatch(id, kw.imie) || anyMatch(ph, kw.imie) || anyMatch(aria, kw.imie) || anyMatch(lab, kw.imie))) out.imie = sel;
        if (!out.nazwisko && (anyMatch(name, kw.nazwisko) || anyMatch(id, kw.nazwisko) || anyMatch(ph, kw.nazwisko) || anyMatch(aria, kw.nazwisko) || anyMatch(lab, kw.nazwisko))) out.nazwisko = sel;
        if (!out.miasto && (anyMatch(name, kw.miasto) || anyMatch(id, kw.miasto) || anyMatch(ph, kw.miasto) || anyMatch(aria, kw.miasto) || anyMatch(lab, kw.miasto))) out.miasto = sel;
        if (!out.email && (anyMatch(name, kw.email) || anyMatch(id, kw.email) || anyMatch(ph, kw.email) || anyMatch(aria, kw.email) || anyMatch(lab, kw.email))) out.email = sel;
        if (!out.odp && (anyMatch(name, kw.odp) || anyMatch(id, kw.odp) || anyMatch(ph, kw.odp) || anyMatch(aria, kw.odp) || anyMatch(lab, kw.odp))) out.odp = sel;
      }
      const textInputs = out.all.filter(r=>r.tag==='input' && (r.type==='text' || r.type===''));
      if (!out.imie && textInputs.length>0) out.imie = textInputs[0].sel;
      if (!out.nazwisko && textInputs.length>1) out.nazwisko = textInputs[1].sel;
      if (!out.email){ const mail = out.all.find(r=>r.type==='email'); if (mail) out.email = mail.sel; }
      if (!out.odp){ const ta = out.all.find(r=>r.tag==='textarea'); if (ta) out.odp = ta.sel; }
      return out;
    }
    """
    try:
        return scope.evaluate(js)
    except Exception as e:
        print('[ERR] scan_fields evaluate failed:', e)
        return None

def pw_fill_or_js(scope, selector: str, value: str) -> bool:
    """Wypełnia pole z losowym opóźnieniem między znakami (human-like)."""
    try:
        loc = scope.locator(selector)
        if loc.count() > 0 and loc.first.is_visible():
            try:
                loc.first.click(timeout=1500)
                loc.first.wait_for_element_state("stable", timeout=500)
            except Exception:
                pass
            try:
                # Wyczyść pole i wpisz znak po znaku
                loc.first.fill("")
                loc.first.type(value, delay=random.randint(30, 120))
                loc.first.press('Tab')
                return True
            except Exception:
                pass
    except Exception:
        pass
    try:
        return bool(scope.evaluate("(sel,val)=>{const el=document.querySelector(sel); if(!el) return false; try{el.focus(); const tag=el.tagName.toLowerCase(); if(tag==='textarea' || tag==='input'){el.value=val; el.dispatchEvent(new Event('input',{bubbles:true})); el.dispatchEvent(new Event('change',{bubbles:true})); el.blur(); return true;} if(el.getAttribute('contenteditable')==='true'){el.textContent=val; el.dispatchEvent(new Event('input',{bubbles:true})); el.dispatchEvent(new Event('change',{bubbles:true})); el.blur(); return true;} }catch(e){return false} return false;}", selector, value))
    except Exception:
        return False

# ===== RODO checkboxy =====
def check_rodo_checkboxes(scope) -> int:
    """Zaznacza wszystkie niezaznaczone checkboxy w formularzu (zgody RODO)."""
    checked = 0
    try:
        cbs = scope.locator("form input[type='checkbox']")
        for i in range(cbs.count()):
            try:
                cb = cbs.nth(i)
                if cb.is_visible() and not cb.is_checked():
                    cb.check(timeout=1000)
                    checked += 1
            except Exception:
                pass
    except Exception:
        pass
    return checked

# ===== SUBMIT =====
def click_submit(scope) -> bool:
    texts = ["Wyślij", "Wyślij zgłoszenie", "Wyślij formularz", "Wyślij odpowiedź", "Wyślij wiadomość"]
    for t in texts:
        try:
            scope.get_by_role("button", name=re.compile(t, re.IGNORECASE)).click(timeout=2000)
            return True
        except Exception:
            pass
    for sel in [
        "button[type='submit']",
        "input[type='submit']",
        "button:has-text('Wyślij')",
        "button:has-text('ZGŁOŚ')",
        ".submit, .btn-submit, .btn-primary[type='submit']",
    ]:
        try:
            loc = scope.locator(sel)
            if loc.count() > 0 and loc.first.is_visible():
                loc.first.click(timeout=2000)
                return True
        except Exception:
            pass
    try:
        ok = scope.evaluate(
            """
            () => {
              const btns = Array.from(document.querySelectorAll('button,input[type=submit]'))
                .filter(el => el.offsetWidth>2 && el.offsetHeight>2);
              if (btns.length>0) { btns[btns.length-1].click(); return true; }
              const forms = Array.from(document.querySelectorAll('form'));
              if (forms.length>0) { forms[0].submit(); return true; }
              return false;
            }
            """
        )
        return bool(ok)
    except Exception:
        return False

# ===== MAIN =====
def run(max_contests: int = 5, max_pages: int = 3, headless: bool = False,
        interactive: bool = False, captcha_mode: str = "wait", save_artifacts: bool = False,
        max_daily: int = 3, dry_run: bool = False):
    ensure_log()
    with sync_playwright() as p:
        ua = random.choice(USER_AGENTS)
        vp = random.choice(VIEWPORTS)
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent=ua,
            viewport=vp,
            locale="pl-PL",
            timezone_id="Europe/Warsaw",
        )
        page = context.new_page()
        # Stealth: ukryj webdriver
        page.add_init_script(STEALTH_JS)
        print(f"[STEALTH] UA: {ua[:50]}... VP: {vp['width']}x{vp['height']}")
        # wstępny guard limitu dziennego
        already = count_today_sent()
        if already >= max_daily:
            print(f"[LIMIT] Dzisiejszy limit {max_daily} wysyłek już osiągnięty ({already}). Kończę.")
            browser.close()
            return
        print("[INFO] Zbieram linki ARTYKULOW...")
        article_links = collect_article_links(page, max_pages=max_pages)
        print(f"[INFO] Znaleziono artykulow: {len(article_links)}")
        print("[INFO] Ekstrahuje pary FORMULARZ<->ARTYKUL...")
        pairs: list[tuple[str, str | None]] = []
        for art in article_links:
            pairs.extend(extract_form_pairs_from_article(page, art))
        if not pairs:
            pairs = [(u, None) for u in SEED_FORMS]
            print("[WARN] Brak par z listy — używam SEED_FORMS.")
        print(f"[INFO] Pary FORM<->ART: {len(pairs)}")
        # filtruj już wysłane
        already_sent_urls = get_already_sent_urls()
        pairs = [(f, a) for f, a in pairs if f not in already_sent_urls]
        if not pairs:
            print("[INFO] Wszystkie znalezione konkursy już wysłane. Kończę.")
            browser.close()
            return
        print(f"[INFO] Nowych do przetworzenia: {len(pairs)}")
        to_process = pairs[:max_contests]
        for contest_idx, (contest_url, article_url) in enumerate(to_process):
            # Losowe opóźnienie między konkursami (jak człowiek)
            if contest_idx > 0:
                delay_s = random.uniform(15, 45)
                print(f"[HUMAN] Czekam {delay_s:.0f}s przed następnym konkursem...")
                page.wait_for_timeout(int(delay_s * 1000))
            today_sent = count_today_sent()
            if today_sent >= max_daily:
                print(f"[LIMIT] Osiągnięto {today_sent}/{max_daily} dziennie. Przeskakuję ten konkurs.")
                status = "SKIPPED_DAILY_LIMIT"
                log_row(contest_url, article_url, "", "", status, "")
                continue
            status = "INIT"
            q = ""
            a = "??"
            fill_stats = ""
            try:
                print(f"\n>>> Konkurs: {contest_url}")
                if not safe_goto(page, contest_url):
                    status = "ERROR:NavigationFailed"
                    continue
                dismiss_cookies_any(page)
                scope = find_form_frame(page)
                # domknij CMP także w ramce
                dismiss_cookies_any(scope)
                # Sprawdź czy konkurs wygasł
                if is_contest_expired(scope):
                    print("[EXPIRED] Konkurs wygasły — pomijam.")
                    status = "SKIPPED_EXPIRED"
                    log_row(contest_url, article_url, "", "", status, "")
                    continue
                # Symuluj zachowanie człowieka
                simulate_human_behavior(page)
                q = get_question(scope) or ""
                print(f"[Pytanie] {q}")
                # --- Rozwiązywanie odpowiedzi ---
                y = resolve_year(q)
                extraction = ""
                if y is not None:
                    a = str(compute_years_ago(y))
                    print(f"[Odpowiedz] Rok {y} -> {a} lat temu")
                    extraction = "YEARS"
                else:
                    # Próba ekstrakcji kroków z artykułu
                    steps = []
                    if article_url and re.search(r"poprzedniej stronie", q, flags=re.IGNORECASE):
                        print(f"[DEBUG] ART URL: {article_url}")
                        steps = extract_three_steps_from_article(context, article_url)
                        if steps:
                            extraction = "EXTRACT_STEPS"
                    # Fallback: szablon
                    if not steps:
                        steps = resolve_three_steps_fallback(q)
                        if steps:
                            extraction = "TEMPLATE_STEPS"
                    if steps:
                        a = "; ".join([f"{i+1}) {s}" for i, s in enumerate(steps)])
                        tag = "[TEMPLATE]" if extraction == "TEMPLATE_STEPS" else ""
                        print(f"[Odpowiedz]{tag} {a}")
                    else:
                        # Fallback: LLM
                        llm_answer = ask_llm(q, get_main_text(page) if q else "")
                        if llm_answer:
                            a = llm_answer
                            extraction = "LLM"
                            print(f"[Odpowiedz][LLM] {a[:200]}")
                        elif interactive:
                            print("[Resolver] Wpisz odpowiedź ręcznie (page.pause).")
                            page.pause()
                            try:
                                a = scope.get_by_label("Twoja odpowiedź", exact=False).input_value() or "??"
                            except Exception:
                                a = "??"
                        else:
                            print("[Resolver] Brak automatycznej odpowiedzi — pomijam.")
                            a = "??"
                            extraction = "MANUAL"
                mapping = scan_fields(scope)
                if mapping:
                    print("[DEBUG] mapping:", {k: v for k, v in mapping.items() if k != 'all'})
                    ok_imie = bool(mapping.get('imie')) and pw_fill_or_js(scope, mapping['imie'], IMIE)
                    ok_nazw = bool(mapping.get('nazwisko')) and pw_fill_or_js(scope, mapping['nazwisko'], NAZWISKO)
                    ok_mail = bool(mapping.get('email')) and pw_fill_or_js(scope, mapping['email'], EMAIL)
                    ok_city = bool(mapping.get('miasto')) and pw_fill_or_js(scope, mapping['miasto'], MIASTO)
                    ok_ans = bool(mapping.get('odp')) and pw_fill_or_js(scope, mapping['odp'], a)
                    fill_stats = str({
                        'imie': bool(ok_imie), 'nazwisko': bool(ok_nazw), 'email': bool(ok_mail), 'miasto': bool(ok_city), 'odp': bool(ok_ans)
                    })
                    print("[DEBUG] fill status:", fill_stats)
                    rodo_cnt = check_rodo_checkboxes(scope)
                    if rodo_cnt:
                        print(f"[RODO] Zaznaczono {rodo_cnt} checkboxów")
                else:
                    print("[WARN] Brak mappingu pól — formularz może być w nietypowym komponencie.")
                if dry_run:
                    print("[DRY-RUN] Pomijam kliknięcie 'Wyślij'.")
                    status = "DRY_FILLED"
                else:
                    cap = detect_captcha(page)
                    if cap != "none":
                        print(f"[CAPTCHA] Wykryto: {cap}; tryb: {captcha_mode}")
                        if captcha_mode == "pause":
                            if interactive:
                                page.pause()
                            else:
                                print("[CAPTCHA] Tryb pause, ale interactive=false — kontynuuję bez pauzy.")
                        elif captcha_mode == "wait":
                            solved = wait_for_captcha_solved(page, timeout_ms=60000)
                            print(f"[CAPTCHA] solved={solved}")
                        elif captcha_mode == "skip":
                            print("[CAPTCHA] Pomijam rozwiązywanie (skip).")
                    sent = click_submit(scope)
                    page.wait_for_timeout(2000)
                    confirmed = has_submission_confirmation(scope)
                    if sent and confirmed:
                        if extraction == "TEMPLATE_STEPS":
                            status = "SENT_TEMPLATE"
                        elif extraction == "EXTRACT_STEPS":
                            status = "SENT_EXTRACT"
                        elif extraction == "YEARS":
                            status = "SENT_YEARS"
                        elif extraction == "LLM":
                            status = "SENT_LLM"
                        else:
                            status = "SENT"
                    elif sent and not confirmed:
                        status = "SENT_UNCONFIRMED"
                    else:
                        status = "NOT_SENT"
                if save_artifacts:
                    art_dir = "artifacts"
                    os.makedirs(art_dir, exist_ok=True)
                    prefix = os.path.join(art_dir, f"run_{datetime.now(ZoneInfo('Europe/Warsaw')).strftime('%Y%m%d_%H%M%S')}")
                    dump_artifacts(page, scope, prefix)
                print(f"[Status] {status}")
            except Exception as e:
                status = f"ERROR:{type(e).__name__}:{e}"
                print(status)
            finally:
                log_row(contest_url, article_url, q, a, status, fill_stats)
        browser.close()


# ===== RAPORT HTML =====
REPORT_FILE = "raport.html"

def generate_report():
    """Generuje raport HTML z logu CSV."""
    if not os.path.exists(LOG_FILE):
        print("[RAPORT] Brak pliku logu — nie ma czego raportować.")
        return

    rows = []
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            rdr = csv.reader(f)
            header = next(rdr, None)
            for row in rdr:
                if row and len(row) >= 6:
                    rows.append(row)
    except Exception as e:
        print(f"[RAPORT] Błąd odczytu logu: {e}")
        return

    now = datetime.now(ZoneInfo("Europe/Warsaw")).strftime("%Y-%m-%d %H:%M")
    total = len(rows)
    sent = sum(1 for r in rows if r[5] in SENT_STATUSES)
    errors = sum(1 for r in rows if r[5].startswith("ERROR"))
    expired = sum(1 for r in rows if r[5] == "SKIPPED_EXPIRED")

    # Grupuj po dacie
    by_date: dict[str, list] = {}
    for r in rows:
        d = r[0].split("T")[0]
        by_date.setdefault(d, []).append(r)

    table_rows = ""
    for r in reversed(rows):
        ts = r[0].replace("T", " ")[:19]
        url = r[1]
        url_short = url.split("/")[-1][:40] if url else ""
        question = (r[3] or "")[:80]
        answer = (r[4] or "")[:120]
        status = r[5]
        # Kolor statusu
        if status in SENT_STATUSES:
            color = "#2e7d32"
            badge = "✅"
        elif status.startswith("ERROR"):
            color = "#c62828"
            badge = "❌"
        elif status.startswith("SKIPPED"):
            color = "#f57f17"
            badge = "⏭️"
        elif status == "DRY_FILLED":
            color = "#1565c0"
            badge = "🧪"
        else:
            color = "#555"
            badge = "⚪"
        table_rows += f"""        <tr>
            <td>{ts}</td>
            <td><a href="{url}" target="_blank" title="{url}">{url_short}</a></td>
            <td title="{question}">{question}</td>
            <td title="{answer}">{answer}</td>
            <td style="color:{color};font-weight:bold">{badge} {status}</td>
        </tr>\n"""

    html = f"""<!DOCTYPE html>
<html lang="pl">
<head>
    <meta charset="UTF-8">
    <title>Raport konkursów — portalmedialny.pl</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: 'Segoe UI', Tahoma, sans-serif; background: #f5f5f5; padding: 20px; color: #333; }}
        .header {{ background: linear-gradient(135deg, #1a237e, #283593); color: white; padding: 24px 32px; border-radius: 12px; margin-bottom: 20px; }}
        .header h1 {{ font-size: 22px; margin-bottom: 8px; }}
        .header .meta {{ opacity: 0.8; font-size: 13px; }}
        .stats {{ display: flex; gap: 16px; margin-bottom: 20px; flex-wrap: wrap; }}
        .stat {{ background: white; padding: 16px 24px; border-radius: 10px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); min-width: 140px; }}
        .stat .num {{ font-size: 28px; font-weight: 700; }}
        .stat .label {{ font-size: 12px; color: #666; text-transform: uppercase; margin-top: 4px; }}
        .stat.sent .num {{ color: #2e7d32; }}
        .stat.err .num {{ color: #c62828; }}
        .stat.exp .num {{ color: #f57f17; }}
        table {{ width: 100%; border-collapse: collapse; background: white; border-radius: 10px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }}
        th {{ background: #e8eaf6; padding: 12px 16px; text-align: left; font-size: 13px; text-transform: uppercase; color: #333; }}
        td {{ padding: 10px 16px; border-bottom: 1px solid #eee; font-size: 13px; max-width: 250px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
        tr:hover {{ background: #f5f5ff; }}
        a {{ color: #1565c0; text-decoration: none; }}
        a:hover {{ text-decoration: underline; }}
        .footer {{ margin-top: 20px; text-align: center; color: #999; font-size: 12px; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>🏆 Raport konkursów — portalmedialny.pl</h1>
        <div class="meta">Wygenerowano: {now} | Uczestnik: {IMIE} {NAZWISKO} ({EMAIL})</div>
    </div>
    <div class="stats">
        <div class="stat"><div class="num">{total}</div><div class="label">Wszystkich</div></div>
        <div class="stat sent"><div class="num">{sent}</div><div class="label">Wysłanych</div></div>
        <div class="stat err"><div class="num">{errors}</div><div class="label">Błędów</div></div>
        <div class="stat exp"><div class="num">{expired}</div><div class="label">Wygasłych</div></div>
        <div class="stat"><div class="num">{len(by_date)}</div><div class="label">Dni aktywnych</div></div>
    </div>
    <table>
        <thead><tr><th>Data/czas</th><th>Konkurs</th><th>Pytanie</th><th>Odpowiedź</th><th>Status</th></tr></thead>
        <tbody>
{table_rows}        </tbody>
    </table>
    <div class="footer">Agent pm_agent_multi.py — auto-generated report</div>
</body>
</html>"""

    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[RAPORT] Zapisano: {REPORT_FILE} ({total} wpisów, {sent} wysłanych)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Agent konkursow portalmedialny.pl")
    parser.add_argument("--max-pages", type=int, default=3, help="Maksymalna liczba stron listy do skanowania")
    parser.add_argument("--max-contests", type=int, default=5, help="Maksymalna liczba konkursow do przetworzenia")
    parser.add_argument("--headless", type=lambda v: v.lower() in ("true", "1", "yes"), default=False, help="Tryb headless (true/false)")
    parser.add_argument("--interactive", type=lambda v: v.lower() in ("true", "1", "yes"), default=False, help="Czy używać page.pause w ścieżkach manual/captcha")
    parser.add_argument("--captcha-mode", type=str, choices=["pause", "skip", "wait"], default="wait", help="Strategia obsługi CAPTCHA")
    parser.add_argument("--save-artifacts", type=lambda v: v.lower() in ("true", "1", "yes"), default=False, help="Zapisywać zrzuty ekranu i HTML do diagnostyki")
    parser.add_argument("--max-daily", type=int, default=10, help="Maksymalna liczba wysyłek dziennie (lokalna data)")
    parser.add_argument("--dry-run", type=lambda v: v.lower() in ("true", "1", "yes"), default=False, help="Tryb testowy bez kliknięcia 'Wyślij'")
    parser.add_argument("--report", action="store_true", help="Generuj raport HTML z dotychczasowych wyników (bez uruchamiania agenta)")
    args = parser.parse_args()
    if args.report:
        generate_report()
    else:
        run(max_contests=args.max_contests, max_pages=args.max_pages, headless=args.headless,
            interactive=args.interactive, captcha_mode=args.captcha_mode, save_artifacts=args.save_artifacts,
            max_daily=args.max_daily, dry_run=args.dry_run)
        generate_report()
