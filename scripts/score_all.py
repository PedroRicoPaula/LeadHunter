"""
Calcula scores determinísticos para todos os leads sem score no pipeline.
Usa a mesma fórmula do 03_ai_brain._compute_checklist_score — não precisa de LLM.

Uso:
  python scripts/score_all.py
"""
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT      = Path(__file__).parent.parent
JSON_FILE = ROOT / "leads_pendentes.json"

_SOCIAL_DOMAINS = frozenset([
    "facebook.com", "fb.com", "instagram.com", "twitter.com", "x.com",
    "linkedin.com", "youtube.com", "youtu.be", "tiktok.com",
])


def _is_social_url(url: str | None) -> bool:
    if not url:
        return False
    try:
        from urllib.parse import urlparse
        domain = re.sub(r"^(www\.|m\.)", "", urlparse(url).netloc.lower())
        return any(domain == sd or domain.endswith("." + sd) for sd in _SOCIAL_DOMAINS)
    except Exception:
        return False


def _has_real_website(lead: dict) -> bool:
    url = lead.get("website")
    return bool(url) and not _is_social_url(url)


def compute_score(lead: dict) -> int:
    """Deterministic opportunity score — same formula as 03_ai_brain._compute_checklist_score."""
    score = 0

    if not _has_real_website(lead):
        score += 30
    else:
        load = lead.get("load_time") or 0
        if load > 5:
            score += 12
        elif load > 3:
            score += 6

    if not lead.get("tem_booking"):
        score += 20

    if not lead.get("whatsapp_link"):
        score += 15

    redes = lead.get("redes_sociais") or {}
    # Rescue social URL stored in website field
    url = lead.get("website")
    if url and _is_social_url(url):
        try:
            from urllib.parse import urlparse
            domain = re.sub(r"^(www\.|m\.)", "", urlparse(url).netloc.lower())
            for platform, plat_domain in {
                "instagram": "instagram.com", "facebook": "facebook.com",
                "twitter": "twitter.com", "youtube": "youtube.com",
            }.items():
                if domain == plat_domain or domain.endswith("." + plat_domain):
                    if platform not in redes:
                        redes = dict(redes)
                        redes[platform] = url
                    break
        except Exception:
            pass

    if not redes:
        score += 10
    elif len(redes) < 2:
        score += 4

    emails = lead.get("emails") or []
    forms  = lead.get("formularios") or 0
    if not emails and forms == 0:
        score += 10
    elif not emails:
        score += 5

    return min(score, 100)


def main():
    if not JSON_FILE.exists():
        print(f"[!] {JSON_FILE} nao encontrado")
        return

    with open(JSON_FILE, encoding="utf-8-sig") as f:
        leads = json.load(f)

    updated = 0
    for lead in leads:
        if lead.get("score") is None:
            lead["score"] = compute_score(lead)
            if lead.get("status") not in ("auditado", "analisado"):
                lead["status"] = "scored"
            updated += 1

    with open(JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(leads, f, ensure_ascii=False, indent=2)

    total_scored = sum(1 for l in leads if l.get("score") is not None)
    print(f"[ok] {updated} leads pontuados deterministicamente")
    print(f"     Total com score: {total_scored}/{len(leads)}")


if __name__ == "__main__":
    main()
