import { useState, useRef, useEffect, useCallback } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { MapContainer, TileLayer, Marker, Popup } from "react-leaflet";
import L from "leaflet";
import { API_BASE, apiFetch, type Company } from "../api/client";
import { ScoreBadge, StatusPill, ScoreLegend, FaviconImg } from "../components/ScoreBadge";
import { ArrowLeft, Globe, Phone, MessageCircle, Send, Loader2, MapPin, ExternalLink, Mail, Brain, Radio, AlertTriangle, Trash2, Copy, Check, Search, ShieldCheck, ShieldOff, Smartphone, BarChart2, Target, Code2, Navigation } from "lucide-react";

const TABS = ["Perfil", "Auditoria", "Análise & IA", "Notas"] as const;
type Tab = typeof TABS[number];

const _SOCIAL_HOSTS = ["facebook.com", "fb.com", "instagram.com", "twitter.com", "x.com",
  "linkedin.com", "youtube.com", "youtu.be", "tiktok.com", "pinterest.com"];
function isSocialUrl(url: string | null | undefined): boolean {
  if (!url) return false;
  try {
    const host = new URL(url.startsWith("http") ? url : `https://${url}`).hostname.replace(/^(www\.|m\.)/, "");
    return _SOCIAL_HOSTS.some(d => host === d || host.endsWith("." + d));
  } catch { return false; }
}
function hasRealWebsite(url: string | null | undefined): boolean {
  return !!url && !isSocialUrl(url);
}

interface SingleState { state: "idle" | "auditing" | "analyzing" | "enriching" | "done" | "error"; step: string; error: string | null; }
interface ChatMsg { role: string; content: string; }
interface HistoryEntry { role: string; content: string; created_at: string; }

function useAuditSingle(companyId: string | undefined) {
  const qc = useQueryClient();
  const [opState, setOpState] = useState<"idle" | "auditing" | "analyzing" | "enriching">("idle");

  const { data: singleStatus } = useQuery<SingleState>({
    queryKey: ["single-status", companyId],
    queryFn: () => apiFetch(`/api/pipeline/single/${companyId}/status`),
    refetchInterval: opState !== "idle" ? 1000 : false,
    enabled: !!companyId,
  });

  useEffect(() => {
    if (!singleStatus) return;
    if (singleStatus.state === "done" || singleStatus.state === "error") {
      setOpState("idle");
      qc.invalidateQueries({ queryKey: ["company", companyId] });
      qc.invalidateQueries({ queryKey: ["stats"] });
      qc.invalidateQueries({ queryKey: ["pipeline-counts"] });
    }
  }, [singleStatus?.state]);

  const startAudit = async () => {
    setOpState("auditing");
    const res = await apiFetch<{ ok: boolean; error?: string }>(`/api/pipeline/audit-single/${companyId}`, { method: "POST" });
    if (!res.ok) { setOpState("idle"); alert(res.error ?? "Erro ao iniciar auditoria"); }
  };

  const startAnalyze = async () => {
    setOpState("analyzing");
    const res = await apiFetch<{ ok: boolean; error?: string }>(`/api/pipeline/analyze-single/${companyId}`, { method: "POST" });
    if (!res.ok) { setOpState("idle"); alert(res.error ?? "Erro ao iniciar análise"); }
  };

  const startEnrich = async () => {
    setOpState("enriching");
    const res = await apiFetch<{ ok: boolean; error?: string }>(`/api/pipeline/enrich-single/${companyId}`, { method: "POST" });
    if (!res.ok) { setOpState("idle"); alert(res.error ?? "Erro ao iniciar pesquisa"); }
  };

  return { opState, singleStatus, startAudit, startAnalyze, startEnrich };
}

export default function CompanyDetail() {
  const { id } = useParams<{ id: string }>();
  const nav = useNavigate();
  const qc = useQueryClient();
  const [tab, setTab] = useState<Tab>("Perfil");
  const [notas, setNotas] = useState("");
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [copied, setCopied] = useState(false);
  const [websiteInput, setWebsiteInput] = useState("");
  const [editingEmail, setEditingEmail] = useState(false);
  const [draftAssunto, setDraftAssunto] = useState("");
  const [draftMensagem, setDraftMensagem] = useState("");
  const chatEndRef = useRef<HTMLDivElement>(null);

  const { data: company, isLoading } = useQuery<Company>({
    queryKey: ["company", id],
    queryFn: () => apiFetch(`/api/companies/${id}`),
  });

  const { opState, singleStatus, startAudit, startAnalyze, startEnrich } = useAuditSingle(id);

  useEffect(() => {
    if (company) {
      setNotas(company.notas || "");
      setDraftAssunto(company.email_assunto || "");
      setDraftMensagem(company.email_mensagem || "");
    }
  }, [company]);
  useEffect(() => { chatEndRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages]);

  // Load persisted chat history whenever the company changes
  useEffect(() => {
    if (!id) return;
    setMessages([]);
    apiFetch<HistoryEntry[]>(`/api/llm/history/${id}`)
      .then(entries => {
        if (entries.length > 0) {
          setMessages(entries.map(e => ({ role: e.role, content: e.content })));
        }
      })
      .catch(() => {});
  }, [id]);

  const saveMutation = useMutation({
    mutationFn: (n: string) => apiFetch(`/api/companies/${id}`, { method: "PATCH", body: JSON.stringify({ notas: n }) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["company", id] }),
  });

  const saveEmailMutation = useMutation({
    mutationFn: (data: { email_assunto: string; email_mensagem: string }) =>
      apiFetch(`/api/companies/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["company", id] });
      setEditingEmail(false);
    },
  });

  const saveWebsiteMutation = useMutation({
    mutationFn: (url: string) => apiFetch(`/api/companies/${id}`, { method: "PATCH", body: JSON.stringify({ website: url }) }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["company", id] });
      qc.invalidateQueries({ queryKey: ["companies"] });
      setWebsiteInput("");
    },
  });

  const clearHistory = useCallback(async () => {
    await apiFetch(`/api/llm/history/${id}`, { method: "DELETE" });
    setMessages([]);
  }, [id]);

  const sendMessage = async (msg: string) => {
    if (!msg.trim() || streaming) return;
    const userMsg: ChatMsg = { role: "user", content: msg };
    const assistantMsg: ChatMsg = { role: "assistant", content: "" };
    setMessages(prev => [...prev, userMsg, assistantMsg]);
    setInput("");
    setStreaming(true);
    let fullResponse = "";
    try {
      const res = await fetch(`${API_BASE}/api/llm/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ company_id: Number(id), message: msg, history: messages }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: `Erro ${res.status}` }));
        setMessages(prev => { const u = [...prev]; u[u.length - 1] = { role: "assistant", content: `Erro: ${err.detail ?? res.statusText}` }; return u; });
        return;
      }
      const reader = res.body!.getReader();
      const dec = new TextDecoder();
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        for (const line of dec.decode(value).split("\n")) {
          if (!line.startsWith("data:")) continue;
          const data = line.slice(5).trim();
          if (data === "[DONE]") break;
          try {
            fullResponse += JSON.parse(data).token ?? "";
            setMessages(prev => { const u = [...prev]; u[u.length - 1] = { role: "assistant", content: fullResponse }; return u; });
          } catch {}
        }
      }
      // Persist the new exchange to SQLite
      if (fullResponse) {
        apiFetch(`/api/llm/history/${id}`, {
          method: "POST",
          body: JSON.stringify({ messages: [userMsg, { role: "assistant", content: fullResponse }] }),
        }).catch(() => {});
      }
    } catch {
      setMessages(prev => { const u = [...prev]; u[u.length - 1] = { role: "assistant", content: "Erro ao contactar Ollama." }; return u; });
    } finally { setStreaming(false); }
  };

  if (isLoading) return <div className="h-full flex items-center justify-center text-gray-500 text-sm">A carregar...</div>;
  if (!company) return <div className="h-full flex items-center justify-center text-red-400 text-sm">Empresa não encontrada</div>;

  const pinIcon = L.divIcon({
    html: `<div class="score-marker" style="background:${(company.score ?? 0) >= 70 ? "#ef4444" : (company.score ?? 0) >= 40 ? "#eab308" : "#22c55e"}">${company.score ?? "?"}</div>`,
    className: "", iconSize: [28, 28], iconAnchor: [14, 14],
  });

  const quickPrompts = [
    company.tem_booking === 0 && "Impacto de um sistema de marcações online?",
    !company.whatsapp_link && "Como o WhatsApp Business ajudaria?",
    (company.load_time ?? 0) > 4 && `Site demora ${company.load_time}s. Otimizações?`,
    "Os 3 passos mais rápidos para melhorar a presença digital?",
    "Analisa a concorrência local no mesmo sector",
  ].filter(Boolean) as string[];

  // Score stale: company was re-audited after last analysis
  const scoreIsStale = company.status === "auditado" && company.score !== null;

  const mapsUrl = company.lat && company.lon
    ? `https://www.google.com/maps?q=${company.lat},${company.lon}`
    : company.morada
    ? `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(company.morada + ", Açores, Portugal")}`
    : null;

  // Social links from OSM tags that could serve as audit target
  const _OSM_SOCIAL_KEYS = ["contact:facebook","facebook","contact:instagram","instagram","contact:website","website"];
  const osmSocialLinks: { label: string; url: string }[] = [];
  if (company.osm_tags) {
    for (const key of _OSM_SOCIAL_KEYS) {
      const val = (company.osm_tags as Record<string, string>)[key];
      if (val && val.startsWith("http")) {
        const label = key.replace("contact:", "").replace(/^./, c => c.toUpperCase());
        if (!osmSocialLinks.find(l => l.url === val)) {
          osmSocialLinks.push({ label, url: val });
        }
      }
    }
  }
  // Also include redes_sociais discovered during audit
  const socialAuditLinks: { label: string; url: string }[] = [
    ...osmSocialLinks,
    ...Object.entries(company.redes_sociais ?? {})
      .filter(([, url]) => !osmSocialLinks.find(l => l.url === url))
      .map(([k, url]) => ({ label: k.charAt(0).toUpperCase() + k.slice(1), url })),
  ];

  return (
    <div className="h-full flex flex-col overflow-hidden">

      {/* Compact header */}
      <div className="px-5 py-3 border-b border-gray-800 bg-[#0d0d14] shrink-0">
        <div className="flex items-center gap-3">
          <button onClick={() => nav(-1)} className="text-gray-500 hover:text-gray-300 shrink-0">
            <ArrowLeft size={16} />
          </button>
          <FaviconImg url={company.favicon_url ?? null} name={company.nome} size={24} />
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <h2 className="text-gray-100 font-semibold text-sm truncate">{company.nome}</h2>
              <span className="text-gray-600 text-xs">·</span>
              <span className="text-gray-500 text-xs">{company.nicho}</span>
              {company.morada && (
                <>
                  <span className="text-gray-700 text-xs">·</span>
                  {mapsUrl ? (
                    <a
                      href={mapsUrl}
                      target="_blank"
                      rel="noreferrer"
                      title="Ver no Google Maps"
                      className="text-gray-600 text-xs flex items-center gap-1 truncate max-w-xs hover:text-cyan-400 transition-colors group"
                    >
                      <MapPin size={10} />
                      <span className="truncate">{company.morada}</span>
                      <Navigation size={9} className="shrink-0 opacity-0 group-hover:opacity-100 transition-opacity" />
                    </a>
                  ) : (
                    <span className="text-gray-600 text-xs flex items-center gap-1 truncate max-w-xs">
                      <MapPin size={10} />{company.morada}
                    </span>
                  )}
                </>
              )}
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0 flex-wrap justify-end">
            {company.updated_at && (
              <span className="text-gray-700 text-xs" title="Data da última atualização">
                {new Date(company.updated_at + "Z").toLocaleDateString("pt-PT", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })}
              </span>
            )}
            <StatusPill status={company.status} />
            {company.score !== null && <ScoreBadge score={company.score} showLabel />}
          </div>
        </div>

        {/* Audit / Analyze actions */}
        <div className="flex items-center gap-2 mt-2 pl-7 flex-wrap">
          {hasRealWebsite(company.website) && opState === "idle" && (
            <button
              onClick={startAudit}
              className={`flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg transition-colors border ${
                company.status === "pendente"
                  ? "bg-blue-900/30 border-blue-700 text-blue-300 hover:bg-blue-800/40"
                  : "bg-gray-800 border-gray-700 text-gray-400 hover:text-gray-200 hover:border-gray-600"
              }`}
            >
              <Radio size={12} />
              {company.status === "pendente" ? "Auditar website" : "Re-auditar"}
            </button>
          )}

          {/* No real website — offer enrichment search (covers null AND social-only) */}
          {!hasRealWebsite(company.website) && company.status === "pendente" && opState === "idle" && (
            <button
              onClick={startEnrich}
              className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg transition-colors border bg-indigo-900/30 border-indigo-700 text-indigo-300 hover:bg-indigo-800/40"
              title="Pesquisa automática via DuckDuckGo + validação LLM"
            >
              <Search size={12} />
              Pesquisar website online
            </button>
          )}

          {company.status !== "pendente" && opState === "idle" && (
            <button
              onClick={startAnalyze}
              disabled={!company.texto_homepage}
              title={!company.texto_homepage ? "Audita o website primeiro" : undefined}
              className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg border bg-purple-900/30 border-purple-700 text-purple-300 hover:bg-purple-800/40 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
            >
              <Brain size={12} />
              {company.status === "analisado" ? "Re-analisar" : "Analisar com IA"}
            </button>
          )}

          {/* Stale score warning */}
          {scoreIsStale && opState === "idle" && (
            <span className="flex items-center gap-1 text-yellow-500 text-xs bg-yellow-900/20 border border-yellow-800/40 px-2 py-1 rounded-lg">
              <AlertTriangle size={11} />
              Score desactualizado — re-analisa
            </span>
          )}

          {opState !== "idle" && (
            <div className="flex items-center gap-2 flex-1">
              <Loader2 size={12} className="animate-spin text-cyan-400 shrink-0" />
              <div className="flex-1 max-w-xs">
                <div className="h-1 bg-gray-800 rounded-full overflow-hidden">
                  <div className="h-full bg-cyan-500 rounded-full animate-pulse" style={{ width: "60%" }} />
                </div>
                <p className="text-gray-500 text-xs mt-0.5">
                  {singleStatus?.step || (
                    opState === "auditing" ? "A visitar o website..."
                    : opState === "enriching" ? "A pesquisar online..."
                    : "A analisar com IA..."
                  )}
                </p>
              </div>
              {singleStatus?.state === "error" && (
                <span className="text-red-400 text-xs">{singleStatus.error}</span>
              )}
            </div>
          )}
        </div>

        {/* Quick contacts */}
        <div className="flex items-center gap-4 mt-1.5 pl-7">
          {hasRealWebsite(company.website) && (
            <a href={company.website!} target="_blank" rel="noreferrer"
              className="flex items-center gap-1 text-cyan-500 text-xs hover:underline">
              <Globe size={11} />{company.website!.replace(/^https?:\/\//, "").split("/")[0]}<ExternalLink size={9} />
            </a>
          )}
          {isSocialUrl(company.website) && (
            <a href={company.website!} target="_blank" rel="noreferrer"
              className="flex items-center gap-1 text-blue-500 text-xs hover:underline"
              title="Apenas rede social — sem website próprio">
              <Globe size={11} />{company.website!.replace(/^https?:\/\//, "").split("/")[0]}
              <span className="text-blue-700 text-[10px]">(rede social)</span>
            </a>
          )}
          {company.telefone && <span className="flex items-center gap-1 text-gray-500 text-xs"><Phone size={11} />{company.telefone}</span>}
          {company.whatsapp_link && (
            <a href={company.whatsapp_link} target="_blank" rel="noreferrer"
              className="flex items-center gap-1 text-green-500 text-xs hover:underline">
              <MessageCircle size={11} />WhatsApp
            </a>
          )}
          {(company.emails ?? []).slice(0, 1).map(e => (
            <a key={e} href={`mailto:${e}`} className="flex items-center gap-1 text-gray-500 text-xs hover:text-cyan-400">
              <Mail size={11} />{e}
            </a>
          ))}
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 px-5 py-2 border-b border-gray-800 shrink-0">
        {TABS.map(t => (
          <button key={t} onClick={() => setTab(t)}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
              tab === t ? "bg-cyan-900/40 text-cyan-400" : "text-gray-500 hover:text-gray-300 hover:bg-gray-800/40"
            }`}>
            {t}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div className="flex-1 min-h-0 overflow-y-auto p-5">

        {tab === "Perfil" && (
          <div className="grid grid-cols-2 gap-5 h-full">
            <div className="space-y-4 overflow-y-auto">
              <div className="space-y-1.5">
                <Row label="Sector" value={company.nicho} />
                <Row label="Região" value={company.regiao} />
                {/* Morada with Google Maps link */}
                <div className="flex gap-3">
                  <span className="text-gray-600 text-xs w-20 shrink-0">Morada</span>
                  {company.morada ? (
                    mapsUrl ? (
                      <a
                        href={mapsUrl}
                        target="_blank"
                        rel="noreferrer"
                        className="text-gray-300 text-xs flex items-center gap-1.5 hover:text-cyan-400 transition-colors group"
                        title="Abrir no Google Maps"
                      >
                        {company.morada}
                        <span className="flex items-center gap-1 text-gray-600 group-hover:text-cyan-500 text-xs border border-gray-700 group-hover:border-cyan-800 px-1.5 py-0.5 rounded-full transition-colors shrink-0">
                          <Navigation size={9} /> Maps
                        </span>
                      </a>
                    ) : (
                      <span className="text-gray-300 text-xs">{company.morada}</span>
                    )
                  ) : (
                    <span className="text-gray-600 text-xs">—</span>
                  )}
                </div>
                <Row label="Fonte" value={
                  company.source === "openstreetmap" ? "OpenStreetMap"
                  : company.source === "openstreetmap+enriched" ? "OSM + pesquisa web automática"
                  : company.source
                } />
                <Row label="Coordenadas" value={company.lat ? `${company.lat.toFixed(4)}, ${company.lon?.toFixed(4)}` : "—"} />
              </div>

              {/* No real website — offer manual input + social links as audit target */}
              {!hasRealWebsite(company.website) && (
                <div className="bg-blue-900/10 border border-blue-800/30 rounded-xl p-3 space-y-3">
                  <div>
                    <p className="text-blue-400 text-xs font-medium mb-1">Adicionar URL para auditoria</p>
                    <p className="text-gray-500 text-xs">Website, página Facebook, Instagram ou qualquer URL público.</p>
                  </div>

                  {/* Quick-set social links found in OSM or previous audit */}
                  {socialAuditLinks.length > 0 && (
                    <div>
                      <p className="text-gray-600 text-xs mb-1.5">Links encontrados — clica para usar como URL de auditoria:</p>
                      <div className="flex flex-wrap gap-1.5">
                        {socialAuditLinks.map(({ label, url }) => (
                          <button
                            key={url}
                            onClick={() => saveWebsiteMutation.mutate(url)}
                            disabled={saveWebsiteMutation.isPending}
                            title={url}
                            className="flex items-center gap-1.5 text-xs bg-gray-800 border border-gray-700 text-cyan-400 hover:border-cyan-700 hover:bg-gray-700 px-2.5 py-1.5 rounded-lg transition-colors disabled:opacity-40"
                          >
                            <Globe size={10} />
                            {label}
                            <span className="text-gray-600 truncate max-w-[120px]">{url.replace(/^https?:\/\/(www\.)?/, "").split("/")[0]}</span>
                          </button>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Manual input */}
                  <div className="flex gap-2">
                    <input
                      className="flex-1 bg-gray-800 border border-gray-700 text-gray-200 text-xs rounded-lg px-3 py-1.5 focus:outline-none focus:border-cyan-600"
                      placeholder="https://facebook.com/nome-da-empresa"
                      value={websiteInput}
                      onChange={e => setWebsiteInput(e.target.value)}
                      onKeyDown={e => e.key === "Enter" && websiteInput.trim() && saveWebsiteMutation.mutate(websiteInput.trim())}
                    />
                    <button
                      onClick={() => websiteInput.trim() && saveWebsiteMutation.mutate(websiteInput.trim())}
                      disabled={!websiteInput.trim() || saveWebsiteMutation.isPending}
                      className="bg-blue-800/60 hover:bg-blue-700 disabled:opacity-40 text-blue-200 text-xs px-3 py-1.5 rounded-lg transition-colors whitespace-nowrap"
                    >
                      {saveWebsiteMutation.isPending ? "A guardar..." : "Usar este URL"}
                    </button>
                  </div>
                </div>
              )}

              {company.osm_tags && Object.keys(company.osm_tags).length > 0 && (
                <div>
                  <p className="text-gray-500 text-xs uppercase tracking-wide mb-2">Informação OSM</p>
                  <div className="flex flex-wrap gap-1.5">
                    {Object.entries(company.osm_tags).map(([k, v]) => (
                      <span key={k} className="bg-gray-800 text-gray-300 text-xs px-2 py-1 rounded-lg">
                        <span className="text-gray-500">{k}:</span> {v}
                      </span>
                    ))}
                  </div>
                </div>
              )}
              {company.redes_sociais && Object.keys(company.redes_sociais).length > 0 && (
                <div>
                  <p className="text-gray-500 text-xs uppercase tracking-wide mb-2">Redes Sociais</p>
                  <div className="flex flex-wrap gap-2">
                    {Object.entries(company.redes_sociais).map(([k, v]) => (
                      <a key={k} href={v} target="_blank" rel="noreferrer"
                        className="flex items-center gap-1 bg-gray-800 text-cyan-400 text-xs px-2.5 py-1.5 rounded-full hover:bg-gray-700 capitalize">
                        {k}<ExternalLink size={9} />
                      </a>
                    ))}
                  </div>
                </div>
              )}
              {(company.emails ?? []).length > 0 && (
                <div>
                  <p className="text-gray-500 text-xs uppercase tracking-wide mb-2">Emails</p>
                  {(company.emails ?? []).map(e => (
                    <a key={e} href={`mailto:${e}`} className="flex items-center gap-1.5 text-cyan-400 text-xs hover:underline"><Mail size={11} />{e}</a>
                  ))}
                </div>
              )}
            </div>
            <div className="rounded-xl overflow-hidden border border-gray-800 min-h-0">
              {company.lat && company.lon ? (
                <MapContainer center={[company.lat, company.lon]} zoom={15} style={{ height: "100%", width: "100%" }}>
                  <TileLayer url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" attribution="© OSM" />
                  <Marker position={[company.lat, company.lon]} icon={pinIcon}>
                    <Popup>{company.nome}</Popup>
                  </Marker>
                </MapContainer>
              ) : (
                <div className="h-full flex flex-col items-center justify-center text-gray-600 gap-2">
                  <MapPin size={24} className="text-gray-700" />
                  <p className="text-xs">Sem coordenadas</p>
                  <p className="text-gray-700 text-xs text-center px-4">Re-executa o Discovery para obter localização OSM</p>
                </div>
              )}
            </div>
          </div>
        )}

        {tab === "Auditoria" && (
          <div className="grid grid-cols-2 gap-5 h-full overflow-y-auto">
            <div className="space-y-4">
              {company.status === "pendente" && (
                hasRealWebsite(company.website) ? (
                  <div className="bg-yellow-900/20 border border-yellow-800/40 rounded-lg p-3 text-yellow-300 text-xs">
                    Empresa não auditada. Usa o botão "Auditar website" no topo para visitar o site.
                  </div>
                ) : isSocialUrl(company.website) ? (
                  <div className="bg-blue-900/20 border border-blue-800/40 rounded-lg p-3 text-blue-300 text-xs">
                    Só tem rede social — não tem website próprio para auditar. Usa "Pesquisar website online" para tentar encontrar um, ou adiciona o URL manualmente no tab Perfil.
                  </div>
                ) : (
                  <div className="bg-gray-800/40 border border-gray-700/50 rounded-lg p-3 text-gray-500 text-xs">
                    Esta empresa não tem website registado no OSM. Vai ao tab <button onClick={() => setTab("Perfil")} className="text-cyan-500 hover:underline">Perfil</button> para adicionar o URL manualmente.
                  </div>
                )
              )}
              <div className="grid grid-cols-2 gap-3">
                <Metric label="Load Time" value={company.load_time ? `${company.load_time}s` : "—"} color={(company.load_time ?? 0) > 4 ? "text-yellow-400" : "text-green-400"} />
                <Metric label="Booking Online" value={company.tem_booking ? "Sim" : "Não"} color={company.tem_booking ? "text-green-400" : "text-red-400"} />
                <Metric label="WhatsApp Direto" value={company.whatsapp_link ? "Sim" : "Não"} color={company.whatsapp_link ? "text-green-400" : "text-red-400"} />
                <Metric label="Formulários" value={String(company.formularios ?? 0)} />
              </div>

              {/* Technical audit signals */}
              {hasRealWebsite(company.website) && (
                <div>
                  <p className="text-gray-500 text-xs uppercase tracking-wide mb-2">Sinais Técnicos</p>
                  <div className="grid grid-cols-2 gap-2">
                    <TechBadge
                      icon={company.has_https ? ShieldCheck : ShieldOff}
                      label="HTTPS"
                      value={!!company.has_https}
                      goodLabel="Seguro"
                      badLabel="Sem HTTPS"
                    />
                    <TechBadge
                      icon={Smartphone}
                      label="Mobile"
                      value={!!company.has_mobile_meta}
                      goodLabel="Otimizado"
                      badLabel="Não otimizado"
                    />
                    <TechBadge
                      icon={BarChart2}
                      label="Google Analytics"
                      value={!!company.has_analytics}
                      goodLabel="Activo"
                      badLabel="Ausente"
                      neutralIfBad
                    />
                    <TechBadge
                      icon={Target}
                      label="Facebook Pixel"
                      value={!!company.has_facebook_pixel}
                      goodLabel="Activo"
                      badLabel="Ausente"
                      neutralIfBad
                    />
                    {company.cms_detected && (
                      <div className="bg-gray-800/50 rounded-xl p-3 col-span-2 flex items-center gap-2">
                        <Code2 size={13} className="text-blue-400 shrink-0" />
                        <p className="text-gray-500 text-xs">CMS:</p>
                        <p className="text-blue-300 text-xs font-semibold">{company.cms_detected}</p>
                      </div>
                    )}
                    {(company.page_word_count ?? 0) > 0 && (
                      <div className="bg-gray-800/50 rounded-xl p-3 col-span-2">
                        <p className="text-gray-600 text-xs mb-1">Conteúdo do site</p>
                        <p className={`text-xs font-semibold ${
                          (company.page_word_count ?? 0) < 100 ? "text-red-400"
                          : (company.page_word_count ?? 0) < 300 ? "text-yellow-400"
                          : "text-green-400"
                        }`}>
                          {company.page_word_count} palavras
                          {(company.page_word_count ?? 0) < 100 && " — muito escasso"}
                          {(company.page_word_count ?? 0) >= 100 && (company.page_word_count ?? 0) < 300 && " — conteúdo limitado"}
                          {(company.page_word_count ?? 0) >= 300 && " — conteúdo adequado"}
                        </p>
                      </div>
                    )}
                  </div>
                </div>
              )}
              {company.tags?.length > 0 && (
                <div>
                  <p className="text-gray-500 text-xs uppercase tracking-wide mb-2">Problemas detectados</p>
                  <div className="flex flex-wrap gap-1.5">
                    {company.tags.map(t => (
                      <span key={t} className={`text-xs px-2 py-1 rounded-lg border ${
                        t.includes("oportunidade_alta") ? "bg-red-900/30 text-red-300 border-red-800/50"
                        : t.includes("oportunidade_media") ? "bg-yellow-900/30 text-yellow-300 border-yellow-800/50"
                        : "bg-gray-800 text-gray-400 border-gray-700"
                      }`}>
                        {t.replace(/_/g, " ")}
                      </span>
                    ))}
                  </div>
                </div>
              )}
              {company.emails?.length > 0 && (
                <div>
                  <p className="text-gray-500 text-xs uppercase tracking-wide mb-2">Emails encontrados</p>
                  {company.emails.map(e => (
                    <a key={e} href={`mailto:${e}`} className="flex items-center gap-1.5 text-cyan-400 text-xs hover:underline"><Mail size={11} />{e}</a>
                  ))}
                </div>
              )}
            </div>
            <div className="flex flex-col gap-2">
              <p className="text-gray-500 text-xs uppercase tracking-wide">Texto extraído do site</p>
              {company.texto_homepage ? (
                <div className="flex-1 bg-gray-800/40 border border-gray-800 rounded-xl p-4 overflow-y-auto">
                  <p className="text-gray-400 text-xs leading-relaxed whitespace-pre-wrap">{company.texto_homepage.slice(0, 1200)}</p>
                </div>
              ) : (
                <div className="flex-1 bg-gray-800/20 border border-gray-800 rounded-xl flex items-center justify-center text-gray-700 text-xs">
                  Sem dados — audita o website primeiro
                </div>
              )}
            </div>
          </div>
        )}

        {tab === "Análise & IA" && (
          <div className="grid grid-cols-2 gap-5 h-full min-h-0">
            <div className="space-y-4 overflow-y-auto">
              {/* No audit data warning */}
              {!company.texto_homepage && (
                <div className="bg-orange-900/20 border border-orange-800/40 rounded-xl p-4 flex items-start gap-3">
                  <AlertTriangle size={16} className="text-orange-400 shrink-0 mt-0.5" />
                  <div>
                    <p className="text-orange-300 text-xs font-medium mb-1">Sem dados de auditoria</p>
                    <p className="text-orange-400/70 text-xs">Audita o website primeiro. Sem dados reais, a análise LLM produz texto genérico sem valor prático.</p>
                  </div>
                </div>
              )}
              {/* Stale score warning */}
              {scoreIsStale && (
                <div className="bg-yellow-900/20 border border-yellow-800/40 rounded-xl p-3 flex items-center gap-2">
                  <AlertTriangle size={13} className="text-yellow-400 shrink-0" />
                  <p className="text-yellow-300 text-xs">Website re-auditado — score pode estar desactualizado. Re-analisa para actualizar.</p>
                </div>
              )}
              {company.status === "analisado" ? (
                <>
                  <div className="flex items-center gap-3 flex-wrap">
                    <ScoreBadge score={company.score} showLabel />
                    <ScoreLegend />
                  </div>
                  {!company.problemas?.length && !company.impacto && (
                    <div className="bg-gray-800/40 border border-gray-700/50 rounded-xl p-4 text-xs text-gray-500">
                      Dados de análise incompletos — re-analisa para obter problemas e recomendações detalhadas.
                    </div>
                  )}
                  {company.problemas?.length > 0 && (
                    <div className="bg-red-900/10 border border-red-900/30 rounded-xl p-4">
                      <p className="text-red-400 text-xs font-medium mb-3">Problemas identificados</p>
                      <ul className="space-y-2">
                        {company.problemas.map((p, i) => (
                          <li key={i} className="text-gray-300 text-xs flex gap-2 leading-relaxed">
                            <span className="text-red-500 shrink-0 mt-0.5">•</span>{p}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {company.impacto && (
                    <div className="bg-yellow-900/10 border border-yellow-900/30 rounded-xl p-4">
                      <p className="text-yellow-400 text-xs font-medium mb-2">Impacto estimado</p>
                      <p className="text-gray-300 text-xs leading-relaxed">{company.impacto}</p>
                    </div>
                  )}
                  {(company.email_assunto || company.email_mensagem) && (
                    <div className="bg-green-900/10 border border-green-900/30 rounded-xl p-4">
                      <div className="flex items-center justify-between mb-3">
                        <p className="text-green-400 text-xs font-medium flex items-center gap-1.5">
                          <Mail size={12} />Draft de abordagem
                        </p>
                        <div className="flex items-center gap-2">
                          {!editingEmail && (
                            <button
                              onClick={() => setEditingEmail(true)}
                              className="text-xs text-gray-500 hover:text-gray-300 transition-colors px-2 py-1 rounded-lg hover:bg-gray-800"
                            >
                              Editar
                            </button>
                          )}
                          {!editingEmail && (
                            <button
                              onClick={() => {
                                const text = `Assunto: ${company.email_assunto}\n\n${company.email_mensagem}`;
                                navigator.clipboard.writeText(text).then(() => { setCopied(true); setTimeout(() => setCopied(false), 2000); });
                              }}
                              className="flex items-center gap-1 text-xs text-gray-500 hover:text-green-400 transition-colors"
                            >
                              {copied ? <Check size={11} className="text-green-400" /> : <Copy size={11} />}
                              {copied ? "Copiado!" : "Copiar"}
                            </button>
                          )}
                        </div>
                      </div>
                      {editingEmail ? (
                        <div className="space-y-2">
                          <div>
                            <p className="text-gray-500 text-xs mb-1">Assunto</p>
                            <input
                              className="w-full bg-gray-800 border border-gray-700 text-gray-200 text-xs rounded-lg px-3 py-2 focus:outline-none focus:border-green-600"
                              value={draftAssunto}
                              onChange={e => setDraftAssunto(e.target.value)}
                            />
                          </div>
                          <div>
                            <p className="text-gray-500 text-xs mb-1">Mensagem</p>
                            <textarea
                              rows={6}
                              className="w-full bg-gray-800 border border-gray-700 text-gray-200 text-xs rounded-lg px-3 py-2 focus:outline-none focus:border-green-600 resize-none leading-relaxed"
                              value={draftMensagem}
                              onChange={e => setDraftMensagem(e.target.value)}
                            />
                          </div>
                          <div className="flex items-center gap-2 pt-1">
                            <button
                              onClick={() => saveEmailMutation.mutate({ email_assunto: draftAssunto, email_mensagem: draftMensagem })}
                              disabled={saveEmailMutation.isPending}
                              className="bg-green-800/60 hover:bg-green-700 disabled:opacity-40 text-green-100 text-xs px-3 py-1.5 rounded-lg transition-colors"
                            >
                              {saveEmailMutation.isPending ? "A guardar..." : "Guardar"}
                            </button>
                            <button
                              onClick={() => { setEditingEmail(false); setDraftAssunto(company.email_assunto || ""); setDraftMensagem(company.email_mensagem || ""); }}
                              className="text-gray-500 hover:text-gray-300 text-xs px-3 py-1.5 rounded-lg transition-colors"
                            >
                              Cancelar
                            </button>
                            <button
                              onClick={() => {
                                const text = `Assunto: ${draftAssunto}\n\n${draftMensagem}`;
                                navigator.clipboard.writeText(text).then(() => { setCopied(true); setTimeout(() => setCopied(false), 2000); });
                              }}
                              className="flex items-center gap-1 text-xs text-gray-500 hover:text-green-400 transition-colors ml-auto"
                            >
                              {copied ? <Check size={11} className="text-green-400" /> : <Copy size={11} />}
                              {copied ? "Copiado!" : "Copiar"}
                            </button>
                          </div>
                        </div>
                      ) : (
                        <>
                          {company.email_assunto && (
                            <div className="mb-2">
                              <p className="text-gray-500 text-xs mb-1">Assunto</p>
                              <p className="text-gray-200 text-xs font-medium bg-gray-800/60 rounded-lg px-3 py-2">{company.email_assunto}</p>
                            </div>
                          )}
                          {company.email_mensagem && (
                            <div>
                              <p className="text-gray-500 text-xs mb-1">Mensagem</p>
                              <p className="text-gray-300 text-xs leading-relaxed whitespace-pre-wrap bg-gray-800/60 rounded-lg px-3 py-2">{company.email_mensagem}</p>
                            </div>
                          )}
                        </>
                      )}
                    </div>
                  )}
                </>
              ) : !company.texto_homepage ? null : (
                <div className="bg-purple-900/10 border border-purple-800/40 rounded-xl p-4 text-purple-300 text-xs">
                  <p className="font-medium mb-1">Empresa não analisada</p>
                  <p className="text-purple-400">Usa o botão "Analisar com IA" no topo para gerar o score de oportunidade e recomendações.</p>
                </div>
              )}
            </div>

            {/* Chat */}
            <div className="border border-gray-800 rounded-xl flex flex-col overflow-hidden min-h-0">
              <div className="px-4 py-2.5 border-b border-gray-800 bg-gray-900/50 shrink-0 flex items-center justify-between">
                <p className="text-cyan-400 text-xs font-medium">Chat IA — Pergunta sobre esta empresa</p>
                {messages.length > 0 && (
                  <button
                    onClick={clearHistory}
                    title="Limpar histórico"
                    className="text-gray-600 hover:text-gray-400 transition-colors"
                  >
                    <Trash2 size={12} />
                  </button>
                )}
              </div>
              {messages.length === 0 && (
                <div className="px-4 pt-3 flex flex-wrap gap-1.5 shrink-0">
                  {quickPrompts.slice(0, 4).map(p => (
                    <button key={p} onClick={() => sendMessage(p)}
                      className="text-xs bg-gray-800 text-gray-400 px-2.5 py-1.5 rounded-full hover:bg-gray-700 hover:text-cyan-400 transition-colors">
                      {p}
                    </button>
                  ))}
                </div>
              )}
              <div className="flex-1 min-h-0 overflow-y-auto px-4 py-3 space-y-3">
                {messages.map((m, i) => (
                  <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
                    <div className={`max-w-[85%] px-3 py-2 rounded-2xl text-xs whitespace-pre-wrap leading-relaxed ${
                      m.role === "user" ? "bg-cyan-900/50 text-cyan-100 rounded-tr-sm" : "bg-gray-800 text-gray-200 rounded-tl-sm"
                    }`}>
                      {m.content || (streaming && i === messages.length - 1 ? <Loader2 size={12} className="animate-spin text-gray-400" /> : null)}
                    </div>
                  </div>
                ))}
                <div ref={chatEndRef} />
              </div>
              <div className="flex gap-2 px-4 py-3 border-t border-gray-800 shrink-0">
                <input
                  className="flex-1 bg-gray-800 border border-gray-700 text-gray-200 text-xs rounded-xl px-3 py-2 focus:outline-none focus:border-cyan-600"
                  placeholder="Pergunta sobre esta empresa..."
                  value={input}
                  onChange={e => setInput(e.target.value)}
                  onKeyDown={e => e.key === "Enter" && !e.shiftKey && sendMessage(input)}
                />
                <button onClick={() => sendMessage(input)} disabled={streaming || !input.trim()}
                  className="bg-cyan-700 hover:bg-cyan-600 disabled:opacity-40 text-white px-3 py-2 rounded-xl transition-colors">
                  {streaming ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
                </button>
              </div>
            </div>
          </div>
        )}

        {tab === "Notas" && (
          <div className="h-full flex flex-col gap-3">
            <p className="text-gray-500 text-xs shrink-0">
              Notas locais — guardadas no SQLite, só visíveis neste sistema.
              <span className="text-gray-500 ml-2">Score e tags são geridos pelo pipeline e podem ser sobreescritos pela próxima análise batch.</span>
            </p>
            <textarea
              className="flex-1 min-h-0 bg-gray-800 border border-gray-700 text-gray-200 text-xs rounded-xl px-4 py-3 focus:outline-none focus:border-cyan-600 resize-none"
              placeholder="Observações, contactos feitos, ideias..."
              value={notas}
              onChange={e => setNotas(e.target.value)}
              onBlur={() => { if (notas !== (company?.notas || "")) saveMutation.mutate(notas); }}
            />
            <div className="flex items-center gap-3 shrink-0">
              <button onClick={() => saveMutation.mutate(notas)}
                className="bg-cyan-700 hover:bg-cyan-600 text-white px-4 py-2 rounded-lg text-xs transition-colors">
                {saveMutation.isPending ? "A guardar..." : "Guardar"}
              </button>
              {saveMutation.isSuccess && <span className="text-green-400 text-xs">Guardado!</span>}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string | null | undefined }) {
  return (
    <div className="flex gap-3">
      <span className="text-gray-600 text-xs w-20 shrink-0">{label}</span>
      <span className="text-gray-300 text-xs">{value || "—"}</span>
    </div>
  );
}

function Metric({ label, value, color = "text-gray-200" }: { label: string; value: string; color?: string }) {
  return (
    <div className="bg-gray-800/50 rounded-xl p-3">
      <p className="text-gray-600 text-xs mb-1">{label}</p>
      <p className={`font-semibold text-sm ${color}`}>{value}</p>
    </div>
  );
}

function TechBadge({ icon: Icon, label, value, goodLabel, badLabel, neutralIfBad = false }: {
  icon: React.ElementType; label: string; value: boolean;
  goodLabel: string; badLabel: string; neutralIfBad?: boolean;
}) {
  const goodCls = "bg-green-900/20 border-green-900/40 text-green-400";
  const badCls  = neutralIfBad ? "bg-gray-800/50 border-gray-700 text-gray-500" : "bg-red-900/20 border-red-900/40 text-red-400";
  return (
    <div className={`rounded-xl p-3 border flex items-center gap-2 ${value ? goodCls : badCls}`}>
      <Icon size={13} className="shrink-0" />
      <div className="min-w-0">
        <p className="text-gray-600 text-xs">{label}</p>
        <p className="text-xs font-semibold">{value ? goodLabel : badLabel}</p>
      </div>
    </div>
  );
}
