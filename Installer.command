#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== Installation Conference Translator ==="
echo

find_python() {
  for candidate in python3.12 python3.11 python3.10; do
    if command -v "$candidate" >/dev/null 2>&1; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

PYTHON_BIN="$(find_python || true)"

if [[ -z "${PYTHON_BIN}" ]]; then
  echo "Aucune version compatible de Python n'a ete trouvee."
  echo "Installez Python 3.11 ou 3.12, puis relancez ce script."
  echo
  echo "Conseil macOS:"
  echo "  brew install python@3.11"
  exit 1
fi

echo "Python detecte: $PYTHON_BIN"

if [[ ! -d ".venv" ]]; then
  echo "Creation de l'environnement virtuel..."
  "$PYTHON_BIN" -m venv .venv
fi

source .venv/bin/activate

echo "Mise a jour de pip..."
python -m pip install --upgrade pip

echo "Installation des dependances..."
pip install -r requirements.txt

if [[ ! -f ".env" ]]; then
  cp .env.example .env
  echo
  echo "Fichier .env cree."
  echo "Ajoutez votre cle Mistral dans .env avant de lancer l'application."
else
  echo ".env deja present, conserve."
fi

echo
echo "Installation terminee."
echo
echo "Etapes suivantes:"
echo "1. Ouvrir le fichier .env"
echo "2. Remplacer MISTRAL_API_KEY=your_mistral_api_key_here"
echo "3. Double-cliquer sur Lancer.command"
echo
read -r "?Appuyez sur Entree pour fermer..."
