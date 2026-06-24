import sys
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

"""
Discovery gratuito via OpenStreetMap / Overpass API.
Sem API key, sem conta, completamente grátis.

Uso:
  python 01_discovery_free.py --nicho "Restaurantes" --regiao "Sao Miguel, Acores"
  python 01_discovery_free.py --listar-nichos
"""

import hashlib
import json
import urllib.parse
from pathlib import Path

import httpx
import typer
from rich.console import Console
from rich.table import Table

from config_loader import CONFIG

app = typer.Typer()
console = Console()

ROOT = Path(__file__).parent.parent
LEADS_FILE = ROOT / CONFIG["output"]["leads_file"]

# ── Bounding boxes das ilhas dos Açores ──────────────────────────────────────
# formato: (sul, oeste, norte, este)
ILHAS_BBOX = {
    "sao_miguel":    (37.68, -25.88, 37.90, -25.08),
    "terceira":      (38.61, -27.40, 38.85, -27.00),
    "faial":         (38.50, -28.82, 38.65, -28.60),
    "pico":          (38.38, -28.55, 38.58, -28.00),
    "sao_jorge":     (38.58, -28.30, 38.78, -27.80),
    "flores":        (39.41, -31.30, 39.55, -31.10),
    "graciosa":      (39.00, -28.10, 39.10, -27.90),
    "santa_maria":   (36.90, -25.20, 37.00, -25.00),
    "corvo":         (39.65, -31.18, 39.72, -31.08),
    "acores":        (36.90, -31.30, 39.75, -25.00),  # todas as ilhas
}

# ── Mapeamento nicho → tags OSM ───────────────────────────────────────────────
NICHO_TAGS = {
    # Alimentação
    "restaurantes":         [("amenity", "restaurant")],
    "cafes":                [("amenity", "cafe")],
    "bares":                [("amenity", "bar"), ("amenity", "pub")],
    "pastelarias":          [("amenity", "cafe"), ("shop", "pastry")],
    "takeaway":             [("amenity", "fast_food")],

    # Saúde
    "clinicas":             [("amenity", "clinic"), ("healthcare", "clinic")],
    "dentistas":            [("healthcare", "dentist"), ("amenity", "dentist")],
    "farmacias":            [("amenity", "pharmacy")],
    "medicos":              [("amenity", "doctors"), ("healthcare", "doctor")],
    "veterinarios":         [("amenity", "veterinary")],

    # Alojamento
    "hoteis":               [("tourism", "hotel")],
    "alojamento_local":     [("tourism", "guest_house"), ("tourism", "hostel")],

    # Comércio
    "supermercados":        [("shop", "supermarket"), ("shop", "convenience")],
    "talhos":               [("shop", "butcher")],
    "peixarias":            [("shop", "seafood"), ("shop", "fishmonger")],
    "padarias":             [("shop", "bakery")],
    "lojas":                [("shop", "clothes"), ("shop", "shoes")],

    # Serviços
    "cabeleireiros":        [("shop", "hairdresser")],
    "ginasios":             [("leisure", "fitness_centre")],
    "garagens":             [("shop", "car_repair")],
    "posto_combustivel":    [("amenity", "fuel")],
    "bancos":               [("amenity", "bank")],
    "seguros":              [("office", "insurance")],
    "contabilidade":        [("office", "accountant"), ("office", "tax_advisor")],
    "advogados":            [("office", "lawyer")],
    "imobiliarias":         [("office", "estate_agent")],

    # Turismo/Lazer
    "museus":               [("tourism", "museum")],
    "aluguer_carros":       [("amenity", "car_rental")],
    "actividades":          [("tourism", "activity"), ("leisure", "sports_centre")],
}


def _slugify_simple(text: str) -> str:
    import unicodedata, re
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _resolve_bbox(regiao: str) -> tuple:
    """Map 'Sao Miguel, Acores' → bounding box tuple."""
    parts = [_slugify_simple(p.strip()) for p in regiao.split(",")]
    # Try ilha first, then full region
    for part in parts:
        if part in ILHAS_BBOX:
            return ILHAS_BBOX[part]
    # Partial match
    for key, bbox in ILHAS_BBOX.items():
        if any(part in key or key in part for part in parts):
            return bbox
    return ILHAS_BBOX["acores"]


def _resolve_tags(nicho: str) -> list[tuple]:
    """Map nicho string to OSM tag pairs."""
    key = _slugify_simple(nicho)
    if key in NICHO_TAGS:
        return NICHO_TAGS[key]
    # Partial match
    for k, tags in NICHO_TAGS.items():
        if key in k or k in key:
            return tags
    # Default: try as amenity value
    return [("amenity", nicho.lower()), ("shop", nicho.lower())]


def _build_overpass_query(bbox: tuple, tags: list[tuple]) -> str:
    s, w, n, e = bbox
    bbox_str = f"{s},{w},{n},{e}"
    filters = []
    for tag_k, tag_v in tags:
        filters.append(f'  node["{tag_k}"="{tag_v}"]({bbox_str});')
        filters.append(f'  way["{tag_k}"="{tag_v}"]({bbox_str});')
    union = "\n".join(filters)
    return f"[out:json][timeout:40];\n(\n{union}\n);\nout body center;"


_OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]


def _query_overpass(query: str) -> list[dict]:
    headers = {
        "User-Agent": "NexusOS/1.0 (local business research)",
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    body = urllib.parse.urlencode({"data": query})
    last_err = None
    for mirror in _OVERPASS_MIRRORS:
        try:
            resp = httpx.post(
                mirror,
                content=body.encode("utf-8"),
                headers=headers,
                timeout=60,
                verify=False,
            )
            resp.raise_for_status()
            return resp.json().get("elements", [])
        except Exception as e:
            console.print(f"[dim]Mirror {mirror} falhou: {e}. A tentar proximo...[/]")
            last_err = e
    raise last_err


def _osm_to_lead(el: dict, nicho: str, regiao: str) -> dict | None:
    tags = el.get("tags", {})
    nome = tags.get("name") or tags.get("brand")
    if not nome:
        return None

    # Coordinates — nodes have lat/lon directly, ways have center
    lat = el.get("lat") or (el.get("center") or {}).get("lat")
    lon = el.get("lon") or (el.get("center") or {}).get("lon")

    # Build address
    parts = []
    if tags.get("addr:street"):
        parts.append(tags["addr:street"])
        if tags.get("addr:housenumber"):
            parts[-1] += " " + tags["addr:housenumber"]
    if tags.get("addr:city") or tags.get("addr:place"):
        parts.append(tags.get("addr:city") or tags.get("addr:place"))
    if tags.get("addr:postcode"):
        parts.append(tags["addr:postcode"])
    morada = ", ".join(parts) if parts else None

    # Phone — OSM uses semicolons for multiple numbers
    phone_raw = (tags.get("contact:phone") or tags.get("phone") or "")
    phone = phone_raw.split(";")[0].strip() if phone_raw else None

    website = tags.get("website") or tags.get("contact:website") or tags.get("url")

    # Stable ID from OSM element
    uid = f"osm_{el['type']}_{el['id']}"

    return {
        "place_id": uid,
        "nome": nome,
        "morada": morada,
        "lat": lat,
        "lon": lon,
        "rating": None,
        "total_reviews": None,
        "website": website,
        "telefone": phone,
        "nicho": nicho,
        "regiao": regiao,
        "status": "pendente",
        "source": "openstreetmap",
        "osm_tags": {k: v for k, v in tags.items()
                     if k in ("cuisine", "opening_hours", "email",
                               "contact:email", "wheelchair", "ref:vatin")},
    }


def discover_osm(nicho: str, regiao: str) -> int:
    """Core logic — returns number of new leads added. Callable from main.py."""
    console.print(f"\n[bold cyan]Nexus OS[/] — Discovery Gratuito (OpenStreetMap)")
    console.print(f"Nicho: [yellow]{nicho}[/]  |  Regiao: [yellow]{regiao}[/]\n")

    bbox = _resolve_bbox(regiao)
    tags = _resolve_tags(nicho)

    console.print(f"[dim]Bounding box: {bbox}[/]")
    console.print(f"[dim]Tags OSM: {tags}[/]")
    console.print("[dim]A consultar Overpass API (OpenStreetMap)...[/]\n")

    try:
        query = _build_overpass_query(bbox, tags)
        elements = _query_overpass(query)
    except Exception as e:
        console.print(f"[red]ERRO Overpass API:[/] {e}")
        return 0

    console.print(f"Elementos OSM encontrados: [bold]{len(elements)}[/]")

    leads = []
    for el in elements:
        lead = _osm_to_lead(el, nicho, regiao)
        if lead:
            leads.append(lead)

    console.print(f"Leads com nome: [bold]{len(leads)}[/]\n")

    existing = []
    if LEADS_FILE.exists():
        with open(LEADS_FILE, "r", encoding="utf-8-sig") as f:
            existing = json.load(f)

    existing_ids = {l["place_id"] for l in existing}
    new_leads = [l for l in leads if l["place_id"] not in existing_ids]
    all_leads = existing + new_leads

    LEADS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LEADS_FILE, "w", encoding="utf-8") as f:
        json.dump(all_leads, f, ensure_ascii=False, indent=2)

    sem_website = sum(1 for l in new_leads if not l.get("website"))
    com_website = len(new_leads) - sem_website
    com_tel = sum(1 for l in new_leads if l.get("telefone"))

    table = Table(title=f"Leads OSM — {nicho} / {regiao}", show_lines=True)
    table.add_column("Nome", style="cyan", max_width=35)
    table.add_column("Morada", max_width=35)
    table.add_column("Telefone", max_width=18)
    table.add_column("Website", style="green", max_width=30)

    for lead in new_leads[:20]:
        table.add_row(
            lead["nome"][:35],
            (lead["morada"] or "—")[:35],
            lead["telefone"] or "—",
            lead["website"] or "[dim]sem website[/]",
        )

    console.print(table)
    console.print(f"\n[green]OK[/] {len(new_leads)} leads novos  |  Com website: {com_website}  |  Com telefone: {com_tel}")
    console.print(f"[dim]Guardado em: {LEADS_FILE}[/]\n")

    if sem_website > 0:
        console.print(
            f"[yellow]Nota:[/] {sem_website} empresas sem website no OSM — "
            "o auditor vai skippar essas.\n"
        )
    return len(new_leads)


@app.command()
def run(
    nicho: str = typer.Option(..., "--nicho", "-n", help='Ex: "Restaurantes"'),
    regiao: str = typer.Option(..., "--regiao", "-r", help='Ex: "Sao Miguel, Acores"'),
):
    """Discovery gratuito via OpenStreetMap — sem API key."""
    discover_osm(nicho=nicho, regiao=regiao)


@app.command(name="listar-nichos")
def listar_nichos():
    """Lista todos os nichos suportados e as suas tags OSM."""
    table = Table(title="Nichos suportados", show_lines=True)
    table.add_column("Nicho", style="cyan")
    table.add_column("Tags OSM")
    for nicho, tags in NICHO_TAGS.items():
        table.add_row(nicho, str(tags))
    console.print(table)

    console.print("\n[dim]Regioes suportadas:[/]")
    for ilha in ILHAS_BBOX:
        console.print(f"  {ilha}")
    console.print()


if __name__ == "__main__":
    app()
