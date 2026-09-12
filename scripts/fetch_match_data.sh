#!/usr/bin/env bash
# Fetch SkillCorner Open Data match files into ./data/bronze/matches/<id>/.
#
# Bronze is the immutable raw layer: this script is the ONLY thing that writes there.
# See docs/data_architecture.md. After fetching, run build_silver.py then build_gold.py.
#
# Usage:
#   scripts/fetch_match_data.sh                 # all 20 matches, events only
#   scripts/fetch_match_data.sh 1874553         # one match, events only
#   scripts/fetch_match_data.sh all --tracking  # all matches incl. tracking (~1.8 GB)
#   scripts/fetch_match_data.sh 1874553 --tracking
#
# Tracking note: {id}_tracking_extrapolated.jsonl is stored via Git LFS upstream, so a
# plain clone leaves a ~130-byte pointer stub. --tracking runs `git lfs pull` for it, which
# works anonymously against SkillCorner/opendata (no LFS credentials needed) and fetches
# ~91 MB per match. Not needed for the event-level gates; see docs/tier3_tracking_plan.md
# for what it does unlock.
#
# Windows note: the upstream repo has notebook paths that exceed MAX_PATH, so checkout
# reports "Filename too long" for a few tutorial notebooks and exits non-zero. That is
# harmless - data/ still checks out - which is why the clone is not run under `set -e`.
set -uo pipefail

TARGET="${1:-all}"
WANT_TRACKING=""
for arg in "$@"; do [ "$arg" = "--tracking" ] && WANT_TRACKING=1; done

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

echo "Cloning SkillCorner/opendata (shallow, LFS deferred) into $TMP_DIR ..."
GIT_LFS_SKIP_SMUDGE=1 git clone --depth 1 https://github.com/SkillCorner/opendata "$TMP_DIR" \
  || echo "(checkout reported errors - expected on Windows for long notebook paths; continuing)"

if [ ! -d "$TMP_DIR/data/matches" ]; then
  echo "Clone failed: no data/matches in $TMP_DIR" >&2
  exit 1
fi

if [ "$TARGET" = "all" ] || [ "$TARGET" = "--tracking" ]; then
  MATCH_IDS="$(ls "$TMP_DIR/data/matches")"
else
  if [ ! -d "$TMP_DIR/data/matches/$TARGET" ]; then
    echo "Match $TARGET not found. Available:" >&2
    ls "$TMP_DIR/data/matches" >&2
    exit 1
  fi
  MATCH_IDS="$TARGET"
fi

if [ -n "$WANT_TRACKING" ]; then
  echo "Pulling LFS tracking files (~91 MB per match) ..."
  for id in $MATCH_IDS; do
    ( cd "$TMP_DIR" && git lfs pull \
        --include="data/matches/$id/${id}_tracking_extrapolated.jsonl" >/dev/null 2>&1 )
  done
fi

mkdir -p data/bronze/matches
COUNT=0
for id in $MATCH_IDS; do
  SRC="$TMP_DIR/data/matches/$id"
  DEST="data/bronze/matches/$id"
  mkdir -p "$DEST"
  cp "$SRC"/*_dynamic_events.csv "$SRC"/*_match.json "$SRC"/*_phases_of_play.csv "$DEST"/ 2>/dev/null
  if [ -n "$WANT_TRACKING" ]; then
    TR="$SRC/${id}_tracking_extrapolated.jsonl"
    # Skip LFS pointer stubs (a real tracking file is ~91 MB, a stub ~130 bytes).
    if [ -f "$TR" ] && [ "$(wc -c <"$TR")" -gt 1000 ]; then
      cp "$TR" "$DEST"/
    else
      echo "  warning: $id tracking is still an LFS stub, skipped" >&2
    fi
  fi
  COUNT=$((COUNT + 1))
done
cp "$TMP_DIR/data/matches.json" data/bronze/ 2>/dev/null

echo "Copied $COUNT match(es) to data/bronze/matches/"
echo "Next: python scripts/build_silver.py && python scripts/build_gold.py"
du -sh data/bronze 2>/dev/null || true
