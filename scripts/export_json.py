"""
Exporta dados do Lead Hunter para docs/data/businesses.json
Lê de leads_pendentes.json (resultados reais do pipeline).

Uso:
  python scripts/export_json.py

Fluxo completo antes de exportar:
  python main.py audit          -- visita sites (Playwright)
  python scripts/score_all.py   -- scores deterministicos para todos
  python scripts/export_json.py -- gera businesses.json
  git add docs/data/businesses.json && git push
"""
import json
import re
import sys
from datetime import date
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT      = Path(__file__).parent.parent
JSON_FILE = ROOT / "leads_pendentes.json"
OUT       = ROOT / "docs" / "data" / "businesses.json"

NICHO_MAP = {
    # ── Alimentação ───────────────────────────────────────────────────────────
    "restaurantes":     "restaurantes",
    "Restaurantes":     "restaurantes",
    "cafes":            "restaurantes",
    "Cafes":            "restaurantes",
    "Café":             "restaurantes",
    "bares":            "restaurantes",
    "Bares":            "restaurantes",
    "pastelarias":      "restaurantes",
    "Pastelarias":      "restaurantes",
    "takeaway":         "restaurantes",
    "Takeaway":         "restaurantes",
    # ── Alojamento ───────────────────────────────────────────────────────────
    "hoteis":           "alojamento",
    "Hoteis":           "alojamento",
    "Hóteis":           "alojamento",
    "Hotéis":           "alojamento",
    "alojamento_local": "alojamento",
    "Alojamento Local": "alojamento",
    # ── Saúde ─────────────────────────────────────────────────────────────────
    "clinicas":         "saude",
    "Clinicas":         "saude",
    "Clínicas":         "saude",
    "dentistas":        "saude",
    "Dentistas":        "saude",
    "farmacias":        "saude",
    "Farmacias":        "saude",
    "Farmácias":        "saude",
    "medicos":          "saude",
    "Medicos":          "saude",
    "Médicos":          "saude",
    "veterinarios":     "saude",
    "Veterinarios":     "saude",
    "Saude":            "saude",
    # ── Beleza ───────────────────────────────────────────────────────────────
    "cabeleireiros":    "beleza",
    "Cabeleireiros":    "beleza",
    # ── Ginásios ─────────────────────────────────────────────────────────────
    "ginasios":         "ginasios",
    "Ginasios":         "ginasios",
    "Ginásios":         "ginasios",
    "actividades":      "ginasios",
    "Actividades":      "ginasios",
    # ── Comércio ─────────────────────────────────────────────────────────────
    "supermercados":    "comercio",
    "Supermercados":    "comercio",
    "lojas":            "comercio",
    "Lojas":            "comercio",
    "padarias":         "comercio",
    "Padarias":         "comercio",
    "talhos":           "comercio",
    "Talhos":           "comercio",
    "peixarias":        "comercio",
    "Peixarias":        "comercio",
    # ── Automóvel ────────────────────────────────────────────────────────────
    "garagens":         "automovel",
    "Garagens":         "automovel",
    "posto_combustivel":"automovel",
    "Posto Combustivel":"automovel",
    "aluguer_carros":   "automovel",
    "Aluguer Carros":   "automovel",
    # ── Serviços Profissionais ────────────────────────────────────────────────
    "imobiliarias":     "servicos_prof",
    "Imobiliarias":     "servicos_prof",
    "Imobiliárias":     "servicos_prof",
    "contabilidade":    "servicos_prof",
    "Contabilidade":    "servicos_prof",
    "advogados":        "servicos_prof",
    "Advogados":        "servicos_prof",
    "seguros":          "servicos_prof",
    "Seguros":          "servicos_prof",
    "bancos":           "servicos_prof",
    "Bancos":           "servicos_prof",
    # ── Turismo ──────────────────────────────────────────────────────────────
    "museus":           "turismo",
    "Museus":           "turismo",
}

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
    "9675": "Povoacoes",
    "9680": "Povoacoes",
}

_SOCIAL_DOMAINS = frozenset([
    "facebook.com", "fb.com", "instagram.com", "twitter.com", "x.com",
    "linkedin.com", "youtube.com", "youtu.be", "tiktok.com",
])


def _is_social_url(url):
    if not url:
        return False
    try:
        from urllib.parse import urlparse
        domain = re.sub(r"^(www\.|m\.)", "", urlparse(url).netloc.lower())
        return any(domain == sd or domain.endswith("." + sd) for sd in _SOCIAL_DOMAINS)
    except Exception:
        return False


def _has_real_website(lead):
    url = lead.get("website")
    return bool(url) and not _is_social_url(url)


def get_municipality(morada):
    if morada:
        m = re.search(r"(\d{4})-\d{3}", str(morada))
        if m:
            return POSTAL_MUN.get(m.group(1), "Sao Miguel")
    return "Sao Miguel"


def compute_gaps(lead):
    gaps = []
    if not _has_real_website(lead):
        gaps.append("sem_website")
    if not lead.get("whatsapp_link"):
        gaps.append("sem_whatsapp")
    if not lead.get("tem_booking"):
        gaps.append("sem_reservas")
    redes = lead.get("redes_sociais") or {}
    if not redes:
        gaps.append("sem_redes_sociais")
    emails = lead.get("emails") or []
    if not emails:
        gaps.append("sem_email")
    if _has_real_website(lead) and not lead.get("has_https"):
        gaps.append("sem_ssl")
    if _has_real_website(lead) and not lead.get("has_mobile_meta"):
        gaps.append("sem_mobile")
    if _has_real_website(lead) and not lead.get("has_analytics"):
        gaps.append("sem_analytics")
    if _has_real_website(lead) and not lead.get("has_facebook_pixel"):
        gaps.append("sem_facebook_pixel")
    return gaps


def clean_website(url):
    if not url:
        return None
    return url.replace("https://", "").replace("http://", "").rstrip("/")


def clean_whatsapp(wa):
    if not wa:
        return None
    digits = "".join(c for c in wa if c.isdigit())
    return digits if digits else None


def get_phone(lead):
    tel = lead.get("telefone")
    if tel and tel.strip() not in ("-", "", "null"):
        return tel.strip()
    tels = lead.get("telefones") or []
    return tels[0] if tels else None


def get_email(lead):
    emails = lead.get("emails") or []
    return emails[0] if emails else None


def export():
    if not JSON_FILE.exists():
        print(f"[!] {JSON_FILE} nao encontrado. Corre o pipeline primeiro.")
        return

    with open(JSON_FILE, encoding="utf-8-sig") as f:
        leads = json.load(f)

    skipped_no_coords = 0
    skipped_no_nicho = 0
    businesses = []

    for lead in leads:
        lat = lead.get("lat")
        lon = lead.get("lon") or lead.get("lng")
        if not lat or not lon:
            skipped_no_coords += 1
            continue

        nicho_raw = lead.get("nicho") or ""
        category = NICHO_MAP.get(nicho_raw)
        if not category:
            skipped_no_nicho += 1
            continue

        score = lead.get("score")
        if score is None:
            # Fallback: shouldn't happen if score_all.py ran, but just in case
            score = 50
        score = max(0, min(100, int(score)))

        businesses.append({
            "id":           str(lead.get("place_id") or lead.get("id") or ""),
            "name":         lead.get("nome") or "",
            "category":     category,
            "lat":          float(lat),
            "lng":          float(lon),
            "address":      lead.get("morada"),
            "municipality": get_municipality(lead.get("morada")),
            "phone":        get_phone(lead),
            "email":        get_email(lead),
            "website":      clean_website(lead.get("website")),
            "whatsapp":     clean_whatsapp(lead.get("whatsapp_link")),
            "score":        score,
            "has_website":  _has_real_website(lead),
            "has_booking":  bool(lead.get("tem_booking")),
            "has_analytics": bool(lead.get("has_analytics")),
            "has_facebook_pixel": bool(lead.get("has_facebook_pixel")),
            "has_ssl":      bool(lead.get("has_https")),
            "is_mobile_friendly": bool(lead.get("has_mobile_meta")),
            "gaps":         compute_gaps(lead),
            "email_assunto":   lead.get("email_assunto") or None,
            "email_mensagem":  lead.get("email_mensagem") or None,
            "problemas":       lead.get("problemas") or [],
            "impacto":         lead.get("impacto") or None,
        })

    # Sort by score descending
    businesses.sort(key=lambda b: b["score"], reverse=True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(businesses, ensure_ascii=False, indent=2), encoding="utf-8")

    from collections import Counter
    cats = Counter(b["category"] for b in businesses)
    with_phone   = sum(1 for b in businesses if b["phone"])
    with_email   = sum(1 for b in businesses if b["email"])
    with_website = sum(1 for b in businesses if b["website"])
    avg_score    = round(sum(b["score"] for b in businesses) / len(businesses), 1) if businesses else 0

    # Write meta.json so UI always shows correct last_updated date
    meta_path = OUT.parent / "meta.json"
    meta = {
        "last_updated": date.today().isoformat(),
        "total": len(businesses),
        "with_phone": with_phone,
        "with_email": with_email,
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    print(f"[ok] {len(businesses)} negocios exportados -> {OUT}")
    print(f"     Categorias: {dict(cats)}")
    print(f"     Com telefone: {with_phone} | email: {with_email} | website: {with_website}")
    print(f"     Score medio: {avg_score}")
    print(f"     Ignorados: {skipped_no_coords} sem coords, {skipped_no_nicho} sem nicho")
    print(f"     meta.json actualizado: {meta['last_updated']}")
    print(f"     Faz 'git add docs/data/ && git push'")


if __name__ == "__main__":
    export()
