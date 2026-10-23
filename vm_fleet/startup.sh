#!/bin/bash
# GCE startup script for a whitepaper worker VM (runs as root on every boot).
# Instance metadata: bucket, workers, only (optional slugs), auto_shutdown (1 = power off when the queue is empty).
set -uo pipefail
export PATH="/snap/bin:/usr/local/bin:$PATH" DEBIAN_FRONTEND=noninteractive HOME=/root
md() { curl -sf -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/attributes/$1" || true; }
BUCKET=$(md bucket); WORKERS=$(md workers); ONLY=$(md only); SHUTDOWN=$(md auto_shutdown)
# DeepSeek spend cap: no LLM call once the account balance is below this (llm.py DEEPSEEK_BALANCE_FLOOR)
DSF=$(md ds_floor); if [ -n "$DSF" ]; then export DEEPSEEK_BALANCE_FLOOR="$DSF"; fi
APP=/opt/wp
LOG=/var/log/wp-worker.log
mkdir -p "$APP/code"
exec > >(tee -a "$LOG") 2>&1          # also on the serial console: `launch.py logs <vm>` reads it without SSH
echo "=== boot $(date -u) bucket=$BUCKET workers=${WORKERS:-6} only=${ONLY:-<all>}"

if [ ! -f "$APP/.setup_done" ]; then
  apt-get update -y
  apt-get install -y python3-venv python3-pip libreoffice-writer-nogui \
      fonts-dejavu fonts-liberation fonts-crosextra-carlito fonts-crosextra-caladea
  python3 -m venv "$APP/venv"
  "$APP/venv/bin/pip" install -q --upgrade pip
  touch "$APP/.setup_done"
fi
# every boot, so a VM made before a dependency was added still gets it (quick when already installed)
"$APP/venv/bin/pip" install -q httpx openai ddgs python-docx matplotlib numpy pillow openpyxl pymupdf google-cloud-storage

# latest code + secrets + market list on every boot (outputs and caches under code/ are kept)
gcloud storage cp "gs://$BUCKET/code/code.tgz" /tmp/code.tgz && tar xzf /tmp/code.tgz -C "$APP/code"
gcloud storage cp "gs://$BUCKET/secrets/.env" "$APP/code/.env"
gcloud storage cp "gs://$BUCKET/markets.jsonl" "$APP/markets.jsonl"
chmod 600 "$APP/code/.env"

MODE=$(md mode)
if [ "$MODE" = "rebuild" ]; then
  # rebuild drafts from their saved data (report step only); the 4 VMs share gs://$BUCKET/rebuild_queue.txt
  # through claims under claims-rebuild/, and a rebuild never downgrades a stored result
  gcloud storage cp "gs://$BUCKET/rebuild_queue.txt" "$APP/rebuild_queue.txt"
  ( cd "$APP/code" && WEB_SEARCH_STRICT=1 WEB_SEARCH_MODE=fast NAME_CHECK_FREE_FIRST=1 ENTITY_PAGE_FETCH=1 \
    SEARCH_SHARED_CACHE="gs://$BUCKET/search-cache" MPLBACKEND=Agg \
    "$APP/venv/bin/python" -u vm_fleet/rebuild_drafts.py --slugs-file "$APP/rebuild_queue.txt" --no-word \
      --workers "${WORKERS:-6}" --claim-prefix claims-rebuild $(for w in $(md rebuild_args | tr ';' ' '); do printf -- "--%s " "$w"; done) )
else
  "$APP/venv/bin/python" "$APP/code/vm_fleet/worker.py" --bucket "$BUCKET" --markets "$APP/markets.jsonl" \
      --root "$APP/code" --workers "${WORKERS:-6}" ${ONLY:+--only "$ONLY"}
fi
gcloud storage cp "$LOG" "gs://$BUCKET/logs/$(hostname).log"

if [ "$SHUTDOWN" = "1" ]; then
  echo "=== queue empty, powering off $(date -u)"
  gcloud storage cp "$LOG" "gs://$BUCKET/logs/$(hostname).log"
  shutdown -h now
fi
