import sys
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

import asyncio
import json
import random
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import typer
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, Page, TimeoutError as PWTimeout
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.table import Table

from config_loader import CONFIG

app = typer.Typer()
console = Console()

ROOT = Path(__file__).parent.parent
LEADS_FILE = ROOT / CONFIG["output"]["leads_file"]

# Patterns
_EMAIL_RE = re.compile(r"\b[a-zA-Z0-9._%+\-]{1,64}@[a-zA-Z0-9.\-]{1,253}\.[a-zA-Z]{2,10}\b")
# Covers all PT landlines (2xx) and all mobile prefixes 91-99 (Vodafone/MEO/NOS/MVNOs)
_PHONE_RE = re.compile(r"(?:\+351[\s\-]?)?(?:2\d{2}|9[1-9]\d)[\s\-]?\d{3}[\s\-]?\d{3}")
_EMAIL_BAD_EXT = re.compile(r"\.(png|jpg|jpeg|gif|svg|webp|ico|css|js|json|xml|pdf|woff|ttf)$", re.I)
_WHATSAPP_RE = re.compile(r"wa\.me/(?:\+?351)?[0-9]{6,}|api\.whatsapp\.com/send\?phone=", re.I)

# Booking: platform links (alta confiança) vs keywords genéricos (média confiança)
_BOOKING_PLATFORM_RE = re.compile(
    r"calendly\.com|booksy\.com|treatwell\.|fresha\.com|"
    r"reservio\.com|simplybook\.me|glofox\.com|mindbody\.io|"
    r"thefork\.com|eltenedor\.|tableo\.com|doctolib\.|"
    r"opentable\.com|resova\.com|zomato\.com|tripadvisor\.com/.*/reserve",
    re.I,
)
_BOOKING_IFRAME_RE = re.compile(
    r'<iframe[^>]+src=["\'][^"\']*(?:calendly|booksy|treatwell|fresha|simplybook|thefork|reservio|tableo)[^"\']*["\']',
    re.I | re.DOTALL,
)
_BOOKING_KEYWORDS_RE = re.compile(
    r"\b(?:reservar?\s+online|agendar?\s+online|marcação\s+online|"
    r"book\s+(?:now|online|here|a\s+table)|reserve\s+(?:now|online)|"
    r"appointment\s+booking|consulta\s+online|marcar\s+(?:consulta|sessão|visita)|"
    r"reservar?\s+(?:mesa|lugar|quarto|serviço))\b",
    re.I,
)
_SOCIAL_RE = {
    "facebook": re.compile(r"facebook\.com/(?!sharer)", re.I),
    "instagram": re.compile(r"instagram\.com/", re.I),
    "linkedin": re.compile(r"linkedin\.com/", re.I),
    "youtube": re.compile(r"youtube\.com/", re.I),
}

_SUBPAGES = [
    "/contacto", "/contactos", "/contact",
    "/sobre", "/sobre-nos", "/quem-somos",
    "/reservas", "/booking", "/agendar",
    "/servicos", "/services",
]


async def _enrich_from_subpages(page: Page, base_url: str, timeout: int) -> dict:
    """Visit up to 3 common sub-pages and extract additional contact/booking data."""
    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"

    extra: dict = {
        "emails": [],
        "telefones": [],
        "whatsapp_link": None,
        "tem_booking": False,
        "booking_hints": [],
        "formularios": 0,
        "subpages_visitadas": [],
        "texto_subpages": "",
    }

    sub_timeout = min(timeout, 8)
    visited = 0
    for path in _SUBPAGES:
        if visited >= 3:
            break
        try:
            resp = await page.goto(
                origin + path,
                wait_until="domcontentloaded",
                timeout=sub_timeout * 1000,
            )
            if not resp or resp.status >= 400:
                continue
            await page.wait_for_timeout(500)
            html = await page.content()
            soup = BeautifulSoup(html, "lxml")
            for tag in soup(["script", "style", "noscript", "svg", "img"]):
                tag.decompose()
            text = soup.get_text(separator=" ", strip=True)

            new_emails = [e for e in list(set(_EMAIL_RE.findall(text + html))) if not e.endswith((".png", ".jpg", ".gif"))]
            extra["emails"].extend(new_emails)
            extra["telefones"].extend(list(set(_PHONE_RE.findall(text))))

            links = [a.get("href", "") for a in soup.find_all("a", href=True)]
            if not extra["whatsapp_link"]:
                wa = [l for l in links if _WHATSAPP_RE.search(l)]
                if wa:
                    extra["whatsapp_link"] = wa[0]

            sub_platform_links = [l for l in links if _BOOKING_PLATFORM_RE.search(l)]
            sub_kw = list(set(_BOOKING_KEYWORDS_RE.findall(text[:3000])))
            if sub_platform_links or len(sub_kw) >= 3:
                extra["tem_booking"] = True
                extra["booking_hints"].extend(
                    [l.split("//")[-1].split("/")[0][:25] for l in sub_platform_links]
                    + list(set(sub_kw[:3]))
                )

            extra["formularios"] += len(soup.find_all("form"))
            extra["subpages_visitadas"].append(path)
            extra["texto_subpages"] += f"\n\n[{path}]:\n{text[:600]}"
            visited += 1
        except (PWTimeout, Exception):
            continue

    extra["emails"] = list(dict.fromkeys(extra["emails"]))[:5]
    extra["telefones"] = list(dict.fromkeys(extra["telefones"]))[:5]
    extra["booking_hints"] = list(dict.fromkeys(extra["booking_hints"]))[:5]
    return extra


_CMS_PATTERNS = [
    ("WordPress",   ["/wp-content/", "/wp-json/", "wp-includes", "xmlrpc.php"]),
    ("Wix",         ["wix.com/", "wixsite.com", "_wix_", "wixstatic.com"]),
    ("Squarespace", ["squarespace.com", "squarespace-cdn.com", "sqs-cdn.com"]),
    ("Shopify",     ["cdn.shopify.com", "shopify.com/s/", "Shopify.theme"]),
    ("Webflow",     ["webflow.com", "webflow.io", "webflow-badge"]),
    ("Jimdo",       ["jimdo.com", "jimdosite.com", "jimdofree.com"]),
    ("PrestaShop",  ["prestashop", "/modules/prestashop", "addons.prestashop"]),
    ("Joomla",      ["/administrator/", "joomla!", "/components/com_"]),
    ("SAPO",        ["sapo.pt/site", "saporedirect.pt"]),
]

_ANALYTICS_RE = re.compile(
    r"gtag\(|google-analytics\.com|googletagmanager\.com|ga\.js|analytics\.js", re.I
)
_PIXEL_RE = re.compile(
    r"fbq\(|facebook\.net/en_US/fbevents\.js|connect\.facebook\.net", re.I
)


async def _audit_page(page: Page, url: str, timeout: int) -> dict:
    result = {
        "url_auditada": url,
        "load_time": None,
        "emails": [],
        "telefones": [],
        "whatsapp_link": None,
        "redes_sociais": {},
        "tem_booking": False,
        "booking_hints": [],
        "formularios": 0,
        "texto_homepage": "",
        "subpages_visitadas": [],
        "favicon_url": None,
        "has_https": int(url.startswith("https://")),
        "has_mobile_meta": 0,
        "has_analytics": 0,
        "has_facebook_pixel": 0,
        "cms_detected": None,
        "page_word_count": 0,
        "erro": None,
    }

    try:
        t0 = time.time()
        response = await page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
        result["load_time"] = round(time.time() - t0, 2)
        result["status_code"] = response.status if response else None

        # Wait a bit for JS-heavy sites
        await page.wait_for_timeout(1500)

        html = await page.content()
        soup = BeautifulSoup(html, "lxml")

        # ── Favicon extraction (before decompose removes link tags) ──────────
        for rel in ("icon", "shortcut icon", "apple-touch-icon"):
            tag = soup.find("link", rel=lambda r, _rel=rel: r and _rel in " ".join(r).lower() if isinstance(r, list) else _rel in str(r).lower())
            if tag and tag.get("href"):
                href = tag["href"]
                result["favicon_url"] = urljoin(url, href) if not href.startswith("http") else href
                break
        if not result["favicon_url"]:
            domain = urlparse(url).netloc
            # Google favicon service always returns 200 — never 404
            result["favicon_url"] = f"https://www.google.com/s2/favicons?domain={domain}&sz=32"

        # ── Technical signals (on raw HTML before decompose) ─────────────────
        result["has_mobile_meta"]    = int(bool(soup.find("meta", attrs={"name": re.compile("viewport", re.I)})))
        result["has_analytics"]      = int(bool(_ANALYTICS_RE.search(html)))
        result["has_facebook_pixel"] = int(bool(_PIXEL_RE.search(html)))

        html_lower = html.lower()
        for cms_name, patterns in _CMS_PATTERNS:
            if any(p in html_lower for p in patterns):
                result["cms_detected"] = cms_name
                break

        # Remove noise for text extraction
        for tag in soup(["script", "style", "noscript", "svg", "img"]):
            tag.decompose()

        text = soup.get_text(separator=" ", strip=True)
        result["page_word_count"] = len(text.split())
        result["texto_homepage"] = text[:3000]

        # Emails — filter asset filenames and validate domain has at least one dot
        raw_emails = set(_EMAIL_RE.findall(text + html))
        result["emails"] = [
            e for e in raw_emails
            if not _EMAIL_BAD_EXT.search(e)
            and "." in e.split("@")[-1]
            and len(e.split("@")[-1]) > 3
        ][:5]

        # Phones
        result["telefones"] = list(set(_PHONE_RE.findall(text)))[:5]

        # WhatsApp
        links = [a.get("href", "") for a in soup.find_all("a", href=True)]
        wa_links = [l for l in links if _WHATSAPP_RE.search(l)]
        result["whatsapp_link"] = wa_links[0] if wa_links else None

        # Social
        for platform, pattern in _SOCIAL_RE.items():
            found = [l for l in links if pattern.search(l)]
            if found:
                result["redes_sociais"][platform] = found[0]

        # Booking — plataformas reais têm precedência sobre keywords genéricas
        # Keywords exigem 3+ frases únicas para reduzir falsos positivos em rodapés genéricos
        platform_links = [l for l in links if _BOOKING_PLATFORM_RE.search(l)]
        platform_iframe = bool(_BOOKING_IFRAME_RE.search(html))
        kw_matches = list(set(_BOOKING_KEYWORDS_RE.findall(text[:5000])))
        result["tem_booking"] = bool(platform_links) or platform_iframe or len(kw_matches) >= 3
        result["booking_hints"] = list(set(
            [l.split("//")[-1].split("/")[0][:25] for l in platform_links]
            + list(set(kw_matches[:3]))
        ))[:5]

        # Forms
        result["formularios"] = len(soup.find_all("form"))

    except PWTimeout:
        result["erro"] = f"timeout ({timeout}s)"
    except Exception as e:
        result["erro"] = str(e)[:200]

    # Sub-page enrichment — only when main page succeeded
    if not result["erro"]:
        sub = await _enrich_from_subpages(page, url, timeout)
        known_emails = set(result["emails"])
        result["emails"] = (result["emails"] + [e for e in sub["emails"] if e not in known_emails])[:5]
        known_tels = set(result["telefones"])
        result["telefones"] = (result["telefones"] + [t for t in sub["telefones"] if t not in known_tels])[:5]
        if not result["whatsapp_link"] and sub["whatsapp_link"]:
            result["whatsapp_link"] = sub["whatsapp_link"]
        if sub["tem_booking"]:
            result["tem_booking"] = True
            known_hints = set(result["booking_hints"])
            result["booking_hints"] = (
                result["booking_hints"] + [h for h in sub["booking_hints"] if h not in known_hints]
            )[:5]
        result["formularios"] += sub["formularios"]
        result["subpages_visitadas"] = sub["subpages_visitadas"]
        if sub["texto_subpages"]:
            result["texto_homepage"] = (result["texto_homepage"] + sub["texto_subpages"])[:4500]

    return result


async def _run_audit(leads: list[dict], max_sites: int | None, progress_callback=None) -> list[dict]:
    cfg_scraper = CONFIG["scraper"]
    delay_min = cfg_scraper["delay_min"]
    delay_max = cfg_scraper["delay_max"]
    timeout = cfg_scraper["timeout"]
    headless = cfg_scraper["headless"]
    ua = cfg_scraper["user_agent"]

    _done = {"auditado", "analisado", "erro_llm"}
    to_audit = [l for l in leads if l.get("website") and l.get("status") not in _done]
    if max_sites:
        to_audit = to_audit[:max_sites]

    if not to_audit:
        console.print("[yellow]Nenhum lead com website pendente de auditoria.[/]")
        return leads

    total = len(to_audit)
    console.print(f"[cyan]A auditar {total} sites...[/]\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Auditando...", total=total)

            for i, lead in enumerate(to_audit):
                nome = lead["nome"] or lead["website"]
                progress.update(task, description=f"[cyan]{nome[:40]}[/]")

                # New page per site — isolates crashes and JS state
                context = await browser.new_context(
                    user_agent=ua,
                    viewport={"width": 1280, "height": 800},
                    ignore_https_errors=True,
                    java_script_enabled=True,
                )
                page = await context.new_page()
                try:
                    audit = await _audit_page(page, lead["website"], timeout)
                finally:
                    await context.close()

                lead.update(audit)
                lead["status"] = "auditado" if not audit["erro"] else "erro_auditoria"

                progress.advance(task)

                if progress_callback:
                    progress_callback(i + 1, total)  # after processing

                if i < total - 1:
                    delay = random.uniform(delay_min, delay_max)
                    await asyncio.sleep(delay)

        await browser.close()

    return leads


def audit_all(max_sites: int | None = None, progress_callback=None) -> None:
    """Callable directly from pipeline.py — no Typer involved."""
    if not LEADS_FILE.exists():
        console.print(f"[red]ERRO:[/] {LEADS_FILE} não encontrado.")
        return
    with open(LEADS_FILE, "r", encoding="utf-8-sig") as f:
        leads = json.load(f)
    pendentes = [l for l in leads if l.get("website") and l.get("status") not in {"auditado", "analisado", "erro_llm"}]
    console.print(f"Leads para auditar: [bold]{len(pendentes)}[/]\n")
    if not pendentes:
        console.print("[yellow]Nenhum lead pendente de auditoria.[/]")
        return
    leads = asyncio.run(_run_audit(leads, max_sites, progress_callback))
    with open(LEADS_FILE, "w", encoding="utf-8") as f:
        json.dump(leads, f, ensure_ascii=False, indent=2)
    ok = sum(1 for l in leads if l.get("status") == "auditado")
    erros = sum(1 for l in leads if l.get("status") == "erro_auditoria")
    console.print(f"\n[green]OK[/] Auditados: {ok}  |  [red]Erros:[/] {erros}\n")


@app.command()
def run(
    max_sites: int = typer.Option(None, "--max", "-m", help="Limite de sites a auditar (útil para testes)"),
    so_com_website: bool = typer.Option(True, "--so-com-website/--todos", help="Auditar apenas leads com website"),
):
    """Fase 3 — Auditoria digital via Playwright."""
    if not LEADS_FILE.exists():
        console.print(f"[red]ERRO:[/] {LEADS_FILE} não encontrado. Corre primeiro 01_discovery.py.")
        raise typer.Exit(1)

    with open(LEADS_FILE, "r", encoding="utf-8-sig") as f:
        leads = json.load(f)

    pendentes = [l for l in leads if l.get("website") and l.get("status") != "auditado"]
    console.print(f"Leads carregados: [bold]{len(leads)}[/]  |  Com website pendente: [bold]{len(pendentes)}[/]\n")

    if not pendentes:
        console.print("[yellow]Todos os leads já auditados ou sem website.[/]")
        raise typer.Exit(0)

    leads = asyncio.run(_run_audit(leads, max_sites))

    # Save
    with open(LEADS_FILE, "w", encoding="utf-8") as f:
        json.dump(leads, f, ensure_ascii=False, indent=2)

    # Summary table
    auditados = [l for l in leads if l.get("status") == "auditado"]
    table = Table(title="Resultado da Auditoria", show_lines=True)
    table.add_column("Nome", max_width=30)
    table.add_column("Load", justify="right")
    table.add_column("Booking", justify="center")
    table.add_column("WhatsApp", justify="center")
    table.add_column("Emails", max_width=30)
    table.add_column("Erro", max_width=25, style="red")

    for lead in auditados[-20:]:
        table.add_row(
            (lead.get("nome") or "—")[:30],
            f"{lead.get('load_time', '—')}s",
            "[green]Sim[/]" if lead.get("tem_booking") else "[red]Nao[/]",
            "[green]Sim[/]" if lead.get("whatsapp_link") else "[red]Nao[/]",
            ", ".join(lead.get("emails", []))[:30] or "—",
            (lead.get("erro") or "—")[:25],
        )

    console.print(table)
    auditados_ok = sum(1 for l in leads if l.get("status") == "auditado")
    erros = sum(1 for l in leads if l.get("status") == "erro_auditoria")
    console.print(f"\n[green]OK[/] Auditados: {auditados_ok}  |  [red]Erros:[/] {erros}")
    console.print(f"[dim]Guardado em: {LEADS_FILE}[/]\n")


if __name__ == "__main__":
    app()
