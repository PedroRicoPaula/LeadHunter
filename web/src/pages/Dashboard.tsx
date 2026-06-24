import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from "recharts";
import { apiFetch, type Stats, type ActionLead } from "../api/client";
import { ScoreBadge, ScoreLegend, FaviconImg } from "../components/ScoreBadge";
import {
  Building2, Globe, TrendingUp, Zap, ArrowRight, Search, Radio, Brain,
  CheckCircle2, AlertTriangle, Mail, MessageCircle, Flame,
} from "lucide-react";

interface PipelineCounts {
  total: number; pendente: number; auditado: number;
  analisado: number; erros: number; ready_audit: number; ready_analyze: number;
}

const SCORE_COLORS = ["#6b7280", "#3b82f6", "#eab308", "#f97316", "#ef4444"];

export default function Dashboard() {
  const nav = useNavigate();

  const { data: stats, isLoading } = useQuery<Stats>({
    queryKey: ["stats"],
    queryFn: () => apiFetch("/api/companies/stats"),
  });

  const { data: counts } = useQuery<PipelineCounts>({
    queryKey: ["pipeline-counts"],
    queryFn: () => apiFetch("/api/pipeline/counts"),
    refetchInterval: 10_000,
  });

  const { data: actionLeads } = useQuery<ActionLead[]>({
    queryKey: ["action-immediate"],
    queryFn: () => apiFetch("/api/companies/action-immediate?limit=6"),
    enabled: !!stats && stats.analisados > 0,
  });

  if (isLoading) return <div className="h-full flex items-center justify-center text-gray-500 text-sm">A carregar...</div>;
  if (!stats) return <div className="h-full flex items-center justify-center text-red-400 text-sm">Erro ao carregar</div>;

  if (stats.total === 0) return <OnboardingGuide onNavigate={nav} />;

  const erros = (counts?.erros ?? 0);
  const nextStep = (() => {
    if ((counts?.ready_audit ?? 0) > 0) return "audit";
    if ((counts?.ready_analyze ?? 0) > 0) return "analyze";
    if (erros > 0) return "errors";
    return "done";
  })();

  const NEXT = {
    audit:   { color: "border-blue-800 bg-blue-900/10 text-blue-400",     icon: Radio,        text: `${counts?.ready_audit} empresas com website prontas para auditar`,           href: "/pipeline" },
    analyze: { color: "border-purple-800 bg-purple-900/10 text-purple-400", icon: Brain,       text: `${counts?.ready_analyze} empresas auditadas prontas para análise LLM`,        href: "/pipeline" },
    errors:  { color: "border-red-800 bg-red-900/10 text-red-400",         icon: AlertTriangle, text: `${erros} empresa${erros !== 1 ? "s" : ""} com erro — vai ao Pipeline para re-tentar`, href: "/pipeline" },
    done:    { color: "border-green-800 bg-green-900/10 text-green-400",   icon: CheckCircle2, text: "Pipeline completo — explora as empresas ou descobre novos sectores",           href: "/companies" },
  }[nextStep];

  const showActionLeads = (actionLeads?.length ?? 0) > 0;

  return (
    <div className="h-full flex flex-col p-5 gap-4 overflow-y-auto">

      {/* Next step banner */}
      <div
        className={`flex items-center gap-3 px-4 py-2.5 rounded-xl border cursor-pointer hover:brightness-110 transition-all shrink-0 ${NEXT.color}`}
        onClick={() => nav(NEXT.href)}
      >
        <NEXT.icon size={15} className="shrink-0" />
        <span className="text-sm flex-1">{NEXT.text}</span>
        <ArrowRight size={14} className="shrink-0 opacity-60" />
      </div>

      {/* Stat cards */}
      <div className="grid grid-cols-5 gap-3 shrink-0">
        <StatCard label="Empresas" value={stats.total} color="text-cyan-400" icon={Building2} onClick={() => nav("/companies")} />
        <StatCard label="Com Website" value={stats.com_website} color="text-blue-400" icon={Globe} onClick={() => nav("/companies?has_website=true")} />
        <StatCard label="Analisadas" value={stats.analisados} color="text-purple-400" icon={TrendingUp} onClick={() => nav("/companies?status=analisado")} />
        <StatCard label="Oport. Média" value={stats.avg_score ?? "—"} color="text-yellow-400" icon={Zap} />
        <div className="bg-gray-900 border border-gray-800 rounded-xl px-4 py-3">
          <p className="text-gray-500 text-xs mb-2">Progresso pipeline</p>
          <div className="h-1.5 bg-gray-800 rounded-full overflow-hidden mb-1.5">
            <div className="h-full flex gap-0">
              <div className="bg-purple-500 transition-all" style={{ width: `${stats.total ? (stats.analisados / stats.total) * 100 : 0}%` }} />
              <div className="bg-blue-500 transition-all" style={{ width: `${stats.total ? ((counts?.auditado ?? 0) / stats.total) * 100 : 0}%` }} />
            </div>
          </div>
          <p className="text-gray-600 text-xs">{stats.analisados}/{stats.total} analisadas</p>
        </div>
      </div>

      {/* Ação Imediata — only when there are actionable leads */}
      {showActionLeads && (
        <div className="shrink-0">
          <div className="flex items-center gap-2 mb-2.5">
            <Flame size={14} className="text-red-400" />
            <p className="text-gray-300 text-sm font-semibold">Ação Imediata</p>
            <span className="text-gray-600 text-xs">— alta oportunidade + contacto disponível</span>
            <button onClick={() => nav("/companies?status=analisado&min_score=65")} className="ml-auto text-xs text-cyan-600 hover:underline flex items-center gap-1">
              Ver todas <ArrowRight size={11} />
            </button>
          </div>
          <div className="grid grid-cols-3 gap-2">
            {actionLeads!.map(c => (
              <ActionCard key={c.id} lead={c} onClick={() => nav(`/companies/${c.id}`)} />
            ))}
          </div>
        </div>
      )}

      {/* Charts row + top 5 */}
      <div className="grid grid-cols-3 gap-4 flex-1 min-h-[220px]">

        {/* Nichos */}
        <div className="bg-gray-900 border border-gray-800 rounded-xl p-4 flex flex-col min-h-0">
          <p className="text-gray-400 text-xs font-medium uppercase tracking-wide mb-3 shrink-0">Por Sector</p>
          <div className="flex-1 min-h-0">
            {stats.nichos.length > 0 ? (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={stats.nichos.slice(0, 8)} layout="vertical" margin={{ left: 4, right: 4 }}>
                  <XAxis type="number" tick={{ fill: "#4b5563", fontSize: 10 }} />
                  <YAxis dataKey="nicho" type="category" tick={{ fill: "#9ca3af", fontSize: 10 }} width={80} />
                  <Tooltip contentStyle={{ background: "#111827", border: "1px solid #374151", borderRadius: 6, fontSize: 12 }} />
                  <Bar dataKey="count" fill="#06b6d4" radius={[0, 3, 3, 0]} />
                </BarChart>
              </ResponsiveContainer>
            ) : (
              <div className="h-full flex items-center justify-center text-gray-600 text-xs">Sem dados</div>
            )}
          </div>
        </div>

        {/* Score dist */}
        <div className="bg-gray-900 border border-gray-800 rounded-xl p-4 flex flex-col min-h-0">
          <div className="flex items-center justify-between mb-2 shrink-0">
            <p className="text-gray-400 text-xs font-medium uppercase tracking-wide">Oportunidade Digital</p>
            <span className="text-gray-600 text-xs">score alto = mais gaps</span>
          </div>
          <ScoreLegend />
          <div className="flex-1 min-h-0 mt-2">
            {stats.analisados > 0 ? (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={stats.score_dist}>
                  <XAxis dataKey="range" tick={{ fill: "#9ca3af", fontSize: 10 }} />
                  <YAxis tick={{ fill: "#4b5563", fontSize: 10 }} />
                  <Tooltip contentStyle={{ background: "#111827", border: "1px solid #374151", borderRadius: 6, fontSize: 12 }} />
                  <Bar dataKey="count" radius={[3, 3, 0, 0]}>
                    {stats.score_dist.map((_, i) => <Cell key={i} fill={SCORE_COLORS[i]} />)}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            ) : (
              <div className="h-full flex flex-col items-center justify-center gap-2 text-gray-600">
                <Zap size={20} className="text-gray-700" />
                <p className="text-xs">Sem análises ainda</p>
                <button onClick={() => nav("/pipeline")} className="text-cyan-600 text-xs hover:underline">Pipeline →</button>
              </div>
            )}
          </div>
        </div>

        {/* Top 5 */}
        <div className="bg-gray-900 border border-gray-800 rounded-xl p-4 flex flex-col min-h-0">
          <p className="text-gray-400 text-xs font-medium uppercase tracking-wide mb-3 shrink-0">Maior Oportunidade</p>
          {stats.top5.length > 0 ? (
            <div className="flex-1 min-h-0 overflow-y-auto space-y-1.5">
              {stats.top5.map((c) => (
                <div
                  key={c.id}
                  onClick={() => nav(`/companies/${c.id}`)}
                  className="flex items-center gap-2.5 p-2.5 rounded-lg bg-gray-800/60 hover:bg-gray-800 cursor-pointer transition-colors group"
                >
                  <FaviconImg url={c.favicon_url} name={c.nome} size={20} />
                  <div className="flex-1 min-w-0">
                    <p className="text-gray-100 text-xs font-medium truncate">{c.nome}</p>
                    <p className="text-gray-600 text-xs truncate">{c.nicho}</p>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    {c.emails?.length > 0 && <Mail size={10} className="text-green-500" title="Email capturado" />}
                    <ScoreBadge score={c.score} />
                    <ArrowRight size={12} className="text-gray-700 group-hover:text-gray-500" />
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="flex-1 flex items-center justify-center text-gray-600 text-xs flex-col gap-2">
              <TrendingUp size={20} className="text-gray-700" />
              <p>Sem empresas analisadas</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function StatCard({ label, value, color, icon: Icon, onClick }: {
  label: string; value: number | string; color: string; icon: React.ElementType; onClick?: () => void;
}) {
  return (
    <div
      className={`bg-gray-900 border border-gray-800 rounded-xl px-4 py-3 ${onClick ? "cursor-pointer hover:border-gray-700 transition-colors" : ""}`}
      onClick={onClick}
    >
      <div className="flex items-center justify-between mb-1.5">
        <span className="text-gray-500 text-xs">{label}</span>
        <Icon size={13} className={color} />
      </div>
      <p className={`text-2xl font-bold ${color}`}>{value}</p>
    </div>
  );
}

function ActionCard({ lead, onClick }: { lead: ActionLead; onClick: () => void }) {
  const hasEmail = lead.emails.length > 0;
  const hasWhatsApp = !!lead.whatsapp_link;

  return (
    <div
      onClick={onClick}
      className="bg-gray-900 border border-red-900/40 rounded-xl p-3 cursor-pointer hover:border-red-700/60 hover:bg-gray-800/60 transition-all group"
    >
      <div className="flex items-start gap-2.5 mb-2">
        <FaviconImg url={lead.favicon_url} name={lead.nome} size={22} />
        <div className="flex-1 min-w-0">
          <p className="text-gray-100 text-xs font-semibold truncate">{lead.nome}</p>
          <p className="text-gray-500 text-xs truncate">{lead.nicho} · {lead.regiao?.split(",")[0]}</p>
        </div>
        <ScoreBadge score={lead.score} showLabel />
      </div>
      <div className="flex items-center gap-2 flex-wrap">
        {hasEmail && (
          <span className="flex items-center gap-1 text-xs text-green-400 bg-green-900/20 border border-green-900/40 px-1.5 py-0.5 rounded">
            <Mail size={9} /> Email
          </span>
        )}
        {hasWhatsApp && (
          <span className="flex items-center gap-1 text-xs text-green-400 bg-green-900/20 border border-green-900/40 px-1.5 py-0.5 rounded">
            <MessageCircle size={9} /> WhatsApp
          </span>
        )}
        {lead.tags?.slice(0, 2).map(t => (
          <span key={t} className="text-xs text-gray-500 bg-gray-800 px-1.5 py-0.5 rounded truncate max-w-[90px]">
            {t.replace(/_/g, " ")}
          </span>
        ))}
        <ArrowRight size={11} className="ml-auto text-gray-700 group-hover:text-gray-400 shrink-0" />
      </div>
    </div>
  );
}

function OnboardingGuide({ onNavigate }: { onNavigate: (p: string) => void }) {
  const steps = [
    { num: "1", icon: Search,       color: "border-cyan-800 bg-cyan-900/10 text-cyan-400",   title: "Descobrir",  desc: "Pesquisa empresas por sector via OpenStreetMap. Grátis, sem API key." },
    { num: "2", icon: Radio,        color: "border-blue-800 bg-blue-900/10 text-blue-400",   title: "Auditar",    desc: "Visita cada website e extrai emails, load time, booking, WhatsApp, CMS." },
    { num: "3", icon: Brain,        color: "border-purple-800 bg-purple-900/10 text-purple-400", title: "Analisar", desc: "Ollama local analisa cada empresa e gera score de oportunidade. Funciona offline." },
  ];

  return (
    <div className="h-full flex flex-col items-center justify-center p-8 gap-8">
      <div className="text-center">
        <h2 className="text-2xl font-bold text-gray-100 mb-2">Bem-vindo ao Nexus OS</h2>
        <p className="text-gray-400 max-w-md">Plataforma de inteligência sobre o tecido empresarial dos Açores. Começa por descobrir empresas.</p>
      </div>
      <div className="grid grid-cols-3 gap-4 w-full max-w-2xl">
        {steps.map((s) => (
          <div key={s.num} className={`border rounded-xl p-5 ${s.color.split(" ").slice(0, 2).join(" ")}`}>
            <div className="flex items-center gap-2 mb-3">
              <span className="w-6 h-6 rounded-full bg-gray-900/60 flex items-center justify-center text-xs font-bold text-gray-400">{s.num}</span>
              <s.icon size={16} className={s.color.split(" ")[2]} />
            </div>
            <h3 className="text-gray-100 font-semibold mb-1">{s.title}</h3>
            <p className="text-gray-500 text-xs leading-relaxed">{s.desc}</p>
          </div>
        ))}
      </div>
      <button
        onClick={() => onNavigate("/pipeline")}
        className="flex items-center gap-2 bg-cyan-700 hover:bg-cyan-600 text-white px-6 py-2.5 rounded-xl font-medium transition-colors"
      >
        <Search size={16} /> Começar — Pipeline <ArrowRight size={16} />
      </button>
    </div>
  );
}
