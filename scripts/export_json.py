"""
Exporta dados do Lead Hunter para docs/data/businesses.json
Corre depois de cada pipeline para actualizar o site.

Uso:
  python scripts/export_json.py
"""
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).parent.parent
DB   = ROOT / "nexus_os.db"
OUT  = ROOT / "docs" / "data" / "businesses.json"


def compute_gaps(row: dict) -> list[str]:
    gaps = []
    if not row.get("has_website"):         gaps.append("sem_website")
    if not row.get("has_booking"):         gaps.append("sem_reservas")
    if not row.get("whatsapp_link"):       gaps.append("sem_whatsapp")
    if not row.get("has_analytics"):       gaps.append("sem_analytics")
    if not row.get("has_facebook_pixel"):  gaps.append("sem_facebook_pixel")
    if row.get("has_website") and not row.get("has_ssl"):           gaps.append("sem_ssl")
    if row.get("has_website") and not row.get("is_mobile_friendly"): gaps.append("sem_mobile")
    return gaps


def export():
    if not DB.exists():
        print(f"[!] Base de dados nao encontrada: {DB}")
        print("    Corre primeiro o pipeline para gerar dados.")
        return

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    cur.execute("""
        SELECT
            id, name, nicho AS category,
            lat, lng, address, municipio AS municipality,
            phone, email, website, whatsapp_link AS whatsapp,
            COALESCE(score, 0) AS score,
            COALESCE(has_website, 0)         AS has_website,
            COALESCE(has_booking, 0)         AS has_booking,
            COALESCE(has_analytics, 0)       AS has_analytics,
            COALESCE(has_facebook_pixel, 0)  AS has_facebook_pixel,
            COALESCE(has_ssl, 0)             AS has_ssl,
            COALESCE(is_mobile_friendly, 0)  AS is_mobile_friendly
        FROM companies
        WHERE lat IS NOT NULL AND lng IS NOT NULL
        ORDER BY score DESC
    """)

    rows = cur.fetchall()
    con.close()

    businesses = []
    for r in rows:
        row = dict(r)
        # Cast booleans
        for field in ("has_website","has_booking","has_analytics","has_facebook_pixel","has_ssl","is_mobile_friendly"):
            row[field] = bool(row[field])

        row["id"]      = str(row["id"])
        row["gaps"]    = compute_gaps(row)
        row["score"]   = int(row["score"])

        # Strip protocol from website
        if row.get("website"):
            row["website"] = row["website"].replace("https://","").replace("http://","").rstrip("/")

        # Clean whatsapp to digits only
        if row.get("whatsapp"):
            row["whatsapp"] = ''.join(c for c in row["whatsapp"] if c.isdigit())

        businesses.append(row)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(businesses, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[ok] {len(businesses)} negocios exportados → {OUT}")
    print(f"     Faz 'git add docs/data/businesses.json && git push' para actualizar o site.")


if __name__ == "__main__":
    export()
