// Pantalla HOY — simplificada, mobile-first

function ScreenHoy({ user, setUser, setActive, onOpenReserva }) {
  const k = kpis();
  const hoyReservas = RESERVAS.filter(r => r.fecha === HOY_ISO && r.estado !== 'cancelada').sort((a,b)=>a.hora.localeCompare(b.hora));
  const ahora = new Date().toTimeString().slice(0,5);
  const porVenir = hoyReservas.filter(r=>r.hora>ahora);

  const toggleBot = (canal) => {
    // Actualiza optimistamente el estado local y delega al listener en app.jsx
    // que sincroniza con /api/portal/bot.
    const key = canal === 'voz' ? 'botVoz' : 'botWa';
    const next = !(user && user[key]);
    if (typeof setUser === 'function') setUser(u => ({ ...u, [key]: next }));
    window.dispatchEvent(new CustomEvent('portal:bot-toggle', { detail: { canal, on: next } }));
  };

  return (
    <>
      <div className="mb-5 flex items-end justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl md:text-2xl font-bold tracking-tight">Hola, {user.nombre.split(' ')[0]}</h1>
          <p className="text-slate-500 dark:text-slate-400 text-sm mt-1">Jueves 23 abril · {porVenir.length} citas por venir</p>
        </div>
        <button onClick={()=>setActive('reservas')} className="flex items-center gap-2 bg-brand-600 hover:bg-brand-700 text-white text-sm font-medium px-3 py-2 rounded-lg">
          <Icon d={ICONS.plus} cls="w-4 h-4"/> Nueva
        </button>
      </div>

      {/* KPIs — 2 en móvil */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-5">
        <MiniKpi label="Hoy"           value={k.reservasHoy}         sub="reservas"/>
        <MiniKpi label="Semana"        value={k.reservasSemana}      sub="reservas"/>
        <MiniKpi label="Ingresos"      value={eur(k.ingresosSemana)} sub="semana"    accent/>
        <MiniKpi label="Hecho por bot" value={`${k.pctBot}%`}        sub="de reservas"/>
      </div>

      {/* Agenda de hoy */}
      <Card className="overflow-hidden mb-5">
        <div className="p-4 md:p-5 border-b border-slate-100 dark:border-slate-800 flex items-center justify-between">
          <div className="text-sm font-semibold">Agenda de hoy</div>
          <button onClick={()=>setActive('reservas')} className="text-xs text-brand-700 hover:underline">Ver todo →</button>
        </div>
        <ul className="divide-y divide-slate-100 dark:divide-slate-800">
          {hoyReservas.map(r => {
            const s = servicioDe(r.servicio);
            const m = miembroDe(r.equipo);
            const pasada = r.hora <= ahora;
            return (
              <li key={r.id} className={`px-4 py-3 flex items-center gap-3 hover:bg-slate-50 dark:hover:bg-slate-800/50 cursor-pointer ${pasada?'opacity-50':''}`} onClick={()=>onOpenReserva(r)}>
                <div className="w-12 text-center shrink-0">
                  <div className="text-sm font-semibold tabular-nums">{r.hora}</div>
                </div>
                <div className="w-1 h-8 rounded-full shrink-0" style={{background:m?.color}}/>
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-medium truncate">{r.cliente}</div>
                  <div className="text-xs text-slate-500 truncate">{s?.nombre} · {m?.nombre}</div>
                </div>
                <CanalBadge canal={r.canal}/>
              </li>
            );
          })}
        </ul>
      </Card>

      {/* Estado del bot — simplificado */}
      <Card className="p-4 md:p-5">
        <div className="flex items-center justify-between mb-3">
          <div className="text-sm font-semibold">Tu bot ahora</div>
          <button onClick={()=>setActive('ajustes')} className="text-xs text-brand-700 hover:underline">Ajustes →</button>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <button type="button" onClick={()=>toggleBot('voz')}
            className={`text-left flex items-center gap-2.5 p-3 rounded-lg border ${user.botVoz?'bg-emerald-50 border-emerald-200 dark:bg-emerald-900/20 dark:border-emerald-900/40':'bg-slate-50 border-transparent dark:bg-slate-800/40'}`}>
            <div className={`w-8 h-8 rounded-lg flex items-center justify-center text-white ${user.botVoz?'bg-emerald-500':'bg-slate-400'}`}><Icon d={ICONS.voz} cls="w-4 h-4"/></div>
            <div className="min-w-0 flex-1">
              <div className="text-sm font-medium">Llamadas</div>
              <div className="text-xs text-slate-500">{user.botVoz?'Activas · toca para pausar':'Pausadas · toca para activar'}</div>
            </div>
            <div className={`tg ${user.botVoz?'on':''}`} role="switch" aria-checked={user.botVoz}/>
          </button>
          <button type="button" onClick={()=>toggleBot('wa')}
            className={`text-left flex items-center gap-2.5 p-3 rounded-lg border ${user.botWa?'bg-emerald-50 border-emerald-200 dark:bg-emerald-900/20 dark:border-emerald-900/40':'bg-slate-50 border-transparent dark:bg-slate-800/40'}`}>
            <div className={`w-8 h-8 rounded-lg flex items-center justify-center text-white ${user.botWa?'bg-emerald-500':'bg-slate-400'}`}><Icon d={ICONS.whatsapp} cls="w-4 h-4"/></div>
            <div className="min-w-0 flex-1">
              <div className="text-sm font-medium">WhatsApp</div>
              <div className="text-xs text-slate-500">{user.botWa?'Activo · toca para pausar':'Pausado · toca para activar'}</div>
            </div>
            <div className={`tg ${user.botWa?'on':''}`} role="switch" aria-checked={user.botWa}/>
          </button>
        </div>
      </Card>
    </>
  );
}

function MiniKpi({ label, value, sub, accent }) {
  return (
    <div className={`rounded-xl border p-3 md:p-4 ${accent?'border-brand-200 bg-brand-50/40 dark:bg-brand-900/15 dark:border-brand-900/40':'border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900'}`}>
      <div className="text-[11px] uppercase tracking-wider text-slate-500">{label}</div>
      <div className="mt-0.5 text-xl md:text-2xl font-semibold tracking-tight tabular-nums">{value}</div>
      <div className="text-[11px] text-slate-400">{sub}</div>
    </div>
  );
}

window.ScreenHoy = ScreenHoy;
