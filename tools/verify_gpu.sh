#!/usr/bin/env bash
set -euo pipefail

prompt="${1:-Write a short sentence about GPU acceleration.}"
ollama_model="${OLLAMA_MODEL:-llama3}"
ollama_url="${OLLAMA_URL:-http://localhost:11434}"
gpu_activity_detected=false

print_nvidia_smi() {
  if ! command -v nvidia-smi >/dev/null 2>&1; then
    echo "nvidia-smi not found on host."
    return
  fi
  nvidia-smi || true
}

print_gpu_sample() {
  if ! command -v nvidia-smi >/dev/null 2>&1; then
    return
  fi
  nvidia-smi --query-gpu=timestamp,name,utilization.gpu,utilization.memory,memory.used,memory.total \
    --format=csv,noheader,nounits || true
  nvidia-smi --query-compute-apps=pid,process_name,used_gpu_memory \
    --format=csv,noheader,nounits || true
  local mem_used
  mem_used=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null | head -n1 | tr -d ' ')
  if [[ -n "${mem_used}" && "${mem_used}" -gt 0 ]]; then
    gpu_activity_detected=true
  fi
  if nvidia-smi --query-compute-apps=process_name --format=csv,noheader 2>/dev/null | grep -qi ollama; then
    gpu_activity_detected=true
  fi
}

echo "=== GPU status (host before) ==="
print_nvidia_smi
echo

echo "=== GPU status (inside CUDA container) ==="
if command -v docker >/dev/null 2>&1; then
  docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi || true
else
  echo "docker not available on host."
fi
echo

echo "=== Ollama test generation ==="
if ! curl -fsS "${ollama_url}/api/tags" >/dev/null; then
  echo "Ollama is not reachable at ${ollama_url}. Start the stack with ./tools/ragaictl start."
  exit 1
fi

curl -s "${ollama_url}/api/generate" \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"${ollama_model}\",\"prompt\":\"${prompt}\",\"stream\":true}" \
  >/tmp/ollama_gpu_probe.json &
ollama_pid=$!

for sample in {1..5}; do
  echo "--- GPU sample ${sample} ---"
  print_gpu_sample
  if ! kill -0 "${ollama_pid}" 2>/dev/null; then
    break
  fi
  sleep 1
done

wait "${ollama_pid}" || true
echo

echo "=== GPU status (host after) ==="
print_nvidia_smi

if [[ "${gpu_activity_detected}" != "true" ]]; then
  cat <<'EOF'

No GPU activity detected during the Ollama request.
Diagnostics checklist:
  1) Install NVIDIA drivers and nvidia-container-toolkit on the host.
  2) Ensure Docker is configured with the NVIDIA runtime:
     - /etc/docker/daemon.json should include:
       { "runtimes": { "nvidia": { "path": "nvidia-container-runtime", "runtimeArgs": [] } } }
  3) Confirm Docker sees the GPU: docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
  4) Verify Ollama model supports GPU offload and OLLAMA_NUM_GPU=1 is set.
EOF
fi
