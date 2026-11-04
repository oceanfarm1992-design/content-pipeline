#!/usr/bin/env bash
# Download the models for one stage listed in scripts/models.txt into models/.
# Files already present (e.g. restored from actions/cache) are skipped.
#   scripts/download_models.sh image
set -euo pipefail
stage="${1:?usage: download_models.sh <stage>}"
root="$(cd "$(dirname "$0")/.." && pwd)"

grep -v '^\s*#' "$root/scripts/models.txt" | while read -r s file url; do
  [ "$s" = "$stage" ] || continue
  dest="$root/models/$file"
  if [ -s "$dest" ]; then
    echo "cached  $file"
    continue
  fi
  mkdir -p "$(dirname "$dest")"
  echo "fetch   $file"
  curl -fSL --retry 3 --retry-delay 5 -o "$dest.part" "$url"
  mv "$dest.part" "$dest"
done
du -sh "$root/models/"* 2>/dev/null || true
