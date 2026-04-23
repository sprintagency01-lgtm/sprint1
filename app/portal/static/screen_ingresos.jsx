// Pantalla Ingresos

function ScreenIngresos() {
  const [rango, setRango] = useState('30d');
  const data = rango==='7d' ? INGRESOS_30D.slice(-7) : rango==='30d' ? INGRESOS_30D : INGRESOS_30D.slice(-14);
  const totalVoz = data.reduce((s,d)=>s+d.voz,0);
  const totalWa  = data.reduce((s,d)=>s+d.wa,0);
  const totalMan = data.reduce((s,d)=>s+d.man,0);
  const total = totalVoz + totalWa + totalMan;
  const totalBot = totalVoz + totalWa;
  const max = Math.max(...data.map(d=>d.total), 1);
  const W = 600, H = 220;

  // Ingresos por servicio y por miembro
  const porServicio = SERVICIOS.filter(s=>s.activo).map(s=>{
    const n = RESERVAS.filter(r=>r.servicio===s.id && r.estado!=='cancelada').length;
    return { ...s, total: n * s.precio, n };
  }).sort((a,b)=>b.total-a.total);

  const porMiembro = EQUIPO.map(m=>{
    const rs = RESERVAS.filter(r=>r.equipo===m.id && r.estado!=='cancelada');
    const total = rs.reduce((s,r)=>s+(servicioDe(r.servicio)?.precio||0),0);
    return { ...m, total, n: rs.length };
  }).sort((a,b)=>b.total-a.total);

  return (
    <>
      <div className="mb-4 md:mb-6 flex items-end justify-between flex-wrap gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Ingresos</h1>
          <p className="text-slate-500 dark:text-slate-400 text-sm mt-1">Estimados a partir del precio del servicio reservado</p>
        </div>
        <div className="inline-flex rounded-lg border border-slate-200 dark:border-slate-700 p-0.5 bg-white dark:bg-slate-900">
          {[['7d','7 días'],['14d','14 días'],['30d','30 días']].map(([k,l])=>(
            <button key={k} onClick={()=>setRango(k)}
              className={`text-xs px-3 py-1.5 rounded-md ${rango===k?'bg-slate-100 dark:bg-slate-800 font-medium':'text-slate-500'}`}>{l}</button>
          ))}
        </div>
      </div>

      {/* Métrica destacada */}
      <Card className="p-5 md:p-6 mb-6 bg-gradient-to-br from-brand-50 to-white dark:from-brand-900/20 dark:to-slate-900 border-brand-100 dark:border-brand-900/30">
        <div className="flex items-start justify-between flex-wrap gap-4">
          <div>
            <div className="text-sm text-slate-600 dark:text-slate-300 flex items-center gap-1.5">
              <Icon d={ICONS.bot} cls="w-4 h-4 text-brand-700"/>
              Ingresos atribuibles al bot este mes
              <span className="group relative">
                <span className="w-4 h-4 rounded-full bg-slate-200 dark:bg-slate-700 inline-flex items-center justify-center text-[9px] text-slate-500 cursor-help">?</span>
                <span className="invisible group-hover:visible absolute left-5 top-0 w-56 text-[11px] bg-slate-900 text-white rounded-lg p-2 z-10 shadow-lg">Ingreso estimado a partir del precio del servicio reservado por el bot (voz + WhatsApp).</span>
              </span>
            </div>
            <div className="text-4xl font-semibold tracking-tight mt-2 tabular-nums">{eur(totalBot)}</div>
            <div className="text-xs text-slate-500 mt-1">vs {eur(Math.round(totalBot*0.82))} mes anterior · <span className="text-emerald-700 font-medium">↑ 22%</span></div>
          </div>
          <div className="flex gap-6 text-sm">
            <div>
              <div className="text-xs text-slate-500">Voz</div>
              <div className="font-semibold text-indigo-600 tabular-nums">{eur(totalVoz)}</div>
            </div>
            <div>
              <div className="text-xs text-slate-500">WhatsApp</div>
              <div className="font-semibold text-brand-700 tabular-nums">{eur(totalWa)}</div>
            </div>
            <div>
              <div className="text-xs text-slate-500">Manual</div>
              <div className="font-semibold text-slate-500 tabular-nums">{eur(totalMan)}</div>
            </div>
          </div>
        </div>
      </Card>

      {/* Gráfico */}
      <Card className="p-5 md:p-6 mb-6">
        <div className="flex items-center justify-between mb-4">
          <div>
            <div className="text-sm font-semibold">Ingresos por canal</div>
            <div className="text-xs text-slate-500">Total {eur(total)} · últimos {data.length} días</div>
          </div>
          <div className="flex items-center gap-3 text-[11px]">
            <span className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-sm bg-indigo-500"/>Voz</span>
            <span className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-sm bg-emerald-500"/>WhatsApp</span>
            <span className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-sm bg-slate-300"/>Manual</span>
          </div>
        </div>
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{height:'220px'}} preserveAspectRatio="none">
          {[0.25,0.5,0.75,1].map(p=>(
            <line key={p} x1="0" x2={W} y1={H-p*(H-20)} y2={H-p*(H-20)} stroke="#e2e8f0" strokeDasharray="2,3"/>
          ))}
          {data.map((d,i)=>{
            const w = W/data.length - 2;
            const x = i*(W/data.length);
            const total = Math.max(d.total,1);
            const hTotal = (total/max)*(H-20);
            const hVoz = (d.voz/total)*hTotal;
            const hWa  = (d.wa/total)*hTotal;
            const hMan = (d.man/total)*hTotal;
            const yVoz = H-hTotal, yWa = yVoz+hVoz, yMan = yWa+hWa;
            return (
              <g key={i}>
                <rect x={x} y={yVoz} width={w} height={hVoz} fill="#6366f1" rx="2"/>
                <rect x={x} y={yWa}  width={w} height={hWa}  fill="#10b981"/>
                <rect x={x} y={yMan} width={w} height={hMan} fill="#cbd5e1"/>
              </g>
            );
          })}
        </svg>
      </Card>

      <div className="grid md:grid-cols-2 gap-4 md:gap-6">
        <Card className="p-5">
          <div className="text-sm font-semibold mb-4">Por servicio</div>
          <div className="space-y-3">
            {porServicio.map(s=>{
              const pct = Math.round((s.total/Math.max(porServicio[0].total,1))*100);
              return (
                <div key={s.id}>
                  <div className="flex items-center justify-between mb-1">
                    <div className="text-sm">{s.nombre} <span className="text-xs text-slate-400">· {s.n} reservas</span></div>
                    <div className="text-sm font-semibold tabular-nums">{eur(s.total)}</div>
                  </div>
                  <div className="h-1.5 rounded-full bg-slate-100 dark:bg-slate-800 overflow-hidden">
                    <div className="h-full bg-brand-500 rounded-full" style={{width:`${pct}%`}}/>
                  </div>
                </div>
              );
            })}
          </div>
        </Card>

        <Card className="p-5">
          <div className="text-sm font-semibold mb-4">Por miembro del equipo</div>
          <div className="space-y-3">
            {porMiembro.map(m=>{
              const pct = Math.round((m.total/Math.max(porMiembro[0].total,1))*100);
              return (
                <div key={m.id} className="flex items-center gap-3">
                  <Avatar name={m.nombre} color={m.color} size="sm"/>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center justify-between mb-1">
                      <div className="text-sm font-medium">{m.nombre} <span className="text-xs text-slate-400">· {m.n}</span></div>
                      <div className="text-sm font-semibold tabular-nums">{eur(m.total)}</div>
                    </div>
                    <div className="h-1.5 rounded-full bg-slate-100 dark:bg-slate-800 overflow-hidden">
                      <div className="h-full rounded-full" style={{width:`${pct}%`, background:m.color}}/>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </Card>
      </div>
    </>
  );
}

window.ScreenIngresos = ScreenIngresos;
