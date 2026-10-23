#!/usr/bin/env bash
# Monthly open-data snapshot of the log (histor/dataset.py), published at
# https://histor.modelmarket.dev/datasets/. Installed as /usr/local/sbin/histor-export-dataset and
# run every 10 minutes by histor-export-dataset.timer (deploy/histor-export-dataset.{service,timer}):
# it does nothing once this month's snapshot exists, and nothing while a crawl runs. A snapshot is a
# copy of a finished cycle, so it is taken in the gap the service leaves after each one
# (HISTOR_CRAWL_MIN_GAP_S, 30 minutes): until 2026-10-23 the timer ran once a day at 15:30 UTC, and a
# cycle longer than a day started the next two minutes after it, so that window never came.
#
#   histor-export-dataset          this month's snapshot, if missing and no crawl is running
#   histor-export-dataset --now    a snapshot dated today even if this month has one (still waits for the crawl)
#
# The export runs in a throw-away container from the service's own image, beside the live service:
# same database, the data volume READ-ONLY (the issuer key signs the manifest), its own memory
# limit, no network but the compose one, and only the settings it needs — never the API keys or
# sandbox tokens. An image built before histor/dataset.py existed gets that one file mounted from
# $MODULE (installed next to this script; the image carries it from the next deploy on).
#
# Output: $DIR/<YYYY-MM-DD>/ (written under $DIR/.staging first, so nginx never serves half a
# snapshot), $DIR/latest → newest, and the $KEEP newest snapshots kept.
set -euo pipefail

DIR="${HISTOR_DATASET_DIR:-/var/lib/histor-datasets}"
KEEP="${HISTOR_DATASET_KEEP:-6}"
APP="${HISTOR_APP_CONTAINER:-histor-histor-1}"
VOLUME="${HISTOR_DATA_VOLUME:-histor_histor-data}"
NETWORK="${HISTOR_NETWORK:-histor_default}"
MODULE="${HISTOR_DATASET_MODULE:-/usr/local/lib/histor-dataset/dataset.py}"
UIDGID="65532:65532"

now=0
[[ "${1:-}" == "--now" ]] && now=1

day="$(date -u +%F)"
month="${day:0:7}"
if [[ $now -eq 0 ]] && compgen -G "$DIR/$month-*/manifest.json" > /dev/null; then
  exit 0
fi
[[ -e "$DIR/$day" ]] && { echo "$DIR/$day exists"; exit 0; }

install -d -m 0755 "$DIR"
install -d -m 0700 -o "${UIDGID%%:*}" -g "${UIDGID##*:}" "$DIR/.staging"
rm -rf "${DIR:?}/.staging/$day"

# Cheap first: ask the service. The export itself refuses a running crawl too (exit 75 below).
if curl -sf --max-time 5 http://127.0.0.1:9490/health 2> /dev/null | grep -q '"crawl_running":true'; then
  exit 0
fi

image="$(docker inspect "$APP" --format '{{.Config.Image}}')"
mounts=(-v "$VOLUME:/data:ro" -v "$DIR/.staging:/export")
if ! docker run --rm --entrypoint python "$image" -c 'import histor.dataset' 2> /dev/null; then
  [[ -f "$MODULE" ]] || { echo "the image has no histor.dataset and $MODULE is missing" >&2; exit 1; }
  mounts+=(-v "$MODULE:/app/histor/dataset.py:ro")
fi

# Only the names the export reads, copied from the running service; values never printed.
envfile="$(mktemp)"
trap 'rm -f "$envfile"' EXIT
chmod 600 "$envfile"
docker inspect "$APP" --format '{{range .Config.Env}}{{println .}}{{end}}' \
  | grep -E '^(HISTOR_PROFILE|HISTOR_DATABASE_URL|HISTOR_OPERATOR_TOKEN|HISTOR_PUBLIC_BASE|HISTOR_DATA_DIR|HISTOR_OPT_OUT|HISTOR_PQC)=' \
  > "$envfile"

set +e
docker run --rm --name "histor-export-$day" --user "$UIDGID" --read-only --tmpfs /tmp \
  --cap-drop ALL --security-opt no-new-privileges:true --memory 1g --pids-limit 64 \
  --network "$NETWORK" --env-file "$envfile" "${mounts[@]}" --entrypoint python \
  "$image" -m histor.dataset "/export/$day"
rc=$?
set -e
if [[ $rc -eq 75 ]]; then
  echo "crawl running; the timer tries again in 10 minutes"
  exit 0
fi
[[ $rc -eq 0 ]] || exit "$rc"

chown -R root:root "$DIR/.staging/$day"
chmod -R a+rX,go-w "$DIR/.staging/$day"
mv "$DIR/.staging/$day" "$DIR/$day"
ln -sfn "$day" "$DIR/latest.tmp" && mv -T "$DIR/latest.tmp" "$DIR/latest"

# Keep the newest $KEEP snapshots.
ls -1d "$DIR"/20??-??-?? 2> /dev/null | sort | head -n -"$KEEP" | xargs -r rm -rf
# The landing page (/datasets/index.html) lists what is left, with the schema.org Dataset block.
python3 "$(dirname "$MODULE")/dataset_index.py" "$DIR" || echo "index page not rebuilt" >&2
echo "histor dataset: $(du -sh "$DIR/$day" | cut -f1) → $DIR/$day"

# Mirror to Hugging Face when the host is set up for it (deploy/upload_hf.sh); the snapshot above
# is published either way.
if [[ -x /usr/local/sbin/histor-upload-hf && -r /etc/histor-hf/token ]]; then
  /usr/local/sbin/histor-upload-hf "$DIR/$day" || echo "Hugging Face upload failed; run histor-upload-hf $DIR/$day again" >&2
fi
