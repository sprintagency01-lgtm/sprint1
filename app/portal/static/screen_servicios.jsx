// Pantalla Servicios — compacta para móvil

function ScreenServicios() {
  const [items, setItems] = useState(SERVICIOS);
  const [editing, setEditing] = useState(null);

  const toggle = async (id) => {
    const s = items.find(x => x.id === id);
    if (!s) return;
    const next = !s.activo;
    setItems(xs => xs.map(x => x.id === id ? { ...x, activo: next } : x));
    try { await window.api.patch(`/servicios/${id}`, { activo: next }); }
    catch (e) { console.warn('toggle servicio failed', e); setItems(xs => xs.map(x => x.id === id ? { ...x, activo: !next } : x)); }
  };

  const remove = async (id) => {
    const prev = items;
    setItems(xs => xs.filter(s => s.id !== id));
    try { await window.api.del(`/servicios/${id}`); }
    catch (e) { console.warn('remove servicio failed', e); setItems(prev); alert('No se pudo borrar: ' + (e.message || e)); }
  };

  const save = async (data) => {
    try {
      if (editing.id) {
        await window.api.patch(`/servicios/${editing.id}`, data);
        setItems(xs => xs.map(s => s.id === editing.id ? { ...s, ...data } : s));
      } else {
        const res = await window.api.post('/servicios', data);
        setItems(xs => [...xs, { ...data, id: String(res.id) }]);
      }
      setEditing(null);
    } catch (e) {
      alert('No se pudo guardar: ' + (e.message || e));
    }
  };

  return (
    <>
      <div className="mb-4 flex items-end justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl md:text-2xl font-bold tracking-tight">Servicios y precios</h1>
          <p className="text-slate-500 dark:text-slate-400 text-sm mt-1">Lo que tu bot ofrece y cuánto cuesta</p>
        </div>
        <button onClick={()=>setEditing({id:null,nombre:'',duracion:30,precio:0,equipo:[],activo:true})}
          className="flex items-center gap-2 bg-brand-600 hover:bg-brand-700 text-white text-sm font-medium px-3 py-2 rounded-lg">
          <Icon d={ICONS.plus} cls="w-4 h-4"/> Nuevo
        </button>
      </div>

      <Card className="overflow-hidden">
        {/* Header desktop */}
        <div className="hidden md:grid grid-cols-[1fr_80px_90px_1fr_70px_70px] gap-4 px-5 py-3 text-[11px] uppercase tracking-wider text-slate-400 border-b border-slate-100 dark:border-slate-800">
          <div>Servicio</div><div>Duración</div><div>Precio</div><div>Quién</div><div>Activo</div><div></div>
        </div>
        <ul className="divide-y divide-slate-100 dark:divide-slate-800">
          {items.map(s=>(
            <li key={s.id} className={`${s.activo?'':'opacity-60'}`}>
              {/* Móvil compacto */}
              <div className="md:hidden flex items-center gap-3 px-4 py-3">
                <div className="flex-1 min-w-0">
                  <div className="flex items-baseline gap-2">
                    <div className="text-sm font-semibold truncate">{s.nombre}</div>
                    <div className="text-xs text-slate-400 tabular-nums shrink-0">{s.duracion}′</div>
                  </div>
                  <div className="mt-0.5 flex items-center gap-1.5 flex-wrap">
                    <span className="text-sm font-semibold tabular-nums">{eur(s.precio)}</span>
                    <span className="text-slate-300">·</span>
                    {s.equipo.map(eid=>{
                      const m=miembroDe(eid);
                      return m && <span key={eid} className="inline-flex items-center gap-1 text-[10px] text-slate-500"><span className="w-1.5 h-1.5 rounded-full" style={{background:m.color}}/>{m.nombre}</span>;
                    })}
                  </div>
                </div>
                <div className={`tg ${s.activo?'on':''}`} onClick={()=>toggle(s.id)} role="switch" aria-checked={s.activo}/>
                <button onClick={()=>setEditing(s)} className="p-2 text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800 rounded-lg"><Icon d={ICONS.edit} cls="w-4 h-4"/></button>
              </div>
              {/* Desktop tabla */}
              <div className="hidden md:grid grid-cols-[1fr_80px_90px_1fr_70px_70px] gap-4 items-center px-5 py-3.5">
                <div className="text-sm font-medium">{s.nombre}</div>
                <div className="text-sm text-slate-600 dark:text-slate-300 tabular-nums">{s.duracion}′</div>
                <div className="text-sm font-semibold tabular-nums">{eur(s.precio)}</div>
                <div className="flex items-center gap-1 flex-wrap">
                  {s.equipo.map(eid=>{
                    const m=miembroDe(eid);
                    return m && <span key={eid} className="inline-flex items-center gap-1 text-[11px] px-1.5 py-0.5 rounded border border-slate-200 dark:border-slate-700"><span className="w-1.5 h-1.5 rounded-full" style={{background:m.color}}/>{m.nombre}</span>;
                  })}
                </div>
                <div className={`tg ${s.activo?'on':''}`} onClick={()=>toggle(s.id)} role="switch" aria-checked={s.activo}/>
                <div className="flex gap-1 justify-end">
                  <button onClick={()=>setEditing(s)} className="p-1.5 text-slate-500 hover:text-brand-700 hover:bg-slate-100 dark:hover:bg-slate-800 rounded"><Icon d={ICONS.edit} cls="w-4 h-4"/></button>
                  <button onClick={()=>confirm(`¿Borrar ${s.nombre}?`)&&remove(s.id)} className="p-1.5 text-slate-500 hover:text-rose-700 hover:bg-rose-50 dark:hover:bg-rose-900/20 rounded"><Icon d={ICONS.trash} cls="w-4 h-4"/></button>
                </div>
              </div>
            </li>
          ))}
        </ul>
      </Card>

      {editing && (
        <div className="fixed inset-0 z-40 flex items-end sm:items-center justify-center">
          <div className="absolute inset-0 bg-slate-900/40" onClick={()=>setEditing(null)}/>
          <div className="relative w-full sm:max-w-md bg-white dark:bg-slate-900 rounded-t-xl sm:rounded-xl shadow-xl p-5">
            <div className="text-base font-semibold mb-4">{editing.id ? 'Editar servicio' : 'Nuevo servicio'}</div>
            <ServForm init={editing} onCancel={()=>setEditing(null)} onSave={save}/>
          </div>
        </div>
      )}
    </>
  );
}

function ServForm({ init, onCancel, onSave }) {
  const [d, setD] = useState(init);
  return (
    <>
      <div className="space-y-3">
        <label className="block">
          <div className="text-xs text-slate-500 mb-1">Nombre</div>
          <input value={d.nombre} onChange={e=>setD({...d,nombre:e.target.value})} className="w-full px-3 py-2 text-sm rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900"/>
        </label>
        <div className="grid grid-cols-2 gap-3">
          <label className="block">
            <div className="text-xs text-slate-500 mb-1">Duración (min)</div>
            <input type="number" value={d.duracion} onChange={e=>setD({...d,duracion:+e.target.value})} className="w-full px-3 py-2 text-sm rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900"/>
          </label>
          <label className="block">
            <div className="text-xs text-slate-500 mb-1">Precio (€)</div>
            <input type="number" value={d.precio} onChange={e=>setD({...d,precio:+e.target.value})} className="w-full px-3 py-2 text-sm rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900"/>
          </label>
        </div>
        <div>
          <div className="text-xs text-slate-500 mb-2">Lo hace</div>
          <div className="flex gap-2 flex-wrap">
            {EQUIPO.map(m=>{
              const sel = d.equipo.includes(m.id);
              return <button key={m.id} onClick={()=>setD({...d, equipo: sel ? d.equipo.filter(x=>x!==m.id) : [...d.equipo, m.id]})}
                className={`text-xs px-2 py-1 rounded-lg border ${sel?'bg-brand-50 border-brand-200 text-brand-700':'border-slate-200 dark:border-slate-700 text-slate-600 dark:text-slate-300'}`}>
                {m.nombre}
              </button>;
            })}
          </div>
        </div>
      </div>
      <div className="flex gap-2 mt-5">
        <button onClick={onCancel} className="flex-1 text-sm py-2 rounded-lg border border-slate-200 dark:border-slate-700">Cancelar</button>
        <button onClick={()=>d.nombre.trim()&&onSave(d)} className="flex-1 text-sm py-2 rounded-lg bg-brand-600 hover:bg-brand-700 text-white">Guardar</button>
      </div>
    </>
  );
}

window.ScreenServicios = ScreenServicios;
