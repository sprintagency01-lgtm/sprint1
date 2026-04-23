// Datos en vivo para el portal del cliente. Sustituye los mocks originales.
//
// El backend inyecta `window.__PORTAL_DATA__` en portal.html con el estado
// inicial del tenant autenticado (user, negocio, equipo, servicios, reservas,
// conversaciones, ingresos). Aquí lo redistribuimos a los globals que
// consumen los screens (NEGOCIO, EQUIPO, SERVICIOS, RESERVAS, ...).
//
// También expone `window.api` — wrapper de fetch contra /api/portal/*.

const __D = (typeof window !== 'undefined' && window.__PORTAL_DATA__) ? window.__PORTAL_DATA__ : {};

function __expandVacRanges(ranges) {
  const out = [];
  for (const v of ranges || []) {
    if (typeof v === 'string') { out.push(v); continue; }
    if (!v || !v.desde || !v.hasta) continue;
    try {
      const d0 = new Date(v.desde + 'T00:00:00');
      const d1 = new Date(v.hasta + 'T00:00:00');
      const d = new Date(d0);
      while (d <= d1) {
        out.push(d.toISOString().slice(0, 10));
        d.setDate(d.getDate() + 1);
      }
    } catch { /* ignore */ }
  }
  return out;
}

const NEGOCIO = __D.negocio || {
  id: '', nombre: '', sector: '', direccion: '', tz: 'Europe/Madrid', telefono: '',
};

const EQUIPO = (__D.equipo || []).map(m => ({
  id: m.id,
  nombre: m.nombre,
  color: m.color || '#059669',
  dias: Array.isArray(m.dias) ? m.dias : (Array.isArray(m.dias_trabajo) ? m.dias_trabajo : [0, 1, 2, 3, 4, 5]),
  turnos: (Array.isArray(m.turnos) && m.turnos.length) ? m.turnos : [['10:00', '20:00']],
  googleOk: typeof m.googleOk === 'boolean' ? m.googleOk : !!(m.calendar_id && m.calendar_id.length),
  vacaciones: __expandVacRanges(m.vacaciones),
  // Conservamos los rangos originales para la UI de vacaciones del portal.
  vacacionesRangos: Array.isArray(m.vacaciones) ? m.vacaciones.filter(v => v && v.desde && v.hasta) : [],
  calendar_id: m.calendar_id || '',
}));

const SERVICIOS = (__D.servicios || []).map(s => ({
  id: s.id,
  nombre: s.nombre,
  duracion: s.duracion ?? s.duracion_min ?? 30,
  precio: s.precio ?? 0,
  equipo: Array.isArray(s.equipo) ? s.equipo : [],
  activo: typeof s.activo === 'boolean' ? s.activo : true,
}));

const HOY_ISO = __D.hoy_iso || new Date().toISOString().slice(0, 10);
const RESERVAS = __D.reservas || [];
const INGRESOS_30D = __D.ingresos_30d || [];
const CONVERSACIONES_WA = __D.conversaciones_wa || [];
const CONVERSACIONES_VOZ = __D.conversaciones_voz || [];

// KPIs derivados
const kpis = () => {
  const hoy = RESERVAS.filter(r => r.fecha === HOY_ISO && r.estado !== 'cancelada');
  const semana = RESERVAS.filter(r => r.estado !== 'cancelada').length;
  const ingresosSemana = RESERVAS.filter(r => r.estado !== 'cancelada')
    .reduce((s, r) => s + (SERVICIOS.find(x => x.id === r.servicio)?.precio || 0), 0);
  const porBot = RESERVAS.filter(r => r.canal !== 'manual' && r.estado !== 'cancelada').length;
  const total = RESERVAS.filter(r => r.estado !== 'cancelada').length;
  return {
    reservasHoy: hoy.length,
    reservasSemana: semana,
    ingresosSemana,
    pctBot: total ? Math.round((porBot / total) * 100) : 0,
  };
};

const servicioDe = id => SERVICIOS.find(s => s.id === id);
const miembroDe = id => EQUIPO.find(m => m.id === id);

// --- API helper ---------------------------------------------------------
const API_BASE = '/api/portal';

async function apiFetch(method, path, body) {
  const opts = {
    method,
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
  };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const res = await fetch(API_BASE + path, opts);
  if (res.status === 401) {
    window.location.href = '/app/login';
    throw new Error('no autenticado');
  }
  let data = null;
  try { data = await res.json(); } catch { /* ignore */ }
  if (!res.ok) {
    const msg = (data && (data.detail || data.error)) || res.statusText || 'error';
    throw new Error(msg);
  }
  return data;
}

const api = {
  get:   (p)       => apiFetch('GET', p),
  post:  (p, body) => apiFetch('POST', p, body),
  patch: (p, body) => apiFetch('PATCH', p, body),
  del:   (p)       => apiFetch('DELETE', p),
};

// Refresh sencillo — suficiente para MVP. Los mutations llaman a esto tras
// guardar para que los demás screens vean el estado actualizado.
function reloadPortal() { window.location.reload(); }

// --- IA parse para reservas ---------------------------------------------
// El screen_reservas usa `window.claude.complete(prompt)` para parsear texto
// libre (voz/texto) → JSON. Lo apuntamos a /api/portal/reservas/ia_parse que
// delega en OpenAI en el servidor (mantiene la API key fuera del cliente).
const claude = {
  async complete(prompt) {
    const data = await api.post('/reservas/ia_parse', { prompt });
    // screen_reservas espera un string parseable como JSON.
    return typeof data === 'string' ? data : JSON.stringify(data);
  },
};

Object.assign(window, {
  NEGOCIO, EQUIPO, SERVICIOS, RESERVAS, HOY_ISO,
  CONVERSACIONES_WA, CONVERSACIONES_VOZ, INGRESOS_30D,
  kpis, servicioDe, miembroDe,
  api, reloadPortal, claude,
  PORTAL_USER: __D.user || { nombre: '', email: '' },
  PORTAL_BOT:  __D.bot  || { voz: true, wa: true },
});
