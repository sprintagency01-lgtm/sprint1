#!/usr/bin/env bash
# -----------------------------------------------------------------------------
#  deploy.sh — prepara el repo git y hace push a GitHub para desplegar en Railway.
#
#  Requisitos:
#    - git instalado
#    - gh (GitHub CLI) instalado y autenticado:  brew install gh && gh auth login
#    - O alternativamente: repo vacío creado a mano en github.com/new
#
#  Uso:
#    chmod +x deploy.sh
#    ./deploy.sh <nombre-del-repo>
#
#  Ejemplo:
#    ./deploy.sh bot-reservas-voz
# -----------------------------------------------------------------------------
set -euo pipefail

REPO_NAME="${1:-bot-reservas-voz}"
GREEN="\033[0;32m"; YELLOW="\033[1;33m"; RED="\033[0;31m"; NC="\033[0m"

info()  { echo -e "${GREEN}[deploy]${NC} $*"; }
warn()  { echo -e "${YELLOW}[deploy]${NC} $*"; }
die()   { echo -e "${RED}[deploy]${NC} $*"; exit 1; }

# --- 1. Sanity checks -------------------------------------------------------

[[ -f requirements.txt ]] || die "Ejecuta este script desde la raíz del proyecto (donde está requirements.txt)."

if [[ -f .env ]]; then
  warn ".env existe. Asegúrate de que está en .gitignore (lo está) y NO lo subirás."
fi

# --- 2. Inicializar git (idempotente) --------------------------------------

if [[ ! -d .git ]]; then
  info "Inicializando repositorio git..."
  git init -b main >/dev/null
else
  info "Repositorio git ya existe. Ok."
  # asegurar rama main
  git branch -M main 2>/dev/null || true
fi

info "Añadiendo archivos..."
git add .

if git diff --cached --quiet; then
  info "Nada nuevo que commitear."
else
  info "Creando commit..."
  git commit -m "deploy: bot de reservas + CMS (v0.2)" >/dev/null
fi

# --- 3. Crear repo en GitHub y pushear --------------------------------------

if git remote get-url origin >/dev/null 2>&1; then
  info "Remoto 'origin' ya configurado:"
  git remote -v | head -1
  info "Haciendo push..."
  git push -u origin main
else
  if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
    info "Creando repo privado en GitHub con gh CLI..."
    gh repo create "$REPO_NAME" --private --source=. --push
  else
    warn "No tienes gh CLI o no estás autenticado."
    echo
    echo "Opción A (más fácil) — instala gh CLI y vuelve a correr:"
    echo "    brew install gh && gh auth login && ./deploy.sh $REPO_NAME"
    echo
    echo "Opción B — crea el repo a mano:"
    echo "    1) Ve a https://github.com/new"
    echo "    2) Nombre: $REPO_NAME   (privado recomendado)"
    echo "    3) NO añadas README ni .gitignore ni license (creas vacío)"
    echo "    4) Copia la URL SSH o HTTPS y ejecuta:"
    echo "         git remote add origin <URL_DEL_REPO>"
    echo "         git push -u origin main"
    exit 0
  fi
fi

# --- 4. Siguiente paso -----------------------------------------------------

echo
info "✓ Código en GitHub. Siguientes pasos en Railway:"
cat <<EOF

  1. Ve a tu proyecto Railway actual (el que tiene el bot antiguo).
  2. Click en el servicio → pestaña "Settings" → "Source".
  3. Cambia la rama o conecta el nuevo repo si hace falta.
  4. En "Variables", pega las variables nuevas del bloque RAILWAY_VARS
     del fichero DEPLOY_RAILWAY.md.
  5. En "Volumes", monta un volumen en /app/data (si no existe).
  6. "Deploy" → espera 2-3 min.
  7. Abre https://<tu-dominio-railway>.up.railway.app/admin/login

Guía detallada: DEPLOY_RAILWAY.md
EOF
