#!/usr/bin/env bash
set -euo pipefail

scope="all-code"
scope_set="false"
max_lines="2000"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --scope)
      scope="${2:-}"
      scope_set="true"
      shift 2
      ;;
    --max-lines)
      max_lines="${2:-}"
      shift 2
      ;;
    *)
      if [[ "$scope_set" == "false" ]]; then
        scope="$1"
        scope_set="true"
      else
        echo "Unknown argument: $1"
        exit 1
      fi
      shift
      ;;
  esac
done

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
output_dir="dumps"
mkdir -p "$output_dir"
output="${output_dir}/project_dump_${scope}_${timestamp}.txt"

exclude_paths=(.git __pycache__ secrets node_modules venv)
include_patterns=("*.py" "*.yml" "*.yaml" "*.md" "Dockerfile" "*.txt" "*.json" "*.html" "*.css" "*.js")
exclude_data=true
if [[ "$scope" == "all" ]]; then
  exclude_data=false
else
  exclude_paths+=(data)
fi

{
  echo "Scope: $scope"
  echo "Timestamp: $timestamp"
  echo "Excluded: ${exclude_paths[*]}"
  echo ""

  for root in "${roots[@]}"; do
    if [[ ! -e "$root" ]]; then
      continue
    fi
    find_excludes=(
      ! -path "*/.git/*"
      ! -path "*/__pycache__/*"
      ! -path "*/secrets/*"
      ! -path "*/node_modules/*"
      ! -path "*/venv/*"
    )
    if [[ "$exclude_data" == "true" ]]; then
      find_excludes+=( ! -path "*/data/*" )
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
      "${find_excludes[@]}" -print0)
  done
} > "$output"

echo "Dump written to $output"
