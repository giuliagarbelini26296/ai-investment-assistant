import React, { useEffect, useRef, useState } from 'react';
import { Bell, BellOff, BellRing, Check, Share, Smartphone, X } from 'lucide-react';
import { AnimatePresence, motion } from './motion';

const csrf = () => document.querySelector('meta[name="csrf-token"]')?.content || '';
const post = (url, body) => fetch(url, {
  method: 'POST', credentials: 'same-origin',
  headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() }, body: JSON.stringify(body),
}).then(async r => { const p = await r.json().catch(() => ({})); if (!r.ok) throw new Error(p.error || 'Não foi possível concluir.'); return p; });

function keyBytes(base64) {
  const pad = '='.repeat((4 - (base64.length % 4)) % 4);
  const raw = atob((base64 + pad).replace(/-/g, '+').replace(/_/g, '/'));
  return Uint8Array.from(raw, c => c.charCodeAt(0));
}

function device() {
  const ua = navigator.userAgent;
  return /iPhone/.test(ua) ? 'iPhone' : /iPad/.test(ua) ? 'iPad' : /Android/.test(ua) ? 'Android'
    : /Mac/.test(ua) ? 'Mac' : /Windows/.test(ua) ? 'Windows' : 'Navegador';
}

function environment() {
  const ios = /iPhone|iPad|iPod/.test(navigator.userAgent);
  const installed = window.matchMedia?.('(display-mode: standalone)').matches || navigator.standalone === true;
  const supported = 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
  if (ios && !installed) return 'ios-install';
  if (!supported) return 'unsupported';
  if (Notification.permission === 'denied') return 'denied';
  return 'ok';
}

async function currentSubscription() {
  const reg = await navigator.serviceWorker.getRegistration('/');
  return reg ? reg.pushManager.getSubscription() : null;
}

export function NotificationsButton({ accounts }) {
  const [open, setOpen] = useState(false);
  const [state, setState] = useState({ loading: true });
  const [chosen, setChosen] = useState(accounts.map(a => a.slug));
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const ref = useRef(null);

  async function refresh() {
    const env = environment();
    if (env !== 'ok') { setState({ loading: false, env }); return; }
    try {
      const sub = await currentSubscription();
      const r = await fetch('/api/v1/push/' + (sub ? `?endpoint=${encodeURIComponent(sub.endpoint)}` : ''), { credentials: 'same-origin', cache: 'no-store' });
      const s = await r.json();
      setState({ loading: false, env, ...s, endpoint: sub?.endpoint });
      if (s.subscribed && s.accounts?.length) setChosen(s.accounts);
    } catch { setState({ loading: false, env, error: true }); }
  }

  useEffect(() => { refresh(); }, []);
  useEffect(() => {
    if (!open) return;
    const close = e => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [open]);

  async function activate() {
    setBusy(true); setMessage('');
    try {
      if (await Notification.requestPermission() !== 'granted') throw new Error('Permissão não concedida. Libere as notificações do ID3 nos ajustes do aparelho.');
      const reg = await navigator.serviceWorker.register('/sw.js', { scope: '/' });
      await navigator.serviceWorker.ready;
      const sub = (await reg.pushManager.getSubscription()) || await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(state.publicKey) });
      await post('/api/v1/push/subscribe/', { subscription: sub.toJSON(), accounts: chosen, device: device() });
      setMessage('Notificações ativadas neste aparelho.');
      await refresh();
    } catch (e) { setMessage(e.message); } finally { setBusy(false); }
  }

  async function deactivate() {
    setBusy(true); setMessage('');
    try {
      const sub = await currentSubscription();
      if (sub) { await post('/api/v1/push/unsubscribe/', { endpoint: sub.endpoint }); await sub.unsubscribe(); }
      setMessage('Notificações desativadas neste aparelho.');
      await refresh();
    } catch (e) { setMessage(e.message); } finally { setBusy(false); }
  }

  async function test() {
    setBusy(true); setMessage('');
    try { await post('/api/v1/push/test/', { endpoint: state.endpoint }); setMessage('Teste enviado. Deve chegar em alguns segundos.'); }
    catch (e) { setMessage(e.message); } finally { setBusy(false); }
  }

  const on = state.subscribed;
  const toggleAccount = slug => setChosen(c => c.includes(slug) ? (c.length > 1 ? c.filter(x => x !== slug) : c) : [...c, slug]);

  return <div className="notif-wrap" ref={ref}>
    <button className={`icon-btn ${on ? 'notif-on' : ''}`} aria-label="Notificações de notícias" aria-expanded={open} onClick={() => setOpen(o => !o)}>
      {on ? <BellRing size={18} /> : <Bell size={18} />}{on && <span className="notif-dot" />}
    </button>
    <AnimatePresence>{open && <motion.div className="notif-panel" role="dialog" aria-label="Notificações de notícias"
      initial={{ opacity: 0, y: -6, scale: .97 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: -6, scale: .97 }} transition={{ duration: .16 }}>
      <header><strong>Notícias no celular</strong><button className="icon-btn" aria-label="Fechar" onClick={() => setOpen(false)}><X size={16} /></button></header>
      {state.loading ? <p className="muted">Conferindo este aparelho…</p>
        : state.env === 'ios-install' ? <div className="notif-help"><Smartphone size={20} /><div>
            <p>No iPhone, as notificações só funcionam com o ID3 <b>instalado na tela de início</b>.</p>
            <ol><li>No Safari, toque em <Share size={13} /> Compartilhar.</li><li>Escolha <b>Adicionar à Tela de Início</b>.</li><li>Abra o ID3 pelo ícone novo e ative aqui.</li></ol></div></div>
        : state.env === 'unsupported' ? <p className="muted">Este navegador não aceita notificações de sites. Use o Chrome, o Edge ou o app instalado.</p>
        : state.env === 'denied' ? <div className="notif-help"><BellOff size={20} /><p>As notificações do ID3 estão bloqueadas neste aparelho. Libere nos ajustes do navegador ou do sistema e volte aqui.</p></div>
        : state.error || !state.enabled ? <p className="muted">As notificações ainda não estão disponíveis no servidor.</p>
        : <>
          <p className="notif-desc">Avisos com as notícias dos ativos da carteira, mesmo com o app fechado.</p>
          <div className="notif-accounts">{accounts.map(a => <label key={a.slug}><input type="checkbox" checked={chosen.includes(a.slug)} onChange={() => toggleAccount(a.slug)} disabled={busy} /><span>{a.name}</span></label>)}</div>
          <ul className="notif-rules"><li><Check size={13} />Agrupadas por ativo, no máximo 1 a cada 4 h</li><li><Check size={13} />Até {state.rules?.daily ?? 8} por dia · silêncio das 22h às 7h</li></ul>
          <div className="notif-actions">{on
            ? <><button className="btn" disabled={busy} onClick={activate}>Salvar carteiras</button><button className="btn btn-outline" disabled={busy} onClick={test}>Enviar teste</button><button className="text-link" disabled={busy} onClick={deactivate}>Desativar</button></>
            : <button className="btn" disabled={busy} onClick={activate}><Bell size={15} />Ativar notificações</button>}</div>
        </>}
      {message && <p className="notif-message" role="status">{message}</p>}
    </motion.div>}</AnimatePresence>
  </div>;
}
