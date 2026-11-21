#!/usr/bin/env bash
# Record real-suite ground truth for every mutant in a directory (evaluation only).
#
#   tools/run_ground_truth.sh MUTANT_DIR [PARALLEL] [DEADLINE_EPOCH] [IMAGE]
#
# Resumable: mutants that already have results/<id>.json are skipped, so after a
# crash just run it again. No new mutant starts after DEADLINE_EPOCH (seconds since
# the epoch; default: no deadline). Each run gets 2 CPUs and 3 GB, the
# suite peaks near 2.4 GB, so budget ~3 GB of RAM per parallel slot.
set -u
M=$(cd "$1" && pwd)
PARALLEL=${2:-2}
DEADLINE=${3:-0}
IMAGE=${4:-blast-radius-entry}
REPO=$(cd "$(dirname "$0")/.." && pwd)
LOG="$M/ground-truth.log"
mkdir -p "$M/results"
: > "$M/commits/baseline.patch.empty"

one() {
  id=$1
  [ -s "$M/results/$id.json" ] && return 0
  if [ "$DEADLINE" -gt 0 ] && [ "$(date +%s)" -ge "$DEADLINE" ]; then return 0; fi
  patch="commits/$id.patch"; [ "$id" = baseline ] && patch="commits/baseline.patch.empty"
  echo "$(date +%T) start $id" >> "$LOG"
  timeout 1500 docker run --rm --platform linux/amd64 --cpus 2 --memory 3g --memory-swap 3g \
    --user "$(id -u):$(id -g)" --network none -v "$REPO:/repo:ro" -v "$M:/m" "$IMAGE" \
    python /repo/tools/ground_truth.py "/m/$patch" "/m/results/$id.json" >"$M/results/$id.out" 2>"$M/results/$id.err"
  echo "$(date +%T) end $id exit=$? $(head -c 120 "$M/results/$id.json" 2>/dev/null)" >> "$LOG"
}
export -f one
export M REPO LOG DEADLINE IMAGE

echo "$(date +%T) begin parallel=$PARALLEL deadline=$DEADLINE" >> "$LOG"
{ echo baseline; ls "$M/commits" | grep '\.patch$' | sed 's/\.patch$//' | sort; } | xargs -P "$PARALLEL" -I{} bash -c 'one {}'
done_count=$(ls "$M/results" | grep -c '\.json$')
echo "$(date +%T) finished: $done_count results" | tee -a "$LOG"
