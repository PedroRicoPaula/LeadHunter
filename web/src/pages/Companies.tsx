import { useState, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { API_BASE, apiFetch, type CompanyList } from "../api/client";
import { ScoreBadge, StatusPill, FaviconImg } from "../components/ScoreBadge";
import {
  Search, Globe, ExternalLink, X, SlidersHorizontal,
  ChevronUp, ChevronDown, Download, Mail, MessageCircle,
  ShieldOff, Smartphone, Navigation,
} from "lucide-react";

const STATUSES = ["", "pendente", "auditado", "analisado", "erro_auditoria", "erro_llm"];

const _SOCIAL_HOSTS = ["facebook.com", "fb.com", "instagram.com", "twitter.com", "x.com", "linkedin.com", "youtube.com", "youtu.be", "tiktok.com", "pinterest.com"];
function isSocialUrl(url: string | null | undefined): boolean {
  if (!url) return false;
  try {
    const host = new URL(url).hostname.replace(/^(www\.|m\.)/, "");
    return _SOCIAL_HOSTS.some(d => host === d || host.endsWith("." + d));
  } catch { return false; }
}
function hasRealWebsite(url: string | null | undefined): boolean {
  return !!url && !isSocialUrl(url);
}
const PAGE = 50;

// Gap tag → compact label shown inline
const TAG_CHIPS: Record<string, { label: string; cls: string }> = {
  sem_website:       { label: "sem site",    cls: "text-red-400 bg-red-900/20 border-red-900/40" },
  sem_booking:       { label: "sem reservas", cls: "text-orange-400 bg-orange-900/20 border-orange-900/40" },
  sem_whatsapp:      { label: "sem WA",      cls: "text-yellow-400 bg-yellow-900/20 border-yellow-900/40" },
  sem_redes_sociais: { label: "sem redes",   cls: "text-blue-400 bg-blue-900/20 border-blue-900/40" },
  sem_email:         { label: "sem email",   cls: "text-gray-400 bg-gray-800 border-gray-700" },
  site_lento:        { label: "site lento",  cls: "text-yellow-400 bg-yellow-900/20 border-yellow-900/40" },
};

export default function Companies() {
  const nav = useNavigate();
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [nicho, setNicho] = useState("");
  const [status, setStatus] = useState("");
  const [minScore, setMinScore] = useState(0);
  const [hasWebsite, setHasWebsite] = useState<"" | "true" | "false">("");
  const [page, setPage] = useState(0);
  const [showFilters, setShowFilters] = useState(false);
  const [sortBy, setSortBy] = useState("score");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  useEffect(() => {
    const t = setTimeout(() => { setDebouncedSearch(search); setPage(0); }, 350);
    return () => clearTimeout(t);
  }, [search]);

  const toggleSort = (col: string) => {
    if (sortBy === col) setSortDir(d => d === "desc" ? "asc" : "desc");
    else { setSortBy(col); setSortDir("desc"); }
    setPage(0);
  };

  const params = new URLSearchParams();
  if (debouncedSearch) params.set("search", debouncedSearch);
  if (nicho) params.set("nicho", nicho);
  if (status) params.set("status", status);
  if (minScore > 0) params.set("min_score", String(minScore));
  if (hasWebsite) params.set("has_website", hasWebsite);
  params.set("sort_by", sortBy);
  params.set("sort_dir", sortDir);
  params.set("limit", String(PAGE));
  params.set("offset", String(page * PAGE));

  const activeFilters = [nicho, status, hasWebsite, minScore > 0 ? `≥${minScore}` : ""].filter(Boolean).length;

  const { data, isLoading } = useQuery<CompanyList>({
    queryKey: ["companies", debouncedSearch, nicho, status, minScore, hasWebsite, sortBy, sortDir, page],
    queryFn: () => apiFetch(`/api/companies?${params}`),
  });

  const { data: nichos } = useQuery<string[]>({
    queryKey: ["nichos"],
    queryFn: () => apiFetch("/api/companies/nichos"),
  });

  const clearFilters = () => { setNicho(""); setStatus(""); setMinScore(0); setHasWebsite(""); setPage(0); };
  const inputCls = "bg-gray-800 border border-gray-700 text-gray-200 text-xs rounded-lg px-2.5 py-1.5 focus:outline-none focus:border-cyan-600";

  return (
    <div className="h-full flex flex-col overflow-hidden">

      {/* Header */}
      <div className="px-5 py-3 border-b border-gray-800 bg-[#05050a] flex flex-col gap-2 shrink-0">
        <div className="flex items-center gap-3">
          <div className="relative flex-1 max-w-xs">
            <Search size={13} className="absolute left-2.5 top-2 text-gray-500" />
            <input
              className={`${inputCls} pl-7 w-full`}
              placeholder="Pesquisar empresa..."
              value={search}
              onChange={e => { setSearch(e.target.value); setPage(0); }}
            />
          </div>

          <button
            onClick={() => setShowFilters(f => !f)}
            className={`flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg border transition-colors ${
              showFilters || activeFilters > 0
                ? "bg-cyan-900/30 border-cyan-700 text-cyan-400"
                : "bg-gray-800 border-gray-700 text-gray-400 hover:text-gray-200"
            }`}
          >
            <SlidersHorizontal size={12} />
            Filtros
            {activeFilters > 0 && (
              <span className="bg-cyan-600 text-white text-xs w-4 h-4 rounded-full flex items-center justify-center font-bold">
                {activeFilters}
              </span>
            )}
          </button>

          {activeFilters > 0 && (
            <button onClick={clearFilters} className="text-gray-600 hover:text-gray-400 text-xs flex items-center gap-1">
              <X size={12} /> Limpar
            </button>
          )}

          <span className="ml-auto text-gray-600 text-xs">
            {isLoading ? "..." : `${data?.total ?? 0} resultados`}
          </span>
          <a
            href={`${API_BASE}/api/companies/export?${(() => {
              const p = new URLSearchParams();
              if (nicho) p.set("nicho", nicho);
              if (status) p.set("status", status);
              if (minScore > 0) p.set("min_score", String(minScore));
              if (hasWebsite) p.set("has_website", hasWebsite);
              return p.toString();
            })()}`}
            download="nexus_os_export.csv"
            className="flex items-center gap-1 text-xs text-gray-500 hover:text-cyan-400 transition-colors px-2 py-1 rounded-lg hover:bg-gray-800"
            title="Exportar lista actual como CSV"
          >
            <Download size={12} /> CSV
          </a>
        </div>

        {showFilters && (
          <div className="flex gap-2 flex-wrap pt-1">
            <select className={inputCls} value={nicho} onChange={e => { setNicho(e.target.value); setPage(0); }}>
              <option value="">Todos os sectores</option>
              {nichos?.map(n => <option key={n} value={n}>{n}</option>)}
            </select>
            <select className={inputCls} value={status} onChange={e => { setStatus(e.target.value); setPage(0); }}>
              {STATUSES.map(s => <option key={s} value={s}>{s || "Todos os estados"}</option>)}
            </select>
            <select className={inputCls} value={hasWebsite} onChange={e => { setHasWebsite(e.target.value as any); setPage(0); }}>
              <option value="">Com/Sem website</option>
              <option value="true">Com website</option>
              <option value="false">Sem website</option>
            </select>
            <div className="flex items-center gap-1.5">
              <span className="text-gray-500 text-xs">Oport. ≥</span>
              <input
                type="number" min={0} max={100}
                className={`${inputCls} w-14`}
                value={minScore || ""}
                onChange={e => { setMinScore(Number(e.target.value)); setPage(0); }}
                placeholder="0"
              />
            </div>
            <div className="flex items-center gap-3 ml-2 text-xs text-gray-600">
              <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-gray-500" />0–30 madura</span>
              <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-blue-400" />31–50 baixa</span>
              <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-yellow-400" />51–70 média</span>
              <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-orange-400" />71–85 alta</span>
              <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-red-400 animate-ping" />86+ crítica</span>
            </div>
          </div>
        )}
      </div>

      {/* Table */}
      <div className="flex-1 min-h-0 overflow-y-auto">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-[#0d0d14] z-10">
            <tr className="border-b border-gray-800 text-gray-500 uppercase tracking-wide text-xs">
              <th className="text-left px-4 py-2.5 font-medium w-8" />
              <SortTh label="Nome" col="nome" sortBy={sortBy} sortDir={sortDir} onSort={toggleSort} className="text-left px-2 py-2.5" />
              <SortTh label="Sector" col="nicho" sortBy={sortBy} sortDir={sortDir} onSort={toggleSort} className="text-left px-4 py-2.5" />
              <th className="text-left px-4 py-2.5 font-medium hidden lg:table-cell">Gaps</th>
              <SortTh label="Oport." col="score" sortBy={sortBy} sortDir={sortDir} onSort={toggleSort} className="text-center px-3 py-2.5" />
              <th className="text-center px-3 py-2.5 font-medium">Contacto</th>
              <SortTh label="Estado" col="status" sortBy={sortBy} sortDir={sortDir} onSort={toggleSort} className="text-left px-4 py-2.5" />
              <th className="text-center px-2 py-2.5 font-medium hidden xl:table-cell" title="Verificações técnicas">Téc.</th>
              <SortTh label="Atualizado" col="updated_at" sortBy={sortBy} sortDir={sortDir} onSort={toggleSort} className="text-left px-4 py-2.5 hidden xl:table-cell" />
            </tr>
          </thead>
          <tbody>
            {isLoading && (
              <tr><td colSpan={9} className="text-center py-12 text-gray-600">A carregar...</td></tr>
            )}
            {!isLoading && data?.items.length === 0 && (
              <tr><td colSpan={9} className="text-center py-12 text-gray-600">
                Sem resultados.{activeFilters > 0 && <button onClick={clearFilters} className="text-cyan-600 ml-2 hover:underline">Limpar filtros</button>}
              </td></tr>
            )}
            {data?.items.map(c => {
              const tags: string[] = Array.isArray(c.tags) ? c.tags : [];
              const gapTags = tags.filter(t => TAG_CHIPS[t]);
              const emails: string[] = Array.isArray(c.emails) ? c.emails : [];
              return (
                <tr
                  key={c.id}
                  onClick={() => nav(`/companies/${c.id}`)}
                  className="border-b border-gray-800/40 hover:bg-gray-800/40 cursor-pointer transition-colors group"
                >
                  {/* Favicon */}
                  <td className="px-4 py-2 w-8">
                    <FaviconImg url={c.favicon_url ?? null} name={c.nome} size={16} />
                  </td>
                  {/* Nome */}
                  <td className="px-2 py-2 text-gray-100 font-medium max-w-[160px] truncate">
                    <div className="flex items-center gap-1.5">
                      <span className="truncate">{c.nome}</span>
                      {hasRealWebsite(c.website) && (
                        <a href={c.website!} target="_blank" rel="noreferrer" onClick={e => e.stopPropagation()}
                          className="text-cyan-700 hover:text-cyan-400 shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
                          <ExternalLink size={10} />
                        </a>
                      )}
                    </div>
                    {c.morada && (() => {
                      const mapsUrl = c.lat && c.lon
                        ? `https://www.google.com/maps?q=${c.lat},${c.lon}`
                        : `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(c.morada + ", Açores, Portugal")}`;
                      return (
                        <a
                          href={mapsUrl}
                          target="_blank"
                          rel="noreferrer"
                          onClick={e => e.stopPropagation()}
                          title="Ver no Google Maps"
                          className="flex items-center gap-1 text-gray-600 hover:text-cyan-400 transition-colors text-xs truncate hidden lg:flex group/maps"
                        >
                          <span className="truncate">{c.morada}</span>
                          <Navigation size={9} className="shrink-0 opacity-0 group-hover/maps:opacity-100 transition-opacity" />
                        </a>
                      );
                    })()}
                  </td>
                  {/* Sector */}
                  <td className="px-4 py-2 text-gray-500 whitespace-nowrap">{c.nicho}</td>
                  {/* Gap tags */}
                  <td className="px-4 py-2 hidden lg:table-cell">
                    <div className="flex gap-1 flex-wrap">
                      {gapTags.slice(0, 3).map(t => {
                        const chip = TAG_CHIPS[t];
                        return (
                          <span key={t} className={`text-xs px-1.5 py-0.5 rounded border ${chip.cls}`}>
                            {chip.label}
                          </span>
                        );
                      })}
                      {!hasRealWebsite(c.website) && !gapTags.find(t => t === "sem_website") && (
                        <span className="text-xs px-1.5 py-0.5 rounded border text-red-400 bg-red-900/20 border-red-900/40">sem site</span>
                      )}
                    </div>
                  </td>
                  {/* Score */}
                  <td className="px-3 py-2 text-center"><ScoreBadge score={c.score ?? null} /></td>
                  {/* Contacto */}
                  <td className="px-3 py-2">
                    <div className="flex items-center justify-center gap-1.5">
                      {emails.length > 0 && (
                        <a href={`mailto:${emails[0]}`} onClick={e => e.stopPropagation()}
                          title={emails[0]} className="text-green-500 hover:text-green-300">
                          <Mail size={12} />
                        </a>
                      )}
                      {c.whatsapp_link && (
                        <a href={c.whatsapp_link} target="_blank" rel="noreferrer" onClick={e => e.stopPropagation()}
                          className="text-green-500 hover:text-green-300">
                          <MessageCircle size={12} />
                        </a>
                      )}
                      {hasRealWebsite(c.website) && !emails.length && !c.whatsapp_link && (
                        <a href={c.website!} target="_blank" rel="noreferrer" onClick={e => e.stopPropagation()}
                          className="text-cyan-700 hover:text-cyan-400" title={c.website!}>
                          <Globe size={12} />
                        </a>
                      )}
                      {isSocialUrl(c.website) && !emails.length && !c.whatsapp_link && (
                        <a href={c.website!} target="_blank" rel="noreferrer" onClick={e => e.stopPropagation()}
                          className="text-blue-600 hover:text-blue-400" title={`Rede social: ${c.website}`}>
                          <Globe size={12} />
                        </a>
                      )}
                      {!c.website && !emails.length && !c.whatsapp_link && (
                        <span className="text-gray-700">—</span>
                      )}
                    </div>
                  </td>
                  {/* Status */}
                  <td className="px-4 py-2"><StatusPill status={c.status || ""} /></td>
                  {/* Technical signals */}
                  <td className="px-2 py-2 hidden xl:table-cell">
                    <div className="flex items-center gap-1 justify-center">
                      {c.has_https ? null : <ShieldOff size={10} className="text-red-500" aria-label="Sem HTTPS" />}
                      {c.has_mobile_meta ? null : <Smartphone size={10} className="text-yellow-500" aria-label="Sem meta viewport mobile" />}
                      {c.has_analytics ? <span className="text-xs text-blue-400" aria-label="Google Analytics activo">GA</span> : null}
                      {(c.website && !c.has_https && !c.has_mobile_meta && !c.has_analytics) ? (
                        <span className="text-gray-700 text-xs">—</span>
                      ) : null}
                    </div>
                  </td>
                  {/* Updated */}
                  <td className="px-4 py-2 text-gray-700 hidden xl:table-cell whitespace-nowrap">
                    {c.updated_at ? new Date(c.updated_at + "Z").toLocaleDateString("pt-PT", { day: "2-digit", month: "short" }) : "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {data && data.total > PAGE && (
        <div className="flex items-center justify-between px-5 py-2 border-t border-gray-800 bg-[#05050a] shrink-0">
          <span className="text-gray-600 text-xs">{page * PAGE + 1}–{Math.min((page + 1) * PAGE, data.total)} de {data.total}</span>
          <div className="flex gap-2">
            <button disabled={page === 0} onClick={() => setPage(p => p - 1)}
              className="px-3 py-1 text-xs bg-gray-800 text-gray-400 rounded-lg disabled:opacity-30 hover:bg-gray-700">
              ← Anterior
            </button>
            <button disabled={(page + 1) * PAGE >= data.total} onClick={() => setPage(p => p + 1)}
              className="px-3 py-1 text-xs bg-gray-800 text-gray-400 rounded-lg disabled:opacity-30 hover:bg-gray-700">
              Próximo →
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function SortTh({ label, col, sortBy, sortDir, onSort, className }: {
  label: string; col: string; sortBy: string; sortDir: "asc" | "desc";
  onSort: (col: string) => void; className?: string;
}) {
  const active = sortBy === col;
  return (
    <th className={`font-medium cursor-pointer select-none hover:text-gray-300 transition-colors ${className ?? ""}`} onClick={() => onSort(col)}>
      <span className="flex items-center gap-1">
        {label}
        {active
          ? sortDir === "desc" ? <ChevronDown size={11} className="text-cyan-400" /> : <ChevronUp size={11} className="text-cyan-400" />
          : <ChevronDown size={11} className="opacity-20" />}
      </span>
    </th>
  );
}
