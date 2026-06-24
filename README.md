# Nexus OS — Guia Rápido

Sistema de prospeção B2B local para os Açores. Descobre empresas, audita a presença digital e gera scores de oportunidade — tudo grátis, sem API keys pagas.

---

## O que faz

### Pipeline (4 passos automáticos)

```
OpenStreetMap / Web → Enriquecimento → Auditoria → Análise IA
   (descoberta)        (encontra sites)  (Playwright)  (Ollama local)
```

| Passo | O que acontece | Ferramentas |
|-------|---------------|-------------|
| **1 — Discovery** | Pesquisa empresas por sector e ilha via OSM/Overpass API ou scraping web (TripAdvisor, visitazores.com) | OpenStreetMap (grátis, sem key) |
| **2 — Enriquecimento** | Encontra websites para empresas sem URL | Google → Bing → DuckDuckGo → TripAdvisor → Google Maps → adivinha domínio `.pt` |
| **3 — Auditoria** | Visita cada website com browser headless | Playwright + BeautifulSoup |
| **4 — Análise IA** | Gera score 0–100, problemas e draft de email | Ollama local (qwen3:8b) |

---

## O que extrai por empresa

### Auditoria (Playwright)
- Emails, telefones, link WhatsApp
- Load time, booking online (Calendly, TheFork, etc.)
- Redes sociais (Facebook, Instagram, LinkedIn, YouTube)
- **Favicon/logo** (para mostrar no painel)
- **HTTPS** — site seguro?
- **Mobile-friendly** — meta viewport presente?
- **Google Analytics / GTM** — tem rastreio?
- **Facebook Pixel** — tem remarketing?
- **CMS detectado** — WordPress, Wix, Shopify, Squarespace, etc.
- Contagem de palavras da página
- Sub-páginas visitadas: `/contacto`, `/reservas`, `/sobre`, etc.

### Score de oportunidade (0–100)
Quanto mais alto = mais gaps digitais = maior oportunidade de venda.

| Range | Significado |
|-------|-------------|
| 0–30 | Presença digital madura |
| 31–50 | Oportunidade baixa |
| 51–70 | Oportunidade média |
| 71–85 | Oportunidade alta |
| **86–100** | **Oportunidade crítica** (pulse vermelho no painel) |

Empresas **sem website** recebem score automático (+35 base) mesmo sem auditoria. Funciona mesmo com Ollama offline.

---

## Painel Web

Acede em `http://localhost:5173` depois de iniciar.

### Dashboard
- Stats: total de empresas, com website, analisadas, score médio
- **Ação Imediata** — empresas com score ≥ 65 + email ou WhatsApp capturado → prontas a contactar
- Gráfico de distribuição de scores por tier
- Top 5 oportunidades com logo real
- Barra de progresso do pipeline

### Lista de Empresas
- Coluna de **favicon/logo** (letra inicial como fallback)
- Coluna **Gaps** — tags coloridas: `sem site`, `sem reservas`, `sem WA`, etc.
- Coluna **Contacto** — email/WhatsApp/site clicável inline
- Coluna técnica: ícones HTTPS / Mobile / Analytics
- Filtros: sector, estado, score mínimo, com/sem website
- Export CSV com filtros activos

### Detalhe de Empresa
- **Perfil**: localização, mapa OSM, redes sociais, emails
- **Auditoria**: métricas técnicas + sinais de presença digital (HTTPS, CMS, Analytics, etc.)
- **Análise & IA**: score, problemas identificados, impacto estimado, draft de email editável
- **Notas**: campo livre persistente no SQLite
- Chat IA com contexto da empresa (streaming via Ollama)

### Pipeline
- 4 cards (Discovery → Enriquecimento → Auditoria → Análise)
- Web Discovery: cola URL do TripAdvisor ou visitazores.com e o sistema pagina automaticamente
- "Executar tudo" com 1 clique
- Log em tempo real
- Estimativa de tempo antes de executar
- Operações por empresa individualmente (auditar/analisar/pesquisar)

---

## Como iniciar

### Pré-requisitos

```bash
# Python 3.13+
pip install -r requirements.txt
playwright install chromium

# Node 18+
cd web && npm install

# Ollama (para análise IA local)
# https://ollama.com — depois:
ollama pull qwen3:8b
ollama serve
```

### Configuração

```bash
cp config/settings.example.yaml config/settings.yaml
# edita config/settings.yaml se necessário (modelo, timeouts, etc.)
```

### Iniciar (ambos os servidores)

```bash
python start.py
# API: http://localhost:8000
# UI:  http://localhost:5173
```

### Sem Ollama (scoring automático)

O sistema funciona sem Ollama. Ao executar a análise, usa scoring baseado em regras verificáveis (sem IA). Re-analisa depois com Ollama activo para resultados mais detalhados.

---

## Como testar o pipeline completo

### 1. Via painel (recomendado)

1. Abre `http://localhost:5173`
2. Clica em **Pipeline**
3. Expande **"Executar Pipeline Completo"**
4. Sector: `Restaurantes` | Região: `Sao Miguel, Acores`
5. Clica **"Executar tudo"**
6. Acompanha o log em tempo real

### 2. Via CLI

```bash
# Discovery gratuito via OpenStreetMap
python -m scripts.main discover-free --nicho "Restaurantes" --regiao "Sao Miguel, Acores"

# Enriquecimento (encontrar websites)
python -m scripts.main enrich --max 10

# Auditoria (visitar websites)
python -m scripts.main audit --max 5

# Análise com IA
python -m scripts.main analyze --max 5
```

### 3. Testar passos individuais

```bash
# Testar só o auditor num URL específico (debugging)
cd scripts
python 02_auditor.py run --max 3

# Testar enriquecimento (multi-source: Google + DDG + Bing + etc.)
python 04_enrichment.py run --max 5

# Testar análise IA
python 03_ai_brain.py run --max 3
```

### 4. Testar uma empresa específica no painel

1. Vai a **Empresas** → clica numa empresa
2. No header aparecem botões: **Pesquisar website**, **Auditar website**, **Analisar com IA**
3. Executa passo a passo e observa os dados a preencher em tempo real

---

## Configuração

### `config/settings.yaml`

```yaml
llm:
  provider: "ollama"     # ou "anthropic"
  model: "qwen3:8b"      # ou gemma3:4b (mais rápido), llama3.2:3b
  temperature: 0.3

scraper:
  headless: true         # false para ver o browser (debug)
  timeout: 15            # segundos por página
  delay_min: 2           # delay entre visitas (anti-bloqueio)
```

### Modelos Ollama recomendados

| Modelo | RAM | Velocidade | Qualidade |
|--------|-----|-----------|-----------|
| `qwen3:8b` | 8GB | Lento | Alta |
| `gemma3:4b` | 4GB | Médio | Boa |
| `llama3.2:3b` | 4GB | Rápido | Aceitável |

```bash
ollama pull gemma3:4b   # alternativa mais rápida
# depois muda model: "gemma3:4b" no settings.yaml
```

---

## Ficheiros importantes

```
nexus_os/
├── start.py                  # inicia API + frontend
├── nexus_os.db               # base de dados SQLite (gerado automaticamente, não commitado)
├── leads_pendentes.json      # estado do pipeline (gerado automaticamente, não commitado)
├── config/
│   ├── settings.example.yaml # template de configuração
│   └── settings.yaml         # configuração activa (não commitada — copia do example)
├── scripts/
│   ├── 01_discovery_free.py  # discovery via OpenStreetMap (grátis)
│   ├── 01_discovery.py       # discovery via Google Places API (requer key)
│   ├── 02_auditor.py         # auditoria com Playwright
│   ├── 03_ai_brain.py        # análise IA + scoring automático
│   ├── 04_enrichment.py      # enriquecimento multi-source (Google, DDG, Bing...)
│   └── 05_web_discovery.py   # scraping web (TripAdvisor, visitazores.com)
├── api/
│   ├── db.py                 # SQLite + migrações automáticas
│   └── routers/
│       ├── companies.py      # CRUD + stats + action-immediate
│       ├── pipeline.py       # execução do pipeline
│       └── llm.py            # chat streaming
└── web/src/pages/
    ├── Dashboard.tsx          # painel principal
    ├── Companies.tsx          # lista com favicons + gap tags
    ├── CompanyDetail.tsx      # detalhe + auditoria técnica
    └── Pipeline.tsx           # controlo do pipeline
```

---

## Verificação rápida de funcionamento

```bash
# API responde?
curl http://localhost:8000/health

# Quantas empresas na BD?
curl http://localhost:8000/api/companies/stats | python -m json.tool

# Empresas prontas a contactar?
curl http://localhost:8000/api/companies/action-immediate | python -m json.tool

# Ollama acessível?
curl http://localhost:11434/api/tags
```

---

## Fontes de dados (todas gratuitas)

| Fonte | Uso | Limite |
|-------|-----|--------|
| OpenStreetMap / Overpass API | Discovery de empresas | Sem limite (uso razoável) |
| TripAdvisor (scraping) | Discovery restaurantes, hotéis, atrações | Sem limite (com delay) |
| visitazores.com (scraping) | Discovery experiências e alojamento | Sem limite (com delay) |
| Google Search | Pesquisa de websites (enriquecimento) | Sem limite (com delay) |
| Bing | Pesquisa de websites (fallback) | Sem limite (com delay) |
| DuckDuckGo | Pesquisa de websites (fallback) | Sem limite (com delay) |
| Google Favicons | Favicon das empresas | Sem limite |
| Ollama | Análise IA | Local, sem limite |
