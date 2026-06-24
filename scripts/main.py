import sys
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

"""
Nexus OS — CLI principal
Corre o pipeline completo ou etapas individuais.

Uso:
  python main.py run --nicho "Restaurantes" --regiao "Lagoa, Acores"
  python main.py discover --nicho "Clinicas" --regiao "Sao Miguel, Acores"
  python main.py audit --max 10
  python main.py analyze --max 5
  python main.py status
"""
import importlib.util
import json
import sys
import time
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

sys.path.insert(0, str(Path(__file__).parent))
from config_loader import CONFIG

app = typer.Typer(
    name="nexus",
    help="Nexus OS — Motor de prospeccao comercial B2B local",
    add_completion=False,
)
console = Console()

ROOT = Path(__file__).parent.parent
LEADS_FILE = ROOT / CONFIG["output"]["leads_file"]
DB_ROOT = ROOT / CONFIG["output"]["database_path"]


# ── Module loader ─────────────────────────────────────────────────────────────

def _load(name: str):
    scripts = Path(__file__).parent
    spec = importlib.util.spec_from_file_location(name, scripts / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── Helpers ───────────────────────────────────────────────────────────────────

def _lead_counts() -> dict:
    if not LEADS_FILE.exists():
        return {"total": 0, "pendente": 0, "auditado": 0, "analisado": 0, "erros": 0, "sem_website": 0}
    with open(LEADS_FILE, "r", encoding="utf-8-sig") as f:
        leads = json.load(f)
    return {
        "total": len(leads),
        "pendente": sum(1 for l in leads if l.get("status") == "pendente"),
        "auditado": sum(1 for l in leads if l.get("status") == "auditado"),
        "analisado": sum(1 for l in leads if l.get("status") == "analisado"),
        "erros": sum(1 for l in leads if "erro" in (l.get("status") or "")),
        "sem_website": sum(1 for l in leads if not l.get("website")),
    }


def _report_count() -> int:
    return len(list(DB_ROOT.rglob("*.md")))


def _banner():
    console.print(Panel.fit(
        "[bold cyan]Nexus OS[/] [dim]— Motor de Prospeccao B2B Local[/]",
        border_style="cyan",
    ))


# ── Commands ──────────────────────────────────────────────────────────────────

@app.command()
def run(
    nicho: str = typer.Option(..., "--nicho", "-n", help='Ex: "Restaurantes"'),
    regiao: str = typer.Option(..., "--regiao", "-r", help='Ex: "Lagoa, Acores"'),
    max_leads: int = typer.Option(None, "--max", "-m", help="Limite de leads por etapa"),
    so_discovery: bool = typer.Option(False, "--so-discovery", help="Parar apos discovery"),
    so_enrich: bool = typer.Option(False, "--so-enrich", help="Parar apos enriquecimento"),
    so_audit: bool = typer.Option(False, "--so-audit", help="Parar apos auditoria"),
):
    """Pipeline completo: Discovery (OSM) → Enriquecimento → Auditoria → Analise LLM."""
    _banner()
    t_inicio = time.time()

    # ── Step 1: Discovery ──
    console.print("\n[bold]Etapa 1/4 — Discovery (OpenStreetMap)[/]")
    disc = _load("01_discovery_free")
    disc.discover_osm(nicho=nicho, regiao=regiao)

    if so_discovery:
        raise typer.Exit(0)

    # ── Step 2: Enrichment ──
    console.print("\n[bold]Etapa 2/4 — Enriquecimento (encontrar websites)[/]")
    enrichment = _load("04_enrichment")
    enrichment.run_enrichment(max_leads=max_leads, progress_cb=None)

    if so_enrich:
        raise typer.Exit(0)

    counts = _lead_counts()
    com_website = counts["total"] - counts["sem_website"]
    if com_website == 0:
        console.print("[yellow]Nenhum lead com website encontrado. Pipeline parado.[/]")
        raise typer.Exit(0)

    # ── Step 3: Audit ──
    console.print("\n[bold]Etapa 3/4 — Auditoria Digital[/]")
    auditor = _load("02_auditor")
    auditor.audit_all(max_sites=max_leads)

    if so_audit:
        raise typer.Exit(0)

    # ── Step 4: LLM ──
    console.print("\n[bold]Etapa 4/4 — Analise LLM[/]")
    brain = _load("03_ai_brain")
    brain.analyze_all(max_leads=max_leads, reprocessar=False)

    # ── Summary ──
    elapsed = time.time() - t_inicio
    counts = _lead_counts()
    reports = _report_count()

    console.print()
    table = Table(title="Resumo Final", show_header=False, box=None, padding=(0, 2))
    table.add_column(style="dim")
    table.add_column(style="bold")
    table.add_row("Nicho / Regiao", f"{nicho} / {regiao}")
    table.add_row("Total leads", str(counts["total"]))
    table.add_row("Sem website", str(counts["sem_website"]))
    table.add_row("Auditados", str(counts["auditado"] + counts["analisado"]))
    table.add_row("Analisados (LLM)", str(counts["analisado"]))
    table.add_row("Relatorios gerados", str(reports))
    table.add_row("Erros", str(counts["erros"]))
    table.add_row("Tempo total", f"{elapsed:.1f}s")
    console.print(table)
    console.print(f"\n[dim]Relatorios em: {DB_ROOT}[/]\n")


@app.command()
def discover(
    nicho: str = typer.Option(..., "--nicho", "-n"),
    regiao: str = typer.Option(..., "--regiao", "-r"),
):
    """Etapa 1: Discovery de empresas via OpenStreetMap (gratis, sem API key)."""
    _banner()
    disc = _load("01_discovery_free")
    disc.discover_osm(nicho=nicho, regiao=regiao)


@app.command(name="discover-free")
def discover_free(
    nicho: str = typer.Option(..., "--nicho", "-n"),
    regiao: str = typer.Option(..., "--regiao", "-r"),
):
    """Alias de discover — Discovery via OpenStreetMap."""
    _banner()
    disc = _load("01_discovery_free")
    disc.discover_osm(nicho=nicho, regiao=regiao)


@app.command()
def enrich(
    max_leads: int = typer.Option(None, "--max", "-m", help="Limite de leads a processar"),
):
    """Etapa 2: Enriquecimento — encontra websites para empresas sem URL."""
    _banner()
    enrichment = _load("04_enrichment")
    enrichment.run_enrichment(max_leads=max_leads, progress_cb=None)


@app.command()
def audit(
    max_sites: int = typer.Option(None, "--max", "-m"),
):
    """Apenas Etapa 2: Auditoria digital dos sites."""
    _banner()
    auditor = _load("02_auditor")
    auditor.audit_all(max_sites=max_sites)


@app.command()
def analyze(
    max_leads: int = typer.Option(None, "--max", "-m"),
    reprocessar: bool = typer.Option(False, "--reprocessar"),
):
    """Apenas Etapa 3: Analise LLM + geracao de relatorios Markdown."""
    _banner()
    brain = _load("03_ai_brain")
    brain.analyze_all(max_leads=max_leads, reprocessar=reprocessar)


@app.command()
def status():
    """Mostra estado atual do pipeline e leads."""
    _banner()
    counts = _lead_counts()
    reports = _report_count()

    table = Table(title="Estado do Pipeline", show_lines=True)
    table.add_column("Etapa", style="cyan")
    table.add_column("Count", justify="right", style="bold")
    table.add_column("Descricao")

    table.add_row("Total leads", str(counts["total"]), "Todos os leads no ficheiro")
    table.add_row("Pendentes", str(counts["pendente"]), "Descobertos, sem auditoria")
    table.add_row("Sem website", str(counts["sem_website"]), "Nao auditaveis")
    table.add_row("Auditados", str(counts["auditado"]), "Site visitado, pendente LLM")
    table.add_row("Analisados", str(counts["analisado"]), "LLM completo")
    table.add_row("Erros", str(counts["erros"]), "Falhas em alguma etapa")
    table.add_row("Relatorios .md", str(reports), f"Em {DB_ROOT.relative_to(ROOT)}/")

    console.print()
    console.print(table)

    if counts["total"] == 0:
        console.print("\n[dim]Sem leads. Corre: python main.py discover --nicho X --regiao Y[/]")
    elif counts["sem_website"] > 0 and counts["pendente"] > 0:
        console.print(f"\n[yellow]Proximo passo:[/] python main.py enrich")
    elif counts["pendente"] > 0:
        console.print(f"\n[yellow]Proximo passo:[/] python main.py audit")
    elif counts["auditado"] > 0:
        console.print(f"\n[yellow]Proximo passo:[/] python main.py analyze")
    else:
        console.print("\n[green]Pipeline completo.[/] Relatorios disponiveis em database/")

    console.print()


@app.command()
def export(
    min_score: int = typer.Option(0, "--min-score", "-s", help="Score minimo (0-100)"),
    regiao: str = typer.Option(None, "--regiao", "-r", help="Filtrar por regiao (texto parcial)"),
    nicho: str = typer.Option(None, "--nicho", "-n", help="Filtrar por nicho (texto parcial)"),
    formato: str = typer.Option("csv", "--formato", "-f", help="csv ou md"),
    output: str = typer.Option(None, "--output", "-o", help="Caminho do ficheiro de saida"),
):
    """Exporta leads analisados ordenados por score para CSV ou Markdown."""
    if not LEADS_FILE.exists():
        console.print("[red]ERRO:[/] Sem leads. Corre primeiro o pipeline.")
        raise typer.Exit(1)

    with open(LEADS_FILE, "r", encoding="utf-8-sig") as f:
        leads = json.load(f)

    targets = [l for l in leads if l.get("status") == "analisado" and l.get("score", 0) >= min_score]

    if regiao:
        targets = [l for l in targets if regiao.lower() in (l.get("regiao") or "").lower()]
    if nicho:
        targets = [l for l in targets if nicho.lower() in (l.get("nicho") or "").lower()]

    targets.sort(key=lambda l: l.get("score", 0), reverse=True)

    if not targets:
        console.print("[yellow]Nenhum lead analisado com esses filtros.[/]")
        raise typer.Exit(0)

    if formato == "csv":
        import csv, io
        buf = io.StringIO()
        fields = ["score", "nome", "nicho", "regiao", "website", "email", "telefone", "tags", "whatsapp_link"]
        writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for lead in targets:
            row = {**lead}
            row["email"] = ", ".join(lead.get("emails") or [])
            row["telefone"] = ", ".join(lead.get("telefones") or [])
            row["tags"] = ", ".join(lead.get("tags") or [])
            writer.writerow({k: row.get(k, "") for k in fields})
        content = buf.getvalue()
        ext = "csv"
    else:
        lines = ["# Nexus OS — Leads Exportados\n",
                 f"Filtros: score>={min_score}"
                 + (f" | regiao={regiao}" if regiao else "")
                 + (f" | nicho={nicho}" if nicho else "") + "\n\n",
                 "| Score | Nome | Nicho | Website | Email | WhatsApp |\n",
                 "|-------|------|-------|---------|-------|----------|\n"]
        for lead in targets:
            email = (lead.get("emails") or ["—"])[0]
            wa = "Sim" if lead.get("whatsapp_link") else "Nao"
            lines.append(
                f"| {lead.get('score','—')} "
                f"| {lead.get('nome','—')} "
                f"| {lead.get('nicho','—')} "
                f"| {lead.get('website','—')} "
                f"| {email} "
                f"| {wa} |\n"
            )
        content = "".join(lines)
        ext = "md"

    out_path = Path(output) if output else ROOT / f"export_leads.{ext}"
    out_path.write_text(content, encoding="utf-8")

    console.print(f"\n[green]OK[/] {len(targets)} leads exportados → {out_path}")

    # Preview table
    table = Table(title=f"Top leads (score >= {min_score})", show_lines=True)
    table.add_column("Score", justify="center", style="bold yellow")
    table.add_column("Nome", max_width=30)
    table.add_column("Nicho", max_width=20)
    table.add_column("Website", max_width=35, style="cyan")
    table.add_column("Email", max_width=30)

    for lead in targets[:15]:
        table.add_row(
            str(lead.get("score", "—")),
            (lead.get("nome") or "—")[:30],
            (lead.get("nicho") or "—")[:20],
            (lead.get("website") or "—")[:35],
            (lead.get("emails") or ["—"])[0][:30],
        )
    console.print(table)
    console.print()


if __name__ == "__main__":
    app()
