#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== Demarrage Conference Translator ==="
echo

if [[ ! -d ".venv" ]]; then
  echo "L'environnement .venv est absent."
  echo "Lancez d'abord Installer.command"
  read -r "?Appuyez sur Entree pour fermer..."
  exit 1
fi

if [[ ! -f ".env" ]]; then
  echo "Le fichier .env est absent."
  echo "Lancez d'abord Installer.command"
  read -r "?Appuyez sur Entree pour fermer..."
  exit 1
fi

if grep -q "your_mistral_api_key_here" .env; then
  echo "La cle API Mistral n'a pas encore ete renseignee dans .env"
  echo "Ouvrez .env puis remplacez la valeur de MISTRAL_API_KEY."
  read -r "?Appuyez sur Entree pour ouvrir .env..."
  open -e .env
  exit 1
fi

source .venv/bin/activate

echo "Verification rapide des dependances..."
python -c "import fastapi, uvicorn, mistralai" >/dev/null

python - <<'PY' &
import os
import uvicorn

uvicorn.run("app:app", host="127.0.0.1", port=8000)
PY
SERVER_PID=$!

cleanup() {
  if kill -0 "$SERVER_PID" >/dev/null 2>&1; then
    kill "$SERVER_PID" >/dev/null 2>&1 || true
  fi
}

trap cleanup EXIT INT TERM

echo
echo "Serveur lance sur http://localhost:8000"
echo "Laissez cette fenetre ouverte pendant la session."
echo
for _ in {1..30}; do
  if curl -s http://127.0.0.1:8000 >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

echo "Ouverture du navigateur..."
if [[ -d "/Applications/Google Chrome.app" ]]; then
  open -a "Google Chrome" "http://localhost:8000/control/default"
else
  open "http://localhost:8000/control/default"
fi

wait "$SERVER_PID"
