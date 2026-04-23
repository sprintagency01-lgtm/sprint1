// App root del portal (producción) — sin TweaksPanel.

const { useState: useStateApp, useEffect: useEffectApp } = React;

function App() {
  const [active, setActive] = useStateApp('hoy');
  const [user, setUser] = useStateApp({
    nombre: (window.PORTAL_USER && window.PORTAL_USER.nombre) || '',
    email:  (window.PORTAL_USER && window.PORTAL_USER.email)  || '',
    botVoz: !!(window.PORTAL_BOT && window.PORTAL_BOT.voz),
    botWa:  !!(window.PORTAL_BOT && window.PORTAL_BOT.wa),
  });

  // Sincroniza el toggle de bot con el backend — la tarjeta "Tu bot ahora"
  // del Hoy lo llama para que el estado persista entre recargas.
  useEffectApp(() => {
    const off = async (e) => {
      const { canal, on } = e.detail || {};
      if (!canal) return;
      try {
        await window.api.patch('/bot', { [canal]: !!on });
      } catch (err) {
        // si falla, revertimos
        console.warn('bot toggle failed', err);
        setUser(u => ({ ...u, [canal === 'voz' ? 'botVoz' : 'botWa']: !on }));
      }
    };
    window.addEventListener('portal:bot-toggle', off);
    return () => window.removeEventListener('portal:bot-toggle', off);
  }, []);

  const [detalleReserva, setDetalleReserva] = useStateApp(null);

  const screen = (() => {
    switch (active) {
      case 'hoy':            return <ScreenHoy user={user} setUser={setUser} setActive={setActive} onOpenReserva={r => { setDetalleReserva(r); setActive('reservas'); }} />;
      case 'conversaciones': return <ScreenConversaciones />;
      case 'reservas':       return <ScreenReservas initialReserva={detalleReserva} onCloseDetalle={() => setDetalleReserva(null)} />;
      case 'ingresos':       return <ScreenIngresos />;
      case 'servicios':      return <ScreenServicios />;
      case 'equipo':         return <ScreenEquipo />;
      case 'ajustes':        return <ScreenAjustes user={user} setUser={setUser} />;
      default:               return null;
    }
  })();

  return <Shell active={active} setActive={setActive} user={user}>{screen}</Shell>;
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
