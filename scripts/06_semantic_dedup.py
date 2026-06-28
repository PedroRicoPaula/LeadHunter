import sys
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

"""
Deduplicação semântica de leads via NIM Embeddings.

Detecta empresas duplicadas mesmo com nomes ligeiramente diferentes
(ex: "Café A Ribeira" vs "Ribeira Café", "Clínica X" vs "Clinica X Lda").

Uso standalone:
  python 06_semantic_dedup.py run
  python 06_semantic_dedup.py similar --nome "Café Central"

Cache de embeddings em database/.nim_embeddings.json
(separado do leads_pendentes.json para não o inflar).
"""

import json
import math
import unicodedata
import re
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from config_loader import CONFIG

app = typer.Typer()
console = Console()

ROOT       = Path(__file__).parent.parent
LEADS_FILE = ROOT / CONFIG["output"]["leads_file"]
DB_ROOT    = ROOT / CONFIG["output"]["database_path"]
CACHE_FILE = DB_ROOT / ".nim_embeddings.json"

_SIMILARITY_THRESHOLD = 0.92  # acima disto → duplicado


# ── Helpers ───────────────────────────────────────────────────────────────────

def _lead_text(lead: dict) -> str:
    """Texto representativo do lead para embedding."""
    parts = [
        lead.get("nome") or "",
        lead.get("nicho") or "",
        lead.get("regiao") or "",
        (lead.get("morada") or "")[:60],
    ]
    return " | ".join(p for p in parts if p).strip()


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na  = math.sqrt(sum(x * x for x in a))
    nb  = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _load_cache() -> dict[str, list[float]]:
    """place_id → embedding vector."""
    DB_ROOT.mkdir(parents=True, exist_ok=True)
    if not CACHE_FILE.exists():
        return {}
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(cache: dict[str, list[float]]) -> None:
    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")


# ── Core ──────────────────────────────────────────────────────────────────────

def embed_all_pending(leads: list[dict]) -> int:
    """
    Calcula embeddings para leads sem cache.
    Retorna número de leads recém-embeddados.
    """
    from nim_client import nim
    if not nim.enabled:
        console.print("[yellow]NIM indisponível — deduplicação semântica ignorada.[/]")
        return 0

    cache = _load_cache()
    pending = [l for l in leads if l.get("place_id") and l["place_id"] not in cache]
    if not pending:
        return 0

    texts = [_lead_text(l) for l in pending]
    console.print(f"  [dim]A calcular embeddings de {len(pending)} leads novos...[/]")
    vectors = nim.embed(texts, input_type="passage")
    if not vectors or len(vectors) != len(pending):
        console.print("  [yellow]NIM embed falhou — deduplicação ignorada.[/]")
        return 0

    for lead, vec in zip(pending, vectors):
        cache[lead["place_id"]] = vec

    _save_cache(cache)
    return len(pending)


def find_duplicates(
    leads: list[dict],
    threshold: float = _SIMILARITY_THRESHOLD,
) -> list[tuple[dict, dict, float]]:
    """
    Detecta pares de leads semanticamente duplicados.
    Retorna lista de (lead_a, lead_b, similarity).
    Complexidade O(n²) — OK até ~5 000 leads.
    """
    cache = _load_cache()
    # Só compara leads com embedding
    indexed = [(l, cache[l["place_id"]]) for l in leads if l.get("place_id") in cache]
    duplicates: list[tuple[dict, dict, float]] = []
    seen: set[frozenset] = set()

    for i, (la, va) in enumerate(indexed):
        for lb, vb in indexed[i + 1:]:
            key = frozenset([la["place_id"], lb["place_id"]])
            if key in seen:
                continue
            sim = _cosine(va, vb)
            if sim >= threshold:
                duplicates.append((la, lb, sim))
                seen.add(key)

    return sorted(duplicates, key=lambda x: x[2], reverse=True)


def find_similar(
    lead: dict,
    all_leads: list[dict],
    top_k: int = 5,
    exclude_self: bool = True,
) -> list[tuple[dict, float]]:
    """
    Encontra os leads mais semelhantes a `lead`.
    Útil para "empresas parecidas" na UI.
    """
    from nim_client import nim
    cache = _load_cache()

    pid = lead.get("place_id", "")
    if pid not in cache:
        # Embedd este lead on-the-fly
        vec = nim.embed([_lead_text(lead)], input_type="query")
        if not vec:
            return []
        query_vec = vec[0]
    else:
        query_vec = cache[pid]

    scored: list[tuple[dict, float]] = []
    for l in all_leads:
        if exclude_self and l.get("place_id") == pid:
            continue
        lpid = l.get("place_id", "")
        if lpid not in cache:
            continue
        sim = _cosine(query_vec, cache[lpid])
        scored.append((l, round(sim, 4)))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_k]


def deduplicate(leads: list[dict], threshold: float = _SIMILARITY_THRESHOLD) -> tuple[list[dict], int]:
    """
    Remove duplicados semânticos de uma lista de leads.
    Mantém o lead com mais dados (website, score, etc.).
    Retorna (leads_únicos, n_removidos).
    """
    cache = _load_cache()
    dups = find_duplicates(leads, threshold)
    if not dups:
        return leads, 0

    to_remove: set[str] = set()
    for la, lb, sim in dups:
        # Mantém o lead com mais campos preenchidos
        score_a = sum(1 for k in ("website", "emails", "score", "redes_sociais") if la.get(k))
        score_b = sum(1 for k in ("website", "emails", "score", "redes_sociais") if lb.get(k))
        loser = lb["place_id"] if score_a >= score_b else la["place_id"]
        to_remove.add(loser)

    unique = [l for l in leads if l.get("place_id") not in to_remove]
    return unique, len(to_remove)


# ── Public callable ───────────────────────────────────────────────────────────

def run_dedup(progress_callback=None) -> dict:
    """
    Ponto de entrada para pipeline.py.
    1. Calcula embeddings para leads novos.
    2. Detecta e remove duplicados.
    3. Guarda leads_pendentes.json actualizado.
    Retorna stats: {embedded, duplicates_found, removed}.
    """
    if not LEADS_FILE.exists():
        return {"embedded": 0, "duplicates_found": 0, "removed": 0}

    with open(LEADS_FILE, "r", encoding="utf-8-sig") as f:
        leads = json.load(f)

    embedded = embed_all_pending(leads)
    dups = find_duplicates(leads)
    unique, removed = deduplicate(leads)

    if removed > 0:
        with open(LEADS_FILE, "w", encoding="utf-8") as f:
            json.dump(unique, f, ensure_ascii=False, indent=2)
        console.print(f"  [green]Dedup:[/] {embedded} embeddados  |  {len(dups)} pares duplicados  |  {removed} removidos")
    else:
        console.print(f"  [dim]Dedup: {embedded} embeddados, sem duplicados detectados[/]")

    return {"embedded": embedded, "duplicates_found": len(dups), "removed": removed}


# ── CLI ───────────────────────────────────────────────────────────────────────

@app.command()
def run(
    threshold: float = typer.Option(_SIMILARITY_THRESHOLD, "--threshold", "-t",
                                    help="Limiar de similaridade (0-1). Default=0.92"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Mostra duplicados sem remover"),
):
    """Deduplicação semântica — detecta e remove leads duplicados via embeddings NIM."""
    from nim_client import nim
    if not nim.enabled:
        console.print("[red]ERRO:[/] NIM não disponível. Verifica NVIDIA_API_KEY no .env")
        raise typer.Exit(1)

    if not LEADS_FILE.exists():
        console.print(f"[red]ERRO:[/] {LEADS_FILE} não encontrado.")
        raise typer.Exit(1)

    with open(LEADS_FILE, "r", encoding="utf-8-sig") as f:
        leads = json.load(f)

    console.print(f"\n[bold cyan]Deduplicação Semântica[/] — {len(leads)} leads")

    embedded = embed_all_pending(leads)
    console.print(f"Embeddings calculados: [bold]{embedded}[/]")

    dups = find_duplicates(leads, threshold)
    if not dups:
        console.print("[green]Sem duplicados detectados.[/]\n")
        raise typer.Exit(0)

    table = Table(title=f"Duplicados detectados (threshold={threshold})", show_lines=True)
    table.add_column("Empresa A", style="cyan", max_width=35)
    table.add_column("Empresa B", style="yellow", max_width=35)
    table.add_column("Sim.", justify="center")
    table.add_column("Nicho", max_width=20)

    for la, lb, sim in dups[:30]:
        table.add_row(
            la.get("nome", "—")[:35],
            lb.get("nome", "—")[:35],
            f"{sim:.3f}",
            la.get("nicho", "—")[:20],
        )
    console.print(table)

    if dry_run:
        console.print(f"\n[yellow]dry-run:[/] {len(dups)} pares encontrados. Sem remoções.\n")
        raise typer.Exit(0)

    unique, removed = deduplicate(leads, threshold)
    if removed > 0:
        with open(LEADS_FILE, "w", encoding="utf-8") as f:
            json.dump(unique, f, ensure_ascii=False, indent=2)
        console.print(f"\n[green]OK[/] {removed} leads duplicados removidos. Restam: {len(unique)}\n")
    else:
        console.print("\n[green]Sem remoções.[/]\n")


@app.command()
def similar(
    nome: str = typer.Option(..., "--nome", "-n", help="Nome da empresa"),
    top_k: int = typer.Option(5, "--top", "-k", help="Número de resultados"),
):
    """Encontra leads semanticamente semelhantes a uma empresa."""
    from nim_client import nim
    if not nim.enabled:
        console.print("[red]ERRO:[/] NIM não disponível.")
        raise typer.Exit(1)

    if not LEADS_FILE.exists():
        console.print(f"[red]ERRO:[/] {LEADS_FILE} não encontrado.")
        raise typer.Exit(1)

    with open(LEADS_FILE, "r", encoding="utf-8-sig") as f:
        leads = json.load(f)

    # Encontra lead pelo nome
    match = next((l for l in leads if nome.lower() in (l.get("nome") or "").lower()), None)
    if not match:
        console.print(f"[red]Empresa '{nome}' não encontrada.[/]")
        raise typer.Exit(1)

    console.print(f"\n[bold]Semelhantes a:[/] {match['nome']} ({match.get('nicho','')}, {match.get('regiao','')})\n")

    results = find_similar(match, leads, top_k=top_k)
    if not results:
        console.print("[yellow]Sem resultados (embeddings não calculados?).[/]")
        raise typer.Exit(0)

    table = Table(show_lines=True)
    table.add_column("Sim.", justify="center", style="bold yellow")
    table.add_column("Nome", style="cyan", max_width=35)
    table.add_column("Nicho", max_width=20)
    table.add_column("Região", max_width=25)
    table.add_column("Score", justify="center")

    for lead, sim in results:
        table.add_row(
            f"{sim:.3f}",
            (lead.get("nome") or "—")[:35],
            (lead.get("nicho") or "—")[:20],
            (lead.get("regiao") or "—")[:25],
            str(lead.get("score", "—")),
        )
    console.print(table)
    console.print()


if __name__ == "__main__":
    app()
