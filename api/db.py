import json
import sqlite3
import threading
import unicodedata
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "nexus_os.db"
LEADS_JSON = Path(__file__).parent.parent / "leads_pendentes.json"

_JSON_LOCK = threading.Lock()


def _normalize(s: str | None) -> str:
    """Strip accents and lowercase — used as a SQLite UDF for accent-insensitive search."""
    if not s:
        return ""
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.create_function("norm", 1, _normalize)
    return conn


@contextmanager
def db_conn():
    """Context manager — always closes the connection, even on exception."""
    conn = get_db()
    try:
        yield conn
    finally:
        conn.close()


def _add_column_safe(conn: sqlite3.Connection, col: str, definition: str) -> None:
    try:
        conn.execute(f"ALTER TABLE companies ADD COLUMN {col} {definition}")
    except sqlite3.OperationalError:
        pass  # column already exists


def init_db():
    conn = get_db()
    conn.execute("""
    CREATE TABLE IF NOT EXISTS companies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        place_id TEXT UNIQUE NOT NULL,
        nome TEXT,
        nicho TEXT,
        regiao TEXT,
        morada TEXT,
        lat REAL,
        lon REAL,
        website TEXT,
        telefone TEXT,
        rating REAL,
        total_reviews INTEGER,
        score INTEGER,
        status TEXT DEFAULT 'pendente',
        source TEXT DEFAULT 'openstreetmap',
        load_time REAL,
        status_code INTEGER,
        emails TEXT DEFAULT '[]',
        telefones_auditados TEXT DEFAULT '[]',
        whatsapp_link TEXT,
        tem_booking INTEGER DEFAULT 0,
        formularios INTEGER DEFAULT 0,
        redes_sociais TEXT DEFAULT '{}',
        booking_hints TEXT DEFAULT '[]',
        texto_homepage TEXT DEFAULT '',
        tags TEXT DEFAULT '[]',
        problemas TEXT DEFAULT '[]',
        impacto TEXT,
        email_assunto TEXT,
        email_mensagem TEXT,
        notas TEXT DEFAULT '',
        osm_tags TEXT DEFAULT '{}',
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_status  ON companies(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_score   ON companies(score DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_nicho   ON companies(nicho)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_website ON companies(website)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_regiao  ON companies(regiao)")

    # Migrate: add new columns to existing databases
    _add_column_safe(conn, "favicon_url",         "TEXT")
    _add_column_safe(conn, "has_https",            "INTEGER DEFAULT 0")
    _add_column_safe(conn, "has_mobile_meta",      "INTEGER DEFAULT 0")
    _add_column_safe(conn, "has_analytics",        "INTEGER DEFAULT 0")
    _add_column_safe(conn, "has_facebook_pixel",   "INTEGER DEFAULT 0")
    _add_column_safe(conn, "cms_detected",         "TEXT")
    _add_column_safe(conn, "social_presence",      "TEXT DEFAULT '{}'")
    _add_column_safe(conn, "page_word_count",      "INTEGER DEFAULT 0")
    conn.execute("""
    CREATE TABLE IF NOT EXISTS chat_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
    )
    """)
    conn.commit()
    conn.close()
    _migrate_from_json()


def _migrate_from_json():
    """Initial import only — uses INSERT OR IGNORE so existing rows are never overwritten."""
    if not LEADS_JSON.exists():
        return
    conn = get_db()
    try:
        with open(LEADS_JSON, "r", encoding="utf-8-sig") as f:
            leads = json.load(f)
    except Exception:
        conn.close()
        return

    migrated = 0
    for lead in leads:
        if not isinstance(lead, dict):
            continue
        try:
            conn.execute("""
            INSERT OR IGNORE INTO companies
            (place_id,nome,nicho,regiao,morada,lat,lon,website,telefone,
             rating,total_reviews,score,status,source,
             load_time,status_code,emails,telefones_auditados,
             whatsapp_link,tem_booking,formularios,redes_sociais,
             booking_hints,texto_homepage,tags,problemas,
             impacto,email_assunto,email_mensagem,notas,osm_tags)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                lead.get("place_id", ""),
                lead.get("nome"), lead.get("nicho"), lead.get("regiao"),
                lead.get("morada"), lead.get("lat"), lead.get("lon"),
                lead.get("website"), lead.get("telefone"),
                lead.get("rating"), lead.get("total_reviews"),
                lead.get("score"), lead.get("status", "pendente"),
                lead.get("source", "openstreetmap"),
                lead.get("load_time"), lead.get("status_code"),
                json.dumps(lead.get("emails") or []),
                json.dumps(lead.get("telefones") or []),
                lead.get("whatsapp_link"),
                int(bool(lead.get("tem_booking"))),
                lead.get("formularios", 0),
                json.dumps(lead.get("redes_sociais") or {}),
                json.dumps(lead.get("booking_hints") or []),
                lead.get("texto_homepage", ""),
                json.dumps(lead.get("tags") or []),
                json.dumps(lead.get("problemas") or []),
                lead.get("impacto"), lead.get("email_assunto"),
                lead.get("email_mensagem"), lead.get("notas", ""),
                json.dumps(lead.get("osm_tags") or {}),
            ))
            migrated += 1
        except Exception:
            pass
    conn.commit()
    conn.close()
    if migrated:
        print(f"[db] Migrated {migrated} leads from JSON to SQLite")


def sync_from_json():
    """
    Sync JSON → SQLite after pipeline steps.
    UPSERT: new companies are inserted, existing ones are updated
    for pipeline-controlled fields only. User data (notas, updated_at
    from single audits) is NEVER overwritten.
    """
    if not LEADS_JSON.exists():
        return
    try:
        with open(LEADS_JSON, "r", encoding="utf-8-sig") as f:
            leads = json.load(f)
    except Exception as e:
        print(f"[db] sync error reading JSON: {e}")
        return

    conn = get_db()
    updated = inserted = 0

    for lead in leads:
        if not isinstance(lead, dict) or not lead.get("place_id"):
            continue
        try:
            existing = conn.execute(
                "SELECT id FROM companies WHERE place_id = ?",
                (lead["place_id"],)
            ).fetchone()

            if existing:
                # Update only pipeline-controlled fields, never touch notas or updated_at
                conn.execute("""
                    UPDATE companies SET
                        nome=?, nicho=?, regiao=?, morada=?, lat=?, lon=?,
                        website=?, telefone=?, rating=?, total_reviews=?,
                        score=?, status=?, source=?, load_time=?, status_code=?,
                        emails=?, telefones_auditados=?, whatsapp_link=?,
                        tem_booking=?, formularios=?, redes_sociais=?,
                        booking_hints=?, texto_homepage=?, tags=?, problemas=?,
                        impacto=?, email_assunto=?, email_mensagem=?, osm_tags=?,
                        favicon_url      = COALESCE(?, favicon_url),
                        has_https        = CASE WHEN ? IS NOT NULL THEN ? ELSE has_https END,
                        has_mobile_meta  = CASE WHEN ? IS NOT NULL THEN ? ELSE has_mobile_meta END,
                        has_analytics    = CASE WHEN ? IS NOT NULL THEN ? ELSE has_analytics END,
                        has_facebook_pixel = CASE WHEN ? IS NOT NULL THEN ? ELSE has_facebook_pixel END,
                        cms_detected     = COALESCE(?, cms_detected),
                        page_word_count  = CASE WHEN ? > 0 THEN ? ELSE page_word_count END
                    WHERE place_id=?
                """, (
                    lead.get("nome"), lead.get("nicho"), lead.get("regiao"),
                    lead.get("morada"), lead.get("lat"), lead.get("lon"),
                    lead.get("website"), lead.get("telefone"),
                    lead.get("rating"), lead.get("total_reviews"),
                    lead.get("score"), lead.get("status", "pendente"),
                    lead.get("source", "openstreetmap"),
                    lead.get("load_time"), lead.get("status_code"),
                    json.dumps(lead.get("emails") or []),
                    json.dumps(lead.get("telefones") or []),
                    lead.get("whatsapp_link"),
                    int(bool(lead.get("tem_booking"))),
                    lead.get("formularios", 0),
                    json.dumps(lead.get("redes_sociais") or {}),
                    json.dumps(lead.get("booking_hints") or []),
                    lead.get("texto_homepage", ""),
                    json.dumps(lead.get("tags") or []),
                    json.dumps(lead.get("problemas") or []),
                    lead.get("impacto"), lead.get("email_assunto"),
                    lead.get("email_mensagem"),
                    json.dumps(lead.get("osm_tags") or {}),
                    # Audit columns — COALESCE/CASE prevents overwriting existing values with NULL
                    lead.get("favicon_url"),
                    lead.get("has_https"), lead.get("has_https"),
                    lead.get("has_mobile_meta"), lead.get("has_mobile_meta"),
                    lead.get("has_analytics"), lead.get("has_analytics"),
                    lead.get("has_facebook_pixel"), lead.get("has_facebook_pixel"),
                    lead.get("cms_detected"),
                    lead.get("page_word_count", 0), lead.get("page_word_count", 0),
                    lead["place_id"],
                ))
                updated += 1
            else:
                conn.execute("""
                    INSERT INTO companies
                    (place_id,nome,nicho,regiao,morada,lat,lon,website,telefone,
                     rating,total_reviews,score,status,source,
                     load_time,status_code,emails,telefones_auditados,
                     whatsapp_link,tem_booking,formularios,redes_sociais,
                     booking_hints,texto_homepage,tags,problemas,
                     impacto,email_assunto,email_mensagem,notas,osm_tags,
                     favicon_url,has_https,has_mobile_meta,has_analytics,
                     has_facebook_pixel,cms_detected,page_word_count)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    lead.get("place_id", ""),
                    lead.get("nome"), lead.get("nicho"), lead.get("regiao"),
                    lead.get("morada"), lead.get("lat"), lead.get("lon"),
                    lead.get("website"), lead.get("telefone"),
                    lead.get("rating"), lead.get("total_reviews"),
                    lead.get("score"), lead.get("status", "pendente"),
                    lead.get("source", "openstreetmap"),
                    lead.get("load_time"), lead.get("status_code"),
                    json.dumps(lead.get("emails") or []),
                    json.dumps(lead.get("telefones") or []),
                    lead.get("whatsapp_link"),
                    int(bool(lead.get("tem_booking"))),
                    lead.get("formularios", 0),
                    json.dumps(lead.get("redes_sociais") or {}),
                    json.dumps(lead.get("booking_hints") or []),
                    lead.get("texto_homepage", ""),
                    json.dumps(lead.get("tags") or []),
                    json.dumps(lead.get("problemas") or []),
                    lead.get("impacto"), lead.get("email_assunto"),
                    lead.get("email_mensagem"), lead.get("notas", ""),
                    json.dumps(lead.get("osm_tags") or {}),
                    lead.get("favicon_url"),
                    int(bool(lead.get("has_https", 0))),
                    int(bool(lead.get("has_mobile_meta", 0))),
                    int(bool(lead.get("has_analytics", 0))),
                    int(bool(lead.get("has_facebook_pixel", 0))),
                    lead.get("cms_detected"),
                    lead.get("page_word_count", 0),
                ))
                inserted += 1
        except Exception:
            pass

    conn.commit()
    conn.close()
    print(f"[db] sync: {inserted} inserted, {updated} updated")


def sync_company_to_json(place_id: str, fields: dict) -> None:
    """Update a single company in the JSON file by place_id.
    Called after single-op audit/analyze so JSON stays in sync with SQLite.
    """
    if not LEADS_JSON.exists():
        return
    with _JSON_LOCK:
        try:
            with open(LEADS_JSON, "r", encoding="utf-8-sig") as f:
                leads = json.load(f)
        except Exception:
            return
        for lead in leads:
            if lead.get("place_id") == place_id:
                lead.update(fields)
                break
        try:
            with open(LEADS_JSON, "w", encoding="utf-8") as f:
                json.dump(leads, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[db] sync_company_to_json write error: {e}")


def get_chat_history(company_id: int) -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        "SELECT role, content, created_at FROM chat_history WHERE company_id = ? ORDER BY id",
        (company_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_chat_messages(company_id: int, messages: list[dict]) -> None:
    if not messages:
        return
    conn = get_db()
    conn.executemany(
        "INSERT INTO chat_history (company_id, role, content) VALUES (?, ?, ?)",
        [(company_id, m["role"], m["content"]) for m in messages],
    )
    conn.commit()
    conn.close()


def clear_chat_history(company_id: int) -> None:
    conn = get_db()
    conn.execute("DELETE FROM chat_history WHERE company_id = ?", (company_id,))
    conn.commit()
    conn.close()


def row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    for field in ("emails", "telefones_auditados", "redes_sociais",
                  "booking_hints", "tags", "problemas", "osm_tags",
                  "social_presence"):
        if d.get(field):
            try:
                d[field] = json.loads(d[field])
            except Exception:
                pass
    return d
