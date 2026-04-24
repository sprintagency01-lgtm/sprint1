"""conftest.py: configuración global de pytest para todo el repo.

Mete las variables de entorno necesarias ANTES de que los módulos del
proyecto se importen. Es la única forma de influir en
`app.config.settings` (dataclass frozen=True que se instancia al import).

Responsabilidades:
 - Usar una BD SQLite temporal para los tests (no tocar `data.db` real).
 - Asignar un TOOL_SECRET ficticio para que los endpoints /tools/* no
   devuelvan 500 por config vacía en entornos CI.
 - No heredar ELEVENLABS_API_KEY / OPENAI_API_KEY reales si estuvieran en
   el entorno: los tests no deben llamar nunca a APIs externas.
"""
from __future__ import annotations

import os
import pathlib
import tempfile

# Directorio temporal aislado por invocación de pytest. Se limpia al salir
# del proceso (tmpdir de usuario).
_TEST_DB_DIR = pathlib.Path(tempfile.mkdtemp(prefix="bot_reservas_tests_"))
_TEST_DB_PATH = _TEST_DB_DIR / "test_data.db"

os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TEST_DB_PATH}")
os.environ.setdefault("TOOL_SECRET", "test-secret-conftest")
os.environ.setdefault("TENANTS_FILE", str(_TEST_DB_DIR / "tenants.yaml"))
# Evita que se monte `.tokens/` dentro del repo durante los tests: cada test
# run usa su propio directorio.
os.environ.setdefault("TOKENS_DIR", str(_TEST_DB_DIR / ".tokens"))
# No permitir fugas de llaves reales a los tests (aunque algún test los
# llame por error, irían a una key vacía y fallarían antes de llegar a red).
for _var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ELEVENLABS_API_KEY"):
    os.environ.setdefault(_var, "")

# Asegura que el directorio de tokens existe (calendar_service.TOKENS_DIR
# hace mkdir al import, pero más vale blindarlo).
(_TEST_DB_DIR / ".tokens").mkdir(parents=True, exist_ok=True)
