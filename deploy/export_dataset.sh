#!/usr/bin/env bash
# Weekly open-data snapshot of the log (histor/dataset.py), published at
# https://histor.modelmarket.dev/datasets/. Installed as /usr/local/sbin/histor-export-dataset and
# run every 10 minutes by histor-export-dataset.timer (deploy/histor-export-dataset.{service,timer}):
# it does nothing once this ISO week has a snapshot (HISTOR_DATASET_PERIOD=month: this calendar
# month, the rule until 2026-10-25), and nothing while a crawl runs. A snapshot is a
# copy of a finished cycle, so it is taken in the gap the service leaves after each one
# (HISTOR_CRAWL_MIN_GAP_S, 30 minutes): until 2026-10-23 the timer ran once a day at 15:30 UTC, and a
# cycle longer than a day started the next two minutes after it, so that window never came.
#
#   histor-export-dataset          this week's snapshot, if missing and no crawl is running
#   histor-export-dataset --now    a snapshot dated today even if this week has one (still refused during a crawl)
#   histor-export-dataset --due    print whether a snapshot is due, and change nothing
#   histor-export-dataset --peers  only the peer cross-checks below, dated today (a retry, or a first run)
#
# After each snapshot the same throw-away container setup runs the cross-checks with other public records of
# tool-definition changes (histor/peer_xcheck.py; today ScraperWatch's MCP tool-change log), published at
# $DIR/peers/<peer>/<YYYY-MM-DD>/ with $DIR/peers/<peer>/latest → newest. It reads the database only and
# fetches the peer's two public files and a few robots.txt; a failure there never fails the snapshot.
#
# The first snapshot of a calendar month is the one archived on Zenodo, by hand: when it is written,
# histor-notify (deploy/notify_telegram.sh) says so on Telegram, and histor-zenodo-reminder repeats it
# each morning until Zenodo has that snapshot.
#
# The export runs in a throw-away container from the service's own image, beside the live service:
# same database, the data volume READ-ONLY (the issuer key signs the manifest), its own memory
# limit, no network but the compose one, and only the settings it needs — never the API keys or
# sandbox tokens. An image built before histor/dataset.py existed gets that one file mounted from
# $MODULE (installed next to this script; the image carries it from the next deploy on).
#
# Output: $DIR/<YYYY-MM-DD>/ (written under $DIR/.staging first, so nginx never serves half a
# snapshot), $DIR/latest → newest, and the $KEEP newest snapshots kept (8: two months of weeks).
set -euo pipefail

DIR="${HISTOR_DATASET_DIR:-/var/lib/histor-datasets}"
KEEP="${HISTOR_DATASET_KEEP:-8}"
PERIOD="${HISTOR_DATASET_PERIOD:-week}"
APP="${HISTOR_APP_CONTAINER:-histor-histor-1}"
VOLUME="${HISTOR_DATA_VOLUME:-histor_histor-data}"
NETWORK="${HISTOR_NETWORK:-histor_default}"
MODULE="${HISTOR_DATASET_MODULE:-/usr/local/lib/histor-dataset/dataset.py}"
PEER_MODULE="${HISTOR_PEER_MODULE:-/usr/local/lib/histor-dataset/peer_xcheck.py}"
UIDGID="65532:65532"

now=0
due_only=0
peers_only=0
[[ "${1:-}" == "--now" ]] && now=1
[[ "${1:-}" == "--due" ]] && due_only=1
[[ "${1:-}" == "--peers" ]] && peers_only=1

case "$PERIOD" in
  week) period_of() { date -u -d "$1" +%G-W%V; } ;;
  month) period_of() { date -u -d "$1" +%Y-%m; } ;;
  *) echo "HISTOR_DATASET_PERIOD must be week or month" >&2; exit 2 ;;
esac

day="$(date -u +%F)"
current="$(period_of "$day")"

# Only the names the export reads, copied from the running service; values never printed.
prepare() {
  image="$(docker inspect "$APP" --format '{{.Config.Image}}')"
  envfile="$(mktemp)"
  trap 'rm -f "$envfile"' EXIT
  chmod 600 "$envfile"
  docker inspect "$APP" --format '{{range .Config.Env}}{{println .}}{{end}}' \
    | grep -E '^(HISTOR_PROFILE|HISTOR_DATABASE_URL|HISTOR_OPERATOR_TOKEN|HISTOR_PUBLIC_BASE|HISTOR_DATA_DIR|HISTOR_OPT_OUT|HISTOR_PQC)=' \
    > "$envfile"
}

# The cross-checks (histor/peer_xcheck.py), dated $1: written under .staging, then moved into place.
peers() {
  local d="$1" out="$DIR/peers/scraperwatch"
  [[ -e "$out/$d" ]] && { echo "$out/$d exists"; return 0; }
  install -d -m 0755 "$DIR/peers" "$out"
  install -d -m 0700 -o "${UIDGID%%:*}" -g "${UIDGID##*:}" "$DIR/.staging"
  rm -rf "${DIR:?}/.staging/peers-$d"
  local pmounts=(-v "$VOLUME:/data:ro" -v "$DIR/.staging:/export")
  if ! docker run --rm --entrypoint python "$image" -c 'import histor.peer_xcheck' 2> /dev/null; then
    [[ -f "$PEER_MODULE" ]] || { echo "the image has no histor.peer_xcheck and $PEER_MODULE is missing" >&2; return 1; }
    pmounts+=(-v "$PEER_MODULE:/app/histor/peer_xcheck.py:ro")
  fi
  docker run --rm --name "histor-peers-$d" --user "$UIDGID" --read-only --tmpfs /tmp \
    --cap-drop ALL --security-opt no-new-privileges:true --memory 1g --pids-limit 64 \
    --network "$NETWORK" --env-file "$envfile" "${pmounts[@]}" --entrypoint python \
    "$image" -m histor.peer_xcheck "/export/peers-$d" || return 1
  chown -R root:root "$DIR/.staging/peers-$d"
  chmod -R a+rX,go-w "$DIR/.staging/peers-$d"
  mv "$DIR/.staging/peers-$d" "$out/$d"
  ln -sfn "$d" "$out/latest.tmp" && mv -T "$out/latest.tmp" "$out/latest"
  ls -1d "$out"/20??-??-?? 2> /dev/null | sort | head -n -"$KEEP" | xargs -r rm -rf
  echo "histor peers: $out/$d"
}

if [[ $peers_only -eq 1 ]]; then
  prepare
  peers "$day"
  exit $?
fi
# The snapshot this period already has, if any: one finished snapshot (a manifest) dated inside it.
taken=""
for manifest in "$DIR"/20??-??-??/manifest.json; do
  [[ -e "$manifest" ]] || continue
  dated="$(basename "$(dirname "$manifest")")"
  [[ "$(period_of "$dated")" == "$current" ]] && taken="$dated"
done
if [[ $due_only -eq 1 ]]; then
  [[ -n "$taken" ]] && echo "not due: $current has $taken" || echo "due: $current has no snapshot"
  exit 0
fi
[[ $now -eq 0 && -n "$taken" ]] && exit 0
[[ -e "$DIR/$day" ]] && { echo "$DIR/$day exists"; exit 0; }

install -d -m 0755 "$DIR"
install -d -m 0700 -o "${UIDGID%%:*}" -g "${UIDGID##*:}" "$DIR/.staging"
rm -rf "${DIR:?}/.staging/$day"

# Cheap first: ask the service. The export itself refuses a running crawl too (exit 75 below).
if curl -sf --max-time 5 http://127.0.0.1:9490/health 2> /dev/null | grep -q '"crawl_running":true'; then
  exit 0
fi

prepare
mounts=(-v "$VOLUME:/data:ro" -v "$DIR/.staging:/export")
if ! docker run --rm --entrypoint python "$image" -c 'import histor.dataset' 2> /dev/null; then
  [[ -f "$MODULE" ]] || { echo "the image has no histor.dataset and $MODULE is missing" >&2; exit 1; }
  mounts+=(-v "$MODULE:/app/histor/dataset.py:ro")
fi

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

peers "$day" || echo "peer cross-check failed; run histor-export-dataset --peers to retry" >&2

# The month's first snapshot goes to Zenodo by hand; say so (histor-zenodo-reminder repeats it daily).
first_of_month=1
for other in "$DIR"/"${day:0:7}"-??; do
  [[ -d "$other" && "$(basename "$other")" < "$day" ]] && first_of_month=0
done
if [[ $first_of_month -eq 1 && -x /usr/local/sbin/histor-notify ]]; then
  /usr/local/sbin/histor-notify "$(/usr/local/sbin/histor-zenodo-reminder --message "$day")" \
    || echo "Telegram reminder not sent" >&2
fi
