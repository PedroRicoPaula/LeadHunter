export const API_BASE = import.meta.env.VITE_API_URL ?? "";

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

export interface Company {
  id: number;
  place_id: string;
  nome: string;
  nicho: string;
  regiao: string;
  morada: string;
  lat: number | null;
  lon: number | null;
  website: string | null;
  telefone: string | null;
  rating: number | null;
  total_reviews: number | null;
  score: number | null;
  status: string;
  source: string;
  load_time: number | null;
  emails: string[];
  telefones_auditados: string[];
  whatsapp_link: string | null;
  tem_booking: number;
  formularios: number;
  redes_sociais: Record<string, string>;
  booking_hints: string[];
  texto_homepage: string;
  tags: string[];
  problemas: string[];
  impacto: string | null;
  email_assunto: string | null;
  email_mensagem: string | null;
  notas: string;
  osm_tags: Record<string, string>;
  created_at: string;
  updated_at: string;
  // Audit extended fields
  favicon_url: string | null;
  has_https: number;
  has_mobile_meta: number;
  has_analytics: number;
  has_facebook_pixel: number;
  cms_detected: string | null;
  social_presence: Record<string, boolean>;
  page_word_count: number;
}

export interface CompanyList {
  total: number;
  items: Partial<Company>[];
  offset: number;
  limit: number;
}

export interface Stats {
  total: number;
  com_website: number;
  sem_website: number;
  analisados: number;
  avg_score: number | null;
  nichos: { nicho: string; count: number }[];
  score_dist: { range: string; count: number }[];
  status_counts: Record<string, number>;
  top5: { id: number; nome: string; nicho: string; score: number; website: string; favicon_url: string | null; status: string; emails: string[] }[];
}

export interface ActionLead {
  id: number;
  nome: string;
  nicho: string;
  regiao: string;
  score: number;
  website: string | null;
  favicon_url: string | null;
  emails: string[];
  whatsapp_link: string | null;
  tags: string[];
  status: string;
  impacto: string | null;
}

export interface PipelineStatus {
  running: boolean;
  step: string;
  progress: number;
  total: number;
  last_error: string | null;
  last_completed: string | null;
}
