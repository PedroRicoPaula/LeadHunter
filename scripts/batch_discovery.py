"""
Batch discovery — corre todos os nichos em todas as ilhas dos Açores via OSM.
Deduplicação automática por place_id. Sem API key necessária.

Uso:
  .venv/bin/python3 scripts/batch_discovery.py
  .venv/bin/python3 scripts/batch_discovery.py --so-novas-ilhas
  .venv/bin/python3 scripts/batch_discovery.py --so-novos-nichos
"""
import sys
import time
import json
import argparse
import importlib.util
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(Path(__file__).parent))
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

from rich.console import Console
from rich.table import Table

console = Console()

# ── Target matrix ─────────────────────────────────────────────────────────────

ILHAS = [
    "Sao Miguel, Acores",
    "Terceira, Acores",
    "Faial, Acores",
    "Pico, Acores",
    "Sao Jorge, Acores",
    "Flores, Acores",
    "Graciosa, Acores",
    "Santa Maria, Acores",
    "Corvo, Acores",
]

NOVAS_ILHAS = {"Flores, Acores", "Corvo, Acores"}

NICHOS = [
    # Alimentação
    "restaurantes", "cafes", "bares", "pastelarias", "takeaway",
    # Alojamento
    "hoteis", "alojamento_local",
    # Saúde
    "clinicas", "dentistas", "farmacias", "medicos", "veterinarios",
    # Beleza & Bem-estar
    "cabeleireiros", "ginasios",
    # Comércio
    "supermercados", "lojas", "padarias", "talhos", "peixarias",
    # Automóvel
    "garagens", "posto_combustivel", "aluguer_carros",
    # Serviços profissionais
    "imobiliarias", "contabilidade", "advogados",
    # Turismo
    "museus", "actividades",
]

NICHOS_JA_COBERTOS = {
    "restaurantes", "cafes", "bares", "hoteis", "alojamento_local",
    "supermercados", "cabeleireiros", "clinicas", "farmacias", "garagens",
    "ginasios", "dentistas", "imobiliarias", "medicos", "veterinarios",
}


def _load_disc():
    spec = importlib.util.spec_from_file_location(
        "disc_free", Path(__file__).parent / "01_discovery_free.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _leads_count() -> int:
    from config_loader import CONFIG
    lf = ROOT / CONFIG["output"]["leads_file"]
    if not lf.exists():
        return 0
    with open(lf, encoding="utf-8-sig") as f:
        return len(json.load(f))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--so-novas-ilhas", action="store_true")
    parser.add_argument("--so-novos-nichos", action="store_true")
    parser.add_argument("--delay", type=float, default=1.5,
                        help="Segundos entre queries Overpass")
    args = parser.parse_args()

    ilhas  = [i for i in ILHAS if i in NOVAS_ILHAS] if args.so_novas_ilhas else ILHAS
    nichos = [n for n in NICHOS if n not in NICHOS_JA_COBERTOS] if args.so_novos_nichos else NICHOS

    total_pairs = len(ilhas) * len(nichos)
    eta_min = total_pairs * args.delay / 60

    console.print(f"\n[bold cyan]Nexus OS — Batch Discovery[/]")
    console.print(f"Ilhas: {len(ilhas)}  Nichos: {len(nichos)}  Queries: {total_pairs}  ETA: ~{eta_min:.0f} min\n")

    disc = _load_disc()

    results: list[tuple[str, str, int]] = []
    done = 0
    grand_total_new = 0

    for ilha in ilhas:
        for nicho in nichos:
            ilha_short = ilha.split(",")[0]
            console.print(f"[dim][{done+1}/{total_pairs}][/] [cyan]{nicho}[/] / [yellow]{ilha_short}[/]", end=" ")
            before = _leads_count()
            try:
                disc.discover_osm(nicho=nicho, regiao=ilha)
            except Exception as e:
                console.print(f"[red]ERRO: {e}[/]")
                results.append((nicho, ilha, 0))
                done += 1
                continue
            after = _leads_count()
            new_n = max(0, after - before)
            grand_total_new += new_n
            results.append((nicho, ilha, new_n))
            console.print(f"[green]+{new_n}[/]")
            done += 1
            if done < total_pairs:
                time.sleep(args.delay)

    # ── Summary ───────────────────────────────────────────────────────────────
    console.print(f"\n[bold green]✓ Completo![/] {grand_total_new} novos leads em {done} queries.\n")

    nicho_totals: dict[str, int] = {}
    ilha_totals: dict[str, int] = {}
    for nicho, ilha, n in results:
        nicho_totals[nicho] = nicho_totals.get(nicho, 0) + n
        ilha_totals[ilha]   = ilha_totals.get(ilha, 0) + n

    tbl = Table(title="Novos leads por nicho", show_lines=False, min_width=40)
    tbl.add_column("Nicho", style="cyan")
    tbl.add_column("Novos", justify="right", style="green")
    for nicho in NICHOS:
        n = nicho_totals.get(nicho, 0)
        if n > 0:
            tbl.add_row(nicho, str(n))
    console.print(tbl)

    tbl2 = Table(title="Novos leads por ilha", show_lines=False, min_width=40)
    tbl2.add_column("Ilha", style="yellow")
    tbl2.add_column("Novos", justify="right", style="green")
    for ilha in ilhas:
        tbl2.add_row(ilha.split(",")[0], str(ilha_totals.get(ilha, 0)))
    console.print(tbl2)

    console.print("\n[bold]Próximo passo:[/] ./run_pipeline.sh")


if __name__ == "__main__":
    main()
