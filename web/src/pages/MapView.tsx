import { useState, useEffect, useCallback } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { MapContainer, TileLayer, useMap } from "react-leaflet";
import L from "leaflet";
import "leaflet.markercluster";
import "leaflet.markercluster/dist/MarkerCluster.css";
import "leaflet.markercluster/dist/MarkerCluster.Default.css";
import { apiFetch } from "../api/client";
import { ScoreBadge, StatusPill } from "../components/ScoreBadge";
import { X, Globe, ExternalLink, ArrowRight, MapPin, AlertCircle } from "lucide-react";

interface MapPoint {
  id: number;
  nome: string;
  nicho: string;
  morada: string | null;
  lat: number;
  lon: number;
  score: number | null;
  website: string | null;
  status: string;
}

function makeIcon(score: number | null, selected = false) {
  const bg =
    score === null ? "#6b7280"
    : score >= 70 ? "#ef4444"
    : score >= 40 ? "#eab308"
    : "#22c55e";
  const ring = selected ? "box-shadow:0 0 0 3px white;" : "";
  return L.divIcon({
    html: `<div class="score-marker" style="background:${bg};${ring}font-size:9px${selected ? ";transform:scale(1.3)" : ""}">${score ?? "?"}</div>`,
    className: "",
    iconSize: [28, 28],
    iconAnchor: [14, 14],
  });
}

// Fit map to all visible points whenever the dataset changes
function FitBounds({ points }: { points: MapPoint[] }) {
  const map = useMap();
  useEffect(() => {
    if (points.length === 0) return;
    const bounds = L.latLngBounds(points.map((p) => [p.lat, p.lon] as L.LatLngTuple));
    if (bounds.isValid()) {
      map.fitBounds(bounds, { padding: [40, 40], maxZoom: 14 });
    }
  }, [map, points]);
  return null;
}

// Cluster layer — rebuilt when points or selection changes
function ClusteredMarkers({
  points,
  selectedId,
  onSelect,
  onMapClick,
}: {
  points: MapPoint[];
  selectedId: number | null;
  onSelect: (p: MapPoint) => void;
  onMapClick: () => void;
}) {
  const map = useMap();

  useEffect(() => {
    const cluster = (L as any).markerClusterGroup({
      chunkedLoading: true,
      maxClusterRadius: 60,
      spiderfyOnMaxZoom: true,
      showCoverageOnHover: false,
      disableClusteringAtZoom: 16,
    });

    points.forEach((p) => {
      const marker = L.marker([p.lat, p.lon], {
        icon: makeIcon(p.score, selectedId === p.id),
      });
      marker.on("click", (e) => {
        L.DomEvent.stopPropagation(e);
        onSelect(p);
      });
      cluster.addLayer(marker);
    });

    map.addLayer(cluster);
    map.on("click", onMapClick);

    return () => {
      map.removeLayer(cluster);
      map.off("click", onMapClick);
    };
  }, [map, points, selectedId, onSelect, onMapClick]);

  return null;
}

export default function MapView() {
  const nav = useNavigate();
  const [nicho, setNicho] = useState("");
  const [minScore, setMinScore] = useState(0);
  const [selected, setSelected] = useState<MapPoint | null>(null);

  const params = new URLSearchParams();
  if (nicho) params.set("nicho", nicho);
  if (minScore > 0) params.set("min_score", String(minScore));

  const { data: points = [], isLoading } = useQuery<MapPoint[]>({
    queryKey: ["map-points", nicho, minScore],
    queryFn: () => apiFetch(`/api/companies/map-points?${params}`),
  });

  const { data: nichos = [] } = useQuery<string[]>({
    queryKey: ["nichos"],
    queryFn: () => apiFetch("/api/companies/nichos"),
  });

  const handleSelect = useCallback((p: MapPoint) => setSelected(p), []);
  const handleMapClick = useCallback(() => setSelected(null), []);

  const withCoords = points.length;
  const inputCls = "bg-gray-800 border border-gray-700 text-gray-200 text-sm rounded-lg px-3 py-2 focus:outline-none focus:border-cyan-600";

  return (
    <div className="flex flex-col h-full relative">
      {/* Filter bar */}
      <div className="flex items-center gap-3 px-4 py-3 bg-[#0d0d14] border-b border-gray-800 shrink-0 flex-wrap">
        <span className="text-gray-300 text-sm font-medium shrink-0">
          {isLoading ? "A carregar..." : `${withCoords} empresa${withCoords !== 1 ? "s" : ""} no mapa`}
        </span>
        <select className={inputCls} value={nicho} onChange={(e) => setNicho(e.target.value)}>
          <option value="">Todos os sectores</option>
          {nichos.map((n) => <option key={n} value={n}>{n}</option>)}
        </select>
        <div className="flex items-center gap-2">
          <span className="text-gray-400 text-xs shrink-0">Oportunidade ≥</span>
          <input
            type="number" min={0} max={100}
            className={`${inputCls} w-16`}
            value={minScore}
            onChange={(e) => setMinScore(Number(e.target.value))}
          />
        </div>
        <div className="ml-auto flex items-center gap-3 text-xs text-gray-400 shrink-0">
          <Dot color="#22c55e" label="0–39" />
          <Dot color="#eab308" label="40–69" />
          <Dot color="#ef4444" label="70+" />
          <Dot color="#6b7280" label="s/ análise" />
        </div>
      </div>

      {/* No coords warning */}
      {withCoords === 0 && !isLoading && (
        <div className="absolute top-16 left-1/2 -translate-x-1/2 z-50 bg-yellow-900/90 border border-yellow-700 text-yellow-200 text-sm rounded-xl px-5 py-3 flex items-center gap-2 shadow-xl">
          <AlertCircle size={16} />
          Sem coordenadas. Re-executa o Discovery para obter posições do OSM.
        </div>
      )}

      {/* Map */}
      <div className="flex-1 relative">
        <MapContainer
          center={[37.77, -25.47]}
          zoom={10}
          style={{ height: "100%", width: "100%" }}
        >
          <TileLayer
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
            attribution='&copy; <a href="https://openstreetmap.org">OpenStreetMap</a>'
          />
          <FitBounds points={points} />
          <ClusteredMarkers
            points={points}
            selectedId={selected?.id ?? null}
            onSelect={handleSelect}
            onMapClick={handleMapClick}
          />
        </MapContainer>

        {/* Side panel */}
        {selected && (
          <div className="absolute top-4 right-4 w-80 bg-[#0d0d14] border border-gray-700 rounded-2xl shadow-2xl z-[1000] overflow-hidden">
            <div className="flex items-start justify-between p-4 border-b border-gray-800">
              <div className="flex-1 min-w-0">
                <h3 className="text-gray-100 font-semibold text-sm truncate">{selected.nome}</h3>
                <p className="text-gray-500 text-xs mt-0.5">{selected.nicho}</p>
              </div>
              <button
                onClick={() => setSelected(null)}
                className="text-gray-500 hover:text-gray-300 ml-3 shrink-0 mt-0.5"
              >
                <X size={16} />
              </button>
            </div>

            <div className="p-4 space-y-3">
              {selected.morada && (
                <div className="flex gap-2">
                  <MapPin size={13} className="text-gray-500 shrink-0 mt-0.5" />
                  <p className="text-gray-400 text-xs leading-relaxed">{selected.morada}</p>
                </div>
              )}

              <div className="flex items-center gap-2 flex-wrap">
                <StatusPill status={selected.status} />
                {selected.score !== null && <ScoreBadge score={selected.score} showLabel />}
              </div>

              {selected.score !== null && (
                <div>
                  <div className="flex justify-between text-xs text-gray-500 mb-1">
                    <span>Oportunidade digital</span>
                    <span>{selected.score}/100</span>
                  </div>
                  <div className="h-1.5 bg-gray-800 rounded-full overflow-hidden">
                    <div
                      className={`h-full rounded-full ${selected.score >= 70 ? "bg-red-500" : selected.score >= 40 ? "bg-yellow-500" : "bg-green-500"}`}
                      style={{ width: `${selected.score}%` }}
                    />
                  </div>
                </div>
              )}

              {selected.website && (
                <a
                  href={selected.website}
                  target="_blank"
                  rel="noreferrer"
                  className="flex items-center gap-1.5 text-cyan-400 text-xs hover:underline"
                >
                  <Globe size={12} />
                  {selected.website.replace(/^https?:\/\//, "").split("/")[0]}
                  <ExternalLink size={10} />
                </a>
              )}
            </div>

            <div className="px-4 pb-4">
              <button
                onClick={() => nav(`/companies/${selected.id}`)}
                className="w-full flex items-center justify-center gap-2 bg-cyan-800/40 hover:bg-cyan-800/70 text-cyan-300 text-sm py-2 rounded-xl transition-colors border border-cyan-800/50"
              >
                Ver detalhe completo <ArrowRight size={14} />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function Dot({ color, label }: { color: string; label: string }) {
  return (
    <span className="flex items-center gap-1.5">
      <span className="w-3 h-3 rounded-full inline-block shrink-0" style={{ background: color }} />
      {label}
    </span>
  );
}
