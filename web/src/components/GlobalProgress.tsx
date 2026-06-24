import { useQuery } from "@tanstack/react-query";
import { apiFetch, type PipelineStatus } from "../api/client";
import { Loader2 } from "lucide-react";
import { useNavigate } from "react-router-dom";

const STEP_LABELS: Record<string, string> = {
  discovery: "A descobrir empresas (OpenStreetMap)...",
  enrich: "A pesquisar websites (DuckDuckGo + LLM)...",
  audit: "A auditar websites (Playwright)...",
  analyze: "A analisar com IA (Ollama)...",
};

export function GlobalProgress() {
  const nav = useNavigate();

  const { data: status } = useQuery<PipelineStatus>({
    queryKey: ["pipeline-status"],
    queryFn: () => apiFetch("/api/pipeline/status"),
    refetchInterval: (q) => (q.state.data?.running ? 1500 : 8000),
  });

  if (!status?.running) return null;

  const hasRealProgress = (status.total ?? 0) > 0;
  const pct = hasRealProgress
    ? Math.min(100, Math.round(((status.progress ?? 0) / status.total) * 100))
    : null;

  const label = hasRealProgress
    ? `${STEP_LABELS[status.step] ?? `Pipeline: ${status.step}...`} (${status.progress}/${status.total})`
    : (STEP_LABELS[status.step] ?? `Pipeline: ${status.step}...`);

  return (
    <div className="fixed top-0 left-0 right-0 z-50 bg-[#0d1117] border-b border-cyan-900/60 shadow-lg">
      <div className="h-0.5 bg-gray-800 overflow-hidden">
        {pct !== null ? (
          <div
            className="h-full bg-cyan-500 transition-all duration-700 ease-out"
            style={{ width: `${pct}%` }}
          />
        ) : (
          <div
            className="h-full bg-cyan-500"
            style={{ width: "40%", animation: "pulse-bar 2s ease-in-out infinite" }}
          />
        )}
      </div>
      <div className="flex items-center gap-3 px-4 py-2.5">
        <Loader2 size={14} className="animate-spin text-cyan-400 shrink-0" />
        <span className="text-cyan-300 text-sm flex-1">{label}</span>
        {pct !== null && (
          <span className="text-cyan-600 text-xs font-mono shrink-0">{pct}%</span>
        )}
        <button
          onClick={() => nav("/pipeline")}
          className="text-gray-400 text-xs hover:text-gray-200 transition-colors"
        >
          ver progresso
        </button>
      </div>
    </div>
  );
}
