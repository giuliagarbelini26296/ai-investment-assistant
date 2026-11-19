import { useEffect, useRef, useState } from 'react';

export async function fetchPortfolio(params = {}, signal) {
  const query = new URLSearchParams(params);
  const response = await fetch(`/api/v1/portfolio/?${query}`, {
    credentials: 'same-origin', cache: 'no-store', signal,
    headers: { Accept: 'application/json' },
  });
  if (response.status === 401) {
    const error = new Error('Sua sessão expirou. Entre novamente para consultar a carteira.');
    error.authentication = true;
    throw error;
  }
  if (!response.ok) throw new Error('Não foi possível consultar o portal. Tente novamente.');
  const data = await response.json();
  if (data.meta?.schemaVersion !== '1.0' || !Array.isArray(data.totals)) {
    throw new Error('Resposta incompatível do portal. Recarregue a página.');
  }
  return data;
}

export function usePortfolio(account = 'ana') {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [refreshing, setRefreshing] = useState(false);
  const [version, setVersion] = useState(0);
  const reload = () => setVersion(v => v + 1);
  useEffect(() => {
    const controller = new AbortController();
    setData(null); setError(null);
    async function load() {
      setRefreshing(true);
      try {
        const next = await fetchPortfolio({account}, controller.signal);
        if (controller.signal.aborted) return;
        setData(next); setError(null);
      } catch (e) {
        if (e.name !== 'AbortError') setError(e);
      } finally { if (!controller.signal.aborted) setRefreshing(false); }
    }
    load();
    const interval = setInterval(load, 60000);
    return () => { controller.abort(); clearInterval(interval); };
  }, [version, account]);
  return { data: data?.meta.account === account ? data : null, error, refreshing, reload };
}

export function useFilteredPortfolio(data, broker, result, search, sort) {
  const [state, setState] = useState({ rows: data.positions, totals: data.totals, loading: true, error: null });
  const requestKey = JSON.stringify([data.meta.account, broker, result, search, sort.field, sort.dir, data.meta.generatedAt]);
  const reuseBase = broker === 'all' && result === 'all' && !search && sort.field === 'country' && sort.dir === 1;
  const keyRef = useRef(null);
  useEffect(() => {
    if (reuseBase) return;
    const controller = new AbortController();
    keyRef.current = requestKey;
    setState({ rows: [], totals: [], loading: true, error: null });
    const timer = setTimeout(async () => {
      try {
        const filtered = await fetchPortfolio({ account: data.meta.account, broker, result, search, sort: sort.field,
          direction: sort.dir === 1 ? 'asc' : 'desc', history: '0' }, controller.signal);
        if (keyRef.current === requestKey && !controller.signal.aborted) {
          setState({ rows: filtered.positions, totals: filtered.totals, loading: false, error: null });
        }
      } catch (error) {
        if (error.name !== 'AbortError' && keyRef.current === requestKey) {
          setState({ rows: [], totals: [], loading: false, error });
        }
      }
    }, search ? 220 : 0);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [requestKey, reuseBase]);
  // Never render an old filter's rows while a different request is pending.
  if (reuseBase) return { rows: data.positions, totals: data.totals, loading: false, error: null };
  return keyRef.current === requestKey ? state : { rows: [], totals: [], loading: true, error: null };
}

export function totalFor(data, currency) {
  return data.totals.find(t => t.currency === currency) || {
    currency, count: 0, quoted: 0, dayCount: 0, invested: '0',
    value: null, gain: null, returnPct: null, day: null, dayPct: null,
  };
}
