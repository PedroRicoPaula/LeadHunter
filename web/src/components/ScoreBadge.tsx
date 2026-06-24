export function scoreConfig(score: number | null) {
  if (score === null || score === undefined) return null;
  if (score >= 86) return { color: "bg-red-900/60 text-red-300 border-red-600",   label: "Crítica",  dot: "bg-red-400",    pulse: true  };
  if (score >= 71) return { color: "bg-orange-900/50 text-orange-300 border-orange-700", label: "Alta",   dot: "bg-orange-400", pulse: false };
  if (score >= 51) return { color: "bg-yellow-900/50 text-yellow-300 border-yellow-700", label: "Média",  dot: "bg-yellow-400", pulse: false };
  if (score >= 31) return { color: "bg-blue-900/40 text-blue-300 border-blue-700", label: "Baixa",  dot: "bg-blue-400",   pulse: false };
  return               { color: "bg-gray-800 text-gray-400 border-gray-700",        label: "Madura", dot: "bg-gray-500",   pulse: false };
}

export function ScoreBadge({ score, showLabel = false }: { score: number | null; showLabel?: boolean }) {
  if (score === null || score === undefined)
    return <span className="text-gray-600 text-xs">—</span>;

  const cfg = scoreConfig(score)!;

  return (
    <span
      className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-semibold border ${cfg.color}`}
      title={`Oportunidade Digital: ${score}/100 — quanto mais alto, mais gaps digitais existem`}
    >
      {cfg.pulse ? (
        <span className="relative flex h-1.5 w-1.5 shrink-0">
          <span className={`animate-ping absolute inline-flex h-full w-full rounded-full ${cfg.dot} opacity-75`} />
          <span className={`relative inline-flex rounded-full h-1.5 w-1.5 ${cfg.dot}`} />
        </span>
      ) : (
        <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${cfg.dot}`} />
      )}
      {score}
      {showLabel && <span className="opacity-70">{cfg.label}</span>}
    </span>
  );
}

export function ScoreLegend() {
  return (
    <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-gray-500">
      <span className="flex items-center gap-1 whitespace-nowrap">
        <span className="w-2 h-2 rounded-full bg-gray-500 inline-block shrink-0" /> 0–30 madura
      </span>
      <span className="flex items-center gap-1 whitespace-nowrap">
        <span className="w-2 h-2 rounded-full bg-blue-400 inline-block shrink-0" /> 31–50 baixa
      </span>
      <span className="flex items-center gap-1 whitespace-nowrap">
        <span className="w-2 h-2 rounded-full bg-yellow-400 inline-block shrink-0" /> 51–70 média
      </span>
      <span className="flex items-center gap-1 whitespace-nowrap">
        <span className="w-2 h-2 rounded-full bg-orange-400 inline-block shrink-0" /> 71–85 alta
      </span>
      <span className="flex items-center gap-1 whitespace-nowrap">
        <span className="relative flex h-2 w-2 shrink-0 items-center justify-center">
          <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-red-400 opacity-75" />
          <span className="relative inline-flex rounded-full h-2 w-2 bg-red-400" />
        </span>
        86–100 crítica
      </span>
    </div>
  );
}

function _normaliseFaviconUrl(url: string): string {
  // Convert legacy DuckDuckGo favicon URLs → Google (Google always returns 200)
  const ddgMatch = url.match(/icons\.duckduckgo\.com\/ip3\/(.+)\.ico$/);
  if (ddgMatch) {
    return `https://www.google.com/s2/favicons?domain=${ddgMatch[1]}&sz=32`;
  }
  return url;
}

export function FaviconImg({ url, name, size = 16 }: { url: string | null; name?: string; size?: number }) {
  if (!url) {
    return (
      <div
        className="rounded shrink-0 bg-gray-800 flex items-center justify-center text-gray-500 font-bold uppercase"
        style={{ width: size, height: size, fontSize: size * 0.5 }}
      >
        {(name || "?")[0]}
      </div>
    );
  }
  return (
    <img
      src={_normaliseFaviconUrl(url)}
      alt=""
      width={size}
      height={size}
      className="rounded shrink-0 object-contain"
      style={{ width: size, height: size }}
      onError={(e) => {
        const el = e.currentTarget as HTMLImageElement;
        el.style.display = "none";
        const parent = el.parentElement;
        if (parent) {
          const fallback = document.createElement("div");
          fallback.className = "rounded shrink-0 bg-gray-800 flex items-center justify-center text-gray-500 font-bold uppercase";
          fallback.style.width = `${size}px`;
          fallback.style.height = `${size}px`;
          fallback.style.fontSize = `${size * 0.5}px`;
          fallback.style.display = "flex";
          fallback.textContent = (name || "?")[0].toUpperCase();
          parent.appendChild(fallback);
        }
      }}
    />
  );
}

export function StatusPill({ status }: { status: string }) {
  const map: Record<string, { cls: string; label: string; hint: string }> = {
    pendente: {
      cls: "bg-gray-800 text-gray-400",
      label: "Pendente",
      hint: "Empresa descoberta mas ainda não visitada",
    },
    auditado: {
      cls: "bg-blue-900/50 text-blue-400",
      label: "Auditado",
      hint: "Website visitado, dados extraídos. Pronto para análise LLM.",
    },
    analisado: {
      cls: "bg-purple-900/50 text-purple-400",
      label: "Analisado",
      hint: "Análise IA completa. Score e recomendações disponíveis.",
    },
    erro_auditoria: {
      cls: "bg-red-900/40 text-red-400",
      label: "Erro auditoria",
      hint: "Site inacessível ou timeout durante visita Playwright.",
    },
    erro_llm: {
      cls: "bg-orange-900/40 text-orange-400",
      label: "Erro LLM",
      hint: "Auditoria OK mas falhou na análise Ollama. Re-analisar.",
    },
  };
  const s = map[status] ?? { cls: "bg-gray-800 text-gray-400", label: status, hint: "" };
  return (
    <span
      className={`px-2 py-0.5 rounded-full text-xs font-medium cursor-help ${s.cls}`}
      title={s.hint}
    >
      {s.label}
    </span>
  );
}
