import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { BrowserRouter, Routes, Route, NavLink } from "react-router-dom";
import { LayoutDashboard, Building2, Map, GitBranch, Loader2 } from "lucide-react";
import Dashboard from "./pages/Dashboard";
import Companies from "./pages/Companies";
import CompanyDetail from "./pages/CompanyDetail";
import MapView from "./pages/MapView";
import Pipeline from "./pages/Pipeline";
import { GlobalProgress } from "./components/GlobalProgress";
import { apiFetch } from "./api/client";

const qc = new QueryClient({ defaultOptions: { queries: { staleTime: 30_000 } } });

export default function App() {
  return (
    <QueryClientProvider client={qc}>
      <BrowserRouter>
        <div className="flex h-screen w-full overflow-hidden bg-[#05050a]">
          <Sidebar />
          <div className="flex-1 flex flex-col h-full overflow-hidden">
            <GlobalProgress />
            <main className="flex-1 min-h-0 overflow-hidden">
              <Routes>
                <Route path="/" element={<Dashboard />} />
                <Route path="/companies" element={<Companies />} />
                <Route path="/companies/:id" element={<CompanyDetail />} />
                <Route path="/map" element={<MapView />} />
                <Route path="/pipeline" element={<Pipeline />} />
              </Routes>
            </main>
          </div>
        </div>
      </BrowserRouter>
    </QueryClientProvider>
  );
}

function Sidebar() {
  const { data: pipelineStatus } = useQuery({
    queryKey: ["pipeline-status"],
    queryFn: () => apiFetch<{ running: boolean; step: string }>("/api/pipeline/status"),
    refetchInterval: (q) => ((q.state.data as any)?.running ? 2000 : 10000),
  });

  const { data: stats } = useQuery({
    queryKey: ["stats"],
    queryFn: () => apiFetch<{ total: number; analisados: number }>("/api/companies/stats"),
    refetchInterval: 15_000,
  });

  const isRunning = pipelineStatus?.running;

  const navItems = [
    { to: "/", icon: LayoutDashboard, label: "Dashboard", end: true },
    {
      to: "/companies", icon: Building2, label: "Empresas", end: false,
      badge: stats?.total ? String(stats.total) : undefined,
    },
    { to: "/map", icon: Map, label: "Mapa", end: false },
    { to: "/pipeline", icon: GitBranch, label: "Pipeline", end: false },
  ];

  return (
    <aside className="w-52 h-full bg-[#0d0d14] border-r border-gray-800 flex flex-col py-5 px-3 shrink-0">
      {/* Logo */}
      <div className="mb-6 px-2">
        <h1 className="text-cyan-400 font-bold text-base tracking-tight">Nexus OS</h1>
        <p className="text-gray-600 text-xs mt-0.5">Açores Intelligence</p>
      </div>

      {/* Nav */}
      <nav className="flex flex-col gap-0.5 flex-1">
        {navItems.map(({ to, icon: Icon, label, end, badge }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) =>
              `flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm transition-colors ${
                isActive
                  ? "bg-cyan-900/40 text-cyan-400 font-medium"
                  : "text-gray-400 hover:text-gray-200 hover:bg-gray-800/50"
              }`
            }
          >
            <Icon size={15} className="shrink-0" />
            <span className="flex-1">{label}</span>
            {label === "Pipeline" && isRunning && (
              <span className="flex items-center gap-1 text-cyan-400">
                <Loader2 size={11} className="animate-spin" />
              </span>
            )}
            {badge && label !== "Pipeline" && (
              <span className="text-gray-600 text-xs bg-gray-800 px-1.5 py-0.5 rounded-full">
                {badge}
              </span>
            )}
          </NavLink>
        ))}
      </nav>

      {/* Footer: pipeline status */}
      {isRunning && (
        <div className="mt-4 px-3 py-2.5 bg-cyan-900/20 border border-cyan-800/50 rounded-lg">
          <div className="flex items-center gap-2">
            <Loader2 size={11} className="animate-spin text-cyan-400 shrink-0" />
            <p className="text-cyan-400 text-xs font-medium">
              {pipelineStatus?.step === "discovery" && "A descobrir..."}
              {pipelineStatus?.step === "audit" && "A auditar..."}
              {pipelineStatus?.step === "analyze" && "A analisar IA..."}
            </p>
          </div>
        </div>
      )}

      {/* Progress summary */}
      {stats && stats.total > 0 && !isRunning && (
        <div className="mt-4 px-3 py-2 border-t border-gray-800/60">
          <div className="flex justify-between text-xs text-gray-600 mb-1">
            <span>Analisadas</span>
            <span>{stats.analisados}/{stats.total}</span>
          </div>
          <div className="h-1 bg-gray-800 rounded-full overflow-hidden">
            <div
              className="h-full bg-purple-600 rounded-full transition-all"
              style={{ width: `${(stats.analisados / stats.total) * 100}%` }}
            />
          </div>
        </div>
      )}
    </aside>
  );
}
