#!/bin/zsh
cd "$(dirname "$0")" || exit 1
exec > >(tee -a /tmp/sprint_push.log) 2>&1

echo "════════════════════════════════════════"
echo "  Push: colgar cierra modal · $(date)"
echo "════════════════════════════════════════"

[ -f .git/index.lock ] && rm -f .git/index.lock
git config user.email > /dev/null 2>&1 || git config user.email "m.fcarrillo9@gmail.com"
git config user.name  > /dev/null 2>&1 || git config user.name  "Marcos"

if ! git pull --rebase --autostash origin main; then
  echo "✗ Pull rebase falló"; read -k 1 "?Pulsa..."; exit 1
fi

git add CHANGELOG.md app/templates/landing.html app/templates/gemini_demo.html
echo ""
git diff --cached --stat

git commit -m "feat(demo+landing): colgar cierra modal · copy del final CTA

- gemini_demo.html: stop() hace window.parent.postMessage cuando EMBED.
- landing.html (modal): listener 'sprintia-demo:close' cierra el modal
  cuando el iframe lo pide. Antes colgar dejaba al usuario en el splash
  dentro del modal, sin pista de que la llamada había terminado.
- landing.html (final CTA): h2 'Ahora toca no hacer nada. De eso nos
  encargamos.' → 'Ahora toca hacer lo que te gusta. De las llamadas nos
  encargamos nosotros.' (énfasis serif italic en 'gusta'). Background
  word 'sprint.' → 'sprintia.' para coherencia con el rebrand."

if [ $? -ne 0 ]; then
  echo "✗ Commit falló"; read -k 1 "?Pulsa..."; exit 1
fi

echo ""
git push origin main
PUSH_RC=$?

if [ $PUSH_RC -eq 0 ]; then
  echo "✓ PUSH OK · HEAD: $(git log -1 --format='%h %s')"
  rm -f _hangup_close_push.command 2>/dev/null
else
  echo "✗ Push falló ($PUSH_RC)"
fi

echo ""
read -k 1 "?Pulsa una tecla para cerrar..."
osascript -e 'tell application "Terminal" to close (every window whose name contains "_hangup_close_push")' &
exit 0
