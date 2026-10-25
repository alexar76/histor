#!/usr/bin/env bash
# The month's first HISTOR snapshot is archived on Zenodo by hand (a new version of the concept
# record, so each month gets its own DOI). This says so on Telegram each morning until Zenodo has it.
# Installed as /usr/local/sbin/histor-zenodo-reminder; histor-zenodo-reminder.timer runs it daily.
#
#   histor-zenodo-reminder              remind if the newest month's first snapshot is not on Zenodo yet
#   histor-zenodo-reminder --dry        print what it would do, send nothing
#   histor-zenodo-reminder --message D  print the reminder for snapshot D (histor-export-dataset uses it)
set -euo pipefail
DIR="${HISTOR_DATASET_DIR:-/var/lib/histor-datasets}"
CONCEPT="${HISTOR_ZENODO_CONCEPT:-23512759}"
PUBLIC="${HISTOR_PUBLIC_BASE:-https://histor.modelmarket.dev}"

message() {
  printf '%s\n' \
    "HISTOR: месячный снимок $1 готов — нужна новая версия на Zenodo." \
    "" \
    "1. Открыть https://zenodo.org/records/$CONCEPT и нажать «New version»." \
    "2. Загрузить файлы снимка: $PUBLIC/datasets/$1/ (или Hugging Face, тег snapshot-$1)." \
    "3. Название «HISTOR snapshot $1: MCP tool definitions and how they change», дата публикации $1. Publish." \
    "" \
    "Напоминание приходит каждое утро, пока версия не появится на Zenodo."
}

case "${1:-}" in
  --message) message "${2:?usage: histor-zenodo-reminder --message YYYY-MM-DD}"; exit 0 ;;
  --dry) dry=1 ;;
  "") dry=0 ;;
  *) echo "usage: histor-zenodo-reminder [--dry | --message YYYY-MM-DD]" >&2; exit 2 ;;
esac

newest="$(ls -1d "$DIR"/20??-??-?? 2> /dev/null | sort | tail -1 || true)"
[[ -n "$newest" ]] || { echo "no snapshots in $DIR"; exit 0; }
month="$(basename "$newest" | cut -c1-7)"
monthly="$(basename "$(ls -1d "$DIR/$month"-?? | sort | head -1)")"

# The newest version on Zenodo, read from its title ("HISTOR snapshot YYYY-MM-DD: ...").
archived="$(curl -sf --max-time 30 "https://zenodo.org/api/records?q=conceptrecid:$CONCEPT&allversions=true&sort=newest&size=1" \
  | python3 -I -c 'import json, re, sys
hits = json.load(sys.stdin).get("hits", {}).get("hits", [])
m = re.search(r"HISTOR snapshot (\d{4}-\d{2}-\d{2})", hits[0]["metadata"]["title"]) if hits else None
print(m.group(1) if m else "")' 2> /dev/null || true)"
if [[ -z "$archived" ]]; then
  echo "Zenodo did not say which snapshot it has; nothing sent"
  exit 0
fi
if [[ ! "$monthly" > "$archived" ]]; then
  echo "Zenodo has $archived; the month's snapshot is $monthly: nothing to do"
  exit 0
fi
echo "Zenodo has $archived; $monthly is not archived yet"
if [[ $dry -eq 1 ]]; then
  message "$monthly"
  exit 0
fi
/usr/local/sbin/histor-notify "$(message "$monthly")"
