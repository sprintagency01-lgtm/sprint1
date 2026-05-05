// Sidebar + topbar shell, matching CMS _layout.html exactly (same palette, same structure).

const { useState, useEffect, useMemo, useRef } = React;

const Icon = ({ d, cls='w-5 h-5' }) => (
  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className={cls} strokeLinecap="round" strokeLinejoin="round" dangerouslySetInnerHTML={{__html:d}} />
);

// Sprintia PulseMark — logo de marca para headers
const PulseMark = ({ size=28 }) => (
  <svg viewBox="0 0 28 28" xmlns="http://www.w3.org/2000/svg" width={size} height={size} aria-hidden="true">
    <rect x="6.58"  y="10.08" width="3.64" height="11.2"  rx="1.82" fill="currentColor"/>
    <rect x="12.18" y="5.6"   width="3.64" height="17.92" rx="1.82" fill="currentColor"/>
    <rect x="17.78" y="11.76" width="3.64" height="8.4"   rx="1.82" fill="currentColor"/>
    <circle cx="19.6" cy="6.16" r="2.38" fill="#2d4cff"/>
  </svg>
);

const ICONS = {
  hoy:            '<path d="M3 12 12 3l9 9M5 10v10h4v-6h6v6h4V10"/>',
  llamadas:       '<path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92Z"/>',
  reservas:       '<path d="M8 2v4M16 2v4M3 10h18M5 6h14a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2Z"/>',
  ingresos:       '<path d="M12 1v22M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/>',
  servicios:      '<path d="M20.59 13.41 13.42 20.58a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82Z"/><path d="M7 7h.01"/>',
  equipo:         '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/>',
  bot:            '<rect x="3" y="8" width="18" height="12" rx="2"/><path d="M12 4v4M8 14h.01M16 14h.01"/>',
  ajustes:        '<path d="M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h0a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v0a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1Z"/>',
  menu:           '<line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/>',
  close:          '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
  voz:            '<path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3Z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2M12 19v4M8 23h8"/>',
  telegram:       '<path d="M21.5 2.5 2.8 9.7c-1.3.5-1.3 1.3-.2 1.7l4.8 1.5 1.8 5.6c.2.6.1.9.8.9.5 0 .7-.2 1-.5l2.3-2.2 4.8 3.5c.9.5 1.5.2 1.8-.8L23 4.1c.4-1.5-.6-2.2-1.5-1.6Z"/>',
  check:          '<path d="M20 6 9 17l-5-5"/>',
  clock:          '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
  euro:           '<path d="M18 7a6 6 0 1 0 0 10M4 10h10M4 14h10"/>',
  play:           '<polygon points="5 3 19 12 5 21 5 3"/>',
  plus:           '<path d="M12 5v14M5 12h14"/>',
  search:         '<circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>',
  chevronDown:    '<polyline points="6 9 12 15 18 9"/>',
  chevronLeft:    '<polyline points="15 18 9 12 15 6"/>',
  chevronRight:   '<polyline points="9 18 15 12 9 6"/>',
  user:           '<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
  phone:          '<path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92Z"/>',
  hand:           '<path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3Z"/>',
  edit:           '<path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5Z"/>',
  trash:          '<polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
  mic:            '<rect x="9" y="2" width="6" height="13" rx="3"/><path d="M19 10v2a7 7 0 0 1-14 0v-2M12 19v3M8 22h8"/>',
  sparkles:       '<path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.64 5.64l2.12 2.12M16.24 16.24l2.12 2.12M5.64 18.36l2.12-2.12M16.24 7.76l2.12-2.12"/>',
};

const NAV = [
  { key:'hoy',            label:'Hoy',              icon:ICONS.hoy },
  { key:'llamadas',       label:'Conversaciones',   icon:ICONS.llamadas },
  { key:'reservas',       label:'Reservas',         icon:ICONS.reservas },
  { key:'ingresos',       label:'Ingresos',         icon:ICONS.ingresos },
  { key:'servicios',      label:'Servicios',        icon:ICONS.servicios },
  { key:'equipo',         label:'Equipo',           icon:ICONS.equipo },
  { key:'ajustes',        label:'Ajustes',          icon:ICONS.ajustes },
];

const MOBILE_TABS = ['hoy','reservas','llamadas','equipo','ajustes'];

function initialsOf(name) {
  return name.split(/[\s·]+/).filter(Boolean).slice(0,2).map(s=>s[0]).join('').toUpperCase();
}
function eur(v) { return new Intl.NumberFormat('es-ES',{style:'currency',currency:'EUR',maximumFractionDigits:0}).format(v); }
function eurPreciso(v) { return new Intl.NumberFormat('es-ES',{style:'currency',currency:'EUR'}).format(v); }

function Avatar({ name, size='md', color }) {
  const sz = size==='sm' ? 'w-7 h-7 text-[10px]' : size==='lg' ? 'w-11 h-11 text-sm' : 'w-9 h-9 text-xs';
  const bg = color || '#64748b';
  return (
    <div className={`${sz} rounded-lg flex items-center justify-center font-semibold text-white shrink-0`} style={{background:bg}}>
      {initialsOf(name || '—')}
    </div>
  );
}

function StatusDot({ on, label }) {
  return (
    <span className={`inline-flex items-center gap-1.5 text-xs px-2 py-0.5 rounded-full font-medium ${on ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300' : 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400'}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${on?'bg-emerald-500 pulse-dot':'bg-slate-400'}`} />
      {label}
    </span>
  );
}

function CanalBadge({ canal }) {
  const map = {
    voz:      { bg:'bg-indigo-50 text-indigo-700 dark:bg-indigo-900/40 dark:text-indigo-300',     label:'Voz',      icon:ICONS.voz },
    manual:   { bg:'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300',           label:'Manual',   icon:ICONS.hand },
  };
  const c = map[canal] || map.manual;
  return (
    <span className={`inline-flex items-center gap-1 text-[11px] px-1.5 py-0.5 rounded font-medium ${c.bg}`}>
      <Icon d={c.icon} cls="w-3 h-3"/>
      {c.label}
    </span>
  );
}

function Card({ children, className='', ...rest }) {
  return <div className={`bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 ${className}`} {...rest}>{children}</div>;
}

function Kpi({ label, value, sub, delta, icon }) {
  return (
    <Card className="p-5">
      <div className="flex items-center justify-between mb-3">
        <div className="w-9 h-9 rounded-lg bg-slate-50 dark:bg-slate-800 flex items-center justify-center text-slate-600 dark:text-slate-300">
          <Icon d={icon} cls="w-5 h-5"/>
        </div>
        {delta != null && (
          <span className={`text-xs px-1.5 py-0.5 rounded font-medium ${delta>0?'bg-emerald-50 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300':'bg-rose-50 text-rose-700 dark:bg-rose-900/40 dark:text-rose-300'}`}>
            {delta>0?'↑':'↓'} {Math.abs(delta)}%
          </span>
        )}
      </div>
      <div className="text-sm text-slate-500 dark:text-slate-400">{label}</div>
      <div className="mt-1 text-2xl font-semibold tracking-tight">{value}</div>
      {sub && <div className="mt-1 text-xs text-slate-400 dark:text-slate-500">{sub}</div>}
    </Card>
  );
}

function Shell({ active, setActive, user, children }) {
  const [menuOpen, setMenuOpen] = useState(false);
  useEffect(() => { setMenuOpen(false); }, [active]);

  const navButton = (item) => (
    <button
      key={item.key}
      onClick={() => setActive(item.key)}
      className={`nav-item flex items-center gap-3 px-3 py-2 rounded-lg text-slate-600 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-800 w-full text-left ${active===item.key?'active':''}`}
    >
      <Icon d={item.icon} cls="w-5 h-5"/>
      <span className="text-sm">{item.label}</span>
    </button>
  );

  const breadcrumbLabel = NAV.find(n=>n.key===active)?.label || '';

  return (
    <div className="flex min-h-screen">
      {/* Desktop sidebar */}
      <aside className="hidden md:flex w-60 shrink-0 border-r border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 flex-col">
        <div className="px-5 h-16 flex items-center gap-2.5 border-b border-slate-200 dark:border-slate-800">
          <span className="text-slate-900 dark:text-slate-100"><PulseMark size={26}/></span>
          <div>
            <div className="font-semibold text-sm leading-tight tracking-tight">{NEGOCIO.nombre}</div>
            <div className="text-[10px] uppercase tracking-wider text-slate-400">por Sprintia</div>
          </div>
        </div>
        <nav className="flex-1 p-3 space-y-1">{NAV.map(navButton)}</nav>
        <div className="p-3 border-t border-slate-200 dark:border-slate-800">
          <div className="flex items-center gap-3 px-2 py-2">
            <Avatar name={user.nombre} color="#475569"/>
            <div className="flex-1 min-w-0">
              <div className="text-sm font-medium truncate">{user.nombre}</div>
              <div className="text-xs text-slate-500 truncate">{user.email}</div>
            </div>
          </div>
        </div>
      </aside>

      {/* Mobile drawer */}
      {menuOpen && (
        <div className="md:hidden fixed inset-0 z-30 flex">
          <div className="absolute inset-0 bg-slate-900/40" onClick={() => setMenuOpen(false)} />
          <aside className="relative w-64 bg-white dark:bg-slate-900 border-r border-slate-200 dark:border-slate-800 flex flex-col slide-in-right">
            <div className="px-5 h-16 flex items-center justify-between border-b border-slate-200 dark:border-slate-800">
              <div className="flex items-center gap-2.5">
                <span className="text-slate-900 dark:text-slate-100"><PulseMark size={24}/></span>
                <div className="font-semibold text-sm tracking-tight">{NEGOCIO.nombre}</div>
              </div>
              <button onClick={()=>setMenuOpen(false)} className="p-1 text-slate-500"><Icon d={ICONS.close}/></button>
            </div>
            <nav className="flex-1 p-3 space-y-1">{NAV.map(navButton)}</nav>
          </aside>
        </div>
      )}

      <main className="flex-1 min-w-0 pb-16 md:pb-0">
        <header className="h-14 md:h-16 bg-white dark:bg-slate-900 border-b border-slate-200 dark:border-slate-800 px-4 md:px-8 flex items-center gap-3 sticky top-0 z-20">
          <button className="md:hidden p-1 -ml-1 text-slate-600 dark:text-slate-300" onClick={() => setMenuOpen(true)}>
            <Icon d={ICONS.menu}/>
          </button>
          <div className="text-sm flex items-center gap-2 min-w-0">
            <span className="hidden md:inline text-slate-500">{NEGOCIO.nombre}</span>
            <span className="hidden md:inline text-slate-500">·</span>
            <span className="text-slate-900 dark:text-slate-100 font-semibold truncate">{breadcrumbLabel}</span>
          </div>
          <div className="ml-auto flex items-center gap-1.5">
            <StatusDot on={user.botVoz} label="Voz"/>
          </div>
        </header>
        <div className="p-4 md:p-8 fade-in">{children}</div>
      </main>

      {/* Bottom tabbar mobile */}
      <nav className="md:hidden fixed bottom-0 inset-x-0 z-20 h-14 bg-white/95 dark:bg-slate-900/95 backdrop-blur border-t border-slate-200 dark:border-slate-800 flex">
        {MOBILE_TABS.map(k => {
          const n = NAV.find(x=>x.key===k);
          if (!n) return null;
          const isActive = active === k;
          return (
            <button key={k} onClick={()=>setActive(k)} className={`flex-1 flex flex-col items-center justify-center gap-0.5 ${isActive?'text-brand-700 dark:text-brand-400':'text-slate-500 dark:text-slate-400'}`}>
              <Icon d={n.icon} cls="w-5 h-5"/>
              <span className="text-[10px] font-medium">{n.label}</span>
            </button>
          );
        })}
      </nav>
    </div>
  );
}

Object.assign(window, { Icon, ICONS, NAV, PulseMark, Avatar, StatusDot, CanalBadge, Card, Kpi, Shell, initialsOf, eur, eurPreciso });
