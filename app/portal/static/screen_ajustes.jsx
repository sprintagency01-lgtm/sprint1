// Pantalla Ajustes — conectada a /api/portal/negocio y /api/portal/usuarios

function ScreenAjustes({ user, setUser }) {
  const [tab, setTab] = useState('negocio');

  return (
    <>
      <div className="mb-4 md:mb-6">
        <h1 className="text-2xl font-bold tracking-tight">Ajustes</h1>
        <p className="text-slate-500 dark:text-slate-400 text-sm mt-1">Datos de tu negocio, avisos y personas con acceso</p>
      </div>

      <div className="border-b border-slate-200 dark:border-slate-800 mb-5 flex gap-6 overflow-x-auto">
        {[['negocio','Negocio'],['avisos','Avisos'],['usuarios','Usuarios'],['cuenta','Mi cuenta']].map(([k,l])=>(
          <button key={k} onClick={()=>setTab(k)}
            className={`tab-btn py-3 -mb-px border-b-2 text-sm whitespace-nowrap ${tab===k?'active':'border-transparent text-slate-500 dark:text-slate-400'}`}>{l}</button>
        ))}
      </div>

      {tab==='negocio' && <TabNegocio/>}
      {tab==='avisos'  && <TabAvisos/>}
      {tab==='usuarios'&& <TabUsuarios user={user}/>}
      {tab==='cuenta'  && <TabCuenta user={user}/>}
    </>
  );
}

function TabNegocio() {
  const [form, setForm] = useState({
    nombre: NEGOCIO.nombre || '',
    sector: NEGOCIO.sector || '',
    telefono: NEGOCIO.telefono || '',
    direccion: NEGOCIO.direccion || '',
  });
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState(null);

  const save = async () => {
    setSaving(true);
    setMsg(null);
    try {
      await window.api.patch('/negocio', {
        nombre: form.nombre,
        sector: form.sector,
        telefono: form.telefono,
      });
      // Mantén sincronía con los globals para el resto de la sesión.
      window.NEGOCIO = { ...window.NEGOCIO, ...form };
      setMsg({ type:'ok', text:'Guardado' });
    } catch (e) {
      setMsg({ type:'err', text:'No se pudo guardar: ' + (e.message || e) });
    } finally {
      setSaving(false);
      setTimeout(()=>setMsg(null), 2500);
    }
  };

  return (
    <div className="grid md:grid-cols-2 gap-4 md:gap-6 max-w-4xl">
      <Card className="p-5">
        <div className="text-sm font-semibold mb-4">Datos del negocio</div>
        <div className="space-y-3">
          <Field label="Nombre"    value={form.nombre}    onChange={v=>setForm({...form,nombre:v})}/>
          <Field label="Sector"    value={form.sector}    onChange={v=>setForm({...form,sector:v})}/>
          <Field label="Dirección" value={form.direccion} onChange={v=>setForm({...form,direccion:v})}/>
          <Field label="Teléfono"  value={form.telefono}  onChange={v=>setForm({...form,telefono:v})}/>
          <Field label="Zona horaria" value="Europe/Madrid (UTC+2)" readOnly/>
        </div>
        <div className="mt-4 flex items-center gap-3">
          <button onClick={save} disabled={saving}
            className="text-sm px-4 py-2 rounded-lg bg-brand-600 hover:bg-brand-700 text-white disabled:opacity-50">
            {saving ? 'Guardando…' : 'Guardar cambios'}
          </button>
          {msg && (
            <span className={`text-xs ${msg.type==='ok'?'text-emerald-700':'text-rose-700'}`}>{msg.text}</span>
          )}
        </div>
      </Card>
      <HorariosNegocio/>
    </div>
  );
}

// ----------------------------------------------------------------------
//  Horario de apertura del negocio.
//  Capa "negocio": cuándo está abierto el local. Cada miembro define sus
//  turnos dentro de esta franja en la pestaña Equipo. El agente intersecta
//  ambas capas — un miembro nunca ofrece huecos fuera del horario del
//  negocio.
// ----------------------------------------------------------------------

function HorariosNegocio() {
  const DIAS = [
    ['mon','Lun'], ['tue','Mar'], ['wed','Mié'], ['thu','Jue'],
    ['fri','Vie'], ['sat','Sáb'], ['sun','Dom'],
  ];

  // Convierte la lista plana ["09:00","13:00","17:00","20:00"] del backend a
  // una lista de pares [["09:00","13:00"],["17:00","20:00"]] con la que es
  // mucho más cómodo trabajar en la UI.
  const flatToPairs = (flat) => {
    if (!Array.isArray(flat) || flat[0] === 'closed') return [];
    const out = [];
    for (let i = 0; i + 1 < flat.length; i += 2) out.push([flat[i], flat[i+1]]);
    return out;
  };
  const pairsToFlat = (pairs) => {
    if (!pairs || !pairs.length) return ['closed'];
    return pairs.flatMap(p => [p[0], p[1]]);
  };

  // Estado inicial: lo que vino del backend en NEGOCIO.horarios (const del
  // closure, hidratada desde __PORTAL_DATA__.negocio.horarios). Si el dict
  // está vacío (tenant sin configurar) sembramos un horario de oficina
  // típico L-V 09:00-18:00 para que el cliente arranque editando algo, no
  // a un lienzo en blanco.
  const seed = (() => {
    const h = (typeof NEGOCIO !== 'undefined' && NEGOCIO.horarios) || {};
    const empty = !DIAS.some(([k]) => Array.isArray(h[k]) && h[k][0] !== 'closed');
    if (empty) {
      return {
        mon: [['09:00','18:00']], tue: [['09:00','18:00']], wed: [['09:00','18:00']],
        thu: [['09:00','18:00']], fri: [['09:00','18:00']], sat: [], sun: [],
      };
    }
    const out = {};
    for (const [k] of DIAS) out[k] = flatToPairs(h[k]);
    return out;
  })();

  const [hours, setHours] = useState(seed);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState(null);

  const setDay = (k, pairs) => setHours(h => ({ ...h, [k]: pairs }));
  const toggleDay = (k) => {
    const open = (hours[k] || []).length > 0;
    setDay(k, open ? [] : [['09:00','18:00']]);
  };
  const addFranja = (k) => {
    const last = (hours[k] || []).slice(-1)[0];
    const start = last ? last[1] : '09:00';
    setDay(k, [...(hours[k] || []), [start, start < '20:00' ? '20:00' : '23:59']]);
  };
  const removeFranja = (k, idx) => {
    setDay(k, (hours[k] || []).filter((_, i) => i !== idx));
  };
  const updateFranja = (k, idx, which, value) => {
    setDay(k, (hours[k] || []).map((p, i) => i === idx ? (which === 'open' ? [value, p[1]] : [p[0], value]) : p));
  };

  const save = async () => {
    setSaving(true);
    setMsg(null);
    try {
      const payload = {};
      for (const [k] of DIAS) payload[k] = pairsToFlat(hours[k] || []);
      const res = await window.api.patch('/negocio/horarios', { horarios: payload });
      // Sincroniza el global por si la UI lo recarga sin refresh.
      window.NEGOCIO = { ...window.NEGOCIO, horarios: res.horarios || payload };
      setMsg({ type:'ok', text:'Horario guardado' });
    } catch (e) {
      setMsg({ type:'err', text:'No se pudo guardar: ' + (e.message || e) });
    } finally {
      setSaving(false);
      setTimeout(()=>setMsg(null), 2500);
    }
  };

  return (
    <Card className="p-5">
      <div className="flex items-center justify-between mb-4">
        <div>
          <div className="text-sm font-semibold">Horario de apertura</div>
          <div className="text-xs text-slate-500">Cuándo está abierto el negocio</div>
        </div>
      </div>

      <div className="space-y-3">
        {DIAS.map(([key, label]) => {
          const franjas = hours[key] || [];
          const open = franjas.length > 0;
          return (
            <div key={key} className="flex items-start gap-3">
              <button
                onClick={()=>toggleDay(key)}
                className={`mt-1 tg ${open ? 'on' : ''}`}
                aria-label={`Día ${label} ${open ? 'abierto' : 'cerrado'}`}
              />
              <div className="w-10 mt-1.5 text-sm font-medium text-slate-700 dark:text-slate-200">{label}</div>
              <div className="flex-1 min-w-0 space-y-2">
                {!open && (
                  <div className="text-xs text-slate-400 py-1.5">Cerrado</div>
                )}
                {franjas.map(([o, c], idx) => (
                  <div key={idx} className="flex items-center gap-2">
                    <input type="time" value={o}
                      onChange={(e)=>updateFranja(key, idx, 'open', e.target.value)}
                      className="px-2 py-1.5 text-sm rounded-md border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 tabular-nums"/>
                    <span className="text-slate-400">–</span>
                    <input type="time" value={c}
                      onChange={(e)=>updateFranja(key, idx, 'close', e.target.value)}
                      className="px-2 py-1.5 text-sm rounded-md border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 tabular-nums"/>
                    {franjas.length > 1 && (
                      <button onClick={()=>removeFranja(key, idx)}
                        className="text-xs text-slate-400 hover:text-rose-600 px-1"
                        title="Quitar franja">
                        ✕
                      </button>
                    )}
                  </div>
                ))}
                {open && (
                  <button onClick={()=>addFranja(key)}
                    className="text-xs text-brand-700 hover:underline">
                    + Añadir franja
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>

      <div className="mt-5 flex items-center gap-3">
        <button onClick={save} disabled={saving}
          className="text-sm px-4 py-2 rounded-lg bg-brand-600 hover:bg-brand-700 text-white disabled:opacity-50">
          {saving ? 'Guardando…' : 'Guardar horario'}
        </button>
        {msg && (
          <span className={`text-xs ${msg.type==='ok'?'text-emerald-700':'text-rose-700'}`}>{msg.text}</span>
        )}
      </div>

      <div className="mt-4 text-xs text-slate-500">
        Cada miembro define sus turnos dentro de este horario en la pestaña <b>Equipo</b>.
        El bot nunca ofrecerá citas fuera de la apertura del negocio.
      </div>
    </Card>
  );
}

function TabAvisos() {
  // Mock local sin persistencia — el backend aún no tiene endpoints de avisos.
  const [avisos, setAvisos] = useState([
    ['Cuando el bot crea una reserva','Recibe un email al instante',true],
    ['Cuando el bot mueve una reserva','Email al dueño',true],
    ['Cuando el bot cancela una reserva','Email al dueño',true],
    ['Resumen diario','Cada noche a las 21:00 con las reservas del día siguiente',false],
    ['Alertas de caída del bot','Si Ana deja de funcionar, te avisamos',true],
  ]);
  return (
    <div className="max-w-2xl space-y-3">
      <div className="text-xs text-slate-500 mb-2">
        Los avisos se guardan localmente durante esta sesión. Próximamente los persistiremos en tu cuenta.
      </div>
      {avisos.map(([t,s,on],i)=>(
        <Card key={i} className="p-4 flex items-center gap-4">
          <div className="flex-1">
            <div className="text-sm font-medium">{t}</div>
            <div className="text-xs text-slate-500">{s}</div>
          </div>
          <div className={`tg ${on?'on':''}`} role="switch" aria-checked={on} tabIndex={0}
            onClick={()=>setAvisos(xs=>xs.map((x,j)=>j===i?[x[0],x[1],!x[2]]:x))}/>
        </Card>
      ))}
    </div>
  );
}

function TabUsuarios({ user }) {
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [inviting, setInviting] = useState(false);
  const [draft, setDraft] = useState({ nombre:'', email:'', role:'manager', password:'' });
  const [err, setErr] = useState(null);
  const soyOwner = (user && user.role) === 'owner' || !user.role; // fallback si no hay role

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const list = await window.api.get('/usuarios');
        if (alive) setUsers(list || []);
      } catch (e) {
        console.warn('load usuarios failed', e);
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
  }, []);

  const invite = async () => {
    setErr(null);
    if (!draft.email.trim() || !draft.password.trim()) {
      setErr('Email y contraseña son obligatorios');
      return;
    }
    try {
      const res = await window.api.post('/usuarios', draft);
      setUsers(xs => [...xs, { id: res.id, email: draft.email.trim().toLowerCase(), nombre: draft.nombre, role: draft.role }]);
      setDraft({ nombre:'', email:'', role:'manager', password:'' });
      setInviting(false);
    } catch (e) {
      setErr(e.message || 'No se pudo invitar');
    }
  };

  const remove = async (uid) => {
    if (!confirm('¿Quitar acceso a este usuario?')) return;
    const prev = users;
    setUsers(xs => xs.filter(u => u.id !== uid));
    try {
      await window.api.del(`/usuarios/${uid}`);
    } catch (e) {
      alert('No se pudo quitar: ' + (e.message || e));
      setUsers(prev);
    }
  };

  const rolEs = (r) => r === 'owner' ? 'Propietario' : r === 'manager' ? 'Gestor' : 'Sólo lectura';

  return (
    <Card className="overflow-hidden max-w-3xl">
      <ul className="divide-y divide-slate-100 dark:divide-slate-800">
        {loading && <li className="p-4 text-sm text-slate-500">Cargando…</li>}
        {!loading && users.length === 0 && <li className="p-4 text-sm text-slate-500">No hay usuarios aún.</li>}
        {users.map((u) => (
          <li key={u.id} className="p-4 flex items-center gap-3">
            <Avatar name={u.nombre || u.email} color="#475569"/>
            <div className="flex-1 min-w-0">
              <div className="text-sm font-medium">{u.nombre || u.email}</div>
              <div className="text-xs text-slate-500 truncate">{u.email}</div>
            </div>
            <span className="text-xs px-2 py-0.5 rounded-full bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300">{rolEs(u.role)}</span>
            {soyOwner && u.role !== 'owner' && (
              <button onClick={()=>remove(u.id)} className="text-xs text-slate-500 hover:text-rose-600">Quitar</button>
            )}
          </li>
        ))}
      </ul>
      {soyOwner && (
        <div className="p-4 border-t border-slate-100 dark:border-slate-800">
          {!inviting && (
            <button onClick={()=>setInviting(true)} className="text-sm flex items-center gap-2 text-brand-700 hover:underline">
              <Icon d={ICONS.plus} cls="w-4 h-4"/> Invitar a alguien
            </button>
          )}
          {inviting && (
            <div className="space-y-3">
              <div className="text-sm font-medium">Nuevo acceso</div>
              <div className="grid md:grid-cols-2 gap-3">
                <Field label="Nombre" value={draft.nombre} onChange={v=>setDraft({...draft,nombre:v})}/>
                <Field label="Email"  value={draft.email}  onChange={v=>setDraft({...draft,email:v})}/>
                <Field label="Contraseña inicial" type="password" value={draft.password} onChange={v=>setDraft({...draft,password:v})}/>
                <label className="block">
                  <div className="text-xs text-slate-500 mb-1">Rol</div>
                  <select value={draft.role} onChange={e=>setDraft({...draft,role:e.target.value})}
                    className="w-full px-3 py-2 text-sm rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900">
                    <option value="manager">Gestor</option>
                    <option value="readonly">Sólo lectura</option>
                    <option value="owner">Propietario</option>
                  </select>
                </label>
              </div>
              {err && <div className="text-xs text-rose-700">{err}</div>}
              <div className="flex gap-2">
                <button onClick={()=>{setInviting(false); setErr(null);}} className="text-sm px-3 py-2 rounded-lg border border-slate-200 dark:border-slate-700">Cancelar</button>
                <button onClick={invite} className="text-sm px-3 py-2 rounded-lg bg-brand-600 hover:bg-brand-700 text-white">Invitar</button>
              </div>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

function TabCuenta({ user }) {
  const logout = async () => {
    try {
      await fetch('/app/logout', { method:'POST', credentials:'same-origin' });
    } catch (e) { /* ignore */ }
    window.location.href = '/app/login';
  };
  return (
    <Card className="p-5 max-w-lg space-y-3">
      <Field label="Tu nombre" value={user.nombre} readOnly/>
      <Field label="Email"     value={user.email}  readOnly/>
      <div className="text-xs text-slate-500">
        Para cambiar tu contraseña, pide al propietario que te la reinicie desde la pestaña <b>Usuarios</b>.
      </div>
      <div className="pt-4 border-t border-slate-100 dark:border-slate-800">
        <button onClick={logout} className="text-sm text-rose-600 hover:underline">Cerrar sesión</button>
      </div>
    </Card>
  );
}

function Field({label, value, onChange, type='text', readOnly=false}) {
  return (
    <label className="block">
      <div className="text-xs text-slate-500 mb-1">{label}</div>
      <input
        type={type}
        value={value ?? ''}
        readOnly={readOnly}
        onChange={onChange ? (e=>onChange(e.target.value)) : undefined}
        className={`w-full px-3 py-2 text-sm rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 focus:border-brand-500 focus:outline-none ${readOnly?'opacity-60':''}`}
      />
    </label>
  );
}

window.ScreenAjustes = ScreenAjustes;
