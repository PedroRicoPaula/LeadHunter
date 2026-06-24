import json
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from api.db import get_db, db_conn, row_to_dict

router = APIRouter(prefix="/api/companies", tags=["companies"])

_SORT_COLS = {"nome", "nicho", "score", "status", "updated_at", "load_time"}
_SORT_DIRS = {"asc", "desc"}


class CompanyPatch(BaseModel):
    notas: Optional[str] = None
    tags: Optional[list[str]] = None
    score: Optional[int] = None
    status: Optional[str] = None
    website: Optional[str] = None
    email_assunto: Optional[str] = None
    email_mensagem: Optional[str] = None


@router.get("")
def list_companies(
    search: Optional[str] = Query(None),
    nicho: Optional[str] = Query(None),
    regiao: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    min_score: Optional[int] = Query(None),
    has_website: Optional[bool] = Query(None),
    sort_by: Optional[str] = Query("score"),
    sort_dir: Optional[str] = Query("desc"),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
):
    wheres, params = [], []

    if search:
        wheres.append("norm(nome) LIKE norm(?)")
        params.append(f"%{search}%")
    if nicho:
        wheres.append("nicho = ?")
        params.append(nicho)
    if regiao:
        wheres.append("regiao LIKE ?")
        params.append(f"%{regiao}%")
    if status:
        wheres.append("status = ?")
        params.append(status)
    if min_score is not None:
        wheres.append("score >= ?")
        params.append(min_score)
    if has_website is True:
        wheres.append("website IS NOT NULL AND website != ''")
    elif has_website is False:
        wheres.append("(website IS NULL OR website = '')")

    col = sort_by if sort_by in _SORT_COLS else "score"
    direction = "ASC" if (sort_dir or "").lower() == "asc" else "DESC"
    nulls = "NULLS LAST" if direction == "DESC" else "NULLS FIRST"

    where_sql = ("WHERE " + " AND ".join(wheres)) if wheres else ""

    with db_conn() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM companies {where_sql}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT id,place_id,nome,nicho,regiao,morada,lat,lon,website,telefone,"
            f"rating,total_reviews,score,status,source,load_time,tem_booking,"
            f"whatsapp_link,tags,emails,redes_sociais,notas,created_at,updated_at,"
            f"favicon_url,has_https,has_mobile_meta,has_analytics,cms_detected "
            f"FROM companies {where_sql} ORDER BY {col} {direction} {nulls} LIMIT ? OFFSET ?",
            params + [limit, offset]
        ).fetchall()

    items = []
    for row in rows:
        d = dict(row)
        for f in ("tags", "emails", "redes_sociais"):
            if d.get(f):
                try:
                    d[f] = json.loads(d[f])
                except Exception:
                    pass
        items.append(d)

    return {"total": total, "items": items, "offset": offset, "limit": limit}


@router.get("/stats")
def get_stats():
    with db_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
        com_website = conn.execute("SELECT COUNT(*) FROM companies WHERE website IS NOT NULL AND website != ''").fetchone()[0]
        analisados = conn.execute("SELECT COUNT(*) FROM companies WHERE status = 'analisado'").fetchone()[0]
        avg_score = conn.execute("SELECT AVG(score) FROM companies WHERE score IS NOT NULL").fetchone()[0]

        nichos = conn.execute(
            "SELECT nicho, COUNT(*) as c FROM companies WHERE nicho IS NOT NULL GROUP BY nicho ORDER BY c DESC LIMIT 10"
        ).fetchall()

        score_dist = conn.execute("""
            SELECT
              SUM(CASE WHEN score BETWEEN 0  AND 19  THEN 1 ELSE 0 END) as s0,
              SUM(CASE WHEN score BETWEEN 20 AND 39  THEN 1 ELSE 0 END) as s20,
              SUM(CASE WHEN score BETWEEN 40 AND 59  THEN 1 ELSE 0 END) as s40,
              SUM(CASE WHEN score BETWEEN 60 AND 79  THEN 1 ELSE 0 END) as s60,
              SUM(CASE WHEN score BETWEEN 80 AND 100 THEN 1 ELSE 0 END) as s80
            FROM companies WHERE score IS NOT NULL
        """).fetchone()

        status_counts = conn.execute(
            "SELECT status, COUNT(*) as c FROM companies GROUP BY status"
        ).fetchall()

        top5 = conn.execute(
            "SELECT id,nome,nicho,score,website,favicon_url,status,emails FROM companies WHERE score IS NOT NULL ORDER BY score DESC LIMIT 5"
        ).fetchall()

    return {
        "total": total,
        "com_website": com_website,
        "sem_website": total - com_website,
        "analisados": analisados,
        "avg_score": round(avg_score, 1) if avg_score else None,
        "nichos": [{"nicho": r["nicho"], "count": r["c"]} for r in nichos],
        "score_dist": [
            {"range": "0-19",   "count": score_dist["s0"]  or 0},
            {"range": "20-39",  "count": score_dist["s20"] or 0},
            {"range": "40-59",  "count": score_dist["s40"] or 0},
            {"range": "60-79",  "count": score_dist["s60"] or 0},
            {"range": "80-100", "count": score_dist["s80"] or 0},
        ],
        "status_counts": {r["status"]: r["c"] for r in status_counts},
        "top5": [
            {**dict(r), "emails": json.loads(r["emails"] or "[]") if r["emails"] else []}
            for r in top5
        ],
    }


@router.get("/map-points")
def get_map_points(
    nicho: Optional[str] = Query(None),
    min_score: Optional[int] = Query(None),
):
    wheres = ["lat IS NOT NULL", "lon IS NOT NULL"]
    params = []
    if nicho:
        wheres.append("nicho = ?")
        params.append(nicho)
    if min_score is not None:
        wheres.append("(score >= ? OR score IS NULL)")
        params.append(min_score)
    where_sql = "WHERE " + " AND ".join(wheres)

    with db_conn() as conn:
        rows = conn.execute(
            f"SELECT id,nome,nicho,morada,lat,lon,score,website,status FROM companies {where_sql}",
            params
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/nichos")
def get_nichos():
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT nicho FROM companies WHERE nicho IS NOT NULL ORDER BY nicho"
        ).fetchall()
    return [r["nicho"] for r in rows]


@router.get("/export")
def export_companies(
    nicho: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    min_score: Optional[int] = Query(None),
    has_website: Optional[bool] = Query(None),
):
    """Export filtered companies as CSV."""
    import csv, io
    from fastapi.responses import StreamingResponse

    wheres, params = [], []
    if nicho:
        wheres.append("nicho = ?")
        params.append(nicho)
    if status:
        wheres.append("status = ?")
        params.append(status)
    if min_score is not None:
        wheres.append("score >= ?")
        params.append(min_score)
    if has_website is True:
        wheres.append("website IS NOT NULL AND website != ''")
    elif has_website is False:
        wheres.append("(website IS NULL OR website = '')")

    where_sql = ("WHERE " + " AND ".join(wheres)) if wheres else ""

    with db_conn() as conn:
        rows = conn.execute(
            f"SELECT nome,nicho,regiao,morada,website,telefone,score,status,"
            f"load_time,tem_booking,whatsapp_link,emails,impacto,email_assunto,notas,updated_at "
            f"FROM companies {where_sql} ORDER BY score DESC NULLS LAST",
            params
        ).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Nome", "Sector", "Região", "Morada", "Website", "Telefone",
                     "Score", "Estado", "Load Time(s)", "Booking", "WhatsApp",
                     "Emails", "Impacto", "Email Assunto", "Notas", "Actualizado"])
    for r in rows:
        emails_raw = r["emails"] or "[]"
        try:
            emails = ", ".join(json.loads(emails_raw))
        except Exception:
            emails = emails_raw
        writer.writerow([
            r["nome"], r["nicho"], r["regiao"], r["morada"],
            r["website"] or "", r["telefone"] or "",
            r["score"] if r["score"] is not None else "",
            r["status"], r["load_time"] or "",
            "Sim" if r["tem_booking"] else "Não",
            r["whatsapp_link"] or "",
            emails, r["impacto"] or "",
            r["email_assunto"] or "", r["notas"] or "",
            r["updated_at"] or "",
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=nexus_os_export.csv"},
    )


@router.get("/action-immediate")
def action_immediate(limit: int = Query(8, le=20)):
    """Top companies with high opportunity score + captured email = ready to contact now."""
    with db_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, nome, nicho, regiao, score, website, favicon_url, emails,
                   whatsapp_link, tags, status, impacto
            FROM companies
            WHERE status = 'analisado'
              AND score >= 65
              AND (
                (emails IS NOT NULL AND emails != '[]' AND emails != '')
                OR whatsapp_link IS NOT NULL
              )
            ORDER BY score DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        try:
            d["emails"] = json.loads(d["emails"] or "[]")
        except Exception:
            d["emails"] = []
        try:
            d["tags"] = json.loads(d.get("tags") or "[]")
        except Exception:
            d["tags"] = []
        result.append(d)
    return result


@router.get("/{company_id}")
def get_company(company_id: int):
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM companies WHERE id = ?", (company_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Company not found")
    return row_to_dict(row)


@router.patch("/{company_id}")
def patch_company(company_id: int, body: CompanyPatch):
    with db_conn() as conn:
        row = conn.execute("SELECT id FROM companies WHERE id = ?", (company_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Company not found")

        updates, params = [], []
        if body.notas is not None:
            updates.append("notas = ?")
            params.append(body.notas)
        if body.tags is not None:
            updates.append("tags = ?")
            params.append(json.dumps(body.tags))
        if body.score is not None:
            updates.append("score = ?")
            params.append(body.score)
        if body.status is not None:
            updates.append("status = ?")
            params.append(body.status)
        if body.website is not None:
            updates.append("website = ?")
            params.append(body.website.strip() or None)
        if body.email_assunto is not None:
            updates.append("email_assunto = ?")
            params.append(body.email_assunto)
        if body.email_mensagem is not None:
            updates.append("email_mensagem = ?")
            params.append(body.email_mensagem)

        if updates:
            updates.append("updated_at = datetime('now')")
            params.append(company_id)
            conn.execute(f"UPDATE companies SET {', '.join(updates)} WHERE id = ?", params)
            conn.commit()

        row = conn.execute("SELECT * FROM companies WHERE id = ?", (company_id,)).fetchone()
    return row_to_dict(row)


@router.delete("/{company_id}")
def delete_company(company_id: int):
    with db_conn() as conn:
        conn.execute("DELETE FROM companies WHERE id = ?", (company_id,))
        conn.commit()
    return {"ok": True}
