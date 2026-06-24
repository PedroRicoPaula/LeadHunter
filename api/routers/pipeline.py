import importlib.util
import json
import sys
import threading
from pathlib import Path
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

SCRIPTS_DIR = Path(__file__).parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])

STATE = {
    "running": False,
    "step": "",
    "progress": 0,
    "total": 0,
    "last_error": None,
    "last_completed": None,
}
_STATE_LOCK = threading.Lock()


def _set_state(**kwargs):
    with _STATE_LOCK:
        STATE.update(kwargs)


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sync_to_db():
    """UPSERT from JSON into SQLite — preserves notas and individual audit timestamps."""
    try:
        from api.db import sync_from_json
        sync_from_json()
    except Exception as e:
        print(f"[pipeline] sync error: {e}")


class DiscoverBody(BaseModel):
    nicho: str
    regiao: str


class AuditBody(BaseModel):
    max_sites: Optional[int] = None


class AnalyzeBody(BaseModel):
    max_leads: Optional[int] = None
    reprocessar: bool = False


class EnrichBody(BaseModel):
    max_companies: Optional[int] = None


class RunAllBody(BaseModel):
    nicho: str
    regiao: str
    max_companies: Optional[int] = None
    max_sites: Optional[int] = None
    max_leads: Optional[int] = None


class WebDiscoverBody(BaseModel):
    url: str
    regiao: Optional[str] = None
    nicho: Optional[str] = None
    max_pages: int = 20


@router.get("/status")
def pipeline_status():
    with _STATE_LOCK:
        return dict(STATE)


@router.get("/counts")
def pipeline_counts():
    """Returns per-step counts to show in Pipeline UI."""
    from api.db import get_db
    conn = get_db()
    rows = conn.execute(
        "SELECT status, COUNT(*) as c, "
        "SUM(CASE WHEN website IS NOT NULL AND website != '' THEN 1 ELSE 0 END) as has_web "
        "FROM companies GROUP BY status"
    ).fetchall()
    conn.close()

    counts: dict[str, int] = {}
    has_web: dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = r["c"]
        has_web[r["status"]] = r["has_web"] or 0

    total = sum(counts.values())
    pendente = counts.get("pendente", 0)
    auditado = counts.get("auditado", 0)
    analisado = counts.get("analisado", 0)
    erros = counts.get("erro_auditoria", 0) + counts.get("erro_llm", 0)

    ready_enrich = pendente - has_web.get("pendente", 0)  # pendente WITHOUT website
    ready_audit = has_web.get("pendente", 0)              # pendente WITH website
    ready_analyze = auditado

    return {
        "total": total,
        "pendente": pendente,
        "auditado": auditado,
        "analisado": analisado,
        "erros": erros,
        "ready_enrich": ready_enrich,
        "ready_audit": ready_audit,
        "ready_analyze": ready_analyze,
    }


@router.post("/discover")
def run_discover(body: DiscoverBody):
    with _STATE_LOCK:
        if STATE["running"]:
            return {"ok": False, "error": "Pipeline already running"}
        STATE["running"] = True  # Reserve inside lock — prevents TOCTOU

    def _run():
        _set_state(step="discovery", progress=0, total=0, last_error=None)
        try:
            mod = _load("01_discovery_free")
            mod.discover_osm(nicho=body.nicho, regiao=body.regiao)
            _sync_to_db()
            _set_state(last_completed="discovery")
        except Exception as e:
            _set_state(last_error=str(e))
        finally:
            _set_state(running=False)

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "step": "discovery"}


@router.post("/web-discover")
def run_web_discover(body: WebDiscoverBody):
    with _STATE_LOCK:
        if STATE["running"]:
            return {"ok": False, "error": "Pipeline already running"}
        STATE["running"] = True

    def _run():
        _set_state(step="web_discovery", progress=0, total=0, last_error=None)
        try:
            mod = _load("05_web_discovery")

            def _cb(current, total):
                _set_state(progress=current, total=total)

            mod.web_discover(
                url=body.url,
                regiao=body.regiao,
                nicho=body.nicho,
                max_pages=body.max_pages,
                progress_callback=_cb,
            )
            _sync_to_db()
            _set_state(last_completed="web_discovery")
        except Exception as e:
            _set_state(last_error=str(e))
        finally:
            _set_state(running=False)

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "step": "web_discovery"}


@router.post("/audit")
def run_audit(body: AuditBody):
    with _STATE_LOCK:
        if STATE["running"]:
            return {"ok": False, "error": "Pipeline already running"}
        STATE["running"] = True

    def _run():
        _set_state(step="audit", progress=0, total=0, last_error=None)
        try:
            mod = _load("02_auditor")

            def _cb(current, total):
                _set_state(progress=current, total=total)

            mod.audit_all(max_sites=body.max_sites, progress_callback=_cb)
            _sync_to_db()
            _set_state(last_completed="audit")
        except Exception as e:
            _set_state(last_error=str(e))
        finally:
            _set_state(running=False)

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "step": "audit"}


@router.post("/analyze")
def run_analyze(body: AnalyzeBody):
    with _STATE_LOCK:
        if STATE["running"]:
            return {"ok": False, "error": "Pipeline already running"}
        STATE["running"] = True

    def _run():
        _set_state(step="analyze", progress=0, total=0, last_error=None)
        try:
            mod = _load("03_ai_brain")

            def _cb(current, total):
                _set_state(progress=current, total=total)

            mod.analyze_all(max_leads=body.max_leads, reprocessar=body.reprocessar, progress_callback=_cb)
            _sync_to_db()
            _set_state(last_completed="analyze")
        except Exception as e:
            _set_state(last_error=str(e))
        finally:
            _set_state(running=False)

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "step": "analyze"}


@router.post("/run-all")
def run_all(body: RunAllBody):
    """Chain all 4 steps: discover → enrich → audit → analyze."""
    with _STATE_LOCK:
        if STATE["running"]:
            return {"ok": False, "error": "Pipeline already running"}
        STATE["running"] = True

    def _run():
        try:
            _set_state(step="discovery", progress=0, total=0, last_error=None)
            mod = _load("01_discovery_free")
            mod.discover_osm(nicho=body.nicho, regiao=body.regiao)
            _sync_to_db()

            _set_state(step="enrich", progress=0, total=0)
            mod = _load("04_enrichment")
            def _cb_e(c, t): _set_state(progress=c, total=t)
            mod.enrich_all(max_companies=body.max_companies, progress_callback=_cb_e)
            _sync_to_db()

            _set_state(step="audit", progress=0, total=0)
            mod = _load("02_auditor")
            def _cb_a(c, t): _set_state(progress=c, total=t)
            mod.audit_all(max_sites=body.max_sites, progress_callback=_cb_a)
            _sync_to_db()

            _set_state(step="analyze", progress=0, total=0)
            mod = _load("03_ai_brain")
            def _cb_z(c, t): _set_state(progress=c, total=t)
            mod.analyze_all(max_leads=body.max_leads, progress_callback=_cb_z)
            _sync_to_db()

            _set_state(last_completed="run-all")
        except Exception as e:
            _set_state(last_error=str(e))
        finally:
            _set_state(running=False)

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "step": "run-all"}


@router.post("/enrich")
def run_enrich(body: EnrichBody):
    with _STATE_LOCK:
        if STATE["running"]:
            return {"ok": False, "error": "Pipeline already running"}
        STATE["running"] = True

    def _run():
        _set_state(step="enrich", progress=0, total=0, last_error=None)
        try:
            mod = _load("04_enrichment")

            def _cb(current, total):
                _set_state(progress=current, total=total)

            mod.enrich_all(max_companies=body.max_companies, progress_callback=_cb)
            _sync_to_db()
            _set_state(last_completed="enrich")
        except Exception as e:
            _set_state(last_error=str(e))
        finally:
            _set_state(running=False)

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "step": "enrich"}


# ── Per-company operations ────────────────────────────────────────────────────

SINGLE_STATE: dict[int, dict] = {}
_SINGLE_LOCK = threading.Lock()


def _set_single(company_id: int, **kwargs):
    with _SINGLE_LOCK:
        current = SINGLE_STATE.get(company_id, {})
        SINGLE_STATE[company_id] = {**current, **kwargs}


@router.get("/single/{company_id}/status")
def single_status(company_id: int):
    with _SINGLE_LOCK:
        return SINGLE_STATE.get(company_id, {"state": "idle", "step": "", "error": None})


@router.post("/audit-single/{company_id}")
def audit_single(company_id: int):
    from api.db import get_db, row_to_dict

    with _SINGLE_LOCK:
        if SINGLE_STATE.get(company_id, {}).get("state") in ("auditing", "analyzing"):
            return {"ok": False, "error": "Already running"}

    conn = get_db()
    row = conn.execute("SELECT * FROM companies WHERE id = ?", (company_id,)).fetchone()
    conn.close()
    if not row:
        return {"ok": False, "error": "Company not found"}

    company = row_to_dict(row)
    if not company.get("website"):
        return {"ok": False, "error": "No website to audit"}

    def _run():
        import asyncio
        import json as j
        from config_loader import CONFIG
        _set_single(company_id, state="auditing", step="Playwright a visitar o site...", error=None)
        try:
            mod = _load("02_auditor")
            cfg = CONFIG["scraper"]

            async def _do():
                from playwright.async_api import async_playwright
                async with async_playwright() as pw:
                    browser = await pw.chromium.launch(headless=cfg["headless"])
                    ctx = await browser.new_context(
                        user_agent=cfg["user_agent"],
                        viewport={"width": 1280, "height": 800},
                        ignore_https_errors=True,
                    )
                    page = await ctx.new_page()
                    try:
                        result = await mod._audit_page(page, company["website"], cfg["timeout"])
                    finally:
                        await ctx.close()
                    await browser.close()
                    return result

            result = asyncio.run(_do())
            new_status = "auditado" if not result.get("erro") else "erro_auditoria"

            # Update SQLite directly
            conn2 = get_db()
            conn2.execute("""
                UPDATE companies SET
                  status=?, load_time=?, status_code=?, emails=?,
                  telefones_auditados=?, whatsapp_link=?, tem_booking=?,
                  formularios=?, redes_sociais=?, booking_hints=?,
                  texto_homepage=?,
                  favicon_url=?, has_https=?, has_mobile_meta=?,
                  has_analytics=?, has_facebook_pixel=?, cms_detected=?,
                  page_word_count=?,
                  updated_at=datetime('now')
                WHERE id=?
            """, (
                new_status,
                result.get("load_time"),
                result.get("status_code"),
                j.dumps(result.get("emails") or []),
                j.dumps(result.get("telefones") or []),
                result.get("whatsapp_link"),
                int(bool(result.get("tem_booking"))),
                result.get("formularios", 0),
                j.dumps(result.get("redes_sociais") or {}),
                j.dumps(result.get("booking_hints") or []),
                result.get("texto_homepage", ""),
                result.get("favicon_url"),
                result.get("has_https", 0),
                result.get("has_mobile_meta", 0),
                result.get("has_analytics", 0),
                result.get("has_facebook_pixel", 0),
                result.get("cms_detected"),
                result.get("page_word_count", 0),
                company_id,
            ))
            conn2.commit()
            conn2.close()

            # Keep JSON in sync so batch sync_from_json won't overwrite (bug #0)
            from api.db import sync_company_to_json
            sync_company_to_json(company["place_id"], {
                "status": new_status,
                "load_time": result.get("load_time"),
                "status_code": result.get("status_code"),
                "emails": result.get("emails") or [],
                "telefones": result.get("telefones") or [],
                "whatsapp_link": result.get("whatsapp_link"),
                "tem_booking": result.get("tem_booking", False),
                "formularios": result.get("formularios", 0),
                "redes_sociais": result.get("redes_sociais") or {},
                "booking_hints": result.get("booking_hints") or [],
                "texto_homepage": result.get("texto_homepage", ""),
                "favicon_url": result.get("favicon_url"),
                "has_https": result.get("has_https", 0),
                "has_mobile_meta": result.get("has_mobile_meta", 0),
                "has_analytics": result.get("has_analytics", 0),
                "has_facebook_pixel": result.get("has_facebook_pixel", 0),
                "cms_detected": result.get("cms_detected"),
                "page_word_count": result.get("page_word_count", 0),
            })

            if result.get("erro"):
                _set_single(company_id, state="error", step="", error=result["erro"])
            else:
                _set_single(company_id, state="done", step="Auditoria concluída", error=None)

        except Exception as e:
            _set_single(company_id, state="error", step="", error=str(e)[:300])

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True}


@router.post("/analyze-single/{company_id}")
def analyze_single(company_id: int):
    from api.db import get_db, row_to_dict
    import json as j

    with _SINGLE_LOCK:
        if SINGLE_STATE.get(company_id, {}).get("state") in ("auditing", "analyzing"):
            return {"ok": False, "error": "Already running"}

    conn = get_db()
    row = conn.execute("SELECT * FROM companies WHERE id = ?", (company_id,)).fetchone()
    conn.close()
    if not row:
        return {"ok": False, "error": "Company not found"}

    company = row_to_dict(row)

    # Block analysis without real audit data — LLM output is not credible without it
    if not company.get("texto_homepage"):
        return {"ok": False, "error": "Audita o website primeiro — sem dados de auditoria a análise não é credível"}

    def _run():
        _set_single(company_id, state="analyzing", step="Ollama a analisar...", error=None)
        try:
            brain = _load("03_ai_brain")

            lead = {**company, "emails": company.get("emails") or [], "telefones": company.get("telefones_auditados") or []}
            analise = brain._call_llm(lead)

            # Write markdown report
            path = brain._lead_to_path(lead)
            brain._write_markdown(lead, analise, path)

            # Update SQLite
            conn2 = get_db()
            conn2.execute("""
                UPDATE companies SET
                  score=?, tags=?, problemas=?, impacto=?,
                  email_assunto=?, email_mensagem=?, status=?,
                  updated_at=datetime('now')
                WHERE id=?
            """, (
                analise.score,
                j.dumps(analise.tags),
                j.dumps(analise.problemas),
                analise.impacto,
                analise.email_assunto,
                analise.email_mensagem,
                "analisado",
                company_id,
            ))
            conn2.commit()
            conn2.close()

            # Keep JSON in sync (bug #0)
            from api.db import sync_company_to_json
            sync_company_to_json(company["place_id"], {
                "score": analise.score,
                "tags": analise.tags,
                "problemas": analise.problemas,
                "impacto": analise.impacto,
                "email_assunto": analise.email_assunto,
                "email_mensagem": analise.email_mensagem,
                "status": "analisado",
            })

            _set_single(company_id, state="done", step="Análise concluída", error=None)

        except Exception as e:
            _set_single(company_id, state="error", step="", error=str(e)[:200])

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True}


@router.post("/enrich-single/{company_id}")
def enrich_single(company_id: int):
    from api.db import get_db, row_to_dict

    with _SINGLE_LOCK:
        if SINGLE_STATE.get(company_id, {}).get("state") in ("auditing", "analyzing", "enriching"):
            return {"ok": False, "error": "Already running"}

    conn = get_db()
    row = conn.execute("SELECT * FROM companies WHERE id = ?", (company_id,)).fetchone()
    conn.close()
    if not row:
        return {"ok": False, "error": "Company not found"}

    company = row_to_dict(row)
    if company.get("website"):
        return {"ok": False, "error": "Esta empresa já tem website registado"}

    def _run():
        import asyncio
        from config_loader import CONFIG
        _set_single(company_id, state="enriching", step="A pesquisar online (DuckDuckGo)...", error=None)
        try:
            mod = _load("04_enrichment")

            async def _do():
                from playwright.async_api import async_playwright
                cfg = CONFIG["scraper"]
                async with async_playwright() as pw:
                    browser = await pw.chromium.launch(headless=cfg["headless"])
                    try:
                        lead = {
                            **company,
                            "emails": company.get("emails") or [],
                            "telefones": company.get("telefones_auditados") or [],
                        }
                        return await mod._enrich_one(lead, browser)
                    finally:
                        await browser.close()

            enriched = asyncio.run(_do())

            if enriched.get("website"):
                conn2 = get_db()
                conn2.execute(
                    "UPDATE companies SET website=?, source=?, updated_at=datetime('now') WHERE id=?",
                    (enriched["website"], enriched.get("source", company.get("source")), company_id),
                )
                conn2.commit()
                conn2.close()

                from api.db import sync_company_to_json
                sync_company_to_json(company["place_id"], {
                    "website": enriched["website"],
                    "source": enriched.get("source", company.get("source")),
                })

                _set_single(company_id, state="done", step=f"Website encontrado!", error=None)
            else:
                _set_single(company_id, state="done", step="Nenhum website encontrado com confiança suficiente", error=None)

        except Exception as e:
            _set_single(company_id, state="error", step="", error=str(e)[:300])

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True}
