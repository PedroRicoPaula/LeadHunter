import sys
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

"""
NVIDIA NIM — cliente central.
Toda a comunicação com a API NIM passa aqui: LLM, Reranking, Embeddings, Vision.
Uma instância singleton `nim` é exportada; os outros scripts importam-na directamente.
"""

import math
import os
import re
import threading
import time
from typing import Optional

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

_BASE_URL = "https://integrate.api.nvidia.com/v1"

_MODEL_LLM    = "meta/llama-3.3-70b-instruct"
_MODEL_RERANK = "nvidia/nv-rerankqa-mistral-4b-v3"
_MODEL_EMBED  = "nvidia/nv-embed-v2"
_MODEL_VISION = "meta/llama-3.2-90b-vision-instruct"


# ── Rate limiter simples ──────────────────────────────────────────────────────

class _RateLimiter:
    """Garante intervalo mínimo entre chamadas (free tier ~30 req/min)."""

    def __init__(self, calls_per_minute: int = 28):
        self._interval = 60.0 / calls_per_minute
        self._last = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            elapsed = time.monotonic() - self._last
            if elapsed < self._interval:
                time.sleep(self._interval - elapsed)
            self._last = time.monotonic()


# ── Funções de similaridade (sem numpy) ───────────────────────────────────────

def _dot(a: list[float], b: list[float]) -> float:
    """Produto interno — cosine similarity para vectores já normalizados."""
    return sum(x * y for x, y in zip(a, b))


def _cosine(a: list[float], b: list[float]) -> float:
    dot  = sum(x * y for x, y in zip(a, b))
    na   = math.sqrt(sum(x * x for x in a))
    nb   = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


# ── Cliente principal ─────────────────────────────────────────────────────────

class NimClient:
    """
    Cliente NIM thread-safe (sem async — usa httpx síncrono).
    Todos os métodos retornam None / [] em caso de falha para degradação graciosa.
    """

    def __init__(self) -> None:
        self._api_key: str = ""
        self._cfg: dict = {}
        self._rl  = _RateLimiter()
        self._loaded = False

    # ── Init lazy ─────────────────────────────────────────────────────────────

    def _load(self) -> None:
        if self._loaded:
            return
        self._api_key = os.getenv("NVIDIA_API_KEY", "")
        try:
            from config_loader import CONFIG  # importado lazily para evitar circular
            nim_cfg = CONFIG.get("nvidia_nim", {})
            self._cfg = nim_cfg
            if not self._api_key:
                self._api_key = nim_cfg.get("api_key", "")
        except Exception:
            pass
        self._loaded = True

    # ── Propriedades ─────────────────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        self._load()
        if not self._cfg.get("enabled", True):
            return False
        return bool(self._api_key and self._api_key.startswith("nvapi-"))

    def _feat(self, name: str) -> bool:
        self._load()
        return self._cfg.get("features", {}).get(name, True)

    def _model(self, key: str, default: str) -> str:
        self._load()
        return self._cfg.get("models", {}).get(key, default)

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    # ── Retry decorator helper ────────────────────────────────────────────────

    def _post(self, endpoint: str, payload: dict, timeout: float = 60.0) -> dict | None:
        """POST com retry em 429/5xx. Retorna JSON ou None."""
        self._rl.wait()
        url = f"{_BASE_URL}/{endpoint}"
        for attempt in range(4):
            try:
                resp = httpx.post(url, headers=self._headers(), json=payload, timeout=timeout)
                if resp.status_code == 429:
                    wait = min(10 * (attempt + 1), 60)
                    time.sleep(wait)
                    continue
                if resp.status_code >= 500:
                    time.sleep(5 * (attempt + 1))
                    continue
                resp.raise_for_status()
                return resp.json()
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.RemoteProtocolError):
                return None
            except httpx.HTTPStatusError:
                return None
            except Exception:
                time.sleep(3)
        return None

    # ── LLM ──────────────────────────────────────────────────────────────────

    def chat(
        self,
        messages: list[dict],
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 1500,
        system: str | None = None,
    ) -> str | None:
        """
        Chat completions OpenAI-compatible.
        Retorna o conteúdo da resposta (str) ou None em caso de falha.
        """
        if not self.enabled or not self._feat("llm"):
            return None
        model = model or self._model("llm", _MODEL_LLM)
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend(messages)
        data = self._post("chat/completions", {
            "model": model,
            "messages": msgs,
            "temperature": temperature,
            "max_tokens": max_tokens,
        })
        if data:
            try:
                return data["choices"][0]["message"]["content"]
            except (KeyError, IndexError):
                pass
        return None

    # ── Reranking ─────────────────────────────────────────────────────────────

    def rerank(
        self,
        query: str,
        passages: list[str],
        model: str | None = None,
    ) -> list[tuple[int, float]]:
        """
        Reranking de passages por relevância à query.
        Retorna lista de (índice_original, score) ordenada do melhor para o pior.
        Lista vazia em caso de falha ou feature desligada.
        """
        if not self.enabled or not self._feat("reranking") or not passages:
            return []
        model = model or self._model("rerank", _MODEL_RERANK)
        data = self._post("ranking", {
            "model": model,
            "query":    {"role": "user", "content": query},
            "passages": [{"role": "user", "content": p} for p in passages],
        }, timeout=30.0)
        if not data:
            return []
        try:
            rankings = data.get("rankings", [])
            return sorted(
                [(r["index"], float(r["logit"])) for r in rankings],
                key=lambda x: x[1],
                reverse=True,
            )
        except Exception:
            return []

    # ── Embeddings ───────────────────────────────────────────────────────────

    def embed(
        self,
        texts: list[str],
        input_type: str = "passage",
        model: str | None = None,
    ) -> list[list[float]]:
        """
        Embeddings de texto.
        input_type: "passage" para indexar, "query" para pesquisar.
        Retorna lista de vectores float ou [] em caso de falha.
        """
        if not self.enabled or not self._feat("embeddings") or not texts:
            return []
        model = model or self._model("embed", _MODEL_EMBED)
        # NIM embed API limita a 96 textos por chamada
        results: list[list[float]] = []
        for i in range(0, len(texts), 96):
            chunk = texts[i:i + 96]
            data = self._post("embeddings", {
                "model":           model,
                "input":           chunk,
                "input_type":      input_type,
                "encoding_format": "float",
                "truncate":        "END",
            }, timeout=30.0)
            if not data:
                return []
            try:
                items = sorted(data["data"], key=lambda x: x["index"])
                results.extend(d["embedding"] for d in items)
            except (KeyError, TypeError):
                return []
        return results

    def similarity(self, a: list[float], b: list[float]) -> float:
        """Cosine similarity entre dois vectores embedding."""
        return _cosine(a, b)

    # ── Vision ───────────────────────────────────────────────────────────────

    def vision(
        self,
        image_b64: str,
        prompt: str,
        model: str | None = None,
    ) -> str | None:
        """
        Análise de imagem (screenshot, foto).
        image_b64: imagem codificada em base64 (JPEG ou PNG).
        Retorna texto da resposta ou None.
        """
        if not self.enabled or not self._feat("vision"):
            return None
        model = model or self._model("vision", _MODEL_VISION)
        data = self._post("chat/completions", {
            "model": model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {
                        "url": f"data:image/jpeg;base64,{image_b64}"
                    }},
                ],
            }],
            "max_tokens":  512,
            "temperature": 0.2,
        }, timeout=90.0)
        if data:
            try:
                return data["choices"][0]["message"]["content"]
            except (KeyError, IndexError):
                pass
        return None


# ── Singleton exportado ───────────────────────────────────────────────────────

nim = NimClient()
