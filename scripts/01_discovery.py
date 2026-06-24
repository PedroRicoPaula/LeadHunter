import sys
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

import json
import time
from pathlib import Path

import googlemaps
import typer
from rich.console import Console
from rich.progress import track
from rich.table import Table

from config_loader import CONFIG

app = typer.Typer()
console = Console()

ROOT = Path(__file__).parent.parent
LEADS_FILE = ROOT / CONFIG["output"]["leads_file"]


def _places_client() -> googlemaps.Client:
    key = CONFIG["google_places"]["api_key"]
    if not key or key.startswith("COLOCA"):
        console.print("[bold red]ERRO:[/] google_places.api_key não configurada em settings.yaml")
        raise typer.Exit(1)
    return googlemaps.Client(key=key)


def _search_places(gmaps: googlemaps.Client, query: str, regiao: str) -> list[dict]:
    """Text search + paginate up to 3 pages (max 60 results)."""
    full_query = f"{query} em {regiao}"
    results = []
    response = gmaps.places(query=full_query, language="pt")
    results.extend(response.get("results", []))

    # Paginate
    for _ in range(2):
        token = response.get("next_page_token")
        if not token:
            break
        time.sleep(2)  # Google requires delay before using next_page_token
        response = gmaps.places(query=full_query, language="pt", page_token=token)
        results.extend(response.get("results", []))

    return results


def _get_place_details(gmaps: googlemaps.Client, place_id: str) -> dict:
    fields = ["name", "formatted_address", "website", "formatted_phone_number",
              "rating", "user_ratings_total", "opening_hours", "url"]
    response = gmaps.place(place_id=place_id, fields=fields, language="pt")
    return response.get("result", {})


def _slugify(text: str) -> str:
    import unicodedata, re
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _regiao_to_path(regiao: str) -> Path:
    """Map 'São Miguel, Açores' → database/Acores/Sao_Miguel"""
    parts = [p.strip() for p in regiao.split(",")]
    db_root = ROOT / CONFIG["output"]["database_path"]
    if len(parts) >= 2:
        ilha = _slugify(parts[0]).replace(" ", "_").title()
        arquipelago = _slugify(parts[1]).replace(" ", "_").title()
        return db_root / arquipelago / ilha
    return db_root / _slugify(parts[0])


@app.command()
def run(
    nicho: str = typer.Option(..., "--nicho", "-n", help='Ex: "Clínicas Dentárias"'),
    regiao: str = typer.Option(..., "--regiao", "-r", help='Ex: "São Miguel, Açores"'),
    detalhar: bool = typer.Option(False, "--detalhar", help="Buscar detalhes (website, tel) via Place Details API"),
):
    """Fase 2 — Descoberta de leads via Google Places API."""
    console.print(f"\n[bold cyan]Nexus OS[/] — Descoberta")
    console.print(f"Nicho: [yellow]{nicho}[/]  |  Região: [yellow]{regiao}[/]\n")

    gmaps = _places_client()

    console.print("[dim]A pesquisar no Google Places...[/]")
    raw_results = _search_places(gmaps, nicho, regiao)
    console.print(f"Encontrados [bold]{len(raw_results)}[/] resultados brutos.\n")

    leads = []
    for place in track(raw_results, description="A processar..."):
        lead = {
            "place_id": place.get("place_id"),
            "nome": place.get("name"),
            "morada": place.get("formatted_address"),
            "rating": place.get("rating"),
            "total_reviews": place.get("user_ratings_total"),
            "website": None,
            "telefone": None,
            "nicho": nicho,
            "regiao": regiao,
            "status": "pendente",
        }

        if detalhar and lead["place_id"]:
            details = _get_place_details(gmaps, lead["place_id"])
            lead["website"] = details.get("website")
            lead["telefone"] = details.get("formatted_phone_number")
            lead["google_url"] = details.get("url")
            time.sleep(0.5)  # rate limit

        leads.append(lead)

    # Save
    LEADS_FILE.parent.mkdir(parents=True, exist_ok=True)

    # Merge with existing leads (avoid duplicates by place_id)
    existing = []
    if LEADS_FILE.exists():
        with open(LEADS_FILE, "r", encoding="utf-8") as f:
            existing = json.load(f)

    existing_ids = {l["place_id"] for l in existing}
    new_leads = [l for l in leads if l["place_id"] not in existing_ids]
    all_leads = existing + new_leads

    with open(LEADS_FILE, "w", encoding="utf-8") as f:
        json.dump(all_leads, f, ensure_ascii=False, indent=2)

    # Summary table
    table = Table(title=f"Leads — {nicho} / {regiao}", show_lines=True)
    table.add_column("Nome", style="cyan", max_width=35)
    table.add_column("Rating", justify="center")
    table.add_column("Website", style="green", max_width=40)
    table.add_column("Morada", max_width=35)

    for lead in leads[:20]:  # show first 20
        table.add_row(
            lead["nome"] or "—",
            str(lead["rating"] or "—"),
            lead["website"] or "[dim]sem website[/]",
            (lead["morada"] or "—")[:40],
        )

    console.print(table)
    console.print(f"\n[bold green]✓[/] {len(new_leads)} leads novos. Total ficheiro: {len(all_leads)}")
    console.print(f"[dim]Guardado em: {LEADS_FILE}[/]\n")

    if not detalhar:
        console.print("[yellow]Dica:[/] Corre com [bold]--detalhar[/] para ir buscar website + telefone de cada empresa.\n")


if __name__ == "__main__":
    app()
