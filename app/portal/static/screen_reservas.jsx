// Pantalla Reservas — semana + lista, con alta manual + asistente IA (voz/texto)

function ScreenReservas({ initialReserva, onCloseDetalle }) {
  const [view, setView] = useState('semana');
  const [detalle, setDetalle] = useState(initialReserva || null);
  const [filtroCanal, setFiltroCanal] = useState('todos');
  const [filtroEquipo, setFiltroEquipo] = useState('todos');
  const [nueva, setNueva] = useState(null); // {mode:'manual'|'ia', draft?}
  const [reservas, setReservas] = useState(RESERVAS);

  const WEEK = ['2026-04-20','2026-04-21','2026-04-22','2026-04-23','2026-04-24','2026-04-25','2026-04-26'];
  const DIAS = ['L 20','M 21','X 22','J 23','V 24','S 25','D 26'];

  const reservasFiltradas = reservas.filter(r => {
    if (filtroCanal !== 'todos' && r.canal !== filtroCanal) return false;
    if (filtroEquipo !== 'todos' && r.equipo !== filtroEquipo) return false;
    return true;
  });
  const reservasOrdenadas = [...reservasFiltradas].sort((a,b)=>(a.fecha+a.hora).localeCompare(b.fecha+b.hora));

  const timeToY = h => { const [hh,mm] = h.split(':').map(Number); return (hh-9)*60+mm; };

  const saveReserva = async (r) => {
    try {
      const res = await window.api.post('/reservas', r);
      setReservas(xs => [...xs, { ...r, id: res.id || ('r_' + Date.now()), estado: 'confirmada' }]);
      setNueva(null);
    } catch (e) {
      alert('No se pudo crear la reserva: ' + (e.message || e));
    }
  };

  const cancelarReserva = async (id) => {
    if (!confirm('¿Cancelar esta reserva?')) return;
    const prev = reservas;
    setReservas(xs => xs.map(r => r.id === id ? { ...r, estado: 'cancelada' } : r));
    try { await window.api.del(`/reservas/${encodeURIComponent(id)}`); setDetalle(null); onCloseDetalle && onCloseDetalle(); }
    catch (e) { setReservas(prev); alert('No se pudo cancelar: ' + (e.message || e)); }
  };

  const moverReserva = async (id) => {
    const r = reservas.find(x => x.id === id);
    if (!r) return;
    const fecha = prompt('Nueva fecha (YYYY-MM-DD)', r.fecha); if (!fecha) return;
    const hora  = prompt('Nueva hora (HH:MM)',   r.hora);  if (!hora)  return;
    const prev = reservas;
    setReservas(xs => xs.map(x => x.id === id ? { ...x, fecha, hora, estado: 'movida' } : x));
    try { await window.api.patch(`/reservas/${encodeURIComponent(id)}`, { fecha, hora, duracion: r.duracion }); }
    catch (e) { setReservas(prev); alert('No se pudo mover: ' + (e.message || e)); }
  };

  return (
    <>
      <div className="mb-4 flex items-end justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl md:text-2xl font-bold tracking-tight">Reservas</h1>
          <p className="text-slate-500 dark:text-slate-400 text-sm mt-1">Semana del 20 al 26 de abril</p>
        </div>
        <div className="flex items-center gap-2">
          <div className="inline-flex rounded-lg border border-slate-200 dark:border-slate-700 p-0.5 bg-white dark:bg-slate-900">
            <button onClick={()=>setView('semana')} className={`text-xs px-3 py-1.5 rounded-md ${view==='semana'?'bg-slate-100 dark:bg-slate-800 font-medium':'text-slate-500'}`}>Semana</button>
            <button onClick={()=>setView('lista')}  className={`text-xs px-3 py-1.5 rounded-md ${view==='lista'?'bg-slate-100 dark:bg-slate-800 font-medium':'text-slate-500'}`}>Lista</button>
          </div>
          <button onClick={()=>setNueva({mode:'manual'})}
            className="flex items-center gap-2 bg-brand-600 hover:bg-brand-700 text-white text-sm font-medium px-3 py-2 rounded-lg">
            <Icon d={ICONS.plus} cls="w-4 h-4"/> Nueva
          </button>
        </div>
      </div>

      <div className="flex items-center gap-2 mb-4 flex-wrap">
        <select value={filtroCanal} onChange={e=>setFiltroCanal(e.target.value)}
          className="text-xs px-3 py-1.5 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900">
          <option value="todos">Todos los canales</option>
          <option value="voz">Voz</option>
          <option value="manual">Manual</option>
        </select>
        <select value={filtroEquipo} onChange={e=>setFiltroEquipo(e.target.value)}
          className="text-xs px-3 py-1.5 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900">
          <option value="todos">Todo el equipo</option>
          {EQUIPO.map(m=><option key={m.id} value={m.id}>{m.nombre}</option>)}
        </select>
      </div>

      <Card className="overflow-hidden mb-4">
        <div className="px-4 py-3 border-b border-slate-100 dark:border-slate-800 flex items-center justify-between gap-3">
          <div>
            <div className="text-sm font-semibold">Lista de reservas</div>
            <div className="text-xs text-slate-500 dark:text-slate-400">Misma vista rápida que en el CMS, filtrada para este negocio</div>
          </div>
          <div className="text-xs text-slate-400">{reservasOrdenadas.length} reservas</div>
        </div>
        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead className="bg-slate-50 dark:bg-slate-800/50 text-slate-500 dark:text-slate-400">
              <tr>
                <th className="text-left font-medium px-4 py-3">Fecha</th>
                <th className="text-left font-medium px-4 py-3">Cliente</th>
                <th className="text-left font-medium px-4 py-3">Servicio</th>
                <th className="text-left font-medium px-4 py-3">Canal</th>
                <th className="text-left font-medium px-4 py-3">Estado</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {reservasOrdenadas.map(r => {
                const s = servicioDe(r.servicio);
                const estadoChip = r.estado==='cancelada'?'bg-rose-50 text-rose-700 dark:bg-rose-900/20 dark:text-rose-300':r.estado==='movida'?'bg-amber-50 text-amber-700 dark:bg-amber-900/20 dark:text-amber-300':'bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300';
                return (
                  <tr key={`table_${r.id}`} onClick={()=>setDetalle(r)} className="hover:bg-slate-50 dark:hover:bg-slate-800/40 cursor-pointer">
                    <td className="px-4 py-3 whitespace-nowrap">
                      <div className="font-medium text-slate-900 dark:text-slate-100">{r.fecha} · {r.hora}</div>
                      <div className="text-xs text-slate-500 dark:text-slate-400">{r.duracion} min</div>
                    </td>
                    <td className="px-4 py-3">
                      <div className="font-medium text-slate-900 dark:text-slate-100">{r.cliente}</div>
                      <div className="text-xs text-slate-500 dark:text-slate-400">{r.telefono || 'Sin teléfono'}</div>
                    </td>
                    <td className="px-4 py-3 text-slate-900 dark:text-slate-100">{s?.nombre || '—'}</td>
                    <td className="px-4 py-3"><CanalBadge canal={r.canal}/></td>
                    <td className="px-4 py-3">
                      <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${estadoChip}`}>{r.estado}</span>
                    </td>
                  </tr>
                );
              })}
              {!reservasOrdenadas.length && (
                <tr>
                  <td colSpan="5" className="px-4 py-10 text-center text-sm text-slate-400">No hay reservas con los filtros actuales.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>

      {view === 'semana' ? (
        <Card className="overflow-hidden">
          <div className="grid grid-cols-[60px_repeat(7,1fr)] border-b border-slate-100 dark:border-slate-800 sticky top-0 bg-white dark:bg-slate-900 z-10">
            <div></div>
            {DIAS.map((d,i) => (
              <div key={i} className={`px-2 py-3 text-center text-xs border-l border-slate-100 dark:border-slate-800 ${WEEK[i]===HOY_ISO?'text-brand-700 font-semibold':'text-slate-500'}`}>
                {WEEK[i]===HOY_ISO && <div className="text-[9px] uppercase tracking-wider text-brand-600">Hoy</div>}
                {d}
              </div>
            ))}
          </div>
          <div className="relative overflow-x-auto">
            <div className="grid grid-cols-[60px_repeat(7,1fr)]" style={{height:'720px'}}>
              <div className="relative border-r border-slate-100 dark:border-slate-800">
                {Array.from({length:12},(_,h)=>(
                  <div key={h} className="absolute left-0 right-0 text-[10px] text-slate-400 pr-2 text-right" style={{top:`${h*60}px`}}>
                    {String(9+h).padStart(2,'0')}:00
                  </div>
                ))}
              </div>
              {WEEK.map((dia) => (
                <div key={dia} className={`relative border-l border-slate-100 dark:border-slate-800 ${dia===HOY_ISO?'bg-emerald-50/20 dark:bg-emerald-900/5':''}`}>
                  {Array.from({length:12},(_,h)=>(
                    <div key={h} className="absolute left-0 right-0 border-t border-slate-50 dark:border-slate-800/50" style={{top:`${h*60}px`}}/>
                  ))}
                  {reservasFiltradas.filter(r=>r.fecha===dia).map(r=>{
                    const m = miembroDe(r.equipo);
                    const s = servicioDe(r.servicio);
                    const y = timeToY(r.hora);
                    const cancelada = r.estado==='cancelada';
                    return (
                      <div key={r.id} onClick={()=>setDetalle(r)}
                        className={`absolute left-1 right-1 rounded px-1.5 py-1 text-[10px] cursor-pointer hover:opacity-90 overflow-hidden border-l-2 ${cancelada?'opacity-50 line-through':''}`}
                        style={{top:`${y}px`, height:`${r.duracion}px`, background:`${m?.color}15`, borderLeftColor:m?.color, color:m?.color}}>
                        <div className="font-semibold leading-tight truncate">{r.hora} {r.cliente}</div>
                        <div className="text-slate-600 dark:text-slate-300 truncate">{s?.nombre}</div>
                      </div>
                    );
                  })}
                </div>
              ))}
            </div>
          </div>
        </Card>
      ) : (
        <Card className="overflow-hidden">
          <ul className="divide-y divide-slate-100 dark:divide-slate-800">
            {reservasOrdenadas.map(r => {
              const s = servicioDe(r.servicio);
              const m = miembroDe(r.equipo);
              const estadoChip = r.estado==='cancelada'?'bg-rose-50 text-rose-700':r.estado==='movida'?'bg-amber-50 text-amber-700':'bg-slate-100 text-slate-600';
              return (
                <li key={r.id} onClick={()=>setDetalle(r)} className="p-4 flex items-center gap-4 hover:bg-slate-50 dark:hover:bg-slate-800/50 cursor-pointer">
                  <div className="w-16 text-center shrink-0">
                    <div className="text-[10px] uppercase text-slate-400">{r.fecha.slice(-5).replace('-','/')}</div>
                    <div className="text-base font-semibold tabular-nums">{r.hora}</div>
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <div className="text-sm font-medium truncate">{r.cliente}</div>
                      <CanalBadge canal={r.canal}/>
                      <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${estadoChip} dark:bg-opacity-20`}>{r.estado}</span>
                    </div>
                    <div className="text-xs text-slate-500 dark:text-slate-400 truncate">{s?.nombre} · {r.duracion}′ · con {m?.nombre}</div>
                  </div>
                  <div className="text-sm font-medium tabular-nums hidden sm:block">{eur(s?.precio||0)}</div>
                </li>
              );
            })}
          </ul>
        </Card>
      )}

      {/* Detalle reserva */}
      {detalle && (
        <div className="fixed inset-0 z-30 flex justify-end">
          <div className="absolute inset-0 bg-slate-900/40" onClick={()=>{setDetalle(null); onCloseDetalle && onCloseDetalle();}}/>
          <div className="relative w-full sm:max-w-md bg-white dark:bg-slate-900 flex flex-col slide-in-right shadow-xl">
            <div className="p-5 border-b border-slate-100 dark:border-slate-800 flex items-center justify-between">
              <div className="text-sm font-semibold">Reserva</div>
              <button onClick={()=>{setDetalle(null); onCloseDetalle && onCloseDetalle();}} className="p-1 text-slate-500"><Icon d={ICONS.close}/></button>
            </div>
            <div className="p-5 space-y-5 flex-1 overflow-auto">
              <div>
                <div className="text-2xl font-semibold tracking-tight">{detalle.cliente}</div>
                <div className="text-sm text-slate-500 mt-1">{detalle.telefono}</div>
              </div>
              <div className="flex items-center gap-2 flex-wrap">
                <CanalBadge canal={detalle.canal}/>
                <span className="text-xs px-2 py-0.5 rounded bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300">{detalle.estado}</span>
              </div>
              <div className="grid grid-cols-2 gap-3 text-sm">
                <div><div className="text-xs text-slate-500 mb-1">Fecha</div><div className="font-medium">{detalle.fecha.slice(-5).replace('-','/')}</div></div>
                <div><div className="text-xs text-slate-500 mb-1">Hora</div><div className="font-medium tabular-nums">{detalle.hora} · {detalle.duracion}′</div></div>
                <div><div className="text-xs text-slate-500 mb-1">Servicio</div><div className="font-medium">{servicioDe(detalle.servicio)?.nombre}</div></div>
                <div><div className="text-xs text-slate-500 mb-1">Precio</div><div className="font-medium">{eur(servicioDe(detalle.servicio)?.precio||0)}</div></div>
                <div className="col-span-2">
                  <div className="text-xs text-slate-500 mb-1">Con</div>
                  <div className="flex items-center gap-2">
                    <Avatar name={miembroDe(detalle.equipo)?.nombre} color={miembroDe(detalle.equipo)?.color} size="sm"/>
                    <span className="font-medium">{miembroDe(detalle.equipo)?.nombre}</span>
                  </div>
                </div>
              </div>
              {detalle.canal !== 'manual' && (
                <button className="w-full text-sm flex items-center justify-center gap-2 py-2 rounded-lg border border-slate-200 dark:border-slate-700 hover:bg-slate-50 dark:hover:bg-slate-800">
                  <Icon d={ICONS.conversaciones} cls="w-4 h-4"/> Ver conversación que la generó
                </button>
              )}
            </div>
            <div className="p-4 border-t border-slate-100 dark:border-slate-800 flex items-center gap-2">
              <button onClick={()=>moverReserva(detalle.id)} className="flex-1 text-sm py-2 rounded-lg border border-slate-200 dark:border-slate-700 hover:bg-slate-50 dark:hover:bg-slate-800">Mover</button>
              <button onClick={()=>{setNueva({mode:'manual', draft:detalle}); setDetalle(null);}} className="flex-1 text-sm py-2 rounded-lg border border-slate-200 dark:border-slate-700 hover:bg-slate-50 dark:hover:bg-slate-800">Editar</button>
              <button onClick={()=>cancelarReserva(detalle.id)} className="flex-1 text-sm py-2 rounded-lg border border-rose-200 text-rose-700 hover:bg-rose-50 dark:border-rose-900/40 dark:hover:bg-rose-900/20">Cancelar</button>
            </div>
          </div>
        </div>
      )}

      {/* Modal alta — manual + IA */}
      {nueva && <NuevaReservaModal state={nueva} onState={setNueva} onSave={saveReserva} onClose={()=>setNueva(null)}/>}
    </>
  );
}

// ─────────────────────────────────────────────
// Modal unificado de nueva reserva
// ─────────────────────────────────────────────
function NuevaReservaModal({ state, onState, onSave, onClose }) {
  const [draft, setDraft] = useState(state.draft || {
    cliente:'', telefono:'', servicio:SERVICIOS[0].id, equipo:EQUIPO[0].id,
    fecha:HOY_ISO, hora:'10:00', duracion:SERVICIOS[0].duracion, canal:'manual',
  });
  const [mode, setMode] = useState(state.mode);

  // Cuando el asistente IA devuelve un borrador, lo recibimos aquí
  useEffect(()=>{ if (state.draft) { setDraft(d=>({...d, ...state.draft})); setMode('manual'); } }, [state.draft]);

  const setServicio = id => {
    const s = servicioDe(id);
    setDraft(d=>({...d, servicio:id, duracion:s?.duracion || d.duracion}));
  };

  const valido = draft.cliente.trim() && draft.telefono.trim();

  return (
    <div className="fixed inset-0 z-40 flex items-end sm:items-center justify-center">
      <div className="absolute inset-0 bg-slate-900/40" onClick={onClose}/>
      <div className="relative w-full sm:max-w-lg bg-white dark:bg-slate-900 rounded-t-2xl sm:rounded-2xl shadow-xl flex flex-col max-h-[95vh]">
        {/* Cabecera con toggle */}
        <div className="p-4 md:p-5 border-b border-slate-100 dark:border-slate-800 flex items-center gap-3">
          <div className="flex-1 min-w-0">
            <div className="text-base font-semibold">Nueva reserva</div>
            <div className="text-xs text-slate-500 mt-0.5">{mode==='ia'?'Dicta o pega los datos, la IA los extrae':'Rellena los campos'}</div>
          </div>
          <div className="inline-flex rounded-lg border border-slate-200 dark:border-slate-700 p-0.5 bg-slate-50 dark:bg-slate-800">
            <button onClick={()=>setMode('manual')}
              className={`text-xs px-2.5 py-1.5 rounded-md flex items-center gap-1.5 ${mode==='manual'?'bg-white dark:bg-slate-900 font-medium shadow-sm':'text-slate-500'}`}>
              <Icon d={ICONS.edit} cls="w-3.5 h-3.5"/> Manual
            </button>
            <button onClick={()=>setMode('ia')}
              className={`text-xs px-2.5 py-1.5 rounded-md flex items-center gap-1.5 ${mode==='ia'?'bg-white dark:bg-slate-900 font-medium shadow-sm text-brand-700':'text-slate-500'}`}>
              <Icon d={ICONS.sparkles} cls="w-3.5 h-3.5"/> Con IA
            </button>
          </div>
          <button onClick={onClose} className="p-1 text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800 rounded"><Icon d={ICONS.close}/></button>
        </div>

        <div className="flex-1 overflow-auto">
          {mode === 'ia' ? (
            <IAReservaPanel onParsed={(parsed)=>{ setDraft(d=>({...d, ...parsed, canal:'manual'})); setMode('manual'); }}/>
          ) : (
            <ManualForm draft={draft} setDraft={setDraft} setServicio={setServicio}/>
          )}
        </div>

        {mode==='manual' && (
          <div className="p-4 border-t border-slate-100 dark:border-slate-800 flex items-center gap-2">
            <button onClick={onClose} className="flex-1 text-sm py-2 rounded-lg border border-slate-200 dark:border-slate-700">Cancelar</button>
            <button onClick={()=>valido && onSave(draft)} disabled={!valido}
              className="flex-1 text-sm py-2 rounded-lg bg-brand-600 hover:bg-brand-700 text-white disabled:opacity-40">
              Crear reserva
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────
// Formulario manual
// ─────────────────────────────────────────────
function ManualForm({ draft, setDraft, setServicio }) {
  const inputCls = "w-full px-3 py-2 text-sm rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 focus:border-brand-500 focus:outline-none";
  return (
    <div className="p-4 md:p-5 space-y-3">
      <div className="grid grid-cols-2 gap-3">
        <label className="block col-span-2">
          <div className="text-xs text-slate-500 mb-1">Cliente</div>
          <input className={inputCls} value={draft.cliente} onChange={e=>setDraft({...draft, cliente:e.target.value})} placeholder="Nombre y apellido"/>
        </label>
        <label className="block col-span-2">
          <div className="text-xs text-slate-500 mb-1">Teléfono</div>
          <input className={inputCls} value={draft.telefono} onChange={e=>setDraft({...draft, telefono:e.target.value})} placeholder="+34 …"/>
        </label>
        <label className="block col-span-2">
          <div className="text-xs text-slate-500 mb-1">Servicio</div>
          <select className={inputCls} value={draft.servicio} onChange={e=>setServicio(e.target.value)}>
            {SERVICIOS.filter(s=>s.activo).map(s=><option key={s.id} value={s.id}>{s.nombre} · {s.duracion}′ · {eur(s.precio)}</option>)}
          </select>
        </label>
        <label className="block col-span-2">
          <div className="text-xs text-slate-500 mb-1">Con</div>
          <div className="flex gap-1.5 flex-wrap">
            {EQUIPO.filter(m=>servicioDe(draft.servicio)?.equipo.includes(m.id)).map(m=>(
              <button key={m.id} onClick={()=>setDraft({...draft, equipo:m.id})}
                className={`inline-flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-lg border ${draft.equipo===m.id?'border-brand-500 bg-brand-50 dark:bg-brand-900/20 text-brand-700':'border-slate-200 dark:border-slate-700'}`}>
                <span className="w-2 h-2 rounded-full" style={{background:m.color}}/>{m.nombre}
              </button>
            ))}
          </div>
        </label>
        <label className="block">
          <div className="text-xs text-slate-500 mb-1">Fecha</div>
          <input type="date" className={inputCls + ' tabular-nums'} value={draft.fecha} onChange={e=>setDraft({...draft, fecha:e.target.value})}/>
        </label>
        <label className="block">
          <div className="text-xs text-slate-500 mb-1">Hora</div>
          <input type="time" className={inputCls + ' tabular-nums'} value={draft.hora} onChange={e=>setDraft({...draft, hora:e.target.value})}/>
        </label>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────
// Asistente IA: voz o texto → borrador de reserva
// ─────────────────────────────────────────────
function IAReservaPanel({ onParsed }) {
  const [input, setInput] = useState('');
  const [recording, setRecording] = useState(false);
  const [recTime, setRecTime] = useState(0);
  const [parsing, setParsing] = useState(false);
  const [parsed, setParsed] = useState(null);
  const [error, setError] = useState(null);

  // Timer de "grabación" mock
  useEffect(()=>{
    if (!recording) return;
    const t = setInterval(()=>setRecTime(x=>x+1), 1000);
    return ()=>clearInterval(t);
  }, [recording]);

  const startRec = () => { setRecTime(0); setRecording(true); setError(null); };
  const stopRec = () => {
    setRecording(false);
    // mock: transcripción
    const mock = 'Ponme una cita el viernes a las once y media para Ana García, teléfono 612345678, para un corte de mujer con Laura.';
    setInput(mock);
    runParse(mock);
  };

  const runParse = async (text) => {
    if (!text.trim()) return;
    setParsing(true); setError(null); setParsed(null);
    try {
      const prompt = `Extrae los datos de una reserva de peluquería del siguiente texto en español. Devuelve SOLO un JSON válido con estos campos exactos:
{
  "cliente": "nombre completo",
  "telefono": "con prefijo +34 si aplica, solo dígitos y espacios",
  "servicio_nombre": "nombre literal del servicio",
  "equipo_nombre": "nombre de la persona, o null",
  "fecha": "YYYY-MM-DD (hoy=${HOY_ISO})",
  "hora": "HH:MM (24h)"
}
Texto: """${text}"""
Servicios disponibles: ${SERVICIOS.map(s=>s.nombre).join(', ')}.
Equipo: ${EQUIPO.map(m=>m.nombre).join(', ')}.
Si falta un dato, pon null. NO incluyas explicaciones, solo el JSON.`;

      const raw = await window.claude.complete(prompt);
      const jsonMatch = raw.match(/\{[\s\S]*\}/);
      if (!jsonMatch) throw new Error('no JSON');
      const data = JSON.parse(jsonMatch[0]);

      // Mapear a IDs del sistema
      const svc = SERVICIOS.find(s => s.nombre.toLowerCase() === (data.servicio_nombre||'').toLowerCase())
               || SERVICIOS.find(s => (data.servicio_nombre||'').toLowerCase().includes(s.nombre.toLowerCase().split(' ')[0]));
      const mem = EQUIPO.find(m => m.nombre.toLowerCase() === (data.equipo_nombre||'').toLowerCase());

      setParsed({
        cliente: data.cliente || '',
        telefono: data.telefono || '',
        servicio: svc?.id || SERVICIOS[0].id,
        equipo: mem?.id || (svc?.equipo[0] || EQUIPO[0].id),
        fecha: data.fecha || HOY_ISO,
        hora: data.hora || '10:00',
        duracion: svc?.duracion || 30,
        _servicio_nombre: data.servicio_nombre,
        _equipo_nombre: data.equipo_nombre,
      });
    } catch (e) {
      setError('No he podido entender los datos. Prueba con más detalle (nombre, teléfono, servicio, fecha y hora).');
    } finally {
      setParsing(false);
    }
  };

  const mmss = s => `${String(Math.floor(s/60)).padStart(2,'0')}:${String(s%60).padStart(2,'0')}`;

  return (
    <div className="p-4 md:p-5 space-y-4">
      {/* Grabación */}
      <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-gradient-to-br from-slate-50 to-white dark:from-slate-800/40 dark:to-slate-900 p-4">
        <div className="text-xs text-slate-500 mb-3 flex items-center gap-1.5">
          <Icon d={ICONS.sparkles} cls="w-3.5 h-3.5 text-brand-600"/> Dicta la reserva y la IA la añade al calendario
        </div>
        <div className="flex items-center gap-3">
          {!recording ? (
            <button onClick={startRec} disabled={parsing}
              className="w-12 h-12 shrink-0 rounded-full bg-brand-600 hover:bg-brand-700 text-white flex items-center justify-center shadow-sm disabled:opacity-40">
              <Icon d={ICONS.mic} cls="w-5 h-5"/>
            </button>
          ) : (
            <button onClick={stopRec} className="w-12 h-12 shrink-0 rounded-full bg-rose-500 text-white flex items-center justify-center shadow-sm rec-pulse">
              <div className="w-4 h-4 bg-white rounded-sm"/>
            </button>
          )}
          <div className="flex-1 min-w-0">
            {recording ? (
              <>
                <div className="text-sm font-medium">Escuchando…</div>
                <div className="text-xs text-slate-500 tabular-nums">{mmss(recTime)}</div>
              </>
            ) : (
              <>
                <div className="text-sm font-medium">Pulsa para hablar</div>
                <div className="text-xs text-slate-500">p.ej. "Ana García mañana a las 11 corte con Laura"</div>
              </>
            )}
          </div>
          {recording && (
            <div className="flex items-end gap-0.5 h-6">
              {[0,1,2,3,4].map(i=><div key={i} className="w-1 bg-rose-400 rounded-full wave-bar" style={{animationDelay:`${i*0.12}s`}}/>)}
            </div>
          )}
        </div>
      </div>

      {/* Separador */}
      <div className="flex items-center gap-3 text-[10px] uppercase text-slate-400 tracking-wider">
        <div className="flex-1 h-px bg-slate-200 dark:bg-slate-800"/> o pega / escribe <div className="flex-1 h-px bg-slate-200 dark:bg-slate-800"/>
      </div>

      {/* Texto libre */}
      <div>
        <textarea value={input} onChange={e=>setInput(e.target.value)} rows={3}
          placeholder="Ej: Reserva para Carlos Pérez, 612 345 678, el viernes 25 a las 10:00, corte hombre con Mario."
          className="w-full px-3 py-2.5 text-sm rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 focus:border-brand-500 focus:outline-none resize-none"/>
        <div className="flex justify-end mt-2">
          <button onClick={()=>runParse(input)} disabled={!input.trim() || parsing}
            className="flex items-center gap-2 text-sm font-medium px-3 py-2 rounded-lg bg-slate-900 dark:bg-white text-white dark:text-slate-900 disabled:opacity-40">
            {parsing ? <><div className="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin"/> Procesando…</> : <><Icon d={ICONS.sparkles} cls="w-4 h-4"/> Extraer datos</>}
          </button>
        </div>
      </div>

      {error && (
        <div className="p-3 rounded-lg bg-rose-50 dark:bg-rose-900/20 border border-rose-200 dark:border-rose-900/40 text-sm text-rose-700 dark:text-rose-300">{error}</div>
      )}

      {/* Vista previa parseado */}
      {parsed && (
        <div className="rounded-xl border-2 border-brand-200 dark:border-brand-900/40 bg-brand-50/50 dark:bg-brand-900/10 p-4 space-y-3 fade-in">
          <div className="flex items-center gap-2 text-xs font-medium text-brand-700">
            <Icon d={ICONS.check} cls="w-4 h-4"/> Datos detectados
          </div>
          <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
            <dt className="text-slate-500">Cliente</dt><dd className="font-medium">{parsed.cliente || <span className="text-rose-600">—</span>}</dd>
            <dt className="text-slate-500">Teléfono</dt><dd className="font-medium tabular-nums">{parsed.telefono || <span className="text-rose-600">—</span>}</dd>
            <dt className="text-slate-500">Servicio</dt><dd className="font-medium">{servicioDe(parsed.servicio)?.nombre}</dd>
            <dt className="text-slate-500">Con</dt><dd className="font-medium">{miembroDe(parsed.equipo)?.nombre}</dd>
            <dt className="text-slate-500">Fecha</dt><dd className="font-medium tabular-nums">{parsed.fecha}</dd>
            <dt className="text-slate-500">Hora</dt><dd className="font-medium tabular-nums">{parsed.hora}</dd>
          </dl>
          <div className="flex gap-2 pt-1">
            <button onClick={()=>setParsed(null)} className="flex-1 text-sm py-2 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900">Volver a intentar</button>
            <button onClick={()=>onParsed(parsed)} className="flex-1 text-sm py-2 rounded-lg bg-brand-600 hover:bg-brand-700 text-white font-medium">Revisar y crear</button>
          </div>
        </div>
      )}
    </div>
  );
}

window.ScreenReservas = ScreenReservas;
