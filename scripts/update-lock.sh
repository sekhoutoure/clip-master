#!/usr/bin/env bash
# Regenerate the pinned dependency sets in requirements-lock/ from requirements.txt (needs https://docs.astral.sh/uv/).
# torch / torchvision and the CUDA runtime wheels are NOT locked: which build you want (CPU or GPU) is your choice,
# install.sh --cpu-torch / pip's default decide it, and the lock then adds everything else at exact versions.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
PYV="${PYV:-3.12}"
mkdir -p requirements-lock
EXCLUDES=(torch torchvision triton cuda-bindings cuda-pathfinder cuda-toolkit nvidia-cublas nvidia-cuda-cccl nvidia-cuda-cupti
  nvidia-cuda-nvrtc nvidia-cuda-runtime nvidia-cudnn-cu13 nvidia-cufft nvidia-cufile nvidia-cusolver nvidia-cusparse
  nvidia-cusparselt-cu13 nvidia-ml-py nvidia-nccl-cu13 nvidia-nvjitlink nvidia-nvshmem-cu13 nvidia-nvtx)
ARGS=(); for p in "${EXCLUDES[@]}"; do ARGS+=(--no-emit-package "$p"); done
gen() {  # <uv platform> <file name>
  uv pip compile requirements.txt --python-platform "$1" --python-version "$PYV" "${ARGS[@]}" -q \
    --custom-compile-command "scripts/update-lock.sh" -o "requirements-lock/$2-py${PYV/./}.txt"
}
gen x86_64-unknown-linux-gnu linux-x86_64
gen x86_64-pc-windows-msvc   windows-x86_64
gen aarch64-apple-darwin     macos-arm64
echo "Lock files written to requirements-lock/"
