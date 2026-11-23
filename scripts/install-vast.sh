#!/usr/bin/env bash
set -euo pipefail

WORKSPACE_DIR="${DUBFORGE_WORKSPACE_DIR:-/workspace}"
DUBFORGE_DIR="${DUBFORGE_DIR:-$WORKSPACE_DIR/DubForge}"
ZAST_DIR="${ZAST_TRANSLATE_PATH:-$WORKSPACE_DIR/ZastTranslate}"
VENV_DIR="${DUBFORGE_VENV_DIR:-$WORKSPACE_DIR/dubforge-env}"
ZAST_REPO="https://github.com/zast57/ZastTranslate.git"
# Última versão usada com sucesso antes das mudanças maiores do ZastTranslate.
ZAST_COMMIT="a567da7ed40df84cdfa6a0abe705c95f4d5b509b"

echo "[1/8] Instalando pacotes do sistema"
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  ffmpeg git tmux libsndfile1 build-essential python3-dev python3-venv

echo "[2/8] Instalando ZastTranslate compatível"
if [[ ! -d "$ZAST_DIR/.git" ]]; then
  git clone "$ZAST_REPO" "$ZAST_DIR"
fi
git -C "$ZAST_DIR" fetch origin "$ZAST_COMMIT"
git -C "$ZAST_DIR" checkout --detach "$ZAST_COMMIT"

echo "[3/8] Criando ambiente Python"
if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  python3 -m venv "$VENV_DIR"
fi
PYTHON_BIN="$VENV_DIR/bin/python"
"$PYTHON_BIN" -m pip install --upgrade pip setuptools wheel

echo "[4/8] Preparando dependências compatíveis"
grep -v '^git+https://github.com/huggingface/transformers.git' \
  "$ZAST_DIR/requirements.txt" > "$ZAST_DIR/requirements-linux.txt"
printf '%s\n' \
  'huggingface-hub==0.36.2' \
  'transformers==4.57.6' \
  'torchcodec==0.7.0' > "$ZAST_DIR/constraints-linux.txt"

echo "[5/8] Instalando ZastTranslate, WhisperX e VoxCPM"
"$PYTHON_BIN" -m pip install \
  -r "$ZAST_DIR/requirements-linux.txt" \
  -c "$ZAST_DIR/constraints-linux.txt"
"$PYTHON_BIN" -m pip install "voxcpm>=2.0.2" \
  -c "$ZAST_DIR/constraints-linux.txt"

echo "[6/8] Instalando PyTorch CUDA 12.6 validado anteriormente"
"$PYTHON_BIN" -m pip install \
  torch==2.8.0 torchvision==0.23.0 torchaudio==2.8.0 \
  --index-url https://download.pytorch.org/whl/cu126 \
  --force-reinstall --no-deps

if [[ "${DUBFORGE_INSTALL_FLASH_ATTN:-0}" == "1" ]]; then
  echo "Instalando FlashAttention opcional"
  "$PYTHON_BIN" -m pip install wheel psutil ninja packaging
  "$PYTHON_BIN" -m pip install flash-attn --no-build-isolation || \
    echo "FlashAttention indisponível; continuando com atenção padrão"
fi

echo "[7/8] Instalando dependências do DubForge"
"$PYTHON_BIN" -m pip install -r "$DUBFORGE_DIR/requirements.txt"

echo "[8/8] Configurando e validando"
ENV_FILE="$DUBFORGE_DIR/.env"
if [[ ! -f "$ENV_FILE" ]]; then
  cp "$DUBFORGE_DIR/.env.example" "$ENV_FILE"
fi
if ! grep -q '^DUBFORGE_PROJECTS_DIR=' "$ENV_FILE"; then
  printf '\n%s\n' "DUBFORGE_PROJECTS_DIR=$DUBFORGE_DIR/projects" >> "$ENV_FILE"
fi
if ! grep -q '^ZAST_TRANSLATE_PATH=' "$ENV_FILE"; then
  printf '%s\n' "ZAST_TRANSLATE_PATH=$ZAST_DIR" >> "$ENV_FILE"
fi
if ! grep -q '^DUBFORGE_PYTHON=' "$ENV_FILE"; then
  printf '%s\n' "DUBFORGE_PYTHON=$PYTHON_BIN" >> "$ENV_FILE"
fi

"$PYTHON_BIN" - <<'PY'
import torch
import voxcpm
import whisperx

print("Torch:", torch.__version__)
print("CUDA disponível:", torch.cuda.is_available())
print("GPU:", torch.cuda.get_device_name(0) if torch.cuda.is_available() else "nenhuma")
print("VoxCPM: OK")
print("WhisperX: OK")
if not torch.cuda.is_available():
    raise SystemExit("ERRO: PyTorch não reconheceu a GPU")
PY

cd "$DUBFORGE_DIR"
"$PYTHON_BIN" -m unittest discover -s tests -v
echo
echo "Instalação concluída. Próximo comando:"
echo "  cd $DUBFORGE_DIR && ./scripts/run-tmux.sh"
