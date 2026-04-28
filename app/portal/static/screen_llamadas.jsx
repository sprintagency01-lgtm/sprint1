// Pantalla Historial de conversaciones del portal cliente.
// Reutiliza el slot histórico de `Llamadas` pero ya muestra Voz + Telegram.

function ScreenLlamadas() {
  const [tab, setTab] = useState('voz');
  const [q, setQ] = useState('');
  const [soloReserva, setSoloReserva] = useState(false);
  const [openId, setOpenId] = useState(null);
  const all = Array.isArray(LLAMADAS) ? LLAMADAS : [];
  const counts = {
    voz: all.filter(c => c.channel !== 'telegram').length,
    telegram: all.filter(c => c.channel === 'telegram').length,
  };
  const list = all.filter(c => (tab === 'telegram' ? c.channel === 'telegram' : c.channel !== 'telegram'));
  const filtered = list.filter(c => {
    if (soloReserva && !c.reserva) return false;
    if (!q) return true;
    const s = q.toLowerCase();
    return (c.display_phone || c.telefono || '').toLowerCase().includes(s) || (c.nombre || '').toLowerCase().includes(s);
  });

  useEffect(() => { setOpenId(null); }, [tab]);

  const emptyLabel = tab === 'telegram'
    ? 'Sin conversaciones de Telegram registradas aún.'
    : 'Sin conversaciones de llamadas registradas aún.';

  return (
    <>
      <div className="mb-4">
        <h1 className="text-xl md:text-2xl font-bold tracking-tight">Historial de conversaciones</h1>
        <p className="text-slate-500 dark:text-slate-400 text-sm mt-1">Lo que el bot ha hablado con tus clientes por llamada o Telegram</p>
      </div>

      <div className="border-b border-slate-200 dark:border-slate-800 mb-3 flex gap-5">
        {[
          ['voz', 'Llamadas', ICONS.phone, counts.voz],
          ['telegram', 'Telegram', ICONS.telegram, counts.telegram],
        ].map(([k, l, ic, n]) => (
          <button key={k} onClick={() => setTab(k)}
            className={`tab-btn flex items-center gap-2 py-2.5 -mb-px border-b-2 text-sm ${tab === k ? 'active' : 'border-transparent text-slate-500 dark:text-slate-400'}`}>
            <Icon d={ic} cls="w-4 h-4"/> {l}
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-100 dark:bg-slate-800 text-slate-500">{n}</span>
          </button>
        ))}
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
            const title = c.nombre && c.nombre !== '—' ? c.nombre : (c.display_phone || c.telefono);
            return (
              <li key={c.id}>
                <button onClick={()=>setOpenId(isOpen ? null : c.id)}
                  className={`w-full text-left px-4 py-3 flex items-center gap-3 hover:bg-slate-50 dark:hover:bg-slate-800/50 ${isOpen?'bg-slate-50 dark:bg-slate-800/50':''}`}>
                  <div className={`w-8 h-8 rounded-lg flex items-center justify-center shrink-0 ${c.channel === 'telegram' ? 'bg-sky-50 text-sky-700 dark:bg-sky-900/30 dark:text-sky-300' : 'bg-emerald-50 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300'}`}>
                    <Icon d={c.channel === 'telegram' ? ICONS.telegram : ICONS.phone} cls="w-4 h-4"/>
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center justify-between gap-2">
                      <div className="text-sm font-medium truncate">{title}</div>
                      <div className="text-[11px] text-slate-400 shrink-0 tabular-nums">{(c.ultimoAt || '').split('T')[1] || ''}</div>
                    </div>
                    <div className="flex items-center gap-1.5">
                      <div className="text-xs text-slate-500 dark:text-slate-400 truncate flex-1">{c.preview || (c.turnos && c.turnos.slice(-1)[0]?.text) || '—'}</div>
                      {c.reserva && <span className="shrink-0 text-[10px] px-1.5 py-0.5 rounded bg-brand-50 text-brand-700 dark:bg-brand-900/30 dark:text-brand-300 font-medium">reserva</span>}
                      <span className={`shrink-0 text-[10px] px-1.5 py-0.5 rounded font-medium ${c.channel === 'telegram' ? 'bg-sky-50 text-sky-700 dark:bg-sky-900/30 dark:text-sky-300' : 'bg-emerald-50 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300'}`}>
                        {c.channel === 'telegram' ? 'Telegram' : 'Llamada'}
                      </span>
                    </div>
                  </div>
                  <Icon d={isOpen?ICONS.chevronDown:ICONS.chevronRight} cls="w-4 h-4 text-slate-400 shrink-0"/>
                </button>

                {isOpen && (
                  <div className="px-4 pb-4 bg-slate-50 dark:bg-slate-950/50 fade-in">
                    <div className="flex items-center gap-2 py-3 flex-wrap">
                      <span className={`inline-flex items-center gap-1.5 text-xs px-2 py-1 rounded-lg border ${c.channel === 'telegram' ? 'bg-sky-50 text-sky-700 border-sky-100 dark:bg-sky-900/20 dark:border-sky-900/40 dark:text-sky-300' : 'bg-emerald-50 text-emerald-700 border-emerald-100 dark:bg-emerald-900/20 dark:border-emerald-900/40 dark:text-emerald-300'}`}>
                        <Icon d={c.channel === 'telegram' ? ICONS.telegram : ICONS.phone} cls="w-3 h-3"/>
                        {c.channel === 'telegram' ? 'Telegram' : 'Llamada'}
                      </span>
                      {c.tools && c.tools.map(t => (
                        <span key={t} className="px-1.5 py-1 rounded bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 font-mono text-[10px]">{t}</span>
                      ))}
                    </div>
                    <div className="space-y-2 pt-2">
                      {(c.turnos || []).map((m, i) => (
                        <div key={i} className={`flex ${m.role === 'assistant' ? 'justify-end' : ''}`}>
                          <div className={`${m.role === 'user' ? 'chat-bubble-in' : 'chat-bubble-out'} max-w-[85%] px-3 py-2 text-sm whitespace-pre-wrap`}>
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
                      </div>
                    )}
                  </div>
                )}
              </li>
            );
          })}
          {!filtered.length && <li className="p-10 text-center text-sm text-slate-400">{emptyLabel}</li>}
        </ul>
      </Card>
    </>
  );
}

window.ScreenLlamadas = ScreenLlamadas;
