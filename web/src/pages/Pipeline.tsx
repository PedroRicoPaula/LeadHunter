import { useState, useEffect, useRef } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch, type PipelineStatus } from "../api/client";
import { Play, Loader2, Zap, ChevronDown, ChevronUp, AlertTriangle, MapPin, Globe, Link } from "lucide-react";

interface PipelineCounts {
  total: number; pendente: number; auditado: number;
  analisado: number; erros: number;
  ready_enrich: number; ready_audit: number; ready_analyze: number;
}

type LogEntry = { msg: string; type: "info" | "ok" | "err"; ts: number };

const SECS_ENRICH = 20;
const SECS_AUDIT = 8;
const SECS_ANALYZE = 75;

function fmt(secs: number) {
  if (secs < 60) return `~${secs}s`;
  const m = Math.round(secs / 60);
  return m < 60 ? `~${m}min` : `~${Math.floor(m / 60)}h${m % 60 > 0 ? `${m % 60}min` : ""}`;
}

// ── Nicho catalogue (mirrors 01_discovery_free.py NICHO_TAGS) ─────────────────
const NICHO_GROUPS: Record<string, string[]> = {
  "Alimentação":  ["Restaurantes", "Cafes", "Bares", "Pastelarias", "Takeaway"],
  "Saúde":        ["Clinicas", "Dentistas", "Farmacias", "Medicos", "Veterinarios"],
  "Alojamento":   ["Hoteis", "Alojamento Local"],
  "Comércio":     ["Supermercados", "Talhos", "Peixarias", "Padarias", "Lojas"],
  "Serviços":     ["Cabeleireiros", "Ginasios", "Garagens", "Posto Combustivel", "Bancos", "Seguros", "Contabilidade", "Advogados", "Imobiliarias"],
  "Turismo":      ["Museus", "Aluguer Carros", "Actividades"],
};

const ILHAS: { label: string; value: string }[] = [
  { label: "São Miguel",   value: "Sao Miguel, Acores" },
  { label: "Terceira",     value: "Terceira, Acores" },
  { label: "Faial",        value: "Faial, Acores" },
  { label: "Pico",         value: "Pico, Acores" },
  { label: "São Jorge",    value: "Sao Jorge, Acores" },
  { label: "Flores",       value: "Flores, Acores" },
  { label: "Graciosa",     value: "Graciosa, Acores" },
  { label: "Santa Maria",  value: "Santa Maria, Acores" },
  { label: "Corvo",        value: "Corvo, Acores" },
  { label: "Todas as Ilhas", value: "Acores" },
];

// ── Selector components ────────────────────────────────────────────────────────

function NichoSelector({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <div className="space-y-2">
      {Object.entries(NICHO_GROUPS).map(([group, items]) => (
        <div key={group}>
          <p className="text-gray-600 text-xs mb-1">{group}</p>
          <div className="flex flex-wrap gap-1">
            {items.map(item => (
              <button
                key={item}
                type="button"
                onClick={() => onChange(item)}
                className={`px-2 py-0.5 rounded text-xs border transition-colors ${
                  value.toLowerCase() === item.toLowerCase()
                    ? "bg-cyan-700 border-cyan-500 text-white"
                    : "bg-gray-800 border-gray-700 text-gray-400 hover:border-gray-500 hover:text-gray-200"
                }`}
              >
                {item}
              </button>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function IlhaSelector({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <div className="flex flex-wrap gap-1">
      {ILHAS.map(ilha => (
        <button
          key={ilha.value}
          type="button"
          onClick={() => onChange(ilha.value)}
          className={`px-2 py-0.5 rounded text-xs border transition-colors flex items-center gap-1 ${
            value === ilha.value
              ? "bg-cyan-700 border-cyan-500 text-white"
              : "bg-gray-800 border-gray-700 text-gray-400 hover:border-gray-500 hover:text-gray-200"
          }`}
        >
          <MapPin size={9} />
          {ilha.label}
        </button>
      ))}
    </div>
  );
}

// ── Limit selector (replaces number input) ────────────────────────────────────

function LimitSelector({
  pending,
  color,
  onSubmit,
  disabled,
}: {
  pending: number;
  color: "indigo" | "blue" | "purple";
  onSubmit: (max: number | undefined) => void;
  disabled: boolean;
}) {
  const presets = [5, 10, 25, 50].filter(n => n < pending);
  const btnBase = "flex items-center gap-1 disabled:opacity-40 text-xs px-2.5 py-1.5 rounded-lg transition-colors border";
  const active = {
    indigo: "bg-indigo-800/60 border-indigo-700 hover:bg-indigo-700 text-indigo-200",
    blue:   "bg-blue-800/60 border-blue-700 hover:bg-blue-700 text-blue-200",
    purple: "bg-purple-800/60 border-purple-700 hover:bg-purple-700 text-purple-200",
  }[color];
  const muted = "bg-gray-800/60 border-gray-700 hover:bg-gray-700 text-gray-400";

  return (
    <div className="flex flex-wrap gap-1.5">
      {presets.map(n => (
        <button key={n} disabled={disabled} onClick={() => onSubmit(n)} className={`${btnBase} ${muted}`}>
          {disabled ? <Loader2 size={10} className="animate-spin" /> : <Play size={10} />}
          {n}
        </button>
      ))}
      <button disabled={disabled} onClick={() => onSubmit(undefined)} className={`${btnBase} ${active}`}>
        {disabled ? <Loader2 size={10} className="animate-spin" /> : <Play size={10} />}
        {pending > 0 ? `Todas (${pending})` : "Todas"}
      </button>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function Pipeline() {
  const qc = useQueryClient();
  const logRef = useRef<HTMLDivElement>(null);

  const [log, setLog] = useState<LogEntry[]>(() => {
    try { return JSON.parse(sessionStorage.getItem("pipeline_log") || "[]"); } catch { return []; }
  });

  // Shared nicho + regiao state (used by both "Run All" and Discovery card)
  const [nicho, setNicho] = useState("Restaurantes");
  const [regiao, setRegiao] = useState("Sao Miguel, Acores");
  const [runAllOpen, setRunAllOpen] = useState(false);
  const [discoverOpen, setDiscoverOpen] = useState(false);

  useEffect(() => {
    sessionStorage.setItem("pipeline_log", JSON.stringify(log.slice(-100)));
  }, [log]);

  useEffect(() => {
    logRef.current?.scrollTo(0, logRef.current.scrollHeight);
  }, [log]);

  const addLog = (msg: string, type: LogEntry["type"] = "info") =>
    setLog(p => [...p, { msg: `[${new Date().toLocaleTimeString()}] ${msg}`, type, ts: Date.now() }]);

  const { data: status } = useQuery<PipelineStatus>({
    queryKey: ["pipeline-status"],
    queryFn: () => apiFetch("/api/pipeline/status"),
    refetchInterval: (query) => ((query.state.data as any)?.running ? 1500 : 5000),
  });

  const { data: counts } = useQuery<PipelineCounts>({
    queryKey: ["pipeline-counts"],
    queryFn: () => apiFetch("/api/pipeline/counts"),
    refetchInterval: () => (status?.running ? 3000 : 10000),
  });

  const prevCompleted = useRef<string | null>(null);
  useEffect(() => {
    if (!status) return;
    if (status.last_error && status.last_error !== prevCompleted.current) {
      addLog(`ERRO: ${status.last_error}`, "err");
      prevCompleted.current = status.last_error;
    }
    if (status.last_completed && status.last_completed !== prevCompleted.current) {
      addLog(`Concluído: ${status.last_completed}`, "ok");
      prevCompleted.current = status.last_completed;
      qc.invalidateQueries({ queryKey: ["pipeline-counts"] });
      qc.invalidateQueries({ queryKey: ["stats"] });
    }
  }, [status?.last_error, status?.last_completed]);

  const discover = useMutation({
    mutationFn: (b: { nicho: string; regiao: string }) =>
      apiFetch("/api/pipeline/discover", { method: "POST", body: JSON.stringify(b) }),
    onSuccess: (_, v) => { addLog(`Discovery: ${v.nicho} / ${v.regiao}`); qc.invalidateQueries({ queryKey: ["pipeline-status"] }); },
  });

  const runAll = useMutation({
    mutationFn: (b: { nicho: string; regiao: string }) =>
      apiFetch("/api/pipeline/run-all", { method: "POST", body: JSON.stringify(b) }),
    onSuccess: (_, v) => { addLog(`Pipeline completo iniciado: ${v.nicho} / ${v.regiao}`); qc.invalidateQueries({ queryKey: ["pipeline-status"] }); },
    onError: () => addLog("Erro ao iniciar pipeline completo", "err"),
  });
  const enrich = useMutation({
    mutationFn: (b: { max_companies?: number }) =>
      apiFetch("/api/pipeline/enrich", { method: "POST", body: JSON.stringify(b) }),
    onSuccess: (_, v) => { addLog(`Enriquecimento iniciado${v.max_companies ? ` (máx ${v.max_companies})` : ""}`); qc.invalidateQueries({ queryKey: ["pipeline-status"] }); },
  });
  const audit = useMutation({
    mutationFn: (b: { max_sites?: number }) =>
      apiFetch("/api/pipeline/audit", { method: "POST", body: JSON.stringify(b) }),
    onSuccess: (_, v) => { addLog(`Auditoria iniciada${v.max_sites ? ` (máx ${v.max_sites})` : ""}`); qc.invalidateQueries({ queryKey: ["pipeline-status"] }); },
  });
  const analyze = useMutation({
    mutationFn: (b: { max_leads?: number }) =>
      apiFetch("/api/pipeline/analyze", { method: "POST", body: JSON.stringify(b) }),
    onSuccess: (_, v) => { addLog(`Análise LLM iniciada${v.max_leads ? ` (máx ${v.max_leads})` : ""}`); qc.invalidateQueries({ queryKey: ["pipeline-status"] }); },
  });

  const webDiscover = useMutation({
    mutationFn: (b: { url: string; regiao?: string; nicho?: string; max_pages: number }) =>
      apiFetch("/api/pipeline/web-discover", { method: "POST", body: JSON.stringify(b) }),
    onSuccess: (_, v) => { addLog(`Web Discovery iniciado: ${v.url}`); qc.invalidateQueries({ queryKey: ["pipeline-status"] }); },
    onError: () => addLog("Erro ao iniciar Web Discovery", "err"),
  });

  const running = !!status?.running;
  const re = counts?.ready_enrich ?? 0;
  const ra = counts?.ready_audit ?? 0;
  const rz = counts?.ready_analyze ?? 0;

  const selectedIlhaLabel = ILHAS.find(i => i.value === regiao)?.label ?? regiao;

  return (
    <div className="h-full flex flex-col p-5 gap-4 overflow-y-auto">

      {/* Header */}
      <div className="flex items-center justify-between shrink-0">
        <h2 className="text-lg font-semibold text-gray-100">Pipeline</h2>
        <div className="flex items-center gap-4">
          {counts && counts.total > 0 && (
            <div className="flex items-center gap-3 text-xs">
              <span className="text-gray-500">{counts.total} total</span>
              <span className="text-indigo-400">{re} s/ website</span>
              <span className="text-blue-400">{counts.auditado} auditadas</span>
              <span className="text-purple-400">{counts.analisado} analisadas</span>
              {counts.erros > 0 && <span className="text-red-400">{counts.erros} erros</span>}
            </div>
          )}
          {running && (
            <span className="flex items-center gap-1.5 text-cyan-400 text-xs bg-cyan-900/20 border border-cyan-800 px-2.5 py-1 rounded-full">
              <Loader2 size={11} className="animate-spin" />
              {status?.step === "discovery" && "A descobrir..."}
              {status?.step === "enrich" && "A enriquecer..."}
              {status?.step === "audit" && "A auditar..."}
              {status?.step === "analyze" && "A analisar IA..."}
            </span>
          )}
        </div>
      </div>

      {/* Run All — collapsible */}
      <div className="border border-cyan-800/60 bg-cyan-900/5 rounded-xl shrink-0 overflow-hidden">
        <button
          onClick={() => setRunAllOpen(o => !o)}
          className="w-full flex items-center justify-between px-4 py-3 hover:bg-cyan-900/10 transition-colors"
        >
          <div className="flex items-center gap-2.5">
            <Zap size={15} className="text-cyan-400" />
            <span className="text-gray-200 text-sm font-medium">Executar Pipeline Completo</span>
            <span className="text-gray-500 text-xs">
              {nicho} · {selectedIlhaLabel}
            </span>
          </div>
          {runAllOpen ? <ChevronUp size={14} className="text-gray-500" /> : <ChevronDown size={14} className="text-gray-500" />}
        </button>
        {runAllOpen && (
          <div className="px-4 pb-4 border-t border-cyan-800/30 space-y-4 pt-3">
            <div>
              <p className="text-gray-500 text-xs mb-2">Sector</p>
              <NichoSelector value={nicho} onChange={setNicho} />
            </div>
            <div>
              <p className="text-gray-500 text-xs mb-2">Ilha</p>
              <IlhaSelector value={regiao} onChange={setRegiao} />
            </div>
            <div className="flex gap-2 pt-1">
              <button
                onClick={() => runAll.mutate({ nicho, regiao })}
                disabled={running}
                className="flex items-center gap-1.5 bg-cyan-700 hover:bg-cyan-600 disabled:opacity-40 text-white px-4 py-1.5 rounded-lg text-sm transition-colors"
              >
                {running ? <Loader2 size={13} className="animate-spin" /> : <Zap size={13} />}
                Executar tudo
              </button>
              <button
                onClick={() => discover.mutate({ nicho, regiao })}
                disabled={running}
                className="flex items-center gap-1.5 bg-gray-800 hover:bg-gray-700 disabled:opacity-40 text-gray-400 px-4 py-1 rounded-lg text-xs transition-colors"
              >
                Só Discovery
              </button>
            </div>
          </div>
        )}
      </div>

      {/* 4 step cards */}
      <div className="grid grid-cols-4 gap-3">

        {/* Step 1 — Discovery */}
        <div className="border border-cyan-800/40 bg-cyan-900/5 rounded-xl overflow-hidden flex flex-col">
          <div className="p-4 flex flex-col gap-3">
            <div className="flex items-center gap-2">
              <span className="w-6 h-6 rounded-full bg-cyan-900 text-cyan-400 flex items-center justify-center text-xs font-bold shrink-0">1</span>
              <div className="min-w-0">
                <p className="text-gray-100 text-sm font-medium leading-tight">Discovery</p>
                <p className="text-gray-600 text-xs">OpenStreetMap · grátis</p>
              </div>
            </div>
            <p className="text-gray-500 text-xs leading-relaxed">Pesquisa empresas por sector e ilha.</p>
            <p className="text-gray-600 text-xs">{counts?.total ?? 0} empresas na BD</p>

            {/* Selected summary + expand */}
            <button
              onClick={() => setDiscoverOpen(o => !o)}
              className="flex items-center justify-between w-full bg-gray-800/60 hover:bg-gray-800 border border-gray-700 rounded-lg px-2.5 py-1.5 transition-colors"
            >
              <span className="text-xs text-gray-300 truncate">
                <span className="text-cyan-400">{nicho}</span>
                <span className="text-gray-600 mx-1">·</span>
                <span className="text-gray-400">{selectedIlhaLabel}</span>
              </span>
              {discoverOpen ? <ChevronUp size={12} className="text-gray-500 shrink-0" /> : <ChevronDown size={12} className="text-gray-500 shrink-0" />}
            </button>

            {discoverOpen && (
              <div className="space-y-3 border-t border-gray-800 pt-3">
                <div>
                  <p className="text-gray-600 text-xs mb-2">Sector</p>
                  <NichoSelector value={nicho} onChange={setNicho} />
                </div>
                <div>
                  <p className="text-gray-600 text-xs mb-2">Ilha</p>
                  <IlhaSelector value={regiao} onChange={setRegiao} />
                </div>
              </div>
            )}

            <button
              onClick={() => discover.mutate({ nicho, regiao })}
              disabled={running}
              className="w-full flex items-center justify-center gap-1.5 bg-cyan-800/60 hover:bg-cyan-700 disabled:opacity-40 text-cyan-200 text-xs px-3 py-1.5 rounded-lg transition-colors"
            >
              {running && status?.step === "discovery"
                ? <Loader2 size={11} className="animate-spin" />
                : <Play size={11} />}
              Executar Discovery
            </button>
          </div>
        </div>

        {/* Step 2 — Enriquecimento */}
        <div className="border border-indigo-800/40 bg-indigo-900/5 rounded-xl p-4 flex flex-col gap-3">
          <div className="flex items-center gap-2">
            <span className="w-6 h-6 rounded-full bg-indigo-900 text-indigo-400 flex items-center justify-center text-xs font-bold shrink-0">2</span>
            <div className="min-w-0">
              <p className="text-gray-100 text-sm font-medium leading-tight">Enriquecimento</p>
              <p className="text-gray-600 text-xs">DDG + Bing + plataformas PT</p>
            </div>
          </div>
          <p className="text-gray-500 text-xs leading-relaxed">Encontra websites para empresas sem URL. Verifica eatbu.com, zomato e mais.</p>
          {re > 0 ? (
            <p className={`text-xs font-medium flex items-center gap-1 ${re * SECS_ENRICH > 600 ? "text-yellow-400" : "text-indigo-400"}`}>
              {re * SECS_ENRICH > 600 && <AlertTriangle size={11} />}
              {re} sem website · {fmt(re * SECS_ENRICH)} total
            </p>
          ) : (
            <p className="text-gray-700 text-xs">todas as empresas têm website</p>
          )}
          <LimitSelector
            pending={re}
            color="indigo"
            disabled={running}
            onSubmit={max => enrich.mutate({ max_companies: max })}
          />
        </div>

        {/* Step 3 — Auditoria */}
        <div className="border border-blue-800/40 bg-blue-900/5 rounded-xl p-4 flex flex-col gap-3">
          <div className="flex items-center gap-2">
            <span className="w-6 h-6 rounded-full bg-blue-900 text-blue-400 flex items-center justify-center text-xs font-bold shrink-0">3</span>
            <div className="min-w-0">
              <p className="text-gray-100 text-sm font-medium leading-tight">Auditoria</p>
              <p className="text-gray-600 text-xs">Playwright · Chromium</p>
            </div>
          </div>
          <p className="text-gray-500 text-xs leading-relaxed">Visita cada website. Extrai emails, load time, booking, WhatsApp, redes sociais.</p>
          {ra > 0 ? (
            <p className={`text-xs font-medium flex items-center gap-1 ${ra * SECS_AUDIT > 600 ? "text-yellow-400" : "text-blue-400"}`}>
              {ra * SECS_AUDIT > 600 && <AlertTriangle size={11} />}
              {ra} sites prontos · {fmt(ra * SECS_AUDIT)} total
            </p>
          ) : (
            <p className="text-gray-700 text-xs">sem sites para auditar</p>
          )}
          <LimitSelector
            pending={ra}
            color="blue"
            disabled={running}
            onSubmit={max => audit.mutate({ max_sites: max })}
          />
        </div>

        {/* Step 4 — Análise LLM */}
        <div className="border border-purple-800/40 bg-purple-900/5 rounded-xl p-4 flex flex-col gap-3">
          <div className="flex items-center gap-2">
            <span className="w-6 h-6 rounded-full bg-purple-900 text-purple-400 flex items-center justify-center text-xs font-bold shrink-0">4</span>
            <div className="min-w-0">
              <p className="text-gray-100 text-sm font-medium leading-tight">Análise LLM</p>
              <p className="text-gray-600 text-xs">Ollama · local · sem custo</p>
            </div>
          </div>
          <p className="text-gray-500 text-xs leading-relaxed">Score de oportunidade, problemas e recomendações. Funciona sem Ollama (scoring automático).</p>
          {rz > 0 ? (
            <p className={`text-xs font-medium flex items-center gap-1 ${rz * SECS_ANALYZE > 600 ? "text-yellow-400" : "text-purple-400"}`}>
              {rz * SECS_ANALYZE > 600 && <AlertTriangle size={11} />}
              {rz} prontas · {fmt(rz * SECS_ANALYZE)} total
            </p>
          ) : (
            <p className="text-gray-700 text-xs">sem empresas auditadas</p>
          )}
          <LimitSelector
            pending={rz}
            color="purple"
            disabled={running}
            onSubmit={max => analyze.mutate({ max_leads: max })}
          />
        </div>
      </div>

      {/* Web Discovery */}
      <WebDiscoverSection
        running={running}
        isWebStep={status?.step === "web_discovery"}
        onSubmit={(url, maxPages) => webDiscover.mutate({ url, max_pages: maxPages })}
      />

      {/* Log */}
      <div className="bg-gray-900 border border-gray-800 rounded-xl flex flex-col flex-1 min-h-[120px]">
        <div className="flex items-center justify-between px-4 py-2 border-b border-gray-800 shrink-0">
          <span className="text-gray-400 text-xs font-medium">Log</span>
          <button onClick={() => { setLog([]); sessionStorage.removeItem("pipeline_log"); }} className="text-gray-700 text-xs hover:text-gray-400">limpar</button>
        </div>
        <div ref={logRef} className="flex-1 overflow-y-auto px-4 py-2 font-mono text-xs space-y-0.5">
          {log.length === 0
            ? <p className="text-gray-700">Sem actividade.</p>
            : log.slice(-20).map((l, i) => (
              <p key={i} className={l.type === "err" ? "text-red-400" : l.type === "ok" ? "text-green-400" : "text-gray-500"}>{l.msg}</p>
            ))
          }
        </div>
      </div>
    </div>
  );
}

// ── Web Discovery section ─────────────────────────────────────────────────────

const KNOWN_SOURCES = [
  // ── TripAdvisor ────────────────────────────────────────────────────────────
  { group: "TripAdvisor", label: "Restaurantes — S. Miguel",   url: "https://www.tripadvisor.pt/Restaurants-g189116-Sao_Miguel_Azores.html" },
  { group: "TripAdvisor", label: "Restaurantes — Terceira",    url: "https://www.tripadvisor.pt/Restaurants-g189114-Terceira_Azores.html" },
  { group: "TripAdvisor", label: "Atrações — S. Miguel",       url: "https://www.tripadvisor.pt/Attractions-g189116-Sao_Miguel_Azores.html" },
  { group: "TripAdvisor", label: "Hotéis — S. Miguel",         url: "https://www.tripadvisor.pt/Hotels-g189116-Sao_Miguel_Azores.html" },
  { group: "TripAdvisor", label: "Hotéis — Terceira",          url: "https://www.tripadvisor.pt/Hotels-g189114-Terceira_Azores.html" },
  // ── VisitAzores ─────────────────────────────────────────────────────────────
  { group: "VisitAzores", label: "Experiências — S. Miguel",   url: "https://www.visitazores.com/explorar?category=experiences&island=sao-miguel&page=1" },
  { group: "VisitAzores", label: "Restaurantes — S. Miguel",   url: "https://www.visitazores.com/explorar?category=restaurants&island=sao-miguel&page=1" },
  { group: "VisitAzores", label: "Alojamento — S. Miguel",     url: "https://www.visitazores.com/explorar?category=accommodations&island=sao-miguel&page=1" },
  { group: "VisitAzores", label: "Experiências — Terceira",    url: "https://www.visitazores.com/explorar?category=experiences&island=terceira&page=1" },
];

function WebDiscoverSection({
  running,
  isWebStep,
  onSubmit,
}: {
  running: boolean;
  isWebStep: boolean;
  onSubmit: (url: string, maxPages: number) => void;
}) {
  const [open, setOpen] = useState(false);
  const [url, setUrl] = useState("");
  const [maxPages, setMaxPages] = useState(10);
  const inputCls = "bg-gray-800 border border-gray-700 text-gray-200 text-xs rounded-lg px-2.5 py-1.5 focus:outline-none focus:border-cyan-600 w-full";

  return (
    <div className="border border-emerald-800/40 bg-emerald-900/5 rounded-xl shrink-0 overflow-hidden">
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between px-4 py-3 hover:bg-emerald-900/10 transition-colors"
      >
        <div className="flex items-center gap-2.5">
          <Globe size={14} className="text-emerald-400 shrink-0" />
          <span className="text-gray-200 text-sm font-medium">Descoberta via Web</span>
          <span className="text-gray-600 text-xs">Scraping de portais · visitazores.com e outros</span>
        </div>
        {open ? <ChevronUp size={14} className="text-gray-500" /> : <ChevronDown size={14} className="text-gray-500" />}
      </button>

      {open && (
        <div className="px-4 pb-4 border-t border-emerald-800/30 space-y-3 pt-3">
          <p className="text-gray-500 text-xs leading-relaxed">
            Cola um URL de listagem — o sistema percorre todas as páginas automaticamente,
            extrai empresas e adiciona ao pipeline. Funciona com visitazores.com e outros portais PT.
          </p>

          {/* Quick-pick known sources grouped by portal */}
          <div className="space-y-2">
            {(["TripAdvisor", "VisitAzores"] as const).map(group => (
              <div key={group}>
                <p className="text-gray-600 text-xs mb-1">
                  <span className={group === "TripAdvisor" ? "text-green-500" : "text-cyan-600"}>{group}</span>
                </p>
                <div className="flex flex-wrap gap-1">
                  {KNOWN_SOURCES.filter(s => s.group === group).map(s => (
                    <button
                      key={s.url}
                      type="button"
                      onClick={() => setUrl(s.url)}
                      className={`text-xs px-2 py-0.5 rounded border transition-colors ${
                        url === s.url
                          ? "bg-emerald-700 border-emerald-500 text-white"
                          : "bg-gray-800 border-gray-700 text-gray-400 hover:border-gray-500 hover:text-gray-200"
                      }`}
                    >
                      {s.label}
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>

          {/* URL input */}
          <div>
            <label className="text-gray-500 text-xs block mb-1 flex items-center gap-1">
              <Link size={10} /> URL de listagem
            </label>
            <input
              className={inputCls}
              placeholder="https://www.visitazores.com/explorar?category=experiences&island=sao-miguel&page=1"
              value={url}
              onChange={e => setUrl(e.target.value)}
            />
          </div>

          {/* Max pages */}
          <div className="flex items-center gap-3">
            <div className="flex-1">
              <label className="text-gray-500 text-xs block mb-1">Máx. páginas a percorrer</label>
              <div className="flex gap-1">
                {[3, 5, 10, 20].map(n => (
                  <button
                    key={n}
                    type="button"
                    onClick={() => setMaxPages(n)}
                    className={`text-xs px-2.5 py-1 rounded border transition-colors ${
                      maxPages === n
                        ? "bg-emerald-700 border-emerald-500 text-white"
                        : "bg-gray-800 border-gray-700 text-gray-400 hover:text-gray-200"
                    }`}
                  >
                    {n}
                  </button>
                ))}
              </div>
            </div>
            <button
              onClick={() => url && onSubmit(url, maxPages)}
              disabled={running || !url}
              className="flex items-center gap-1.5 bg-emerald-700 hover:bg-emerald-600 disabled:opacity-40 text-white px-4 py-2 rounded-lg text-sm transition-colors self-end"
            >
              {running && isWebStep
                ? <Loader2 size={13} className="animate-spin" />
                : <Play size={13} />}
              Scrape
            </button>
          </div>

          {running && isWebStep && (
            <p className="text-emerald-400 text-xs flex items-center gap-1.5">
              <Loader2 size={10} className="animate-spin" />
              A fazer scraping... (pode demorar vários minutos)
            </p>
          )}
        </div>
      )}
    </div>
  );
}
