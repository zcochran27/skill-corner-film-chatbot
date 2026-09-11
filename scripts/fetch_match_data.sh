#!/usr/bin/env bash
# Fetch one match's files from the SkillCorner Open Data repo into ./data/matches/<id>/.
# Usage: scripts/fetch_match_data.sh [match_id]
# Defaults to 1874553 (a match with a real, non-LFS-only dynamic_events.csv used in
# docs/real_data_validation.md).
#
# Note: {id}_tracking_extrapolated.jsonl is stored via Git LFS in the upstream repo.
# A plain `git clone` leaves it as an LFS pointer stub; run `git lfs pull` inside
# the clone afterward (with LFS credentials for SkillCorner/opendata) to get the
# real per-frame tracking data. It is not required for the event-level gates
# validated so far.
set -euo pipefail

MATCH_ID="${1:-1874553}"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

echo "Cloning SkillCorner/opendata (shallow) into $TMP_DIR ..."
GIT_LFS_SKIP_SMUDGE=1 git clone --depth 1 https://github.com/SkillCorner/opendata "$TMP_DIR"

SRC="$TMP_DIR/data/matches/$MATCH_ID"
if [ ! -d "$SRC" ]; then
  echo "Match $MATCH_ID not found in data/matches/. See $TMP_DIR/data/matches.json for available ids." >&2
  exit 1
fi

DEST="data/matches/$MATCH_ID"
mkdir -p "$DEST"
cp "$SRC"/* "$DEST"/
echo "Copied match $MATCH_ID files to $DEST"
