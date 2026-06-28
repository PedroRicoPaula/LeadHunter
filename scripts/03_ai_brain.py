import sys
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

import asyncio
import json
import re
import unicodedata
from pathlib import Path
from urllib.parse import urlparse

import httpx
import typer
from jinja2 import Environment, FileSystemLoader
from pydantic import BaseModel, Field
from rich.console import Console
from rich.progress import track
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from config_loader import CONFIG

app = typer.Typer()
console = Console()

ROOT = Path(__file__).parent.parent
LEADS_FILE = ROOT / CONFIG["output"]["leads_file"]
DB_ROOT = ROOT / CONFIG["output"]["database_path"]

# ── Pydantic schema for LLM output ──────────────────────────────────────────

class AuditoriaLLM(BaseModel):
    score: int = Field(..., ge=0, le=100, description="Lead score 0-100")
    tags: list[str] = Field(..., description="Ex: ['sem_booking','site_lento','oportunidade_alta']")
    problemas: list[str] = Field(..., description="Lista de problemas identificados")
    impacto: str = Field(..., description="Impacto estimado em revenue, 2-3 frases")
    email_assunto: str = Field(..., description="Assunto do email de abordagem")
    email_mensagem: str = Field(..., description="Mensagem de 3-4 frases em PT-PT")


# ── Helpers ──────────────────────────────────────────────────────────────────

def _slugify(text: str) -> str:
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _lead_to_path(lead: dict) -> Path:
    """Resolve database path: Acores/Sao_Miguel/dentistas/empresa.md"""
    regiao = lead.get("regiao", "")
    nicho = lead.get("nicho", "geral")
    parts = [p.strip() for p in regiao.split(",")]
    if len(parts) >= 2:
        arquipelago = _slugify(parts[1])
        ilha = _slugify(parts[0])
        base = DB_ROOT / arquipelago.title() / ilha.replace(" ", "_").title() / _slugify(nicho)
    else:
        base = DB_ROOT / _slugify(parts[0]) / _slugify(nicho)

    base.mkdir(parents=True, exist_ok=True)
    nome_slug = _slugify(lead.get("nome", "empresa"))
    return base / f"{nome_slug}.md"


_SECTOR_HINTS: dict[str, str] = {
    "restaurante": "Foca em: sistema de reservas online (TheFork, Otter), menu digital actualizado com preços, horários bem visíveis, delivery/takeaway, resposta a reviews no Google.",
    "café": "Foca em: horários visíveis, menu com preços, promoções nas redes sociais, take-away ou reservas.",
    "pizzaria": "Foca em: pedido online, menu com preços, horários, delivery próprio ou via Glovo/Uber Eats.",
    "snack": "Foca em: horários, menu visível, localização clara, redes sociais.",
    "dentista": "Foca em: marcação online de consultas, lista de tratamentos e preços, fotografia da equipa, contacto de urgência 24h.",
    "médico": "Foca em: especialidade clara, convenções e seguradoras aceites, marcação de consultas online, morada exacta.",
    "clinica": "Foca em: especialidades, equipa médica, marcação online, seguradoras aceites.",
    "cabeleireiro": "Foca em: marcação online (Treatwell, Booksy), galeria de trabalhos, lista de serviços com preços.",
    "barbearia": "Foca em: marcação online, lista de serviços e preços, galeria de trabalhos, Instagram activo.",
    "hotel": "Foca em: motor de reservas próprio, galeria profissional, políticas claras, contacto directo.",
    "alojamento": "Foca em: reservas directas, galeria, calendário de disponibilidade, preços visíveis.",
    "ginásio": "Foca em: planos e preços visíveis, horário de aulas, período experimental, área de membros.",
    "farmácia": "Foca em: horários especiais, serviços disponíveis (análises, vacinas), contacto de urgência.",
    "advogado": "Foca em: áreas de prática, credenciais visíveis, formulário de consulta inicial.",
    "imobiliária": "Foca em: listagem actualizada de imóveis, estimativa de avaliação, formulário de contacto.",
    "garage": "Foca em: serviços e preços indicativos, marcação de revisões online, contacto directo.",
    "oficina": "Foca em: serviços e preços indicativos, marcação online, contacto directo.",
    "supermercado": "Foca em: horários, promoções, contacto, localização clara.",
}


def _get_sector_hint(nicho: str) -> str:
    nicho_l = (nicho or "").lower()
    for key, hint in _SECTOR_HINTS.items():
        if key in nicho_l:
            return hint
    return ""


_SOCIAL_DOMAINS = frozenset([
    "facebook.com", "fb.com", "m.facebook.com",
    "instagram.com", "twitter.com", "x.com",
    "linkedin.com", "youtube.com", "youtu.be",
    "tiktok.com", "pinterest.com",
])


def _is_social_url(url: str | None) -> bool:
    """True if URL is a social media profile — not a real company website."""
    if not url:
        return False
    try:
        domain = re.sub(r"^(www\.|m\.)", "", urlparse(url).netloc.lower())
        return any(domain == sd or domain.endswith("." + sd) for sd in _SOCIAL_DOMAINS)
    except Exception:
        return False


def _has_real_website(lead: dict) -> bool:
    """True only when company has a genuine website (not a social media profile)."""
    url = lead.get("website")
    return bool(url) and not _is_social_url(url)


_SOCIAL_PLATFORM_DOMAINS = {
    "instagram": "instagram.com",
    "facebook":  "facebook.com",
    "twitter":   "twitter.com",
    "youtube":   "youtube.com",
    "tiktok":    "tiktok.com",
    "linkedin":  "linkedin.com",
    "pinterest": "pinterest.com",
}


def _effective_redes(lead: dict) -> dict:
    """
    Return redes_sociais dict, rescuing any social URL stored in the website field.
    Handles legacy data where enrichment stored instagram.com as the website.
    """
    redes = dict(lead.get("redes_sociais") or {})
    url = lead.get("website")
    if url and _is_social_url(url):
        try:
            domain = re.sub(r"^(www\.|m\.)", "", urlparse(url).netloc.lower())
            for platform, plat_domain in _SOCIAL_PLATFORM_DOMAINS.items():
                alt = "x.com" if platform == "twitter" else None
                if domain == plat_domain or domain.endswith("." + plat_domain) or domain == alt:
                    if platform not in redes or not redes[platform]:
                        redes[platform] = url
                    break
        except Exception:
            pass
    return redes


def _compute_checklist_score(lead: dict) -> tuple[int, list[str]]:
    """Deterministic opportunity score from verifiable signals only."""
    score = 0
    signals: list[str] = []

    if not _has_real_website(lead):
        score += 30
        label = "sem website real" if _is_social_url(lead.get("website")) else "sem website"
        signals.append(f"{label} (+30)")
    else:
        load = lead.get("load_time") or 0
        if load > 5:
            score += 12
            signals.append(f"site muito lento ({load}s) (+12)")
        elif load > 3:
            score += 6
            signals.append(f"site lento ({load}s) (+6)")

    if not bool(lead.get("tem_booking")):
        score += 20
        signals.append("sem sistema de marcações online (+20)")

    if not lead.get("whatsapp_link"):
        score += 15
        signals.append("sem WhatsApp Business (+15)")

    redes = _effective_redes(lead)
    if not redes:
        score += 10
        signals.append("sem redes sociais detectadas (+10)")
    elif len(redes) < 2:
        score += 4
        signals.append(f"apenas {len(redes)} rede social (+4)")

    emails = lead.get("emails") or []
    forms = lead.get("formularios") or 0
    if not emails and forms == 0:
        score += 10
        signals.append("sem email nem formulário de contacto (+10)")
    elif not emails:
        score += 5
        signals.append("sem email de contacto (+5)")

    return min(score, 100), signals


def _build_prompt(lead: dict) -> str:
    redes = _effective_redes(lead)
    redes_str = ", ".join(redes.keys()) if redes else "nenhuma detectada"
    emails_str = ", ".join(lead.get("emails") or []) or "não encontrado"
    tels_str = ", ".join(lead.get("telefones") or []) or "não encontrado"
    booking = "SIM" if bool(lead.get("tem_booking")) else "NÃO"
    whatsapp = lead.get("whatsapp_link") or "NÃO detectado"
    load = lead.get("load_time")
    forms = lead.get("formularios", 0)
    nome = lead.get("nome") or "empresa"
    nicho = lead.get("nicho") or "negócio local"
    rating = lead.get("rating")
    reviews = lead.get("total_reviews") or 0
    texto = (lead.get("texto_homepage") or "")[:3000]
    subpages = lead.get("subpages_visitadas") or []
    sector_hint = _get_sector_hint(nicho)

    base_score, score_signals = _compute_checklist_score(lead)
    signals_str = "\n".join(f"  • {s}" for s in score_signals) if score_signals else "  • nenhum sinal negativo — presença digital madura"
    score_range_min = max(base_score - 10, 0)
    score_range_max = min(base_score + 10, 95)

    load_comment = ""
    if load:
        if load > 5:
            load_comment = " — MUITO LENTO (perde +50% dos visitantes)"
        elif load > 3:
            load_comment = " — acima do ideal (>3s perde ~40% dos visitantes)"
        else:
            load_comment = " — aceitável"

    subpages_line = f"Sub-páginas visitadas: {', '.join(subpages)}" if subpages else "Sub-páginas: nenhuma acessível"

    return f"""És um consultor de marketing digital sénior especializado em PMEs portuguesas (mercado insular — Açores).
Analisa com base nos dados reais. Sê concreto e específico. Não inventes dados ausentes.

═══ PERFIL DA EMPRESA ═══
Nome: {nome}
Sector: {nicho}
Localização: {lead.get("morada") or lead.get("regiao") or "Açores, Portugal"}
Website: {lead.get("website") or "sem website registado"}
Google Maps: {f"{rating}⭐ ({reviews} reviews)" if rating else "sem dados Google Maps"}

═══ PRÉ-CÁLCULO DE OPORTUNIDADE (objectivo) ═══
Score base verificado: {base_score}/100
{signals_str}

→ Âncora: Score final deve estar entre {score_range_min} e {score_range_max}. Ajusta ±10 conforme o conteúdo do site e contexto do sector. Se negócio está digitalmente maduro (score baixo), mantém-no baixo.

═══ AUDITORIA TÉCNICA ═══
Velocidade: {f"{load}s{load_comment}" if load else "não medido"}
Marcações online: {booking}
WhatsApp Business: {whatsapp}
Formulários: {forms}
Redes sociais: {redes_str}
Emails: {emails_str}
Telefones: {tels_str}
{subpages_line}

═══ CONTEÚDO DO WEBSITE ═══
{texto if texto else "(sem conteúdo)"}
{f"{chr(10)}═══ DICAS DO SECTOR ═══{chr(10)}{sector_hint}" if sector_hint else ""}

═══ INSTRUÇÕES ═══
PROBLEMAS: 3-4 problemas específicos para este negócio. Usa dados reais da auditoria.
IMPACTO: Estima perda de receita em números (ex: "15-20 marcações/mês perdidas").
EMAIL: Começa com o nome real. Menciona UMA descoberta concreta. Tom consultivo.

Responde APENAS com JSON válido (sem texto, sem ```, sem markdown):

{{
  "score": <inteiro {score_range_min}-{score_range_max}, âncora {base_score}>,
  "tags": ["<snake_case_max_5>"],
  "problemas": ["<problema específico 1>", "<problema específico 2>", "<problema específico 3>"],
  "impacto": "<2-3 frases, perda estimada em números>",
  "email_assunto": "<assunto personalizado para {nome}>",
  "email_mensagem": "<3-4 frases PT-PT, começa com 'Olá {nome},'>"
}}

Tags válidas: sem_booking, site_lento, sem_whatsapp, sem_redes_sociais, sem_email, sem_formulario, sem_telefone, oportunidade_alta, oportunidade_media, oportunidade_baixa, site_desactualizado, sem_precos_visiveis, sem_menu_online, poucas_reviews_google, negocio_maduro_digital"""


# ── LLM backends ─────────────────────────────────────────────────────────────

_NIM_SYSTEM = (
    "És um consultor de marketing digital especializado em PMEs portuguesas. "
    "Respondes SEMPRE com JSON válido e nada mais. "
    "Não adicionas markdown, explicações ou texto antes/depois do JSON."
)


def _parse_llm_json(raw: str) -> AuditoriaLLM:
    raw = raw.strip()
    # Strip <think>...</think> blocks (qwen3 thinking mode)
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    # Strip markdown code fences
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    # Extract first JSON object if there's surrounding text
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        raw = match.group(0)
    return AuditoriaLLM.model_validate_json(raw)


def _call_ollama(lead: dict) -> AuditoriaLLM:
    cfg = CONFIG["llm"]
    ollama_cfg = CONFIG.get("ollama", {})
    base_url = ollama_cfg.get("base_url", "http://localhost:11434")
    prompt = _build_prompt(lead)

    model_name = cfg["model"].lower()
    no_think_prefix = "/no_think\n" if "qwen" in model_name else ""
    system_msg = (
        f"{no_think_prefix}"
        "És um consultor de marketing digital especializado em PMEs portuguesas. "
        "Respondes SEMPRE com JSON válido e nada mais. "
        "Não adicionas markdown, explicações ou texto antes/depois do JSON."
    )

    payload = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "options": {
            "temperature": cfg["temperature"],
            "num_predict": cfg["max_tokens"],
        },
        "think": False,
    }

    resp = httpx.post(
        f"{base_url}/api/chat",
        json=payload,
        timeout=300.0,
    )
    resp.raise_for_status()
    raw = resp.json()["message"]["content"]
    return _parse_llm_json(raw)


def _call_nvidia_nim(lead: dict) -> AuditoriaLLM:
    from nim_client import nim
    raw = nim.chat(
        messages=[{"role": "user", "content": _build_prompt(lead)}],
        system=_NIM_SYSTEM,
        temperature=CONFIG["llm"]["temperature"],
        max_tokens=CONFIG["llm"]["max_tokens"],
    )
    if raw is None:
        raise RuntimeError("NIM: sem resposta")
    return _parse_llm_json(raw)


def _call_anthropic(lead: dict) -> AuditoriaLLM:
    import anthropic as _anthropic
    cfg = CONFIG["llm"]
    api_key = cfg.get("api_key", "")
    client = _anthropic.Anthropic(api_key=api_key)

    @retry(
        retry=retry_if_exception_type((
            _anthropic.APIConnectionError,
            _anthropic.RateLimitError,
            _anthropic.APITimeoutError,
        )),
        wait=wait_exponential(multiplier=2, min=4, max=60),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def _call():
        msg = client.messages.create(
            model=cfg["model"],
            max_tokens=cfg["max_tokens"],
            temperature=cfg["temperature"],
            timeout=30.0,
            system=(
                "És um consultor de marketing digital especializado em PMEs portuguesas. "
                "Respondes SEMPRE com JSON válido e nada mais. "
                "Não adicionas markdown, explicações ou texto antes/depois do JSON."
            ),
            messages=[{"role": "user", "content": _build_prompt(lead)}],
        )
        return _parse_llm_json(msg.content[0].text)

    return _call()


def _rule_based_analysis(lead: dict) -> AuditoriaLLM:
    """Instant scoring when Ollama is offline or company has no website."""
    base_score, signals = _compute_checklist_score(lead)
    nome = lead.get("nome", "empresa")
    nicho = lead.get("nicho", "negócio local")

    problems: list[str] = []
    tags: list[str] = []

    if not _has_real_website(lead):
        if _is_social_url(lead.get("website")):
            problems.append(f"{nome} não tem website próprio — só tem rede social, o que limita credibilidade e SEO")
        else:
            problems.append(f"{nome} não tem presença online — clientes não conseguem encontrar o negócio via pesquisa web")
        tags.append("sem_website")
    if not lead.get("tem_booking"):
        problems.append("Sem sistema de marcações online — clientes têm de ligar para marcar, perdendo conversões")
        tags.append("sem_booking")
    if not lead.get("whatsapp_link"):
        problems.append("Sem WhatsApp Business — canal de contacto instantâneo em falta")
        tags.append("sem_whatsapp")
    redes = _effective_redes(lead)
    if not redes:
        problems.append("Sem redes sociais detectadas — visibilidade e engagement limitados")
        tags.append("sem_redes_sociais")
    if not lead.get("emails"):
        problems.append("Sem email de contacto capturado — potenciais clientes sem forma de contactar por escrito")
        tags.append("sem_email")
    if (lead.get("load_time") or 0) > 5:
        problems.append(f"Website demora {lead['load_time']}s a carregar — mais de 50% dos visitantes abandona antes de ver o conteúdo")
        tags.append("site_lento")

    if base_score >= 70:
        tags.append("oportunidade_alta")
    elif base_score >= 40:
        tags.append("oportunidade_media")
    else:
        tags.append("oportunidade_baixa")

    gap_list = ", ".join(signals[:3]) if signals else "presença digital limitada"
    impacto = (
        f"Score calculado automaticamente: {base_score}/100. "
        f"Principais gaps: {gap_list}. "
        "Cada gap representa clientes que não encontram ou não contactam este negócio. "
        "Re-analisa com Ollama activo para estimativa detalhada de receita perdida."
    )

    return AuditoriaLLM(
        score=base_score,
        tags=tags[:5],
        problemas=problems[:4] if problems else ["Presença digital aparentemente madura — re-analisa para confirmação"],
        impacto=impacto,
        email_assunto=f"Oportunidade de crescimento digital para {nome}",
        email_mensagem=(
            f"Olá {nome}, analisei a presença online do vosso negócio e identifiquei "
            f"alguns pontos que podem estar a limitar o número de clientes que chegam até vocês. "
            "Poderia ter uma conversa rápida de 10 minutos para partilhar o que encontrei? "
            "Não há qualquer compromisso da vossa parte."
        ),
    )


def _call_llm(lead: dict) -> AuditoriaLLM:
    """Provider cascade: nvidia_nim → ollama → anthropic → rule_based."""
    provider = CONFIG["llm"].get("provider", "ollama")

    if provider == "anthropic":
        return _call_anthropic(lead)

    if provider == "nvidia_nim":
        try:
            return _call_nvidia_nim(lead)
        except Exception as e:
            console.print(f"  [yellow]NIM falhou ({e.__class__.__name__}) — fallback Ollama[/]")
            try:
                return _call_ollama(lead)
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.RemoteProtocolError):
                console.print("  [yellow]Ollama offline — usando scoring automático[/]")
                return _rule_based_analysis(lead)

    # provider == "ollama" — usa NIM como cloud fallback se Ollama cair
    try:
        return _call_ollama(lead)
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.RemoteProtocolError):
        from nim_client import nim
        if nim.enabled:
            console.print("  [yellow]Ollama offline — fallback NIM cloud[/]")
            try:
                return _call_nvidia_nim(lead)
            except Exception:
                pass
        console.print("  [yellow]Ollama offline, NIM indisponível — scoring automático[/]")
        return _rule_based_analysis(lead)


# ── Markdown writer ───────────────────────────────────────────────────────────

_jinja_env = Environment(
    loader=FileSystemLoader(str(ROOT / "templates")),
    autoescape=False,
    keep_trailing_newline=True,
)


def _write_markdown(lead: dict, analise: AuditoriaLLM, path: Path) -> None:
    redes = _effective_redes(lead)
    redes_str = "  ".join(f"[{k.capitalize()}]({v})" for k, v in redes.items()) or "não encontradas"

    template = _jinja_env.get_template("report_template.md")
    report = template.render(
        nome=lead.get("nome", ""),
        nicho=lead.get("nicho", ""),
        localizacao=lead.get("morada", ""),
        url=lead.get("website", ""),
        score=analise.score,
        status="Analisado",
        tags=analise.tags,
        problemas=analise.problemas,
        impacto=analise.impacto,
        email_assunto=analise.email_assunto,
        email_mensagem=analise.email_mensagem,
        email=", ".join(lead.get("emails") or []) or "não encontrado",
        telefone=", ".join(lead.get("telefones") or []) or "não encontrado",
        redes_sociais=redes_str,
        load_time=lead.get("load_time", "—"),
        tem_booking="Sim" if lead.get("tem_booking") else "Nao",
        tem_whatsapp="Sim" if lead.get("whatsapp_link") else "Nao",
    )
    path.write_text(report, encoding="utf-8")


# ── Public callable (used by pipeline.py) ────────────────────────────────────

def score_no_website_companies(leads: list[dict]) -> int:
    """Auto-score pendente companies without website using rule-based scoring only.
    Returns count of companies scored. No LLM call needed."""
    targets = [
        l for l in leads
        if not _has_real_website(l) and l.get("status") == "pendente" and not l.get("score")
    ]
    for lead in targets:
        analise = _rule_based_analysis(lead)
        lead["score"] = analise.score
        lead["tags"] = analise.tags
        lead["problemas"] = analise.problemas
        lead["impacto"] = analise.impacto
        lead["email_assunto"] = analise.email_assunto
        lead["email_mensagem"] = analise.email_mensagem
        # Keep status as 'pendente' — these weren't audited, just auto-scored
    return len(targets)


def analyze_all(max_leads: int | None = None, reprocessar: bool = False, progress_callback=None) -> None:
    """Callable directly from pipeline.py — no Typer involved."""
    cfg_llm = CONFIG["llm"]
    provider = cfg_llm.get("provider", "ollama")
    ollama_online = True

    if provider == "anthropic":
        api_key = cfg_llm.get("api_key", "")
        if not api_key:
            console.print("[red]ERRO:[/] ANTHROPIC_API_KEY nao configurada.")
            return
    elif provider == "nvidia_nim":
        from nim_client import nim
        if not nim.enabled:
            console.print("[red]ERRO:[/] NVIDIA_API_KEY nao configurada ou NIM desactivado.")
            return
        console.print("[dim]NIM disponível — análise via cloud NVIDIA.[/]")
    else:
        try:
            httpx.get(f"{CONFIG.get('ollama', {}).get('base_url', 'http://localhost:11434')}/api/tags", timeout=3)
        except Exception:
            ollama_online = False
            console.print("[yellow]Aviso:[/] Ollama offline — usando scoring automático para todas as empresas")

    if not LEADS_FILE.exists():
        console.print("[red]ERRO:[/] leads_pendentes.json nao encontrado.")
        return

    with open(LEADS_FILE, "r", encoding="utf-8-sig") as f:
        leads = json.load(f)

    # Auto-score companies without websites (no LLM needed)
    auto_scored = score_no_website_companies(leads)
    if auto_scored:
        console.print(f"  [dim]Auto-score (sem website): {auto_scored} empresas[/]")

    if reprocessar:
        targets = [l for l in leads if l.get("status") in ("auditado", "analisado")]
    else:
        targets = [l for l in leads if l.get("status") == "auditado" and not l.get("score")]

    if max_leads:
        targets = targets[:max_leads]

    console.print(f"Provider: [bold cyan]{provider}[/]  Modelo: [bold]{cfg_llm['model']}[/]")
    console.print(f"Leads para analisar: [bold]{len(targets)}[/]\n")

    if not targets:
        console.print("[yellow]Nenhum lead auditado pendente.[/]")
        if auto_scored:
            with open(LEADS_FILE, "w", encoding="utf-8") as f:
                json.dump(leads, f, ensure_ascii=False, indent=2)
        return

    total = len(targets)
    use_rule_based = not ollama_online and provider not in ("anthropic", "nvidia_nim")

    async def _run_parallel() -> int:
        _erros = 0

        async def _analyze_one(lead: dict, i: int) -> tuple[dict, bool]:
            nome = lead.get("nome", "")[:40]
            try:
                loop = asyncio.get_event_loop()
                if use_rule_based:
                    analise = _rule_based_analysis(lead)
                else:
                    analise = await loop.run_in_executor(None, lambda l=lead: _call_llm(l))
                lead["score"] = analise.score
                lead["tags"] = analise.tags
                lead["problemas"] = analise.problemas
                lead["impacto"] = analise.impacto
                lead["email_assunto"] = analise.email_assunto
                lead["email_mensagem"] = analise.email_mensagem
                lead["status"] = "analisado"
                path = _lead_to_path(lead)
                _write_markdown(lead, analise, path)
                console.print(f"  [green]✓[/] {nome} score={analise.score}")
                if progress_callback:
                    progress_callback(i + 1, total)
                return lead, False
            except Exception as e:
                console.print(f"  [red]ERRO[/] {nome}: {e}")
                lead["status"] = "erro_llm"
                if progress_callback:
                    progress_callback(i + 1, total)
                return lead, True

        # NIM rate-limiter serialises LLM calls; Semaphore(3) overlaps file I/O only
        sem = asyncio.Semaphore(3)

        async def _analyze_sem(lead: dict, i: int) -> tuple[dict, bool]:
            async with sem:
                return await _analyze_one(lead, i)

        results = await asyncio.gather(
            *[_analyze_sem(lead, i) for i, lead in enumerate(targets)],
            return_exceptions=True,
        )

        for i, result in enumerate(results):
            if isinstance(result, Exception):
                _erros += 1
                continue
            lead, is_err = result
            if is_err:
                _erros += 1
            pid = lead.get("place_id")
            idx = next((j for j, l in enumerate(leads) if l.get("place_id") == pid), None)
            if idx is not None:
                leads[idx] = lead
            if (i + 1) % 10 == 0:
                with open(LEADS_FILE, "w", encoding="utf-8") as f:
                    json.dump(leads, f, ensure_ascii=False, indent=2)

        return _erros

    erros = asyncio.run(_run_parallel())

    with open(LEADS_FILE, "w", encoding="utf-8") as f:
        json.dump(leads, f, ensure_ascii=False, indent=2)

    ok = total - erros
    console.print(f"\n[green]OK[/] Analisados: {ok}  |  [red]Erros:[/] {erros}\n")


# ── CLI ───────────────────────────────────────────────────────────────────────

@app.command()
def run(
    max_leads: int = typer.Option(None, "--max", "-m", help="Limite de leads a processar"),
    reprocessar: bool = typer.Option(False, "--reprocessar", help="Reprocessar leads ja analisados"),
):
    """Fase 4 — Analise LLM + output Markdown por empresa."""
    cfg_llm = CONFIG["llm"]
    provider = cfg_llm.get("provider", "ollama")

    if provider == "anthropic":
        api_key = cfg_llm.get("api_key", "")
        if not api_key:
            console.print("[red]ERRO:[/] ANTHROPIC_API_KEY nao configurada no .env")
            raise typer.Exit(1)
    elif provider == "nvidia_nim":
        from nim_client import nim
        if not nim.enabled:
            console.print("[red]ERRO:[/] NVIDIA_API_KEY nao configurada ou NIM desactivado.")
            raise typer.Exit(1)
        console.print("[dim]NIM disponível — análise via cloud NVIDIA.[/]")
    else:
        try:
            httpx.get(f"{CONFIG.get('ollama', {}).get('base_url', 'http://localhost:11434')}/api/tags", timeout=3)
        except Exception:
            console.print("[red]ERRO:[/] Ollama nao esta a correr. Inicia com: ollama serve")
            raise typer.Exit(1)

    console.print(f"Provider: [bold cyan]{provider}[/]  Modelo: [bold]{cfg_llm['model']}[/]\n")

    if not LEADS_FILE.exists():
        console.print(f"[red]ERRO:[/] {LEADS_FILE} nao encontrado.")
        raise typer.Exit(1)

    with open(LEADS_FILE, "r", encoding="utf-8-sig") as f:
        leads = json.load(f)

    if reprocessar:
        targets = [l for l in leads if l.get("status") in ("auditado", "analisado")]
    else:
        targets = [l for l in leads if l.get("status") == "auditado" and not l.get("score")]

    if max_leads:
        targets = targets[:max_leads]

    console.print(f"Leads para analisar: [bold]{len(targets)}[/]\n")

    if not targets:
        console.print("[yellow]Nenhum lead auditado pendente de analise LLM.[/]")
        raise typer.Exit(0)

    erros = 0

    for lead in track(targets, description="Analisando..."):
        nome = lead.get("nome", "")[:40]
        try:
            analise = _call_llm(lead)
            lead["score"] = analise.score
            lead["tags"] = analise.tags
            lead["status"] = "analisado"

            path = _lead_to_path(lead)
            _write_markdown(lead, analise, path)
            console.print(f"  [green]{nome}[/] score={analise.score} -> {path.name}")

        except Exception as e:
            console.print(f"  [red]ERRO {nome}:[/] {e}")
            lead["status"] = "erro_llm"
            erros += 1

    with open(LEADS_FILE, "w", encoding="utf-8") as f:
        json.dump(leads, f, ensure_ascii=False, indent=2)

    ok = len(targets) - erros
    console.print(f"\n[green]OK[/] Analisados: {ok}  |  [red]Erros:[/] {erros}")
    console.print(f"Relatorios em: [dim]{DB_ROOT}[/]\n")


if __name__ == "__main__":
    app()
