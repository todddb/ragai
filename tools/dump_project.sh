#!/usr/bin/env bash
set -euo pipefail

scope="${1:-all}"
max_lines="${2:-2000}"

if [[ "$scope" == "--scope" ]]; then
  scope="$2"
  shift 2
fi
if [[ "${1:-}" == "--max-lines" ]]; then
  max_lines="$2"
fi

case "$scope" in
  all)
    roots=(.)
    ;;
  all-code)
    roots=(services config tools README.md docker-compose.yml)
    ;;
  frontend)
    roots=(services/frontend)
    ;;
  api)
    roots=(services/api)
    ;;
  crawler)
    roots=(services/crawler)
    ;;
  ingestor)
    roots=(services/ingestor)
    ;;
  *)
    echo "Unknown scope: $scope"
    exit 1
    ;;
esac

timestamp=$(date +"%Y%m%d_%H%M%S")
output="project_dump_${scope}_${timestamp}.txt"

exclude_paths=(.git __pycache__ data secrets node_modules venv)
include_patterns=("*.py" "*.yml" "*.yaml" "*.md" "Dockerfile" "*.txt" "*.json" "*.html" "*.css" "*.js")

{
  echo "Scope: $scope"
  echo "Timestamp: $timestamp"
  echo "Excluded: ${exclude_paths[*]}"
  echo ""

  for root in "${roots[@]}"; do
    if [[ ! -e "$root" ]]; then
      continue
    fi
    while IFS= read -r -d '' file; do
      echo "===== $file ====="
      head -n "$max_lines" "$file"
      echo ""
    done < <(find "$root" -type f \( \
      -name "${include_patterns[0]}" -o -name "${include_patterns[1]}" -o -name "${include_patterns[2]}" \
      -o -name "${include_patterns[3]}" -o -name "${include_patterns[4]}" -o -name "${include_patterns[5]}" \
      -o -name "${include_patterns[6]}" -o -name "${include_patterns[7]}" -o -name "${include_patterns[8]}" \
      -o -name "${include_patterns[9]}" \) \
      ! -path "*/.git/*" ! -path "*/__pycache__/*" ! -path "*/data/*" ! -path "*/secrets/*" \
      ! -path "*/node_modules/*" ! -path "*/venv/*" -print0)
  done
} > "$output"

echo "Dump written to $output"
