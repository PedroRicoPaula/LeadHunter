"""
Exporta dados do Lead Hunter para docs/data/businesses.json
Corre depois de cada pipeline para actualizar o site.

Uso:
  python scripts/export_json.py
"""
import hashlib
import json
import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).parent.parent
DB   = ROOT / "nexus_os.db"
OUT  = ROOT / "docs" / "data" / "businesses.json"

# Map DB nichos → app.html category keys
NICHO_MAP = {
    "Restaurantes": "restaurantes",
    "Cafes":        "restaurantes",
    "Cafés":        "restaurantes",
    "Bares":        "restaurantes",
    "Takeaway":     "restaurantes",
    "Pastelarias":  "restaurantes",
    "Alojamento Local": "alojamento",
    "Hóteis":       "alojamento",
    "Hotéis":       "alojamento",
    "Hoteis":       "alojamento",
    "Cabeleireiros": "saloes",
    "Ginasios":     "ginasios",
    "Ginásios":     "ginasios",
    "Lojas":        "servicos",
    "Garagens":     "servicos",
    "Aluguer Carros": "servicos",
    "Imobiliarias": "servicos",
    "Clínicas":     "clinicas",
    "Clinicas":     "clinicas",
}

# Postal code prefix → municipality name
POSTAL_MUN = {
    "9500": "Ponta Delgada",
    "9501": "Ponta Delgada",
    "9510": "Ponta Delgada",
    "9520": "Ponta Delgada",
    "9540": "Lagoa",
    "9545": "Lagoa",
    "9550": "Ribeira Grande",
    "9555": "Ribeira Grande",
    "9560": "Ribeira Grande",
    "9570": "Ribeira Grande",
    "9580": "Vila Franca do Campo",
    "9585": "Vila Franca do Campo",
    "9600": "Nordeste",
    "9610": "Nordeste",
    "9630": "Nordeste",
    "9640": "Nordeste",
    "9650": "Furnas",
    "9675": "Povoação",
    "9680": "Povoação",
}


def get_municipality(morada: str | None) -> str:
    if morada:
        m = re.search(r"(\d{4})-\d{3}", morada)
        if m:
            return POSTAL_MUN.get(m.group(1), "São Miguel")
    return "São Miguel"


def compute_gaps(row: dict) -> list[str]:
    """Compute digital gaps from available fields."""
    gaps = []
    has_website = bool(row.get("website"))
    if not has_website:                             gaps.append("sem_website")
    if not row.get("whatsapp_link"):                gaps.append("sem_whatsapp")
    if not row.get("tem_booking"):                  gaps.append("sem_reservas")
    if not row.get("has_analytics"):                gaps.append("sem_analytics")
    if not row.get("has_facebook_pixel"):           gaps.append("sem_facebook_pixel")
    if has_website and not row.get("has_https"):    gaps.append("sem_ssl")
    if has_website and not row.get("has_mobile_meta"): gaps.append("sem_mobile")
    return gaps


def compute_score(row: dict) -> int:
    """
    Rule-based opportunity score for companies not yet audited by the full pipeline.
    Higher = more digital gaps = better prospecting lead.
    For audited companies (score in DB), uses the AI score directly.
    """
    if row.get("score") is not None:
        return int(row["score"])

    has_website = bool(row.get("website"))
    has_wa      = bool(row.get("whatsapp_link"))

    base = 35
    if not has_website: base += 35
    if not has_wa:      base += 15

    # Deterministic ±7 variation so markers don't all sit on the same score
    h = int(hashlib.md5(str(row.get("id", "")).encode()).hexdigest()[:2], 16) % 14 - 7
    return max(30, min(97, base + h))


def extract_email(emails_json: str | None) -> str | None:
    """Extract first valid email from JSON array field."""
    if not emails_json:
        return None
    try:
        lst = json.loads(emails_json)
        return lst[0] if lst else None
    except Exception:
        return None


def clean_phone(telefone: str | None) -> str | None:
    if not telefone or telefone.strip() in ("-", ""):
        return None
    return telefone.strip()


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
            id, nome, nicho,
            lat, lon, morada,
            telefone, emails, website, whatsapp_link,
            score,
            COALESCE(has_https, 0)           AS has_https,
            COALESCE(has_mobile_meta, 0)     AS has_mobile_meta,
            COALESCE(has_analytics, 0)       AS has_analytics,
            COALESCE(has_facebook_pixel, 0)  AS has_facebook_pixel,
            COALESCE(tem_booking, 0)         AS tem_booking
        FROM companies
        WHERE lat IS NOT NULL AND lon IS NOT NULL
        ORDER BY score DESC NULLS LAST
    """)

    rows = cur.fetchall()
    con.close()

    skipped = 0
    businesses = []
    for r in rows:
        row = dict(r)

        # Map nicho → app category (skip unknown nichos)
        category = NICHO_MAP.get(row["nicho"])
        if not category:
            skipped += 1
            continue

        score = compute_score(row)
        gaps  = compute_gaps(row)
        municipality = get_municipality(row.get("morada"))

        # Strip protocol from website
        website = row.get("website")
        if website:
            website = website.replace("https://", "").replace("http://", "").rstrip("/")

        # Clean WhatsApp to digits only
        wa = row.get("whatsapp_link")
        if wa:
            wa = "".join(c for c in wa if c.isdigit())

        businesses.append({
            "id":           str(row["id"]),
            "name":         row["nome"],
            "category":     category,
            "lat":          row["lat"],
            "lng":          row["lon"],
            "address":      row.get("morada"),
            "municipality": municipality,
            "phone":        clean_phone(row.get("telefone")),
            "email":        extract_email(row.get("emails")),
            "website":      website,
            "whatsapp":     wa,
            "score":        score,
            "has_website":  bool(row.get("website")),
            "has_booking":  bool(row.get("tem_booking")),
            "has_analytics": bool(row.get("has_analytics")),
            "has_facebook_pixel": bool(row.get("has_facebook_pixel")),
            "has_ssl":      bool(row.get("has_https")),
            "is_mobile_friendly": bool(row.get("has_mobile_meta")),
            "gaps":         gaps,
        })

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(businesses, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[ok] {len(businesses)} negocios exportados -> {OUT}")
    print(f"     {skipped} ignorados (nicho sem mapeamento)")
    print(f"     Faz 'git add docs/data/businesses.json && git push' para actualizar o site.")


if __name__ == "__main__":
    export()
