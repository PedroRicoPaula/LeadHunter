import json
import sys
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))
from config_loader import CONFIG

from api.db import get_db, row_to_dict, get_chat_history, save_chat_messages, clear_chat_history

router = APIRouter(prefix="/api/llm", tags=["llm"])

OLLAMA_URL = CONFIG.get("ollama", {}).get("base_url", "http://localhost:11434")
_DEFAULT_MODEL = CONFIG["llm"]["model"]


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatBody(BaseModel):
    company_id: int
    message: str
    history: list[ChatMessage] = []
    model: str = _DEFAULT_MODEL


class SaveHistoryBody(BaseModel):
    messages: list[ChatMessage]


def _company_context(company: dict) -> str:
    tags = company.get("tags") or []
    problemas = company.get("problemas") or []
    redes = company.get("redes_sociais") or {}
    osm = company.get("osm_tags") or {}
    return f"""EMPRESA EM ANÁLISE:
- Nome: {company.get('nome')}
- Sector/Nicho: {company.get('nicho')}
- Localização: {company.get('morada')} (Açores)
- Website: {company.get('website') or 'Não tem'}
- Telefone: {company.get('telefone') or 'Desconhecido'}
- Score de oportunidade: {company.get('score') or 'Não calculado'}/100
- Status: {company.get('status')}
- Tempo carregamento site: {company.get('load_time') or 'N/A'}s
- Tem sistema de booking: {'Sim' if company.get('tem_booking') else 'Não'}
- Tem WhatsApp direto: {'Sim' if company.get('whatsapp_link') else 'Não'}
- Redes sociais: {', '.join(redes.keys()) or 'Nenhuma encontrada'}
- Tags de problemas: {', '.join(tags) or 'Nenhuma'}
- Problemas identificados: {'; '.join(problemas) or 'Nenhum'}
- Horário (OSM): {osm.get('opening_hours', 'Desconhecido')}
- Tipo de cozinha/especialidade: {osm.get('cuisine', 'N/A')}
- Impacto estimado: {company.get('impacto') or 'Não calculado'}"""


@router.get("/history/{company_id}")
def get_history(company_id: int):
    return get_chat_history(company_id)


@router.post("/history/{company_id}")
def save_history(company_id: int, body: SaveHistoryBody):
    save_chat_messages(company_id, [{"role": m.role, "content": m.content} for m in body.messages])
    return {"ok": True}


@router.delete("/history/{company_id}")
def delete_history(company_id: int):
    clear_chat_history(company_id)
    return {"ok": True}


@router.post("/chat")
async def chat(body: ChatBody):
    # Verify Ollama is reachable before starting stream — avoids ASGI crash
    try:
        async with httpx.AsyncClient(timeout=3) as probe:
            await probe.get(f"{OLLAMA_URL}/api/tags")
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Ollama não está a correr. Abre um terminal e executa: ollama serve",
        )

    conn = get_db()
    row = conn.execute("SELECT * FROM companies WHERE id = ?", (body.company_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Company not found")

    company = row_to_dict(row)
    context = _company_context(company)

    model_name = body.model.lower()
    no_think_prefix = "/no_think\n" if "qwen" in model_name else ""
    system_msg = (
        f"{no_think_prefix}"
        "És um consultor de inteligência de negócios especializado em PMEs nos Açores, Portugal. "
        "Tens acesso aos dados de auditoria digital desta empresa. "
        "Responde em português de Portugal, de forma concisa e prática. "
        "Foca em insights acionáveis baseados nos dados disponíveis."
    )

    messages = [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": f"Contexto da empresa:\n{context}"},
        {"role": "assistant", "content": "Entendido. Tenho os dados desta empresa. Como posso ajudar?"},
    ]
    for h in body.history:
        messages.append({"role": h.role, "content": h.content})
    messages.append({"role": "user", "content": body.message})

    async def stream_ollama():
        payload = {
            "model": body.model,
            "messages": messages,
            "stream": True,
            "think": False,
            "options": {"temperature": 0.4, "num_predict": 800},
        }
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                async with client.stream(
                    "POST", f"{OLLAMA_URL}/api/chat", json=payload
                ) as resp:
                    async for line in resp.aiter_lines():
                        if not line:
                            continue
                        try:
                            chunk = json.loads(line)
                            token = chunk.get("message", {}).get("content", "")
                            if token:
                                yield f"data: {json.dumps({'token': token})}\n\n"
                            if chunk.get("done"):
                                yield "data: [DONE]\n\n"
                        except Exception:
                            continue
        except httpx.ConnectError:
            yield f"data: {json.dumps({'token': 'Erro: Ollama desligou durante a resposta. Reinicia com: ollama serve'})}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'token': f'Erro: {str(e)[:120]}'})}\n\n"
            yield "data: [DONE]\n\n"

    return StreamingResponse(stream_ollama(), media_type="text/event-stream")
