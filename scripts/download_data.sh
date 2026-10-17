#!/usr/bin/env bash
# Download the Kaggle datasets in docs/specs/03-ml-spec.md section 3 into ml/data/raw/<name>/.
#
# Usage (repo root):
#   bash scripts/download_data.sh              # all four
#   bash scripts/download_data.sh ieee ulb     # only some
# Windows: run in Git Bash, not the WSL `bash` in System32:
#   & "C:\Program Files\Git\bin\bash.exe" scripts/download_data.sh
#
# Needs Kaggle credentials (~/.kaggle/kaggle.json, or KAGGLE_USERNAME + KAGGLE_KEY).
# IEEE-CIS is a competition: accept its rules once on kaggle.com or the download returns 403.
# Re-running skips datasets that already finished. Never deletes extracted data.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RAW="$ROOT/ml/data/raw"

# name | kind | kaggle ref
DATASETS=(
  "ieee|competition|ieee-fraud-detection"
  "ulb|dataset|mlg-ulb/creditcardfraud"
  "paysim|dataset|ealaxi/paysim1"
  "elliptic|dataset|ellipticco/elliptic-data-set"
)

die() { echo "ERROR: $*" >&2; exit 1; }

# Kaggle CLI: PATH first, then the repo venv (Windows or POSIX layout).
find_kaggle() {
  if command -v kaggle >/dev/null 2>&1; then echo "kaggle"; return; fi
  for candidate in "$ROOT/.venv/Scripts/kaggle.exe" "$ROOT/.venv/bin/kaggle"; do
    if [[ -x "$candidate" ]]; then echo "$candidate"; return; fi
  done
  die "Kaggle CLI not found. Run: pip install -r ml/requirements.txt"
}

check_credentials() {
  local config_dir="${KAGGLE_CONFIG_DIR:-$HOME/.kaggle}"
  # New-style API token (kaggle CLI >= 1.8), then legacy kaggle.json, then legacy env vars.
  if [[ -f "$config_dir/access_token" || -n "${KAGGLE_API_TOKEN:-}" ]]; then return; fi
  if [[ -f "$config_dir/kaggle.json" ]]; then return; fi
  if [[ -n "${KAGGLE_USERNAME:-}" && -n "${KAGGLE_KEY:-}" ]]; then return; fi
  die "No Kaggle credentials. kaggle.com > Settings > API Tokens > Generate New Token, then save it as $config_dir/access_token"
}

download() {
  local name="$1" kind="$2" ref="$3"
  local dest="$RAW/$name"
  local marker="$dest/.download_complete"

  if [[ -f "$marker" ]]; then
    echo "[$name] already downloaded, skipping"
    return
  fi

  mkdir -p "$dest"
  echo "[$name] downloading $ref ..."
  if [[ "$kind" == "competition" ]]; then
    "$KAGGLE" competitions download -c "$ref" -p "$dest" \
      || die "[$name] download failed. For competitions, accept the rules first: https://www.kaggle.com/competitions/$ref/rules"
  else
    "$KAGGLE" datasets download -d "$ref" -p "$dest" || die "[$name] download failed"
  fi

  # Extract every archive we just downloaded, then remove the archive (data stays).
  shopt -s nullglob
  for archive in "$dest"/*.zip; do
    echo "[$name] extracting $(basename "$archive")"
    unzip -o -q "$archive" -d "$dest"
    rm "$archive"
  done
  shopt -u nullglob

  date -u +"%Y-%m-%dT%H:%M:%SZ" > "$marker"
  echo "[$name] done -> ml/data/raw/$name/"
}

main() {
  command -v unzip >/dev/null 2>&1 || die "unzip not found"

  local wanted=("$@")
  local entry name kind ref found

  # Validate names before downloading anything.
  for name in "${wanted[@]}"; do
    found=0
    for entry in "${DATASETS[@]}"; do [[ "${entry%%|*}" == "$name" ]] && found=1; done
    [[ $found -eq 1 ]] || die "Unknown dataset '$name' (choose from: ieee ulb paysim elliptic)"
  done

  KAGGLE="$(find_kaggle)"
  check_credentials

  for entry in "${DATASETS[@]}"; do
    IFS="|" read -r name kind ref <<< "$entry"
    if [[ ${#wanted[@]} -gt 0 && ! " ${wanted[*]} " =~ " $name " ]]; then continue; fi
    download "$name" "$kind" "$ref"
  done

  echo
  echo "Raw data in ml/data/raw/:"
  du -sh "$RAW"/*/ 2>/dev/null || true
}

main "$@"
