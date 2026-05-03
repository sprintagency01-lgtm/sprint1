#!/bin/zsh
# Dev server local para Sprint — landing + CMS + portal
# Doble click para arrancar. Ctrl+C para parar.

cd "$(dirname "$0")" || exit 1

if [ ! -f ".venv310/bin/activate" ]; then
  echo "❌ No existe .venv310/bin/activate."
  echo "   Crea el venv con: python3 -m venv .venv310 && source .venv310/bin/activate && pip install -r requirements.txt"
  read -k 1 "?Pulsa una tecla para cerrar..."
  exit 1
fi

source .venv310/bin/activate

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Sprint dev server"
echo "  Landing:  http://localhost:8000/"
echo "  CMS:      http://localhost:8000/admin/"
echo "  Portal:   http://localhost:8000/app/"
echo "  Ctrl+C para parar."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

uvicorn app.main:app --reload --port 8000
