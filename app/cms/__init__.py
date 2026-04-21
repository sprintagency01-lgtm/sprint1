"""Panel de administración (CMS) del bot de reservas.

Se monta como sub-router en app/main.py bajo el prefijo /admin.
"""
from .routes import router  # re-export para que main.py haga `from .cms import router`

__all__ = ["router"]
