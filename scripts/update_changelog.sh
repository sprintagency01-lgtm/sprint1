#!/usr/bin/env bash
# update_changelog.sh
#
# Genera un borrador de entrada para CHANGELOG.md a partir de los commits
# locales no publicados todavía en origin/main. NO modifica CHANGELOG.md;
# imprime el borrador por stdout para que el desarrollador lo copie, edite
# y pegue al principio del fichero.
#
# Uso:
#   scripts/update_changelog.sh                # compara HEAD vs origin/main
#   scripts/update_changelog.sh origin/other   # compara HEAD vs otra rama remota
#
# Convención en CLAUDE.md.

set -euo pipefail

BASE_REF="${1:-origin/main}"
REPO_ROOT="$(git rev-parse --show-toplevel)"
CHANGELOG="${REPO_ROOT}/CHANGELOG.md"
TODAY="$(date +%Y-%m-%d)"

cd "$REPO_ROOT"

# Refrescar conocimiento del remoto sin tocar nada local.
git fetch --quiet origin 2>/dev/null || true

# Rango de commits que se pushearán.
if ! git rev-parse --verify --quiet "$BASE_REF" >/dev/null; then
  echo "⚠  No encuentro $BASE_REF. Prueba con otra rama remota como argumento." >&2
  exit 2
fi

COMMITS=$(git log --pretty=format:"%h %s" "${BASE_REF}..HEAD" || true)

if [ -z "$COMMITS" ]; then
  echo "No hay commits locales por delante de ${BASE_REF}. Nada que añadir al changelog." >&2
  exit 0
fi

# Clasificar commits por tipo convencional.
ADDED=""
CHANGED=""
FIXED=""
ENV=""
OTHER=""

while IFS= read -r line; do
  [ -z "$line" ] && continue
  HASH="${line%% *}"
  SUBJECT="${line#* }"
  TYPE=$(printf '%s' "$SUBJECT" | sed -n 's/^\([a-z]*\)(.*$/\1/p')
  [ -z "$TYPE" ] && TYPE=$(printf '%s' "$SUBJECT" | sed -n 's/^\([a-z]*\):.*$/\1/p')

  ENTRY="- ${SUBJECT} (commit \`${HASH}\`)"

  case "$TYPE" in
    feat)       ADDED+="${ENTRY}"$'\n' ;;
    fix)        FIXED+="${ENTRY}"$'\n' ;;
    refactor|perf|chore|style)
                CHANGED+="${ENTRY}"$'\n' ;;
    deploy|env) ENV+="${ENTRY}"$'\n' ;;
    *)          OTHER+="${ENTRY}"$'\n' ;;
  esac
done <<< "$COMMITS"

# Imprimir borrador.
printf '\n'
printf '=== BORRADOR PARA CHANGELOG.md — copia al principio bajo la entrada de %s ===\n\n' "$TODAY"
printf '## %s\n\n' "$TODAY"

if [ -n "$ADDED" ]; then
  printf '### Añadido\n\n%s\n' "$ADDED"
fi
if [ -n "$CHANGED" ]; then
  printf '### Cambiado\n\n%s\n' "$CHANGED"
fi
if [ -n "$FIXED" ]; then
  printf '### Corregido\n\n%s\n' "$FIXED"
fi
if [ -n "$ENV" ]; then
  printf '### Env / despliegue\n\n%s\n' "$ENV"
fi
if [ -n "$OTHER" ]; then
  printf '### Otros\n\n%s\n' "$OTHER"
fi

printf '=== FIN BORRADOR ===\n\n'
printf 'Siguiente paso:\n'
printf '  1. Edita el bloque anterior según haga falta (agrupa, aclara, elimina ruido).\n'
printf '  2. Pégalo al principio de CHANGELOG.md justo debajo de la cabecera.\n'
printf '  3. git add CHANGELOG.md && git commit -m "docs(changelog): update for push %s"\n' "$TODAY"
printf '  4. git push\n'
