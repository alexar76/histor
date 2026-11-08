#!/usr/bin/env bash
# Keep the images HISTOR's package sandbox starts on this host (histor_observe.IMAGES and RUNTIMES). Without them
# every npm or PyPI observation fails with "Unable to find image 'histor-node:22' locally": on 2026-11-02 a disk
# cleanup removed them on pingblip and competing-lab, and on 2026-11-07 they went again on pingblip after a test run
# that had used histor-node:22, which left 534 packages unobserved for a day. Installed by install.sh as
# /usr/local/sbin/histor-sandbox-images.
#
#   histor-sandbox-images ensure    build each image that is missing, then hold every image (the timer runs this)
#   histor-sandbox-images rebuild   build every image afresh, then hold every image (install.sh runs this)
#   histor-sandbox-images check     print each image and its holder; exit 1 when one is missing; change nothing
#
# Two layers:
#   hold    a small container per image keeps it in use: `sleep` under docker-init, no network, no capabilities,
#           read-only root, user nobody, 16 MB, restart always. While it runs, `docker image prune -a`,
#           `docker system prune -a` and `docker rmi` without -f refuse to remove the image. Holders are named
#           histor-sandbox-hold-<repository>-<tag> and carry no histor-sandbox=1 label, so the observer's own cleanup
#           of its run containers leaves them alone. After a rebuild a holder moves to the new image and the old
#           image is left to the next dangling-image prune.
#   repair  histor-sandbox-images.timer runs `ensure` 30 s after boot and every 2 minutes, so an image removed anyway
#           (`docker rmi -f`, holders removed first) is rebuilt within minutes. Each repair is a journal line
#           (journalctl -t histor-sandbox-images); a build that fails leaves the unit failed.
#
# Builds take the current base image (--pull); when the registry cannot be reached they fall back to the base image
# already on the host. A run's report names the image it used by its id, so a rebuild is visible in the record.
set -euo pipefail

DIR="${HISTOR_SANDBOX_IMAGE_DIR:-/usr/local/lib/histor-sandbox/images}"
# tag, Dockerfile in $DIR, build argument
IMAGES=(
  "histor-node:22 node NODE=22"          # IMAGES["npm"]
  "histor-node:24 node NODE=24"          # RUNTIMES["npm"]["node24"]
  "histor-python:3.12 python PYTHON=3.12" # IMAGES["pypi"]
  "histor-python:3.13 python PYTHON=3.13" # RUNTIMES["pypi"]["py313"]
  "histor-python:3.14 python PYTHON=3.14" # RUNTIMES["pypi"]["py314"]
)

mode="${1:-ensure}"
case "$mode" in
  ensure | rebuild | check) ;;
  *) echo "usage: $0 ensure|rebuild|check" >&2; exit 2 ;;
esac

# Under the timer stdout already goes to the journal; by hand, the line goes to the journal as well.
say() {
  echo "$*"
  [[ $mode == check || -n ${JOURNAL_STREAM:-} ]] || logger -t histor-sandbox-images -- "$*" 2> /dev/null || true
}

holder_name() { local t="$1"; t="${t//:/-}"; echo "histor-sandbox-hold-${t//./-}"; }

image_id() { docker image inspect -f '{{.Id}}' "$1" 2> /dev/null; }

build() {
  local tag="$1" file="$2" arg="$3" started=$SECONDS
  local cmd=(docker build -q -t "$tag" --build-arg "$arg" -f "$DIR/$file.Dockerfile" "$DIR")
  if "${cmd[@]}" --pull > /dev/null || "${cmd[@]}" > /dev/null; then
    say "built $tag in $((SECONDS - started)) s ($(image_id "$tag"))"
  else
    say "FAILED to build $tag"
    return 1
  fi
}

hold() {
  local tag="$1" name want have
  name="$(holder_name "$tag")"
  want="$(image_id "$tag")" || return 1
  have="$(docker inspect -f '{{.Image}} {{.State.Running}}' "$name" 2> /dev/null || true)"
  [[ $have == "$want true" ]] && return 0
  [[ -n $have ]] && docker rm -f "$name" > /dev/null
  docker run -d --name "$name" --restart always --init --runtime runc --network none --read-only \
    --cap-drop ALL --security-opt no-new-privileges --user 65534:65534 --memory 16m --pids-limit 4 --cpus 0.05 \
    --log-driver none --label org.modelmarket.histor.sandbox-hold=1 --label org.modelmarket.keep=1 \
    --entrypoint sleep "$tag" infinity > /dev/null
  say "holding $tag with $name${have:+ (was: $have)}"
}

if [[ $mode == check ]]; then
  status=0
  for entry in "${IMAGES[@]}"; do
    read -r tag _ _ <<< "$entry"
    id="$(image_id "$tag" || true)"
    held="$(docker inspect -f '{{.Image}} {{.State.Running}}' "$(holder_name "$tag")" 2> /dev/null || true)"
    if [[ -z $id ]]; then
      echo "$tag MISSING"; status=1
    elif [[ $held != "$id true" ]]; then
      echo "$tag $id NOT HELD${held:+ ($held)}"; status=1
    else
      echo "$tag $id held"
    fi
  done
  exit "$status"
fi

exec 9> /run/histor-sandbox-images.lock
flock 9 # install.sh's rebuild and the timer's ensure take turns
status=0
for entry in "${IMAGES[@]}"; do
  read -r tag file arg <<< "$entry"
  if [[ $mode == rebuild ]] || ! image_id "$tag" > /dev/null; then
    [[ $mode == rebuild ]] || say "$tag is missing on this host; rebuilding it"
    build "$tag" "$file" "$arg" || { status=1; continue; }
  fi
  hold "$tag" || status=1
done
exit "$status"
