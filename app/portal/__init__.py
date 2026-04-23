"""Portal del cliente (/app).

SPA React que corre contra /api/portal/*. Pensado para el dueño del negocio
y su equipo de recepción — distinto del CMS interno (/admin) que usa
Sprintagency.
"""
from .routes import router  # noqa: F401
