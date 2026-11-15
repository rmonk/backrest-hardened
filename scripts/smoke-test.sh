#!/usr/bin/env bash
# Start the image, then drive backrest's API through a full cycle:
# set instance -> add repo (restic init) -> add plan -> backup -> list snapshots,
# then verify the snapshot with restic check/restore and confirm bundled tools run.
#
# Usage: scripts/smoke-test.sh <image>      (DOCKER=podman to use podman; needs curl + jq)
set -euo pipefail

image="${1:?usage: smoke-test.sh <image>}"
docker="${DOCKER:-docker}"
name="backrest-smoke-$$"
port="${SMOKE_PORT:-19898}"
url="http://127.0.0.1:$port/v1.Backrest"

cleanup() {
  rc=$?
  if [ $rc -ne 0 ]; then echo "--- logs ---"; $docker logs --tail 60 "$name" 2>&1 || true; fi
  $docker rm -f "$name" >/dev/null 2>&1 || true
  exit $rc
}
trap cleanup EXIT

api() {  # api <Method> <json>  -> prints response, fails on RPC error
  local out
  out="$(curl -sS -X POST -H 'Content-Type: application/json' -d "$2" "$url/$1")"
  if jq -e 'has("code")' >/dev/null <<<"$out"; then echo "smoke: FAIL $1: $out" >&2; return 1; fi
  printf '%s' "$out"
}

$docker run -d --name "$name" -p "127.0.0.1:$port:9898" "$image" >/dev/null

echo "smoke: waiting for web UI"
for _ in $(seq 60); do curl -sf -o /dev/null "http://127.0.0.1:$port/" && break; sleep 1; done
curl -sf -o /dev/null "http://127.0.0.1:$port/" || { echo "smoke: FAIL web UI not up"; exit 1; }

logs="$($docker logs "$name" 2>&1)"   # not piped into grep -q: SIGPIPE under pipefail
grep -qF 'restic binary "/bin/restic" in $PATH matches required version' <<<"$logs" \
  || { echo "smoke: FAIL backrest did not accept /bin/restic"; exit 1; }
echo "smoke: backrest uses bundled /bin/restic"

$docker exec "$name" sh -eu -c 'mkdir -p /tmp/smoke-data/sub; head -c 2000000 /dev/urandom > /tmp/smoke-data/random.bin; echo hello > /tmp/smoke-data/sub/hello.txt'

api SetConfig "$(api GetConfig '{}' | jq -c '.instance="smoke"')" >/dev/null
api AddRepo '{"repo":{"id":"smoke","uri":"/tmp/smoke-repo","password":"smoke"}}' >/dev/null
api SetConfig "$(api GetConfig '{}' | jq -c '.plans=[{id:"smoke-plan",repo:"smoke",paths:["/tmp/smoke-data"],schedule:{disabled:true}}]')" >/dev/null
api Backup '{"value":"smoke-plan"}' >/dev/null
count="$(api ListSnapshots '{"repoId":"smoke"}' | jq '.snapshots | length')"
[ "$count" -ge 1 ] || { echo "smoke: FAIL no snapshot after backup"; exit 1; }
echo "smoke: backup via backrest API OK ($count snapshot)"

$docker exec -e RESTIC_PASSWORD=smoke -e RESTIC_REPOSITORY=/tmp/smoke-repo "$name" sh -eu -c '
  restic check --read-data -q
  restic restore latest --target /tmp/restore -q
  diff -r /tmp/smoke-data /tmp/restore/tmp/smoke-data
'
echo "smoke: restic check + restore OK"

$docker exec "$name" sh -eu -c 'restic version; rclone version | sed -n 1p; ssh -V 2>&1; docker --version; curl --version | sed -n 1p'
echo "smoke: PASS"
