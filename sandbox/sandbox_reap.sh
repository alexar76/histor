#!/bin/bash
# histor-sandbox-reap: remove HISTOR sandbox containers that outlived every observation stage.
#
#   histor-sandbox-reap [--dry-run]
#
# Each stage of an observation ends within minutes (install 6, scripts 2, run 2.5) and removes its
# container. Under gVisor a killed sandbox can leave its containerd shim waiting for an exit event
# that never comes: Docker keeps showing the container as running, `docker rm -f` hangs, and the
# `docker run` client waits for ever. On 2026-11-12 at 05:45Z one package did this on both sandbox
# hosts; the two containers stayed 30 hours and made the nightly backup's `docker exec` probe hang.
# histor-observe runs unprivileged and cannot end a shim; this script, run as root by
# histor-sandbox-reap.timer, can.
#
# A container named histor-<12 hex>-<stage> created more than REAP_AFTER_MIN minutes ago (15) is
# removed with `docker rm -f`. If Docker cannot remove it, its shim and the shim's processes (runsc)
# are killed, containerd reports the task gone, and it is removed again. A `docker run` client still
# waiting for it is ended. Every action goes to the journal; the unit fails when a container stays.
set -u
AFTER=${REAP_AFTER_MIN:-15}
DRY=0
[ "${1:-}" = --dry-run ] && DRY=1
now=$(date +%s)
left=0
log() { echo "histor-sandbox-reap: $*"; }
present() { timeout 20 docker ps -a --no-trunc --filter "id=$1" --format '{{.ID}}' | grep -q .; }

while IFS='|' read -r id name created; do
  [[ $name =~ ^histor-[0-9a-f]{12}-[a-z]$ ]] || continue
  t=$(date -d "${created% *}" +%s 2>/dev/null) || { log "$name: cannot read its creation time ($created)"; continue; }
  age=$(( (now - t) / 60 ))
  [ "$age" -ge "$AFTER" ] || continue
  log "$name (${id:0:12}) created ${age} min ago"
  [ "$DRY" = 1 ] && continue
  timeout 40 docker rm -f "$id" >/dev/null 2>&1
  if present "$id"; then
    shim=$(pgrep -f -- "^[^ ]*containerd-shim[^ ]* .*-id $id( |$)" | head -1)
    if [ -n "$shim" ]; then
      kids=$(pgrep -P "$shim" | tr '\n' ' ')
      log "$name: Docker cannot remove it; killing shim $shim and its processes: ${kids:-none}"
      # shellcheck disable=SC2086
      kill -KILL $kids "$shim" 2>/dev/null
    else
      log "$name: Docker cannot remove it and no shim was found"
    fi
    timeout 40 docker rm -f "$id" >/dev/null 2>&1
    # Docker finishes the removal it already had in progress once containerd reports the task gone:
    # seconds to a minute after the shim ends.
    for _ in $(seq 12); do present "$id" || break; sleep 5; done
  fi
  if present "$id"; then
    log "$name: STILL PRESENT"
    left=$(( left + 1 ))
  else
    log "$name: removed"
  fi
  # Only processes that are the docker client itself (not a shell whose command line mentions one).
  for c in $(pgrep -x docker); do
    tr '\0' ' ' < "/proc/$c/cmdline" 2>/dev/null | grep -qE -- " run .*--name $name( |$)" || continue
    log "$name: ending its docker run client $c"
    kill -KILL "$c" 2>/dev/null
  done
done < <(timeout 30 docker ps --no-trunc --filter label=histor-sandbox=1 --format '{{.ID}}|{{.Names}}|{{.CreatedAt}}')
[ "$left" -eq 0 ]
