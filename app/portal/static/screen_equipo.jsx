// Pantalla Equipo — editable (añadir personas, editar disponibilidad, turnos, excepciones)

function ScreenEquipo() {
  const [equipo, setEquipo] = useState(EQUIPO);
  const [selId, setSelId] = useState(EQUIPO[0].id);
  const [editor, setEditor] = useState(null); // {mode:'new'|'edit', member}
  const [turnoEditor, setTurnoEditor] = useState(null); // {idx|null}
  const m = equipo.find(x=>x.id===selId) || equipo[0];
  const DIAS = ['Lun','Mar','Mié','Jue','Vie','Sáb','Dom'];

  // ID del tenant (necesario para el oauth/start). El backend lo inyecta en
  // window.__PORTAL_DATA__.negocio.id.
  const tenantId = (window.__PORTAL_DATA__ || {}).negocio?.id || '';

  // Convierte el estado local del miembro al formato que acepta la API:
  // - vacaciones como lista de {desde, hasta} (agrupa días consecutivos)
  // - dias, turnos, color y nombre se envían tal cual
  const __vacsToRanges = (days) => {
    const sorted = [...new Set(days || [])].sort();
    const out = []; let start = null; let prev = null;
    const toDate = (d) => new Date(d + 'T00:00:00');
    const plusDay = (d) => { const x = toDate(d); x.setDate(x.getDate()+1); return x.toISOString().slice(0,10); };
    for (const d of sorted) {
      if (start == null) { start = d; prev = d; continue; }
      if (plusDay(prev) === d) { prev = d; continue; }
      out.push({ desde: start, hasta: prev });
      start = d; prev = d;
    }
    if (start != null) out.push({ desde: start, hasta: prev });
    return out;
  };
  const __buildPayload = (patch, base) => {
    const merged = { ...base, ...patch };
    const p = {};
    if ('nombre' in patch) p.nombre = merged.nombre;
    if ('color' in patch)  p.color  = merged.color;
    if ('dias' in patch)   p.dias   = merged.dias;
    if ('turnos' in patch) p.turnos = merged.turnos;
    if ('vacaciones' in patch) p.vacaciones = __vacsToRanges(merged.vacaciones);
    return p;
  };

  const updateMember = async (id, patch) => {
    const prev = equipo;
    setEquipo(xs => xs.map(x => x.id === id ? { ...x, ...patch } : x));
    const base = prev.find(x => x.id === id) || {};
    // Sólo persistimos si el id es numérico (id del servidor). Los ids
    // 'u_*' vienen de creaciones optimistas aún sin confirmar.
    if (!String(id).match(/^\d+$/)) return;
    try { await window.api.patch(`/equipo/${id}`, __buildPayload(patch, base)); }
    catch (e) { console.warn('update equipo failed', e); setEquipo(prev); alert('No se pudo guardar: ' + (e.message || e)); }
  };
  const deleteMember = async (id) => {
    const prev = equipo;
    const rest = equipo.filter(x => x.id !== id);
    setEquipo(rest);
    if (id === selId) setSelId(rest[0]?.id);
    if (!String(id).match(/^\d+$/)) return;
    try { await window.api.del(`/equipo/${id}`); }
    catch (e) { console.warn('delete equipo failed', e); setEquipo(prev); alert('No se pudo borrar: ' + (e.message || e)); }
  };

  const toggleDia = (di) => {
    const dias = m.dias.includes(di) ? m.dias.filter(x=>x!==di) : [...m.dias, di].sort();
    updateMember(m.id, {dias});
  };
  const updateTurno = (idx, turno) => {
    const turnos = m.turnos.map((t,i)=>i===idx?turno:t);
    updateMember(m.id, {turnos});
  };
  const addTurno = () => updateMember(m.id, {turnos:[...m.turnos, ['10:00','14:00']]});
  const removeTurno = (idx) => updateMember(m.id, {turnos: m.turnos.filter((_,i)=>i!==idx)});

  const addVacacion = () => {
    const d = prompt('Fecha (YYYY-MM-DD)', '2026-05-01');
    if (d) updateMember(m.id, {vacaciones:[...m.vacaciones, d].sort()});
  };
  const removeVacacion = (v) => updateMember(m.id, {vacaciones: m.vacaciones.filter(x=>x!==v)});

  const saveMember = async (member) => {
    if (editor.mode === 'new') {
      try {
        const body = { nombre: member.nombre, color: member.color, dias: [0,1,2,3,4], turnos: [['10:00','14:00'],['17:00','20:00']], vacaciones: [] };
        const res = await window.api.post('/equipo', body);
        const nuevo = { id: String(res.id), ...body, googleOk: false };
        setEquipo([...equipo, nuevo]);
        setSelId(String(res.id));
      } catch (e) { alert('No se pudo crear: ' + (e.message || e)); return; }
    } else {
      await updateMember(editor.member.id, { nombre: member.nombre, color: member.color });
    }
    setEditor(null);
  };

  return (
    <>
      <div className="mb-4 flex items-end justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl md:text-2xl font-bold tracking-tight">Equipo</h1>
          <p className="text-slate-500 dark:text-slate-400 text-sm mt-1">Quién trabaja y cuándo puede el bot dar cita</p>
        </div>
        <button onClick={()=>setEditor({mode:'new', member:{nombre:'', color:'#059669'}})}
          className="flex items-center gap-2 bg-brand-600 hover:bg-brand-700 text-white text-sm font-medium px-3 py-2 rounded-lg">
          <Icon d={ICONS.plus} cls="w-4 h-4"/> Añadir persona
        </button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-4 md:gap-6">
        {/* Lista — scroll horizontal en móvil */}
        <Card className="lg:col-span-4 overflow-hidden">
          <div className="lg:hidden flex gap-2 p-3 overflow-x-auto scroll-hide">
            {equipo.map(p=>(
              <button key={p.id} onClick={()=>setSelId(p.id)}
                className={`shrink-0 flex items-center gap-2 px-3 py-2 rounded-lg border ${selId===p.id?'border-brand-500 bg-brand-50 dark:bg-brand-900/20':'border-slate-200 dark:border-slate-700'}`}>
                <Avatar name={p.nombre} color={p.color} size="sm"/>
                <span className="text-sm font-medium">{p.nombre}</span>
              </button>
            ))}
          </div>
          <ul className="hidden lg:block divide-y divide-slate-100 dark:divide-slate-800">
            {equipo.map(p=>(
              <li key={p.id} onClick={()=>setSelId(p.id)}
                className={`p-4 cursor-pointer flex items-center gap-3 hover:bg-slate-50 dark:hover:bg-slate-800/50 ${selId===p.id?'bg-slate-50 dark:bg-slate-800/50':''}`}>
                <Avatar name={p.nombre} color={p.color}/>
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-medium">{p.nombre}</div>
                  <div className="text-xs text-slate-500">{p.dias.length} días · {p.turnos.length} turno{p.turnos.length>1?'s':''}</div>
                </div>
                <StatusDot on={p.googleOk} label={p.googleOk?'Cal.':'Pend.'}/>
              </li>
            ))}
          </ul>
        </Card>

        {m && (
          <div className="lg:col-span-8 space-y-4">
            {/* Cabecera persona */}
            <Card className="p-4 md:p-5">
              <div className="flex items-center gap-3 mb-1">
                <Avatar name={m.nombre} color={m.color} size="lg"/>
                <div className="flex-1 min-w-0">
                  <div className="text-lg font-semibold truncate">{m.nombre}</div>
                  <div className="text-xs text-slate-500">{m.googleOk?'Calendario conectado':'Calendario pendiente'}</div>
                </div>
                <button onClick={()=>setEditor({mode:'edit', member:m})} className="p-2 text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800 rounded-lg"><Icon d={ICONS.edit} cls="w-4 h-4"/></button>
                {equipo.length>1 && (
                  <button onClick={()=>confirm(`¿Quitar a ${m.nombre}?`) && deleteMember(m.id)} className="p-2 text-slate-500 hover:text-rose-600 hover:bg-rose-50 dark:hover:bg-rose-900/20 rounded-lg"><Icon d={ICONS.trash} cls="w-4 h-4"/></button>
                )}
              </div>
            </Card>

            {/* Días */}
            <Card className="p-4 md:p-5">
              <div className="text-sm font-semibold mb-3">Días que trabaja</div>
              <div className="flex gap-1.5 flex-wrap">
                {DIAS.map((d,i)=>{
                  const on = m.dias.includes(i);
                  return (
                    <button key={i} onClick={()=>toggleDia(i)}
                      className={`px-3 py-2 text-xs font-medium rounded-lg border transition ${on?'bg-brand-600 text-white border-brand-600':'bg-white dark:bg-slate-900 border-slate-200 dark:border-slate-700 text-slate-600 dark:text-slate-300'}`}>
                      {d}
                    </button>
                  );
                })}
              </div>
            </Card>

            {/* Turnos */}
            <Card className="p-4 md:p-5">
              <div className="flex items-center justify-between mb-3">
                <div>
                  <div className="text-sm font-semibold">Horario</div>
                  <div className="text-xs text-slate-500">Soporta turno partido</div>
                </div>
                <button onClick={addTurno} className="text-xs text-brand-700 hover:underline flex items-center gap-1"><Icon d={ICONS.plus} cls="w-3 h-3"/> Añadir turno</button>
              </div>
              <div className="space-y-2">
                {m.turnos.map((t,i)=>(
                  <div key={i} className="flex items-center gap-2 p-2 rounded-lg bg-slate-50 dark:bg-slate-800/40">
                    <span className="text-xs text-slate-500 w-14 shrink-0">Turno {i+1}</span>
                    <input type="time" value={t[0]} onChange={e=>updateTurno(i,[e.target.value,t[1]])} className="flex-1 min-w-0 px-2 py-1.5 text-sm rounded-md border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 tabular-nums"/>
                    <span className="text-slate-400">–</span>
                    <input type="time" value={t[1]} onChange={e=>updateTurno(i,[t[0],e.target.value])} className="flex-1 min-w-0 px-2 py-1.5 text-sm rounded-md border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 tabular-nums"/>
                    {m.turnos.length>1 && <button onClick={()=>removeTurno(i)} className="p-1 text-slate-400 hover:text-rose-600"><Icon d={ICONS.trash} cls="w-4 h-4"/></button>}
                  </div>
                ))}
              </div>
            </Card>

            {/* Excepciones */}
            <Card className="p-4 md:p-5">
              <div className="flex items-center justify-between mb-3">
                <div className="text-sm font-semibold">Vacaciones y festivos</div>
                <button onClick={addVacacion} className="text-xs text-brand-700 hover:underline flex items-center gap-1"><Icon d={ICONS.plus} cls="w-3 h-3"/> Añadir</button>
              </div>
              {m.vacaciones.length ? (
                <ul className="space-y-1.5">
                  {m.vacaciones.map(v=>(
                    <li key={v} className="flex items-center justify-between text-sm p-2 rounded-lg bg-slate-50 dark:bg-slate-800/40">
                      <span className="tabular-nums">{v.split('-').reverse().join('/')}</span>
                      <button onClick={()=>removeVacacion(v)} className="text-slate-400 hover:text-rose-600"><Icon d={ICONS.trash} cls="w-4 h-4"/></button>
                    </li>
                  ))}
                </ul>
              ) : (
                <div className="text-xs text-slate-400 py-4 text-center">Sin excepciones</div>
              )}
            </Card>

            <GoogleCalendarPanel
              member={m}
              tenantId={tenantId}
              onLocalUpdate={(patch)=> setEquipo(xs => xs.map(x => x.id === m.id ? { ...x, ...patch } : x)) }
            />
          </div>
        )}
      </div>

      {/* Modal alta/edición persona */}
      {editor && <MemberEditor editor={editor} onClose={()=>setEditor(null)} onSave={saveMember}/>}
    </>
  );
}

// -----------------------------------------------------------------------
//  GoogleCalendarPanel — conectar / elegir / desconectar el Calendar de
//  un miembro. Equivalente al bloque del CMS (tab_equipo.html), pero
//  consumiendo /api/portal/equipo/{mid}/calendars*.
// -----------------------------------------------------------------------

function GoogleCalendarPanel({ member, tenantId, onLocalUpdate }) {
  const [loading, setLoading] = useState(false);
  const [calendars, setCalendars] = useState([]);
  const [connected, setConnected] = useState(!!member.googleOk);
  const [creating, setCreating] = useState(false);

  // Si el id del miembro es 'u_*' (creación optimista no persistida),
  // no podemos conectar Google todavía: el backend necesita el id real.
  const isPersisted = String(member.id).match(/^\d+$/);

  // Carga la lista de calendarios desde el backend cuando el miembro está
  // conectado. Se relanza al cambiar de miembro.
  useEffect(() => {
    if (!isPersisted) return;
    let cancelled = false;
    (async () => {
      try {
        setLoading(true);
        const data = await window.api.get(`/equipo/${member.id}/calendars`);
        if (cancelled) return;
        setConnected(!!data.connected);
        setCalendars(data.calendars || []);
        // Sincroniza el estado del padre por si Google se reconectó en otra
        // pestaña y la lista de calendars revela un cambio.
        if (data.connected !== member.googleOk) {
          onLocalUpdate({ googleOk: !!data.connected });
        }
      } catch (e) {
        console.warn('[equipo] no pude cargar calendarios', e);
        setConnected(false);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [member.id]);

  const onPickCalendar = async (calendarId) => {
    onLocalUpdate({ calendar_id: calendarId });
    try {
      await window.api.patch(`/equipo/${member.id}`, { calendar_id: calendarId });
    } catch (e) {
      alert('No se pudo guardar el calendario: ' + (e.message || e));
    }
  };

  const onCreate = async () => {
    const name = prompt('Nombre del calendario nuevo:', `Trabajo — ${member.nombre || 'Miembro'}`);
    if (!name) return;
    setCreating(true);
    try {
      const data = await window.api.post(`/equipo/${member.id}/calendars/create`, { summary: name });
      // Lo añadimos al desplegable y lo seleccionamos.
      setCalendars(cs => [...cs, { id: data.id, summary: data.summary, primary: false }]);
      onLocalUpdate({ calendar_id: data.id });
    } catch (e) {
      alert('No se pudo crear el calendario: ' + (e.message || e));
    } finally {
      setCreating(false);
    }
  };

  const onDisconnect = async () => {
    if (!confirm(`¿Desconectar Google de ${member.nombre}?`)) return;
    try {
      await window.api.post(`/equipo/${member.id}/disconnect`, {});
      setConnected(false);
      setCalendars([]);
      onLocalUpdate({ googleOk: false, calendar_id: '' });
    } catch (e) {
      alert('No se pudo desconectar: ' + (e.message || e));
    }
  };

  // No-persisted aún: mostramos un aviso suave para que entiendan que tienen
  // que guardar el miembro antes (el botón "Añadir persona" hace POST y
  // recarga la lista, así que esto es solo durante el blink).
  if (!isPersisted) {
    return (
      <div className="p-3 rounded-xl border border-slate-200 bg-slate-50 dark:bg-slate-800/40 text-xs text-slate-500">
        Guarda este miembro para poder conectar Google.
      </div>
    );
  }

  if (!connected) {
    const startUrl = `/oauth/start?tenant_id=${encodeURIComponent(tenantId)}&member_id=${member.id}&back=portal`;
    return (
      <div className="p-3 rounded-xl border border-amber-200 bg-amber-50 dark:bg-amber-900/20 dark:border-amber-900/40 text-sm flex items-center gap-3">
        <div className="text-amber-600 shrink-0">⚠</div>
        <div className="flex-1 text-xs text-amber-900 dark:text-amber-200">
          Conecta el calendario de {member.nombre} para que el bot vea sus huecos reales.
        </div>
        <a
          href={startUrl}
          className="text-xs px-2.5 py-1.5 rounded-lg bg-brand-600 hover:bg-brand-700 text-white whitespace-nowrap"
        >
          Conectar Google
        </a>
      </div>
    );
  }

  return (
    <Card className="p-4 md:p-5">
      <div className="flex items-center justify-between mb-3">
        <div>
          <div className="text-sm font-semibold">Google Calendar conectado</div>
          <div className="text-xs text-emerald-700 dark:text-emerald-400">Cuenta de {member.nombre} enlazada</div>
        </div>
        <button onClick={onDisconnect} className="text-xs text-slate-400 hover:text-rose-600">
          Desconectar
        </button>
      </div>
      <div className="text-xs text-slate-500 mb-2">
        Calendario donde el bot escribirá las reservas de {member.nombre}
      </div>
      <div className="flex gap-2">
        <select
          value={member.calendar_id || ''}
          onChange={(e)=>onPickCalendar(e.target.value)}
          className="flex-1 px-3 py-2 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 text-sm"
        >
          {!member.calendar_id && <option value="">— elige un calendario —</option>}
          {loading && <option value="">Cargando...</option>}
          {calendars.map(c => (
            <option key={c.id} value={c.id}>
              {c.summary}{c.primary ? ' (principal)' : ''}
            </option>
          ))}
        </select>
        <button
          onClick={onCreate}
          disabled={creating}
          className="text-xs px-3 py-2 rounded-lg border border-slate-200 dark:border-slate-700 hover:bg-slate-50 dark:hover:bg-slate-800 whitespace-nowrap disabled:opacity-50"
          title="Crear un calendario nuevo en la cuenta del miembro"
        >
          {creating ? 'Creando...' : '+ Nuevo'}
        </button>
      </div>
    </Card>
  );
}

function MemberEditor({ editor, onClose, onSave }) {
  const [nombre, setNombre] = useState(editor.member.nombre);
  const [color, setColor] = useState(editor.member.color);
  const COLORS = ['#059669','#6366f1','#ec4899','#f59e0b','#0ea5e9','#8b5cf6','#ef4444','#14b8a6'];
  return (
    <div className="fixed inset-0 z-40 flex items-end sm:items-center justify-center">
      <div className="absolute inset-0 bg-slate-900/40" onClick={onClose}/>
      <div className="relative w-full sm:max-w-md bg-white dark:bg-slate-900 rounded-t-xl sm:rounded-xl shadow-xl p-5">
        <div className="text-base font-semibold mb-4">{editor.mode==='new'?'Añadir persona':'Editar persona'}</div>
        <label className="block mb-3">
          <div className="text-xs text-slate-500 mb-1">Nombre</div>
          <input autoFocus value={nombre} onChange={e=>setNombre(e.target.value)}
            className="w-full px-3 py-2 text-sm rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 focus:border-brand-500 focus:outline-none"/>
        </label>
        <div className="mb-4">
          <div className="text-xs text-slate-500 mb-2">Color</div>
          <div className="flex gap-2 flex-wrap">
            {COLORS.map(c=>(
              <button key={c} onClick={()=>setColor(c)}
                className={`w-8 h-8 rounded-lg ring-2 transition ${color===c?'ring-slate-900 dark:ring-white':'ring-transparent'}`}
                style={{background:c}} aria-label={c}/>
            ))}
          </div>
        </div>
        <div className="flex gap-2">
          <button onClick={onClose} className="flex-1 text-sm py-2 rounded-lg border border-slate-200 dark:border-slate-700">Cancelar</button>
          <button onClick={()=>nombre.trim() && onSave({nombre:nombre.trim(), color})} disabled={!nombre.trim()}
            className="flex-1 text-sm py-2 rounded-lg bg-brand-600 hover:bg-brand-700 text-white disabled:opacity-50">Guardar</button>
        </div>
      </div>
    </div>
  );
}

window.ScreenEquipo = ScreenEquipo;
