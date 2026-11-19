// Camada de animação do portal (Framer Motion).
// Respeita "reduzir movimento" do sistema via <MotionConfig reducedMotion="user">.
import React, { Suspense, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { AnimatePresence, MotionConfig, motion, animate, stagger, useAnimate, useReducedMotion } from 'framer-motion';
import { Routes, useLocation } from 'react-router-dom';

export { AnimatePresence, MotionConfig, motion };

export const ease = [0.22, 1, 0.36, 1];
export const spring = { type: 'spring', stiffness: 420, damping: 38 };

// Blocos que entram em cascata quando uma página abre.
const REVEAL = [
  '.wealth-heading', '.page-heading', '.wealth-hero', '.wealth-metrics > *', '.snapshot-note',
  '.panel', '.country-card', '.history-mini', '.source-card', '.method-note',
].join(',');

function PageReveal({ children }) {
  const [scope, run] = useAnimate();
  const reduce = useReducedMotion();
  useLayoutEffect(() => {
    if (reduce || !scope.current) return;
    // Só o primeiro nível de cada bloco, para não animar painéis dentro de painéis.
    const items = [...scope.current.querySelectorAll(REVEAL)]
      .filter((el, _, all) => !all.some(other => other !== el && other.contains(el)))
      .slice(0, 18);
    if (!items.length) return;
    items.forEach(el => { el.style.opacity = '0'; });
    run(items, { opacity: [0, 1], y: [18, 0] }, { duration: 0.5, ease, delay: stagger(0.05, { startDelay: 0.05 }) });
  }, []);
  return (
    <motion.div
      ref={scope}
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -6, transition: { duration: 0.14 } }}
      transition={{ duration: 0.28, ease }}
    >
      {children}
    </motion.div>
  );
}

// Troca de página com saída/entrada suave.
export function AnimatedRoutes({ account, children }) {
  const location = useLocation();
  useEffect(() => { window.scrollTo({ top: 0, behavior: 'instant' in window ? 'instant' : 'auto' }); }, [location.pathname]);
  return (
    <AnimatePresence mode="wait" initial={false}>
      <PageReveal key={account + location.pathname}>
        <Suspense fallback={<div className="page-loading" role="status"><span className="loading-bar"><motion.i animate={{ x: ['-100%', '260%'] }} transition={{ duration: 1.1, repeat: Infinity, ease: 'easeInOut' }} /></span><p>Abrindo…</p></div>}><Routes location={location}>{children}</Routes></Suspense>
      </PageReveal>
    </AnimatePresence>
  );
}

// Número que "conta" até o valor novo. `format` recebe o número e devolve o texto.
export function CountUp({ value, format, hidden = false, duration = 0.9 }) {
  const reduce = useReducedMotion();
  const target = value === null || value === undefined ? null : Number(value);
  const last = useRef(target === null ? null : target * 0.92);
  const [shown, setShown] = useState(last.current ?? target);
  useEffect(() => {
    if (target === null || hidden || reduce) { last.current = target; setShown(target); return; }
    const controls = animate(last.current ?? target, target, {
      duration, ease, onUpdate: v => { last.current = v; setShown(v); },
    });
    return () => controls.stop();
  }, [target, hidden, reduce]);
  return <span className="count-up">{format(hidden || target === null ? value : shown)}</span>;
}

// Overlay + painel lateral (drawer) ou modal central.
export function MotionOverlay({ className = 'overlay', onMouseDown, children }) {
  return (
    <motion.div
      className={className}
      onMouseDown={onMouseDown}
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0, transition: { duration: 0.18, delay: 0.05 } }}
      transition={{ duration: 0.2 }}
    >
      {children}
    </motion.div>
  );
}

export const drawerMotion = {
  initial: { x: '100%' },
  animate: { x: 0, transition: { type: 'spring', stiffness: 340, damping: 36 } },
  exit: { x: '100%', transition: { duration: 0.22, ease: [0.4, 0, 1, 1] } },
};

export const modalMotion = {
  initial: { opacity: 0, scale: 0.96, y: 14 },
  animate: { opacity: 1, scale: 1, y: 0, transition: spring },
  exit: { opacity: 0, scale: 0.97, y: 8, transition: { duration: 0.15 } },
};

export const toastMotion = {
  initial: { opacity: 0, y: 24, scale: 0.96 },
  animate: { opacity: 1, y: 0, scale: 1, transition: spring },
  exit: { opacity: 0, y: 12, scale: 0.98, transition: { duration: 0.16 } },
};
