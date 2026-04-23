// Pantalla Llamadas · lista las llamadas del bot de voz (ElevenLabs).
// Sustituye a la antigua `ScreenConversaciones` tras eliminar el canal WhatsApp.

function ScreenLlamadas() {
  const [q, setQ] = useState('');
  const [soloReserva, setSoloReserva] = useState(false);
  const [openId, setOpenId] = useState(null);
  const list = LLAMADAS;
  const filtered = list.filter(c => {
    if (soloReserva && !c.reserva) return false;
    if (!q) return true;
    const s = q.toLowerCase();
    return c.telefono.toLowerCase().includes(s) || (c.nombre||'').toLowerCase().includes(s);
  });

  return (
    <>
      <div className="mb-4">
        <h1 className="text-xl md:text-2xl font-bold tracking-tight">Llamadas</h1>
        <p className="text-slate-500 dark:text-slate-400 text-sm mt-1">Lo que el bot ha hablado por teléfono con tus clientes</p>
      </div>

      <div className="flex items-center gap-2 mb-3 flex-wrap">
        <div className="relative flex-1 min-w-[160px] max-w-xs">
          <Icon d={ICONS.search} cls="w-4 h-4 absolute left-3 top-2.5 text-slate-400"/>
          <input value={q} onChange={e=>setQ(e.target.value)} placeholder="Buscar por nombre o teléfono…"
            className="w-full pl-9 pr-3 py-2 text-sm rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 focus:border-brand-500 focus:outline-none"/>
        </div>
        <button onClick={()=>setSoloReserva(v=>!v)}
          className={`text-xs px-2.5 py-2 rounded-lg border ${soloReserva?'bg-brand-50 border-brand-200 text-brand-700 dark:bg-brand-900/30':'border-slate-200 dark:border-slate-700 text-slate-600 dark:text-slate-300'}`}>
          Con reserva
        </button>
      </div>

      <Card className="overflow-hidden">
        <ul className="divide-y divide-slate-100 dark:divide-slate-800">
          {filtered.map(c => {
            const isOpen = openId === c.id;
            return (
              <li key={c.id}>
                <button onClick={()=>setOpenId(isOpen?null:c.id)}
                  className={`w-full text-left px-4 py-3 flex items-center gap-3 hover:bg-slate-50 dark:hover:bg-slate-800/50 ${isOpen?'bg-slate-50 dark:bg-slate-800/50':''}`}>
                  <Avatar name={c.nombre !== '—' ? c.nombre : c.telefono.slice(-4)} color="#64748b" size="sm"/>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center justify-between gap-2">
                      <div className="text-sm font-medium truncate">{c.nombre !== '—' ? c.nombre : c.telefono}</div>
                      <div className="text-[11px] text-slate-400 shrink-0 tabular-nums">{(c.ultimoAt || '').split('T')[1] || ''}</div>
                    </div>
                    <div className="flex items-center gap-1.5">
                      <div className="text-xs text-slate-500 dark:text-slate-400 truncate flex-1">{c.preview || (c.turnos && c.turnos.slice(-1)[0]?.text) || '—'}</div>
                      {c.reserva && <span className="shrink-0 text-[10px] px-1.5 py-0.5 rounded bg-brand-50 text-brand-700 dark:bg-brand-900/30 dark:text-brand-300 font-medium">reserva</span>}
                      {c.duracion && <span className="shrink-0 text-[10px] text-slate-400 tabular-nums">{c.duracion}</span>}
                    </div>
                  </div>
                  <Icon d={isOpen?ICONS.chevronDown:ICONS.chevronRight} cls="w-4 h-4 text-slate-400 shrink-0"/>
                </button>

                {isOpen && (
                  <div className="px-4 pb-4 bg-slate-50 dark:bg-slate-950/50 fade-in">
                    {c.duracion && (
                      <div className="flex items-center gap-2 py-3 flex-wrap">
                        <button className="flex items-center gap-1.5 text-xs bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 px-2.5 py-1.5 rounded-lg">
                          <Icon d={ICONS.play} cls="w-3 h-3"/> Escuchar ({c.duracion})
                        </button>
                        {c.tools && c.tools.map(t => (
                          <span key={t} className="px-1.5 py-1 rounded bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 font-mono text-[10px]">{t}</span>
                        ))}
                      </div>
                    )}
                    <div className="space-y-2 pt-2">
                      {(c.turnos || []).map((m,i)=>(
                        <div key={i} className={`flex ${m.role==='assistant'?'justify-end':''}`}>
                          <div className={`${m.role==='user'?'chat-bubble-in':'chat-bubble-out'} max-w-[85%] px-3 py-2 text-sm whitespace-pre-wrap`}>
                            {m.text}
                            <div className="text-[10px] text-slate-500 mt-1 text-right tabular-nums">{m.at}</div>
                          </div>
                        </div>
                      ))}
                    </div>
                    {c.reserva && (
                      <div className="mt-3 p-2.5 rounded-lg bg-brand-50 dark:bg-brand-900/20 flex items-center gap-2 text-xs">
                        <Icon d={ICONS.check} cls="w-4 h-4 text-brand-700"/>
                        <div className="flex-1">Terminó creando una reserva.</div>
                        <button className="text-brand-700 font-medium hover:underline">Ver →</button>
                      </div>
                    )}
                  </div>
                )}
              </li>
            );
          })}
          {!filtered.length && <li className="p-10 text-center text-sm text-slate-400">Sin llamadas registradas aún.</li>}
        </ul>
      </Card>
    </>
  );
}

window.ScreenLlamadas = ScreenLlamadas;
