#!/usr/bin/env bash
set -euo pipefail

scope="all-code"
max_lines="2000"

# Args:
#   export_code [scope] [--max-lines N]
# Scopes are positional; flags use --.

while [[ $# -gt 0 ]]; do
  case "$1" in
    --max-lines)
      max_lines="${2:-}"
      shift 2
      ;;
    -h|--help)
      cat <<'EOF'
Usage: export_code [scope] [--max-lines N]

Scopes:
  all-code   (default, no data, no secrets)
  all        (includes data, excludes secrets)
  api
  ingestor
  frontend

Options:
  --max-lines N   Max lines per file (default: 2000)
EOF
      exit 0
      ;;
    -*)
      echo "Unknown option: $1"
      exit 1
      ;;
    *)
      # positional scope
      scope="$1"
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
  ingestor)
    roots=(services/ingestor)
    ;;
  *)
    echo "Unknown scope: $scope"
    exit 1
    ;;
esac

timestamp=$(date +"%Y%m%d_%H%M%S")
output_dir="exports"
mkdir -p "$output_dir"
output="${output_dir}/project_export_${scope}_${timestamp}.txt"

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

echo "Export written to $output"
