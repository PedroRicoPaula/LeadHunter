import sys
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

"""
Web Discovery — scrapes external tourism/business portals page by page.
Supports: visitazores.com, tripadvisor.pt, generic fallback.

Usage (CLI):
  python 05_web_discovery.py run --url "https://www.visitazores.com/explorar?category=experiences&island=sao-miguel"
  python 05_web_discovery.py run --url "https://www.tripadvisor.pt/Restaurants-g189116-Sao_Miguel_Azores.html"
  python 05_web_discovery.py run --url "https://..." --max-pages 5
"""

import asyncio
import json
import re
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse, urljoin

import typer
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from rich.console import Console
from rich.table import Table

from config_loader import CONFIG

app = typer.Typer()
console = Console()

ROOT = Path(__file__).parent.parent
LEADS_FILE = ROOT / CONFIG["output"]["leads_file"]

_PHONE_RE = re.compile(r"(?:\+351[\s\-]?)?(?:2\d{2}|9[1-9]\d)[\s\-]?\d{3}[\s\-]?\d{3}")
_EMAIL_RE = re.compile(r"\b[a-zA-Z0-9._%+\-]{1,64}@[a-zA-Z0-9.\-]{1,253}\.[a-zA-Z]{2,10}\b")
_EMAIL_BAD = re.compile(r"\.(png|jpg|jpeg|gif|svg|webp|ico|css|js|json|xml|pdf)$", re.I)
_SOCIAL_DOMAINS = {"facebook.com", "instagram.com", "twitter.com", "x.com",
                   "linkedin.com", "youtube.com", "tiktok.com", "pinterest.com"}

_ISLAND_MAP = {
    "sao-miguel":   "Sao Miguel, Acores",
    "terceira":     "Terceira, Acores",
    "faial":        "Faial, Acores",
    "pico":         "Pico, Acores",
    "sao-jorge":    "Sao Jorge, Acores",
    "flores":       "Flores, Acores",
    "graciosa":     "Graciosa, Acores",
    "santa-maria":  "Santa Maria, Acores",
    "corvo":        "Corvo, Acores",
}

_CATEGORY_MAP = {
    "experiences":      "Actividades",
    "restaurants":      "Restaurantes",
    "accommodations":   "Alojamento",
    "hotels":           "Hoteis",
    "bars":             "Bares",
    "cafes":            "Cafes",
    "shops":            "Lojas",
    "services":         "Serviços",
    "health":           "Saúde",
    "tourism":          "Turismo",
}

# TripAdvisor geo IDs for Azores islands (used in URL detection)
_TA_GEO_MAP = {
    "g189116": "Sao Miguel, Acores",   # Sao Miguel island
    "g189166": "Sao Miguel, Acores",   # Ponta Delgada
    "g189114": "Terceira, Acores",     # Terceira island
    "g189115": "Faial, Acores",        # Faial island
    "g315076": "Pico, Acores",         # Pico island
    "g315075": "Sao Jorge, Acores",    # Sao Jorge
    "g315073": "Flores, Acores",       # Flores
    "g315074": "Graciosa, Acores",     # Graciosa
    "g315077": "Santa Maria, Acores",  # Santa Maria
    "g315078": "Corvo, Acores",        # Corvo
    "g189113": "Acores",               # Azores (all)
}

_TA_NICHO_MAP = {
    "Restaurants":  "Restaurantes",
    "Hotels":       "Hoteis",
    "Attractions":  "Actividades",
    "VacationRentals": "Alojamento",
}


# ── URL utilities ─────────────────────────────────────────────────────────────

def _set_page(url: str, page: int) -> str:
    """Generic ?page=N pagination."""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    qs["page"] = [str(page)]
    return urlunparse(parsed._replace(query=urlencode({k: v[0] for k, v in qs.items()})))


def _ta_page_url(base_url: str, page: int) -> str:
    """TripAdvisor offset pagination: inserts oa{offset} before .html."""
    offset = (page - 1) * 30  # 30 results per page on TA
    if page == 1:
        return base_url
    # Insert offset marker before .html
    if "oa" in base_url and re.search(r"oa\d+", base_url):
        # Replace existing offset
        return re.sub(r"oa\d+", f"oa{offset}", base_url)
    # Insert before .html
    if base_url.endswith(".html"):
        return base_url[:-5] + f"-oa{offset}.html"
    return base_url + f"-oa{offset}"


def _is_social(url: str) -> bool:
    try:
        domain = re.sub(r"^(www\.|m\.)", "", urlparse(url).netloc.lower())
        return any(domain == s or domain.endswith("." + s) for s in _SOCIAL_DOMAINS)
    except Exception:
        return False


def _url_meta(base_url: str) -> tuple[str, str]:
    """Extract (regiao, nicho) from URL — handles visitazores.com and tripadvisor.pt."""
    if "tripadvisor" in base_url:
        return _ta_url_meta(base_url)
    qs = parse_qs(urlparse(base_url).query)
    island_raw = qs.get("island", [qs.get("ilha", ["acores"])[0]])[0]
    cat_raw    = qs.get("category", [qs.get("tipo", ["turismo"])[0]])[0]
    regiao = _ISLAND_MAP.get(island_raw, f"{island_raw.replace('-',' ').title()}, Acores")
    nicho  = _CATEGORY_MAP.get(cat_raw, cat_raw.replace("-", " ").title())
    return regiao, nicho


def _ta_url_meta(url: str) -> tuple[str, str]:
    """Extract (regiao, nicho) from TripAdvisor URL."""
    # Match geo ID like g189116
    geo_match = re.search(r"-g(\d+)-", url)
    geo_id = f"g{geo_match.group(1)}" if geo_match else None
    regiao = _TA_GEO_MAP.get(geo_id, "Acores") if geo_id else "Acores"

    # Match category from URL segment
    nicho = "Turismo"
    for seg, n in _TA_NICHO_MAP.items():
        if seg in url:
            nicho = n
            break
    return regiao, nicho


# ── visitazores.com scraper ───────────────────────────────────────────────────

def _va_listing_links(html: str, base_url: str) -> list[str]:
    """Extract detail page URLs from a visitazores.com listing page."""
    soup = BeautifulSoup(html, "lxml")
    seen: set[str] = set()
    links: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        full = urljoin(base_url, href).split("?")[0].rstrip("/")
        if re.search(r"visitazores\.com/(en|pt)/[^/]+/[^/?#]+$", full):
            if full not in seen and "explorar" not in full and "search" not in full:
                seen.add(full)
                links.append(full)
    return links


async def _va_detail(page, url: str, regiao: str, nicho: str) -> dict | None:
    """Scrape one visitazores.com detail page → lead dict."""
    try:
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        if not resp or resp.status >= 400:
            return None
        await page.wait_for_timeout(1200)
        html = await page.content()
    except Exception as e:
        console.print(f"    [dim]detalhe VA: {e}[/]")
        return None

    soup = BeautifulSoup(html, "lxml")
    h1 = soup.find("h1")
    name = h1.get_text(strip=True) if h1 else None
    if not name:
        title_tag = soup.find("title")
        if title_tag:
            name = title_tag.get_text(strip=True).split("|")[0].split("–")[0].strip()
    if not name or len(name) < 2:
        return None

    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    text = soup.get_text(separator=" ", strip=True)

    morada = None
    addr = soup.find("address")
    if addr:
        morada = addr.get_text(separator=", ", strip=True)[:150]
    if not morada:
        for el in soup.find_all(attrs={"itemprop": "address"}):
            morada = el.get_text(separator=", ", strip=True)[:150]
            break

    phones = _PHONE_RE.findall(text)
    website = None
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if (href.startswith("http") and "visitazores.com" not in href
                and not _is_social(href) and not _EMAIL_BAD.search(href)):
            website = href
            break

    raw_emails = [e for e in _EMAIL_RE.findall(text)
                  if not _EMAIL_BAD.search(e) and "." in e.split("@")[-1]]

    lat = lon = None
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            items = data if isinstance(data, list) else [data]
            for item in items:
                geo = item.get("geo") or {}
                if geo.get("latitude"):
                    lat = float(geo["latitude"])
                    lon = float(geo.get("longitude", 0))
                    break
        except Exception:
            pass

    slug = url.rstrip("/").split("/")[-1]
    return {
        "place_id":  f"web_visitazores_{slug}",
        "nome":      name,
        "morada":    morada,
        "lat":       lat, "lon": lon,
        "rating":    None, "total_reviews": None,
        "website":   website,
        "telefone":  phones[0] if phones else None,
        "nicho":     nicho, "regiao": regiao,
        "status":    "pendente",
        "source":    "visitazores",
        "url_fonte": url,
        "osm_tags":  {},
        "emails":    raw_emails[:3],
    }


# ── TripAdvisor scraper ───────────────────────────────────────────────────────

def _ta_listing_links(html: str, base_url: str) -> list[str]:
    """
    Extract business detail page links from a TripAdvisor listing page.
    Matches /Restaurant_Review-*, /Hotel_Review-*, /Attraction_Review-* patterns.
    """
    soup = BeautifulSoup(html, "lxml")
    seen: set[str] = set()
    links: list[str] = []
    base_domain = "https://www.tripadvisor.pt"

    ta_patterns = re.compile(
        r"/(Restaurant_Review|Hotel_Review|Attraction_Review|VacationRentalReview)-[^\"'\s#?]+"
    )

    for a in soup.find_all("a", href=True):
        href = a["href"]
        m = ta_patterns.match(href)
        if m:
            # Remove query string and anchor
            clean = href.split("?")[0].split("#")[0]
            full = base_domain + clean if clean.startswith("/") else clean
            if full not in seen:
                seen.add(full)
                links.append(full)

    return links


async def _ta_accept_cookies(page) -> None:
    """Accept TripAdvisor cookie consent if present."""
    for sel in [
        'button[id*="accept"]',
        'button[class*="accept"]',
        'button[data-testid="accept-button"]',
        '#onetrust-accept-btn-handler',
        'button:has-text("Aceitar")',
        'button:has-text("Accept")',
    ]:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=1500):
                await btn.click()
                await page.wait_for_timeout(1000)
                return
        except Exception:
            pass


async def _ta_detail(page, url: str, regiao: str, nicho: str) -> dict | None:
    """
    Scrape one TripAdvisor detail page.
    TripAdvisor uses React — need to wait for JS render.
    """
    try:
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=25000)
        if not resp or resp.status >= 400:
            return None
        await page.wait_for_timeout(3000)  # React needs more time
        await _ta_accept_cookies(page)
        html = await page.content()
    except Exception as e:
        console.print(f"    [dim]detalhe TA: {e}[/]")
        return None

    soup = BeautifulSoup(html, "lxml")

    # ── Name ──────────────────────────────────────────────────────────────────
    name = None
    for sel in [
        soup.find("h1", attrs={"data-automation": "mainH1"}),
        soup.find("h1", class_=re.compile(r"HjBfq|biGQs|header")),
        soup.find("h1"),
    ]:
        if sel:
            name = sel.get_text(strip=True)
            break

    if not name:
        title = soup.find("title")
        if title:
            name = title.get_text(strip=True).split("|")[0].split("–")[0].strip()

    if not name or len(name) < 2:
        return None

    # Remove script/style noise
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    text = soup.get_text(separator=" ", strip=True)

    # ── Rating + Reviews ──────────────────────────────────────────────────────
    rating = None
    total_reviews = None
    # TA renders rating as "4,5" or "4.5" in aria-label or text
    rating_el = soup.find(attrs={"aria-label": re.compile(r"\d[,\.]\d.*(de|of|em)\s*5", re.I)})
    if rating_el:
        m = re.search(r"(\d[,\.]\d)", rating_el.get("aria-label", ""))
        if m:
            try:
                rating = float(m.group(1).replace(",", "."))
            except Exception:
                pass
    # Reviews count: "1.234 avaliações" or "1,234 reviews"
    rev_match = re.search(r"([\d\.]+)\s*(avalia[çc][oõ]es|reviews?)", text, re.I)
    if rev_match:
        try:
            total_reviews = int(rev_match.group(1).replace(".", "").replace(",", ""))
        except Exception:
            pass

    # ── Address ───────────────────────────────────────────────────────────────
    morada = None
    # TA puts address in span[class*=biGQs] or div with itemprop="address"
    for el in soup.find_all(attrs={"itemprop": "address"}):
        morada = el.get_text(separator=", ", strip=True)[:150]
        break
    if not morada:
        addr_el = soup.find("address") or soup.find(attrs={"data-automation": "location"})
        if addr_el:
            morada = addr_el.get_text(separator=", ", strip=True)[:150]
    if not morada:
        # Heuristic: look for Açores postcode pattern (9xxx-xxx)
        postcode_m = re.search(r"9\d{3}-\d{3}", text)
        if postcode_m:
            # Extract surrounding context as address
            idx = text.find(postcode_m.group())
            morada = text[max(0, idx-80):idx+20].strip()[:150]

    # ── Phone ─────────────────────────────────────────────────────────────────
    phones = _PHONE_RE.findall(text)
    # Prefer numbers shown after "Telefone" or "Phone" labels
    phone_ctx = re.search(r"(?:Telefone|Phone|Tel\.?)[:\s]+(\+?[\d\s\-]{9,15})", text, re.I)
    if phone_ctx:
        p = re.sub(r"\s+", "", phone_ctx.group(1))
        if len(p) >= 9:
            phones = [phone_ctx.group(1).strip()] + phones

    # ── Website ───────────────────────────────────────────────────────────────
    website = None
    # TA website links have data-blcontact="URL_WEBSITE" or aria-label="Website"
    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        label = (a.get("aria-label") or a.get("data-blcontact") or "").lower()
        text_content = a.get_text(strip=True).lower()
        if "website" in label or "site" in label or text_content in ("website", "site"):
            if href.startswith("http") and "tripadvisor" not in href and not _is_social(href):
                website = href
                break
    # Fallback: any external non-social link in the business panel
    if not website:
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            if (href.startswith("http")
                    and "tripadvisor" not in href
                    and not _is_social(href)
                    and not _EMAIL_BAD.search(href)
                    and not any(d in href for d in ["google.", "apple.com/maps", "maps."])):
                website = href
                break

    # ── Coordinates from JSON-LD ──────────────────────────────────────────────
    lat = lon = None
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            items = data if isinstance(data, list) else [data]
            for item in items:
                geo = item.get("geo") or {}
                if geo.get("latitude"):
                    lat = float(geo["latitude"])
                    lon = float(geo.get("longitude", 0))
                    break
        except Exception:
            pass

    # ── Stable ID from TA URL ─────────────────────────────────────────────────
    # URL format: /Restaurant_Review-g189116-d12345678-...html
    d_match = re.search(r"-d(\d+)-", url)
    ta_id = d_match.group(1) if d_match else url.rstrip("/").split("/")[-1][:30]

    return {
        "place_id":      f"ta_{ta_id}",
        "nome":          name,
        "morada":        morada,
        "lat":           lat, "lon": lon,
        "rating":        rating,
        "total_reviews": total_reviews,
        "website":       website,
        "telefone":      phones[0] if phones else None,
        "nicho":         nicho, "regiao": regiao,
        "status":        "pendente",
        "source":        "tripadvisor",
        "url_fonte":     url,
        "osm_tags":      {},
        "emails":        [],
    }


# ── Generic fallback ──────────────────────────────────────────────────────────

async def _generic_listing_links(page, url: str) -> list[str]:
    html = await page.content()
    soup = BeautifulSoup(html, "lxml")
    base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    seen: set[str] = set()
    links: list[str] = []
    skip = re.compile(
        r"/(page|pagina|categoria|category|tag|search|pesquisa|login|register|"
        r"cart|checkout|privacy|termos|about|contact|contacto|sitemap)(/|$)", re.I,
    )
    for a in soup.find_all("a", href=True):
        href = a["href"]
        full = urljoin(base, href).split("?")[0].rstrip("/")
        parsed = urlparse(full)
        if (parsed.netloc == urlparse(url).netloc
                and parsed.path.count("/") >= 2
                and not skip.search(parsed.path)
                and full not in seen):
            seen.add(full)
            links.append(full)
    return links


# ── Scraper engine ────────────────────────────────────────────────────────────

def _detect_scraper(url: str):
    """Return (listing_fn, detail_fn, next_page_fn) for the given URL."""
    if "visitazores.com" in url:
        return _va_listing_links, _va_detail, _set_page
    if "tripadvisor." in url:
        return _ta_listing_links, _ta_detail, _ta_page_url
    return None, None, _set_page


async def _run_scraper(
    base_url: str,
    max_pages: int,
    regiao: str,
    nicho: str,
    progress_callback=None,
) -> list[dict]:
    cfg = CONFIG["scraper"]
    listing_fn, detail_fn, next_page_fn = _detect_scraper(base_url)

    is_ta = "tripadvisor." in base_url
    # TripAdvisor needs a real browser fingerprint
    ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        if is_ta else cfg["user_agent"]
    )

    all_detail_urls: set[str] = set()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=cfg["headless"])
        context = await browser.new_context(
            user_agent=ua,
            viewport={"width": 1280, "height": 900},
            locale="pt-PT" if is_ta else "pt",
            timezone_id="Europe/Lisbon" if is_ta else None,
        )

        # ── Phase 1: collect detail URLs ──────────────────────────────────────
        console.print("[cyan]Fase 1 — A percorrer listagens...[/]")
        for page_num in range(1, max_pages + 1):
            page_url = next_page_fn(base_url, page_num)
            pg = await context.new_page()
            try:
                resp = await pg.goto(page_url, wait_until="domcontentloaded", timeout=25000)
                if not resp or resp.status >= 400:
                    console.print(f"  Página {page_num}: HTTP {getattr(resp,'status','?')} — fim")
                    break

                # TripAdvisor needs extra wait for React render
                await pg.wait_for_timeout(3000 if is_ta else 2000)

                if is_ta:
                    await _ta_accept_cookies(pg)
                    await pg.wait_for_timeout(1000)

                if listing_fn:
                    html = await pg.content()
                    detail_urls = listing_fn(html, base_url)
                else:
                    detail_urls = await _generic_listing_links(pg, page_url)

                if not detail_urls:
                    console.print(f"  Página {page_num}: sem links de detalhe — fim")
                    break

                new_urls = [u for u in detail_urls if u not in all_detail_urls]
                if not new_urls:
                    console.print(f"  Página {page_num}: sem resultados novos — fim")
                    break

                all_detail_urls.update(new_urls)
                console.print(f"  Página {page_num}: +{len(new_urls)} → {len(all_detail_urls)} total")

            except Exception as e:
                console.print(f"  [red]Página {page_num}: {e}[/]")
                break
            finally:
                await pg.close()

            # Be polite with TripAdvisor — longer pause
            await asyncio.sleep(2.5 if is_ta else 1.5)

        # ── Phase 2: scrape detail pages ──────────────────────────────────────
        leads: list[dict] = []
        total_detail = len(all_detail_urls)
        console.print(f"\n[cyan]Fase 2 — A extrair dados de {total_detail} páginas...[/]")

        for i, detail_url in enumerate(sorted(all_detail_urls)):
            if progress_callback:
                progress_callback(i, total_detail)

            pg = await context.new_page()
            try:
                if detail_fn:
                    lead = await detail_fn(pg, detail_url, regiao, nicho)
                else:
                    lead = None

                if lead:
                    leads.append(lead)
                    status = "✓ website" if lead.get("website") else "✓ sem site"
                    rating_str = f" ★{lead['rating']}" if lead.get("rating") else ""
                    console.print(f"  [green]{status}[/]{rating_str} {lead['nome'][:50]}")
                else:
                    console.print(f"  [dim]✗[/] {detail_url[-55:]}")
            except Exception as e:
                console.print(f"  [red]✗[/] {detail_url[-40:]}: {e}")
            finally:
                await pg.close()

            if progress_callback:
                progress_callback(i + 1, total_detail)

            await asyncio.sleep(2.0 if is_ta else 1.0)

        await context.close()
        await browser.close()

    return leads


# ── Public callable ───────────────────────────────────────────────────────────

def web_discover(
    url: str,
    regiao: str | None = None,
    nicho: str | None = None,
    max_pages: int = 20,
    progress_callback=None,
) -> int:
    """
    Scrape a URL (auto-paginating) and merge new leads into leads_pendentes.json.
    Returns count of new leads added.
    """
    console.print(f"\n[bold cyan]Nexus OS[/] — Web Discovery")
    console.print(f"URL: [yellow]{url}[/]")

    _regiao, _nicho = _url_meta(url)
    regiao = regiao or _regiao
    nicho  = nicho  or _nicho
    source = "TripAdvisor" if "tripadvisor." in url else "VisitAzores" if "visitazores." in url else "Web"
    console.print(f"Fonte: [yellow]{source}[/]  |  Região: [yellow]{regiao}[/]  |  Nicho: [yellow]{nicho}[/]  |  Máx páginas: [yellow]{max_pages}[/]\n")

    new_leads = asyncio.run(_run_scraper(url, max_pages, regiao, nicho, progress_callback))

    if not new_leads:
        console.print("[yellow]Nenhuma empresa extraída.[/]")
        return 0

    existing: list[dict] = []
    if LEADS_FILE.exists():
        with open(LEADS_FILE, "r", encoding="utf-8-sig") as f:
            try:
                existing = json.load(f)
            except Exception:
                existing = []

    existing_ids = {l["place_id"] for l in existing}
    added = [l for l in new_leads if l["place_id"] not in existing_ids]
    all_leads = existing + added

    LEADS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LEADS_FILE, "w", encoding="utf-8") as f:
        json.dump(all_leads, f, ensure_ascii=False, indent=2)

    com_website = sum(1 for l in added if l.get("website"))
    com_rating  = sum(1 for l in added if l.get("rating"))

    table = Table(title=f"{source} Discovery — {nicho} / {regiao}", show_lines=True)
    table.add_column("Nome", style="cyan", max_width=38)
    table.add_column("Rating", max_width=8)
    table.add_column("Morada", max_width=30)
    table.add_column("Telefone", max_width=15)
    table.add_column("Website", style="green", max_width=28)

    for lead in added[:20]:
        rating_str = f"★{lead['rating']}" if lead.get("rating") else "—"
        table.add_row(
            lead["nome"][:38],
            rating_str,
            (lead.get("morada") or "—")[:30],
            lead.get("telefone") or "—",
            lead.get("website") or "[dim]sem website[/]",
        )
    console.print(table)
    console.print(
        f"\n[green]OK[/] {len(added)} novas  |  Com website: {com_website}"
        f"  |  Com rating: {com_rating}  |  Já existiam: {len(new_leads) - len(added)}\n"
    )
    return len(added)


# ── CLI ───────────────────────────────────────────────────────────────────────

@app.command()
def run(
    url:       str = typer.Option(...,  "--url",       "-u", help="URL da listagem"),
    regiao:    str = typer.Option(None, "--regiao",    "-r", help="Sobrepor região"),
    nicho:     str = typer.Option(None, "--nicho",     "-n", help="Sobrepor nicho"),
    max_pages: int = typer.Option(20,  "--max-pages",  "-m", help="Máximo de páginas"),
):
    """Web Discovery — TripAdvisor, VisitAzores e outros portais."""
    web_discover(url=url, regiao=regiao, nicho=nicho, max_pages=max_pages)


if __name__ == "__main__":
    app()
