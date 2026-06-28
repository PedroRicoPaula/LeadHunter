import sys
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

import asyncio
import json
import random
import re
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote, quote_plus

import httpx
import typer
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, Browser
from rich.console import Console

from config_loader import CONFIG

app = typer.Typer()
console = Console()


def _cleanup_playwright_procs() -> None:
    """Kill playwright/node/chromium processes that are NOT in uninterruptible-sleep (UE).
    UE processes can only be cleared by the kernel (reboot). Non-UE ones accumulate and block new launches."""
    import subprocess as _sp, signal as _sig
    try:
        out = _sp.run(["ps", "axo", "pid,stat,command"], capture_output=True, text=True).stdout
        killed = []
        for line in out.splitlines():
            parts = line.split(None, 2)
            if len(parts) < 3:
                continue
            pid_s, stat, cmd = parts
            if stat.startswith("U"):  # uninterruptible — skip, can't kill
                continue
            if any(kw in cmd for kw in ["playwright/driver", "playwright-driver", "chromium-headless"]):
                try:
                    _sp.run(["kill", "-9", pid_s], capture_output=True)
                    killed.append(pid_s)
                except Exception:
                    pass
        if killed:
            console.print(f"[dim]Cleanup: {len(killed)} proc(s) playwright removidos {killed}[/]")
    except Exception:
        pass


ROOT = Path(__file__).parent.parent
LEADS_FILE = ROOT / CONFIG["output"]["leads_file"]

# Domains to skip — aggregators, search engines, maps, social media
# Social media URLs must never be saved as "website" — they go in redes_sociais
_SKIP_DOMAINS = {
    "openstreetmap.org", "google.com", "google.pt", "maps.google.com",
    "bing.com", "duckduckgo.com", "yahoo.com",
    "pai.pt", "paginas-amarelas.pt", "superpages.com",
    "wikipedia.org", "wikidata.org",
    # Social media — not real websites
    "facebook.com", "fb.com", "m.facebook.com",
    "instagram.com",
    "twitter.com", "x.com",
    "linkedin.com",
    "youtube.com", "youtu.be",
    "tiktok.com",
    "pinterest.com",
    # Aggregators / OTAs — listings, not business websites
    "tripadvisor.com", "tripadvisor.pt", "tripadvisor.co.uk",
    "booking.com",
    "airbnb.com", "airbnb.pt",
    "expedia.com", "expedia.pt",
    "decolar.com", "despegar.com",
    "lastminute.com",
    "thefork.pt", "thefork.com",
    "zomato.com",
    "yelp.com", "yelp.pt",
    "foursquare.com",
}

# Foreign ccTLD suffixes that are almost certainly wrong for an Açores business
_FOREIGN_CCTLDS = {
    ".com.br", ".net.br", ".org.br",  # Brazil
    ".com.ar", ".net.ar",             # Argentina
    ".com.mx", ".net.mx",             # Mexico
    ".co.uk", ".org.uk",              # UK (unlikely for PT business)
    ".com.co",                        # Colombia
    ".com.uy",                        # Uruguay
    ".com.ve",                        # Venezuela
}

# Nichos where TripAdvisor has good coverage
_TA_NICHOS = {
    "restaurantes", "restaurante", "cafés", "café", "cafes", "cafe",
    "hotéis", "hotel", "hoteis", "turismo", "alojamento",
    "bar", "bars", "pastelaria", "padaria",
}

# Known PT hospitality/service platforms with subdomain-per-business pattern
# Format: (base_domain, subdomain_pattern, path_pattern)
# subdomain_pattern = "sub" → try https://{slug}.domain
# path_pattern = "path" → try https://domain/{slug}
_PLATFORM_PROBES = [
    ("eatbu.com", "sub", None),   # miromarestaurante.eatbu.com — confirmed PT restaurant platform
]


# ── URL helpers ───────────────────────────────────────────────────────────────

def _decode_ddg_href(href: str) -> str | None:
    """Extract real URL from DuckDuckGo redirect href."""
    if not href:
        return None
    if href.startswith("/l/?"):
        href = "https://duckduckgo.com" + href
    if href.startswith("//"):
        href = "https:" + href
    try:
        parsed = urlparse(href)
        if "duckduckgo.com" in (parsed.netloc or ""):
            qs = parse_qs(parsed.query)
            if "uddg" in qs:
                return unquote(qs["uddg"][0])
    except Exception:
        pass
    return href if href.startswith("http") else None


_SOCIAL_PLATFORM_MAP = {
    "instagram": "instagram.com",
    "facebook":  "facebook.com",
    "twitter":   "twitter.com",
    "youtube":   "youtube.com",
    "tiktok":    "tiktok.com",
    "linkedin":  "linkedin.com",
    "pinterest": "pinterest.com",
}


def _is_social_url(url: str | None) -> bool:
    if not url:
        return False
    try:
        domain = re.sub(r"^(www\.|m\.)", "", urlparse(url).netloc.lower())
        return any(
            domain == sd or domain.endswith("." + sd)
            for sd in _SKIP_DOMAINS
            if sd in _SOCIAL_PLATFORM_MAP.values() or sd in ("fb.com", "youtu.be", "x.com")
        )
    except Exception:
        return False


def _rescue_social_website(lead: dict) -> None:
    """
    If website field contains a social media URL, move it to redes_sociais
    and clear the website field so enrichment can search for a real site.
    """
    url = lead.get("website")
    if not url or not _is_social_url(url):
        return
    try:
        domain = re.sub(r"^(www\.|m\.)", "", urlparse(url).netloc.lower())
        for platform, plat_domain in _SOCIAL_PLATFORM_MAP.items():
            if domain == plat_domain or domain.endswith("." + plat_domain) or domain == "x.com":
                real_platform = "twitter" if domain in ("x.com", "twitter.com") else platform
                redes = dict(lead.get("redes_sociais") or {})
                if real_platform not in redes or not redes[real_platform]:
                    redes[real_platform] = url
                    lead["redes_sociais"] = redes
                break
    except Exception:
        pass
    lead["website"] = None


def _valid_candidate(url: str) -> bool:
    if not url or not url.startswith("http"):
        return False
    try:
        netloc = urlparse(url).netloc.lower()
        domain = netloc.replace("www.", "").replace("m.", "")
        if domain in _SKIP_DOMAINS:
            return False
        # Reject if any skip domain appears as a suffix (e.g. pt.tripadvisor.com)
        if any(domain == d or domain.endswith("." + d) for d in _SKIP_DOMAINS):
            return False
        # Reject foreign country-code TLDs — Açores businesses use .pt / .com / .eu
        if any(domain.endswith(ccTLD) for ccTLD in _FOREIGN_CCTLDS):
            return False
        return True
    except Exception:
        return False


def _name_to_slug(name: str) -> tuple[str, str]:
    """Return (slug_nodash, slug_dash) from business name."""
    import unicodedata
    normalized = unicodedata.normalize("NFD", name)
    no_accents = "".join(c for c in normalized if unicodedata.category(c) != "Mn")
    lower = no_accents.lower()
    slug_nodash = re.sub(r"[^a-z0-9]", "", lower)
    slug_dash = re.sub(r"\s+", "-", lower.strip())
    slug_dash = re.sub(r"[^a-z0-9-]", "", slug_dash).strip("-")
    return slug_nodash, slug_dash


# ── Domain guessing with HTTP verification ────────────────────────────────────

async def _verify_domain_guesses(name: str) -> list[str]:
    """
    Generate common PT domain patterns and HEAD-check each one.
    Only returns URLs that actually resolve — avoids ERR_NAME_NOT_RESOLVED.
    """
    slug, slug_dash = _name_to_slug(name)
    if len(slug) < 3:
        return []

    # Also try first meaningful word only (e.g. "Coffee Bar Alfredo" → "alfredo")
    words = [w for w in re.sub(r"[^a-z0-9 ]", "", slug_dash).split("-") if len(w) > 2]
    first_slug = words[0] if words else ""
    last_slug = words[-1] if len(words) > 1 else ""

    patterns: list[str] = []
    for s in dict.fromkeys([slug, slug_dash, first_slug, last_slug]):
        if not s or len(s) < 3:
            continue
        patterns += [
            f"https://www.{s}.pt",
            f"https://{s}.pt",
            f"https://www.{s}.com",
            f"https://{s}.com",
        ]

    seen_p: set[str] = set()
    unique = []
    for p in patterns:
        if p not in seen_p:
            seen_p.add(p)
            unique.append(p)

    found: list[str] = []
    seen_final: set[str] = set()
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    async with httpx.AsyncClient(
        timeout=6.0, verify=False, follow_redirects=True
    ) as client:
        tasks = [client.head(url, headers=headers) for url in unique[:12]]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for url, res in zip(unique[:12], results):
            if isinstance(res, Exception):
                continue
            if res.status_code < 400:
                final = str(res.url).rstrip("/")
                if _valid_candidate(final) and final not in seen_final:
                    seen_final.add(final)
                    found.append(final)
    return found


# ── Platform subdomain probing (async HEAD checks) ────────────────────────────

async def _probe_platform_subdomains(name: str) -> list[str]:
    """
    Probe known PT hospitality platforms for business subdomain.
    Uses HTTP HEAD — fast, no browser needed.
    E.g. miromarestaurante.eatbu.com
    """
    slug, slug_dash = _name_to_slug(name)
    if len(slug) < 3:
        return []

    probes: list[str] = []
    for domain, sub_pat, _path_pat in _PLATFORM_PROBES:
        if sub_pat == "sub":
            probes.append(f"https://{slug}.{domain}/")
            if slug_dash != slug:
                probes.append(f"https://{slug_dash}.{domain}/")

    found: list[str] = []
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

    async with httpx.AsyncClient(
        timeout=6.0, verify=False, follow_redirects=True
    ) as client:
        tasks = [client.head(url, headers=headers) for url in probes]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for url, res in zip(probes, results):
            if isinstance(res, Exception):
                continue
            if res.status_code < 400:
                # Use the final URL after redirects
                final_url = str(res.url).split("?")[0].rstrip("/")
                if _valid_candidate(final_url):
                    found.append(final_url)
                    console.print(f"    [green]Platform hit:[/] {final_url}")

    return found


# ── httpx-based searches (no browser) ────────────────────────────────────────

_HTTPX_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "pt-PT,pt;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


async def _search_ddg_httpx(name: str, city: str, nicho: str) -> list[str]:
    """DuckDuckGo HTML endpoint — no JS, no browser needed."""
    candidates: list[str] = []
    queries = [
        f'"{name}" {city} Açores site oficial',
        f'{name} {nicho} {city} Portugal',
    ]
    async with httpx.AsyncClient(follow_redirects=True, timeout=12, headers=_HTTPX_HEADERS) as client:
        for query in queries:
            if candidates:
                break
            try:
                url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
                resp = await client.get(url)
                soup = BeautifulSoup(resp.text, "lxml")
                for a in soup.select("a.result__a")[:8]:
                    href = _decode_ddg_href(a.get("href", "")) or a.get("href", "")
                    if href and href.startswith("http") and _valid_candidate(href) and href not in candidates:
                        candidates.append(href)
            except Exception:
                continue
    return candidates[:5]


async def _search_bing_httpx(name: str, city: str, nicho: str) -> list[str]:
    """Bing HTML search — no browser needed."""
    query = f'"{name}" {nicho} {city} Açores Portugal'
    candidates: list[str] = []
    try:
        url = f"https://www.bing.com/search?q={quote_plus(query)}&setlang=pt-PT&cc=PT"
        async with httpx.AsyncClient(follow_redirects=True, timeout=12, headers=_HTTPX_HEADERS) as client:
            resp = await client.get(url)
        soup = BeautifulSoup(resp.text, "lxml")
        for a in soup.select("li.b_algo h2 a, .b_title a")[:8]:
            href = a.get("href", "")
            if href and href.startswith("http") and _valid_candidate(href) and href not in candidates:
                candidates.append(href)
    except Exception:
        pass
    return candidates[:5]


# ── Search functions ──────────────────────────────────────────────────────────

async def _search_google(browser: Browser, name: str, city: str, nicho: str) -> list[str]:
    """
    Scrape Google web search — most reliable source for local PT businesses.
    Returns organic result URLs (not ads).
    """
    query = f'"{name}" {city} Açores {nicho} site oficial'
    url = f"https://www.google.com/search?q={quote_plus(query)}&hl=pt&gl=pt&num=10"

    context = await browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        viewport={"width": 1280, "height": 900},
        locale="pt-PT",
        timezone_id="Europe/Lisbon",
    )
    page = await context.new_page()
    candidates: list[str] = []
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_timeout(2000)

        # Accept cookie consent if shown
        for sel in ['button[aria-label*="Aceitar"]', 'button[id="L2AGLb"]', 'button[id="W0wltc"]']:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=1000):
                    await btn.click()
                    await page.wait_for_timeout(1000)
                    break
            except Exception:
                pass

        html = await page.content()
        soup = BeautifulSoup(html, "lxml")

        # Organic results: <div class="g"> → <a href> that starts with http
        for a in soup.select("div.g a[href], div[data-sokoban-container] a[href], h3 ~ div a[href]"):
            href = a.get("href", "")
            if href.startswith("http") and _valid_candidate(href):
                if href not in candidates:
                    candidates.append(href)
            if len(candidates) >= 6:
                break

        # Fallback: any link in results area
        if not candidates:
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if href.startswith("http") and _valid_candidate(href):
                    domain = urlparse(href).netloc.lower()
                    if not any(skip in domain for skip in ["google.", "gstatic.", "googleapis."]):
                        if href not in candidates:
                            candidates.append(href)
                if len(candidates) >= 6:
                    break

    except Exception as e:
        console.print(f"    [dim]Google: {e}[/]")
    finally:
        await context.close()
    return candidates[:5]


async def _search_duckduckgo(
    browser: Browser, name: str, city: str, nicho: str, query_variant: int = 0
) -> list[str]:
    """Search DuckDuckGo — tries JS site first (less blocked), falls back to HTML endpoint."""
    if query_variant == 0:
        query = f'"{name}" {city} Açores site oficial'
    elif query_variant == 1:
        query = f'{name} {nicho} {city} Portugal'
    else:
        query = f'"{name}" {city} eatbu menu reservas'

    # Try JS version first (less likely to be blocked than html endpoint)
    ddg_url = f"https://duckduckgo.com/?q={quote_plus(query)}&ia=web"
    html_url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"

    candidates: list[str] = []
    for target_url, wait_ms in [(ddg_url, 3000), (html_url, 1500)]:
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
            locale="pt-PT",
        )
        page = await context.new_page()
        try:
            await page.goto(target_url, wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(wait_ms)
            html = await page.content()
            soup = BeautifulSoup(html, "lxml")
            # JS DDG result links
            for a in soup.select("a[data-testid='result-title-a'], a.result__a")[:8]:
                real = _decode_ddg_href(a.get("href", "")) or a.get("href", "")
                if real and real.startswith("http") and _valid_candidate(real) and real not in candidates:
                    candidates.append(real)
        except Exception as e:
            console.print(f"    [dim]DDG v{query_variant}: {e}[/]")
        finally:
            await context.close()
        if candidates:
            break
    return candidates[:5]


async def _search_bing(browser: Browser, name: str, city: str, nicho: str) -> list[str]:
    """Scrape Bing search."""
    query = f'"{name}" {nicho} {city} Açores Portugal'
    url = f"https://www.bing.com/search?q={quote_plus(query)}&setlang=pt&cc=PT"

    context = await browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        viewport={"width": 1280, "height": 800},
        locale="pt-PT",
    )
    page = await context.new_page()
    candidates: list[str] = []
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
        await page.wait_for_timeout(1800)
        html = await page.content()
        soup = BeautifulSoup(html, "lxml")
        for a in soup.select("li.b_algo h2 a, li.b_algo .b_title a, h2 a[href]")[:8]:
            href = a.get("href", "")
            if href and href.startswith("http") and _valid_candidate(href) and href not in candidates:
                candidates.append(href)
    except Exception as e:
        console.print(f"    [dim]Bing: {e}[/]")
    finally:
        await context.close()
    return candidates[:5]


async def _search_tripadvisor(browser: Browser, name: str, city: str) -> str | None:
    """Search TripAdvisor PT and return first result page URL."""
    query = f"{name} {city} Açores"
    url = f"https://www.tripadvisor.pt/Search?q={quote_plus(query)}"

    context = await browser.new_context(
        user_agent=CONFIG["scraper"]["user_agent"],
        viewport={"width": 1280, "height": 800},
    )
    page = await context.new_page()
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_timeout(2000)
        html = await page.content()
        soup = BeautifulSoup(html, "lxml")
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            if any(t in href for t in ["/Restaurant_Review", "/Hotel_Review", "/Attraction_Review"]):
                if href.startswith("/"):
                    return f"https://www.tripadvisor.pt{href.split('?')[0]}"
                if href.startswith("https://www.tripadvisor."):
                    return href.split("?")[0]
    except Exception as e:
        console.print(f"    [dim]TripAdvisor: {e}[/]")
    finally:
        await context.close()
    return None


async def _search_google_maps(browser: Browser, name: str, lat: float | None, lon: float | None, city: str) -> str | None:
    """
    Scrape Google Maps for the business website URL.
    Uses lat/lon from OSM when available (most precise), falls back to name+city search.
    Returns the website URL shown in the Maps knowledge panel, or None.
    """
    if lat and lon:
        # Search by coordinates — much more precise
        query = f"{name}"
        maps_url = f"https://www.google.com/maps/search/{quote_plus(query)}/@{lat},{lon},17z?hl=pt"
    else:
        maps_url = f"https://www.google.com/maps/search/{quote_plus(name + ' ' + city)}?hl=pt"

    context = await browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        viewport={"width": 1280, "height": 800},
        locale="pt-PT",
    )
    page = await context.new_page()
    try:
        await page.goto(maps_url, wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_timeout(3000)

        # Accept cookie consent if present
        for selector in [
            'button[aria-label*="Aceitar"]',
            'button[aria-label*="Accept"]',
            'form[action*="consent"] button',
            '#L2AGLb',  # "Accept all" button ID
        ]:
            try:
                btn = page.locator(selector).first
                if await btn.is_visible(timeout=1500):
                    await btn.click()
                    await page.wait_for_timeout(1500)
                    break
            except Exception:
                pass

        # If search returned a list, click first result
        first_result = page.locator('a[href*="/maps/place/"]').first
        try:
            if await first_result.is_visible(timeout=2000):
                await first_result.click()
                await page.wait_for_timeout(2500)
        except Exception:
            pass

        html = await page.content()
        soup = BeautifulSoup(html, "lxml")

        # Google Maps website links have data-tooltip="Abrir website" or similar
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            tooltip = (a.get("data-tooltip") or a.get("aria-label") or "").lower()
            if "website" in tooltip or "site" in tooltip:
                if href.startswith("http") and _valid_candidate(href):
                    # Clean Google redirect URLs
                    if "google.com/url" in href:
                        parsed = urlparse(href)
                        qs = parse_qs(parsed.query)
                        real = qs.get("q", qs.get("url", [None]))[0]
                        if real and real.startswith("http"):
                            return real
                    return href

        # Fallback: look for links in the panel that aren't Maps/Google
        for a in soup.select("div[data-section-id] a[href], div.rogA2c a[href], div.W4Efsd a[href]"):
            href = a.get("href", "")
            if href.startswith("http") and _valid_candidate(href):
                domain = urlparse(href).netloc.lower()
                # Skip google domains and common social/aggregator sites
                if not any(d in domain for d in ["google", "maps", "facebook", "instagram", "tripadvisor", "booking"]):
                    return href

    except Exception as e:
        console.print(f"    [dim]Google Maps: {e}[/]")
    finally:
        await context.close()
    return None


# ── URL validation ────────────────────────────────────────────────────────────

def _url_to_passage(url: str) -> str:
    """Converte URL em texto legível para o reranker."""
    try:
        from urllib.parse import urlparse as _up
        p = _up(url)
        domain = p.netloc.lower().replace("www.", "").replace(".", " ")
        path = p.path.strip("/").replace("/", " ").replace("-", " ").replace("_", " ")
        return f"{domain} {path}".strip() or url
    except Exception:
        return url


def _validate_with_nim_reranker(lead: dict, candidates: list[str]) -> dict | None:
    """Usa NIM reranker para seleccionar a melhor URL. Mais rápido e preciso que LLM."""
    if not candidates:
        return None
    try:
        from nim_client import nim
        if not nim.enabled:
            return None
        nome  = lead.get("nome", "")
        nicho = lead.get("nicho", "")
        regiao = lead.get("regiao", "Açores")
        city  = regiao.split(",")[0].strip()
        query = f"site oficial de {nome}, {nicho} em {city}, Portugal"
        passages = [_url_to_passage(u) for u in candidates]
        rankings = nim.rerank(query, passages)
        if not rankings:
            return None
        best_idx, best_score = rankings[0]
        # Normaliza logit para confidence 0-1 (logit típico: -5 a +5)
        import math as _math
        confidence = 1.0 / (1.0 + _math.exp(-best_score * 0.5))
        url = candidates[best_idx]
        return {"url": url, "confidence": round(confidence, 3)}
    except Exception as e:
        console.print(f"    [dim]NIM reranker: {e}[/]")
        return None


def _validate_with_llm(lead: dict, candidates: list[str]) -> dict | None:
    """
    Validação de URL em cascata:
      1. NIM reranker (cloud, sem Ollama)
      2. Ollama LLM (local)
      3. None → fallback heurístico no chamador
    """
    if not candidates:
        return None

    # ── 1. NIM reranker ───────────────────────────────────────────────────────
    nim_result = _validate_with_nim_reranker(lead, candidates)
    if nim_result and nim_result.get("url"):
        console.print(f"    [dim]NIM rerank: {nim_result['confidence']:.0%}[/]")
        return nim_result

    # ── 2. Ollama LLM ─────────────────────────────────────────────────────────
    base_url = CONFIG.get("ollama", {}).get("base_url", "http://localhost:11434")
    model = CONFIG["llm"]["model"]

    candidates_str = "\n".join(f"{i+1}. {url}" for i, url in enumerate(candidates))
    prompt = f"""Empresa: "{lead.get('nome')}" ({lead.get('nicho')}) em {lead.get('regiao')}, Portugal.

Candidatos encontrados via pesquisa web:
{candidates_str}

Qual URL pertence especificamente a ESTA empresa? Responde com JSON:
{{"url": "<url exacto da lista acima, ou null se nenhum corresponde>", "confidence": <float 0.0-1.0>}}

Sem texto adicional."""

    try:
        resp = httpx.post(
            f"{base_url}/api/chat",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": "/no_think\nRespondes APENAS com JSON válido."},
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
                "think": False,
                "options": {"temperature": 0.1, "num_predict": 150},
            },
            timeout=30.0,
        )
        resp.raise_for_status()
        raw = resp.json()["message"]["content"].strip()
        raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            result = json.loads(m.group(0))
            url = result.get("url")
            confidence = float(result.get("confidence", 0))
            if not url:
                return {"url": None, "confidence": 0}
            if any(url in c or c in url or _same_domain(url, c) for c in candidates):
                return {"url": url, "confidence": confidence}
            if url.startswith("http") and confidence >= 0.90:
                return {"url": url, "confidence": confidence * 0.8}
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.RemoteProtocolError):
        pass
    except Exception as e:
        console.print(f"    [dim]Ollama LLM: {e}[/]")
    return None


def _heuristic_best_url(lead: dict, candidates: list[str]) -> dict | None:
    """
    Rule-based URL selection when Ollama is offline.
    Scores candidates by how well they match the business name.
    """
    if not candidates:
        return None

    name = (lead.get("nome") or "").lower()
    slug, slug_dash = _name_to_slug(lead.get("nome") or "")
    name_words = set(re.findall(r"[a-z]+", name))

    def score(url: str) -> float:
        domain = urlparse(url).netloc.lower().replace("www.", "")
        full = url.lower()
        s = 0.0

        # Exact slug match in domain → very strong signal
        if slug and slug in domain:
            s += 0.80
        elif slug_dash and slug_dash in domain:
            s += 0.70

        # Individual word overlap between business name and URL
        url_words = set(re.findall(r"[a-z]{3,}", full))
        common = {"pt", "com", "www", "net", "org", "restaurante", "hotel", "cafe",
                  "acores", "azores", "portugal", "web", "site", "online"}
        meaningful_name_words = name_words - common
        overlap = meaningful_name_words & url_words
        if overlap:
            s += min(0.45, len(overlap) * 0.18)

        # Platform bonus
        for plat_domain, _, _ in _PLATFORM_PROBES:
            if plat_domain in domain:
                s += 0.15

        # PT domain bonus (local business)
        if domain.endswith(".pt"):
            s += 0.05

        # Penalise deep paths (aggregator listing pages, not homepages)
        path_depth = url.count("/") - 2
        if path_depth > 3:
            s -= 0.08 * (path_depth - 3)

        # Penalise aggregator domains even if not in _SKIP_DOMAINS
        for agg in ["tripadvisor", "booking", "zomato", "thefork", "yelp", "foursquare"]:
            if agg in domain:
                s -= 0.30

        return max(0.0, min(s, 1.0))

    scored = [(score(u), u) for u in candidates]
    scored.sort(reverse=True)
    best_score, best_url = scored[0]

    # Accept if confident OR if it's the only candidate and somewhat plausible
    threshold = 0.40
    if len(candidates) == 1 and best_score >= 0.20:
        threshold = 0.20

    if best_score >= threshold:
        return {"url": best_url, "confidence": best_score}
    return None


def _same_domain(a: str, b: str) -> bool:
    try:
        da = urlparse(a).netloc.lower().replace("www.", "")
        db = urlparse(b).netloc.lower().replace("www.", "")
        return da == db and bool(da)
    except Exception:
        return False


# ── Core enrichment ───────────────────────────────────────────────────────────

async def _enrich_one(lead: dict, browser) -> dict:
    """
    Find website for a company. Cascade (fast-to-slow):
      0. OSM tags — free, instant, reliable
      1. Platform HEAD probes (eatbu.com subdomains)
      2. Google Search — best quality for PT local businesses
      3. Bing — reliable fallback
      4. DuckDuckGo — tertiary
      5. Google Maps — coordinates-based, very precise
      6. TripAdvisor — food/tourism nichos
      7. DuckDuckGo alt queries
      8. Domain guessing + HTTP HEAD verify
    Stops as soon as it has ≥ 3 good candidates OR one high-confidence hit.
    """
    # Move any social URL from website → redes_sociais before searching
    _rescue_social_website(lead)

    name  = lead.get("nome", "")
    regiao = lead.get("regiao", "Açores")
    city  = regiao.split(",")[0].strip() if "," in regiao else "Açores"
    nicho = (lead.get("nicho") or "").lower()
    lat   = lead.get("lat")
    lon   = lead.get("lon")

    # ── Source 0: OSM raw tags — mappers often add website/contact:website ──────
    osm = lead.get("osm_tags") or {}
    for osm_key in ("website", "contact:website", "url", "contact:url"):
        osm_url = osm.get(osm_key, "")
        if osm_url and osm_url.startswith("http") and _valid_candidate(osm_url):
            lead["website"] = osm_url
            lead["source"]  = "openstreetmap"
            console.print(f"  [green]✓ OSM:[/] {name[:40]} → {osm_url[:55]}")
            return lead

    candidates: list[str] = []

    def _add(urls, prepend=False):
        for u in (urls if isinstance(urls, list) else [urls]):
            if u and u not in candidates:
                if prepend:
                    candidates.insert(0, u)
                else:
                    candidates.append(u)

    # ── Source 1: Platform HEAD probes — instant, no browser ────────────────────
    _add(await _probe_platform_subdomains(name), prepend=True)
    if candidates:
        console.print(f"    [cyan]Platform hit →[/] {candidates[0]}")

    if browser is not None:
        # ── Source 2: Google Search — primary search engine ──────────────────────────
        _add(await _search_google(browser, name, city, nicho))

        # ── Source 3: Bing — usually succeeds even when Google is blocked ─────────────
        if len(candidates) < 4:
            _add(await _search_bing(browser, name, city, nicho))

        # ── Source 4: DuckDuckGo ─────────────────────────────────────────────────────
        if len(candidates) < 3:
            _add(await _search_duckduckgo(browser, name, city, nicho, query_variant=0))

        # ── Source 5: Google Maps — lat/lon makes it very precise ────────────────────
        if len(candidates) < 3:
            gm = await _search_google_maps(browser, name, lat, lon, city)
            _add(gm, prepend=bool(gm))  # Prepend — Maps result is usually the real site

        # ── Source 6: TripAdvisor (food/tourism only) — save to booking_hints, never website ──
        if nicho in _TA_NICHOS and not lead.get("booking_hints"):
            ta_url = await _search_tripadvisor(browser, name, city)
            if ta_url:
                hints = lead.get("booking_hints") or []
                if ta_url not in hints:
                    hints.append(ta_url)
                lead["booking_hints"] = hints
    else:
        # ── Httpx fallbacks when browser unavailable ──────────────────────────────────
        if len(candidates) < 3:
            _add(await _search_ddg_httpx(name, city, nicho))
        if len(candidates) < 3:
            _add(await _search_bing_httpx(name, city, nicho))

    # ── Source 7: DDG alternate queries (browser only) ───────────────────────────
    if browser is not None:
        if len(candidates) < 2:
            _add(await _search_duckduckgo(browser, name, city, nicho, query_variant=1))
        if nicho in _TA_NICHOS and len(candidates) < 3:
            _add(await _search_duckduckgo(browser, name, city, nicho, query_variant=2))

    # ── Source 8: Domain guessing + HTTP HEAD verify ──────────────────────────────
    _add(await _verify_domain_guesses(name))

    if not candidates:
        console.print(f"  [dim]✗[/] {name[:40]} — sem candidatos")
        return lead

    console.print(f"    [dim]{len(candidates)} candidatos: {candidates[:3]}[/]")

    # ── Validation ────────────────────────────────────────────────────────────────
    result = _validate_with_llm(lead, candidates)
    if result is None:
        result = _heuristic_best_url(lead, candidates)

    # Lower confidence threshold: 0.40 (was 0.50)
    # Platform hits and Google Maps results get a free pass at ≥ 0.30
    min_conf = 0.30 if candidates and any(
        p[0] in (candidates[0] if candidates else "")
        for p in _PLATFORM_PROBES
    ) else 0.40

    confidence = result["confidence"] if result else 0

    if result and result.get("url") and confidence >= min_conf:
        pct = int(confidence * 100)
        lead = {**lead, "website": result["url"], "source": "openstreetmap+enriched"}
        console.print(f"  [green]✓[/] {name[:40]} → {result['url'][:55]} ({pct}%)")
    else:
        pct = int(confidence * 100)
        console.print(f"  [dim]✗[/] {name[:40]} — confiança insuficiente ({pct}%)")

    return lead


async def _run_enrichment(
    leads: list[dict],
    max_companies: int | None,
    progress_callback=None,
) -> list[dict]:
    cfg = CONFIG["scraper"]

    # Include leads with social URL as website — they need rescue + real-site search
    to_enrich = [
        l for l in leads
        if l.get("status") == "pendente" and (not l.get("website") or _is_social_url(l.get("website")))
    ]
    if max_companies:
        to_enrich = to_enrich[:max_companies]

    if not to_enrich:
        console.print("[yellow]Nenhuma empresa sem website pendente.[/]")
        return leads

    total = len(to_enrich)
    console.print(f"[cyan]A enriquecer {total} empresas...[/]\n")

    import subprocess as _sp
    _vm = _sp.run(["vm_stat"], capture_output=True, text=True).stdout
    _free_mb = sum(
        int(l.split(":")[1].strip().rstrip(".")) * 16384 // 1024 // 1024
        for l in _vm.splitlines()
        if l.startswith("Pages free") or l.startswith("Pages speculative")
    )
    browser = None
    pw_ctx = None
    if _free_mb >= 80:
        try:
            pw_ctx = await asyncio.wait_for(async_playwright().__aenter__(), timeout=30)
            browser = await asyncio.wait_for(
                pw_ctx.chromium.launch(
                    headless=cfg["headless"],
                    args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                          "--memory-pressure-off", "--disable-extensions",
                          "--disable-background-networking"],
                ),
                timeout=30,
            )
            console.print("[dim]Browser lançado.[/]")
        except Exception as e:
            console.print(f"[yellow]Browser falhou ({e.__class__.__name__}) — modo sem browser.[/]")
            if pw_ctx:
                try:
                    await pw_ctx.__aexit__(None, None, None)
                except Exception:
                    pass
            pw_ctx = None
            browser = None
            _cleanup_playwright_procs()
    else:
        console.print(f"[yellow]RAM livre: {_free_mb}MB (<80MB) — modo sem browser (probes + domain-guess).[/]")

    try:
        for i, lead in enumerate(to_enrich):
            if progress_callback:
                progress_callback(i, total)

            enriched = await _enrich_one(lead, browser)

            idx = next(
                (j for j, l in enumerate(leads) if l.get("place_id") == lead.get("place_id")),
                None,
            )
            if idx is not None:
                leads[idx] = enriched

            if progress_callback:
                progress_callback(i + 1, total)

            if i < total - 1:
                await asyncio.sleep(random.uniform(2.5, 4.0))
    finally:
        if browser:
            try:
                await browser.close()
            except Exception:
                pass
        if pw_ctx:
            try:
                await pw_ctx.__aexit__(None, None, None)
            except Exception:
                pass
        _cleanup_playwright_procs()

    return leads


# ── Public callable ───────────────────────────────────────────────────────────

def enrich_all(max_companies: int | None = None, progress_callback=None) -> None:
    """Callable from pipeline.py — enriches companies without website. Works with or without Ollama."""
    if not LEADS_FILE.exists():
        console.print("[red]ERRO:[/] leads_pendentes.json não encontrado.")
        return

    with open(LEADS_FILE, "r", encoding="utf-8-sig") as f:
        leads = json.load(f)

    sem_website = [l for l in leads if not l.get("website") and l.get("status") == "pendente"]
    console.print(f"Empresas sem website: [bold]{len(sem_website)}[/]\n")

    if not sem_website:
        console.print("[yellow]Todas as empresas já têm website ou não estão pendentes.[/]")
        return

    # Check Ollama — inform user but don't block (heuristic fallback available)
    ollama_online = False
    try:
        httpx.get(
            f"{CONFIG.get('ollama', {}).get('base_url', 'http://localhost:11434')}/api/tags",
            timeout=3,
        )
        ollama_online = True
    except Exception:
        console.print("[yellow]Ollama offline — a usar validação heurística (sem LLM).[/]\n")

    if ollama_online:
        console.print("[dim]Ollama disponível — a usar validação LLM.[/]\n")

    before = sum(1 for l in leads if l.get("website"))
    leads = asyncio.run(_run_enrichment(leads, max_companies, progress_callback))
    after = sum(1 for l in leads if l.get("website"))

    with open(LEADS_FILE, "w", encoding="utf-8") as f:
        json.dump(leads, f, ensure_ascii=False, indent=2)

    found = after - before
    console.print(f"\n[green]OK[/] Websites encontrados: {found}  |  Sem presença: {len(sem_website) - found}\n")


# ── CLI ───────────────────────────────────────────────────────────────────────

@app.command()
def run(
    max_companies: int = typer.Option(None, "--max", "-m", help="Limite de empresas a enriquecer"),
):
    """Fase 2 — Pesquisa automática de websites (DDG + TripAdvisor + Bing + plataformas PT + Google Maps)."""
    enrich_all(max_companies=max_companies)


if __name__ == "__main__":
    app()
