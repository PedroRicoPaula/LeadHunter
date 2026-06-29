#!/bin/bash
# Full pipeline: enrich → audit → analyze → export
# Corre após batch_discovery.py ter adicionado novos leads.
set -e
cd "$(dirname "$0")"
LOG=pipeline.log

log() { echo "$1" | tee -a $LOG; }

log "=== PIPELINE ==="
log "Inicio: $(date)"

log "--- ENRICH ---"
.venv/bin/python3 scripts/main.py enrich 2>&1 | tee -a $LOG

log "--- AUDIT ---"
.venv/bin/python3 scripts/main.py audit 2>&1 | tee -a $LOG

log "--- ANALYZE (novos) ---"
.venv/bin/python3 scripts/main.py analyze 2>&1 | tee -a $LOG

log "--- ANALYZE (reprocessar todos) ---"
.venv/bin/python3 scripts/main.py analyze --reprocessar 2>&1 | tee -a $LOG

log "--- EXPORT businesses.json ---"
.venv/bin/python3 scripts/export_json.py 2>&1 | tee -a $LOG

log "=== FIM: $(date) ==="
