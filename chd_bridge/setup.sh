#!/usr/bin/env bash
# ChatHouseDiffusion — automated setup for macOS (Apple Silicon / MPS)
# Usage: bash setup.sh [--checkpoint-url URL]
set -euo pipefail

CHD_DIR="${CHD_DIR:-$HOME/Projects/chathousediffusion}"
BRIDGE_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "═══ ChatHouseDiffusion Setup ═══"
echo "Target: $CHD_DIR"

# ── 1. Clone repo ──────────────────────────────────────────────────────────
if [ ! -d "$CHD_DIR" ]; then
  git clone https://github.com/ChatHouseDiffusion/chathousediffusion.git "$CHD_DIR"
fi
cd "$CHD_DIR"

# ── 2. Create venv with Python 3.11 ────────────────────────────────────────
if ! command -v uv &>/dev/null; then
  echo "Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

if [ ! -f venv311/bin/python ]; then
  uv venv --python 3.11 venv311
fi

PY="$CHD_DIR/venv311/bin/python"

echo "Installing dependencies (this may take a few minutes)..."
uv pip install --python "$PY" \
  "numpy<2" "torch==2.2.0" "torchvision==0.17.0" \
  openai Pillow requests tqdm "dgl==2.1.0" \
  ema_pytorch "transformers<4.36" pandas einops \
  fuzzywuzzy "langchain_core<0.3" opencv-python 2>&1 | tail -3

# ── 3. Patch CHD code for MPS ──────────────────────────────────────────────
echo "Patching CHD code for MPS compatibility..."

# predict.py — device detection
cat > predict.py << 'PYEOF'
from denoising_diffusion_pytorch import Unet, GaussianDiffusion, Trainer
import os, sys, pickle, torch
DEVICE = "mps" if torch.backends.mps.is_available() else "cpu"
if DEVICE == "cpu":
    DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
print(f"Using device: {DEVICE}", file=sys.stderr, flush=True)

def predict_prepare():
    results_folder = "./predict_model"
    with open(f"{results_folder}/params.pkl", "rb") as f:
        params = pickle.load(f)
    model = Unet(**params["unet_dict"])
    diffusion = GaussianDiffusion(model, **params["diffusion_dict"])
    trainer = Trainer(diffusion, "", "", "", **params["trainer_dict"],
        results_folder=results_folder, train_num_workers=0,
        mode="predict", inject_step=40)
    trainer.load(98)
    return trainer
PYEOF

# trainer.py — device
python3 -c "
import re
with open('denoising_diffusion_pytorch/trainer.py') as f:
    s = f.read()
s = s.replace('    def device(self):\n        return \"cuda\"',
    '    def device(self):\n        import torch\n        if torch.cuda.is_available():\n            return \"cuda\"\n        if torch.backends.mps.is_available():\n            return \"mps\"\n        return \"cpu\"')
s = s.replace('torch.cuda.empty_cache()', '# patched: torch.cuda.empty_cache()')
with open('denoising_diffusion_pytorch/trainer.py', 'w') as f:
    f.write(s)
"

# t5.py — CUDA → device
python3 -c "
with open('denoising_diffusion_pytorch/t5.py') as f:
    s = f.read()
s = s.replace('    if torch.cuda.is_available():\n        t5 = t5.to(DEVICE)',
    '    device = torch.device(\"mps\" if torch.backends.mps.is_available() else \"cpu\")\n    if torch.cuda.is_available():\n        device = torch.device(\"cuda\")\n    t5 = t5.to(device)')
with open('denoising_diffusion_pytorch/t5.py', 'w') as f:
    f.write(s)
"

# image_process.py — float64 → float32 for MPS
python3 -c "
with open('denoising_diffusion_pytorch/image_process.py') as f:
    s = f.read()
s = s.replace('torch.tensor(cmap[i], device=img.device)', 'torch.tensor(cmap[i], device=img.device, dtype=torch.float32)')
s = s.replace('torch.tensor(cmap[13], device=img.device)', 'torch.tensor(cmap[13], device=img.device, dtype=torch.float32)')
with open('denoising_diffusion_pytorch/image_process.py', 'w') as f:
    f.write(s)
"

# ── 4. Copy bridge script ───────────────────────────────────────────────────
cp "$BRIDGE_DIR/predict_floorplan.py" "$CHD_DIR/"
chmod +x "$CHD_DIR/predict_floorplan.py"

# ── 5. Download checkpoint (1.1 GB) ─────────────────────────────────────────
CHECKPOINT_URL="${1:-https://cloud.tsinghua.edu.cn/f/a01a8205be55462685fd/}"
if [ ! -f predict_model/model-98.pt ]; then
  echo "Downloading checkpoint (1.1 GB from Tsinghua Cloud)..."
  curl -L -# -o /tmp/chd_checkpoint.rar "$CHECKPOINT_URL?dl=1"
  echo "Extracting..."
  brew list unar &>/dev/null || brew install unar 2>/dev/null
  unar -q -o predict_model/ /tmp/chd_checkpoint.rar
  mv predict_model/predict_model/* predict_model/ 2>/dev/null || true
  rmdir predict_model/predict_model 2>/dev/null || true
  rm -f /tmp/chd_checkpoint.rar
  echo "Checkpoint extracted: $(ls -lh predict_model/model-98.pt | awk '{print $5}')"
fi

# ── 6. Create api_info.json ─────────────────────────────────────────────────
if [ ! -f api_info.json ]; then
  cat > api_info.json << 'JSONEOF'
{
  "api_key": "***",
  "base_url": "http://localhost:1234/v1",
  "model": "local-model"
}
JSONEOF
  echo "Created default api_info.json (LM Studio). Edit if needed."
fi

# ── 7. Verify ───────────────────────────────────────────────────────────────
echo ""
echo "═══ Verification ═══"
uv run --python "$PY" python3 -c "
from predict import predict_prepare
t = predict_prepare()
print(f'✓ Trainer ready on {t.device}')
print(f'  Weights: {t.results_folder}/model-98.pt')
" 2>&1 | grep -E "(✓|Trainer|  )"

echo ""
echo "✅ ChatHouseDiffusion setup complete!"
echo "   Source: $CHD_DIR"
echo "   Python: $PY"
echo "   Bridge: $CHD_DIR/predict_floorplan.py"
echo ""
echo "To use with botarbitraj, set:"
echo "  export CHD_PYTHON=$PY"
echo "  export CHD_BRIDGE_SCRIPT=$CHD_DIR/predict_floorplan.py"
echo ""
echo "Then restart the backend."
