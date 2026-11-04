#!/usr/bin/env bash
# Download the models for one stage listed in scripts/models.txt into models/.
# Files already present (e.g. restored from actions/cache) are skipped.
# Lines with a 4th column are converted with sd-cli (SD_CLI, default bin/sd-cli) to that quant type.
#   scripts/download_models.sh image
set -euo pipefail
stage="${1:?usage: download_models.sh <stage>}"
root="$(cd "$(dirname "$0")/.." && pwd)"
sd_cli="${SD_CLI:-$root/bin/sd-cli}"

grep -v '^\s*#' "$root/scripts/models.txt" | while read -r s file url quant; do
  [ "$s" = "$stage" ] || continue
  dest="$root/models/$file"
  if [ -s "$dest" ]; then
    echo "cached  $file"
    continue
  fi
  mkdir -p "$(dirname "$dest")"
  if [ -z "${quant:-}" ]; then
    echo "fetch   $file"
    curl -fSL --retry 3 --retry-delay 5 -o "$dest.part" "$url"
    mv "$dest.part" "$dest"
  else
    src="$dest.src.${url##*.}"
    echo "fetch   ${url##*/} (to convert)"
    curl -fSL --retry 3 --retry-delay 5 -o "$src" "$url"
    echo "convert $file ($quant)"
    "$sd_cli" -M convert -m "$src" -o "$dest.part" --type "$quant" -v
    rm -f "$src"
    mv "$dest.part" "$dest"
  fi
done
du -sh "$root/models/"* 2>/dev/null || true
