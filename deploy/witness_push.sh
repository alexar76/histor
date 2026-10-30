#!/usr/bin/env bash
# Push HISTOR's checkpoint to the witnesses it is listed with, and keep their cosignatures
# (histor-tlog/2, docs/spec/transparency-log.md §8.2). Installed as /usr/local/sbin/histor-witness-push
# and run by histor-witness-push.timer every 10 minutes — 144 add-checkpoint requests per witness per
# day, the `qpd` the log was listed with on witness-network.org. Change both together.
#
# Does nothing until $CONF exists: the log is pushed only to witnesses that have configured it (an
# unlisted log gets 404 from every one, which is noise for their operators). Copy
# deploy/witnesses.staging.json there once the witness network has merged the log into its list.
#
# The push runs in a throw-away container from the service's own image, as export_dataset.sh does:
# no volume of the service is mounted, the only setting passed is the operator token (copied from the
# running service, never printed), the checkpoint and proofs are read from the service's published
# loopback port, and the state — the size each witness last co-signed — lives in $STATE_DIR on the
# host. An image built before `witness-submit --witnesses-file` existed gets $MODULE mounted over its
# histor/cosign_cli.py (installed next to this script; the image carries it from the next deploy on).
#
# Exit status: that of `python -m histor witness-submit` (1 = a witness refused or answered with a
# cosignature that does not verify; the others' lines are still stored).
set -euo pipefail

APP="${HISTOR_APP_CONTAINER:-histor-histor-1}"
CONF="${HISTOR_WITNESSES_FILE:-/etc/histor/witnesses.json}"
STATE_DIR="${HISTOR_WITNESS_STATE_DIR:-/var/lib/histor-witness}"
MODULE="${HISTOR_WITNESS_MODULE:-/usr/local/lib/histor-witness/cosign_cli.py}"
UIDGID="${HISTOR_UIDGID:-65532:65532}"

if [[ ! -s "$CONF" ]]; then
  echo "no $CONF: no witness lists this log yet, nothing to push"
  exit 0
fi
python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); assert isinstance(d, list) and d' "$CONF" \
  || { echo "$CONF is not a non-empty JSON list" >&2; exit 2; }
install -d -m 0700 -o "${UIDGID%%:*}" -g "${UIDGID##*:}" "$STATE_DIR"

image="$(docker inspect "$APP" --format '{{.Config.Image}}')"
mounts=(-v "$CONF:/witnesses.json:ro" -v "$STATE_DIR:/state")
if ! docker run --rm --entrypoint python "$image" -c \
    'import sys, histor.cosign_cli as m; sys.exit(0 if hasattr(m, "load_witnesses") else 1)' 2> /dev/null; then
  [[ -f "$MODULE" ]] || { echo "the image's witness-submit predates --witnesses-file and $MODULE is missing" >&2; exit 1; }
  mounts+=(-v "$MODULE:/app/histor/cosign_cli.py:ro")
fi

envfile="$(mktemp)"
trap 'rm -f "$envfile"' EXIT
chmod 600 "$envfile"
docker inspect "$APP" --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -E '^HISTOR_OPERATOR_TOKEN=' > "$envfile" \
  || { echo "the service has no HISTOR_OPERATOR_TOKEN" >&2; exit 1; }

exec docker run --rm --name histor-witness-push --user "$UIDGID" --read-only --tmpfs /tmp \
  --cap-drop ALL --security-opt no-new-privileges:true --memory 256m --pids-limit 32 \
  --network host --env-file "$envfile" "${mounts[@]}" --entrypoint python "$image" \
  -m histor witness-submit --log http://127.0.0.1:9490 \
  --witnesses-file /witnesses.json --state /state/witness-push-state.json --submit-to-log
