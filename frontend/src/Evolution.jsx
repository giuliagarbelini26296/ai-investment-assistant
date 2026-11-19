import React, { useEffect, useState } from 'react';
import { ResponsiveContainer, AreaChart, Area, LineChart, Line, XAxis, YAxis, Tooltip, CartesianGrid, ReferenceLine, ReferenceArea } from 'recharts';
import { AlertCircle, CalendarClock, Info, TrendingUp } from 'lucide-react';
import { money, percent, shortDate, tone } from './finance';

const SERIES = { portfolio: '#245e50', benchmark: '#d2ad6a', cdi: '#809db7' };

function Reconstructed({ from, to }) {
  if (!from || !to) return null;
  return <ReferenceArea x1={from} x2={to} fill="var(--muted)" fillOpacity={0.07} stroke="none" ifOverflow="extendDomain"
    label={{ value: 'Reconstruído (BTG)', position: 'insideTopLeft', fill: 'var(--muted)', fontSize: 10 }} />;
}

const reconstructedRange = rows => {
  const r = rows.filter(x => x.source === 'btg');
  return r.length ? [r[0].date, r[r.length - 1].date] : [null, null];
};

function useEvolution(account, version) {
  const [state, setState] = useState({ data: null, error: null });
  useEffect(() => {
    const c = new AbortController();
    setState({ data: null, error: null });
    fetch(`/api/v1/evolution/?account=${account}`, { credentials: 'same-origin', cache: 'no-store', signal: c.signal })
      .then(r => r.json().then(body => { if (!r.ok) throw new Error(body.error?.message || 'Não foi possível consultar a evolução.'); return body; }))
      .then(data => setState({ data, error: null }))
      .catch(e => { if (e.name !== 'AbortError') setState({ data: null, error: e.message }); });
    return () => c.abort();
  }, [account, version]);
  return state;
}

function Pct({ label, value, color }) {
  return <div className="evo-stat"><span><i style={{ background: color }} />{label}</span><strong className={tone(value)}>{value === null || value === undefined ? '—' : percent(value)}</strong></div>;
}

function PatrimonyChart({ rows, currency, hidden }) {
  if (rows.length < 2) {
    const first = rows[0];
    return <div className="evo-empty"><CalendarClock size={22} />
      <div><strong>{first ? `Registro diário iniciado em ${shortDate(first.date)}` : 'O registro diário começa no próximo ciclo do coletor.'}</strong>
        <p>O portal grava uma foto do patrimônio a cada dia. A curva aparece a partir do segundo dia e cresce sozinha.</p>
        {first && <p className="evo-today">Hoje: <b>{money(first.value, currency, hidden)}</b> · investido {money(first.invested, currency, hidden)}{!first.complete && ' · algumas posições ainda sem cotação'}</p>}
      </div></div>;
  }
  const data = rows.map(r => ({ date: r.date, value: r.value === null ? null : Number(r.value), invested: Number(r.invested), complete: r.complete }));
  const [from, to] = reconstructedRange(rows);
  return <div className="evo-chart"><ResponsiveContainer width="100%" height="100%"><AreaChart data={data} margin={{ top: 10, right: 6, left: 0, bottom: 0 }}>
    <defs><linearGradient id="evoFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={SERIES.portfolio} stopOpacity={0.22} /><stop offset="100%" stopColor={SERIES.portfolio} stopOpacity={0} /></linearGradient></defs>
    <CartesianGrid vertical={false} stroke="var(--chart-grid)" strokeDasharray="3 5" />
    <XAxis dataKey="date" tickFormatter={shortDate} axisLine={false} tickLine={false} minTickGap={36} tick={{ fill: 'var(--muted)', fontSize: 11 }} dy={8} />
    <YAxis domain={['auto', 'auto']} width={hidden ? 8 : 64} axisLine={false} tickLine={false} tick={hidden ? false : { fill: 'var(--muted)', fontSize: 11 }} tickFormatter={v => Number(v).toLocaleString('pt-BR', { maximumFractionDigits: 0 })} />
    <Tooltip formatter={(v, n) => [money(v, currency, hidden), n === 'value' ? 'Patrimônio' : 'Investido']} labelFormatter={shortDate} contentStyle={{ background: 'var(--panel)', border: '1px solid var(--border)', borderRadius: 12, fontSize: 12 }} />
    <Reconstructed from={from} to={to} />
    <Area type="monotone" dataKey="invested" stroke="var(--muted)" strokeDasharray="5 5" strokeWidth={1.5} fill="none" dot={false} animationDuration={700} />
    <Area type="monotone" dataKey="value" stroke={SERIES.portfolio} strokeWidth={2.4} fill="url(#evoFill)" connectNulls={false} animationDuration={900} />
  </AreaChart></ResponsiveContainer></div>;
}

function PerformanceChart({ perf, currency }) {
  const points = perf.points.map(p => ({ date: p.date, portfolio: Number(p.portfolio), benchmark: Number(p.benchmark), cdi: p.cdi === undefined || p.cdi === null ? null : Number(p.cdi) }));
  const blocking = !points.length;
  return <>
    <div className="evo-stats">
      <Pct label="Carteira" value={perf.portfolio} color={SERIES.portfolio} />
      <Pct label={perf.benchmarkLabel} value={perf.reference} color={SERIES.benchmark} />
      {currency === 'BRL' && <Pct label="CDI" value={perf.cdi} color={SERIES.cdi} />}
      {perf.start && <div className="evo-stat evo-period"><span>Período</span><strong>{shortDate(perf.start)} → {shortDate(perf.end)}</strong></div>}
    </div>
    {blocking ? <div className="evo-empty"><AlertCircle size={22} /><div><strong>Comparação ainda indisponível nesta moeda.</strong><p>{perf.warnings[0]}</p></div></div>
      : points.length < 3 ? <div className="evo-empty"><CalendarClock size={22} /><div><strong>Poucos pregões registrados até agora.</strong><p>A comparação começa em {shortDate(perf.start)} e ganha um ponto a cada fechamento.</p></div></div>
      : <div className="evo-chart"><ResponsiveContainer width="100%" height="100%"><LineChart data={points} margin={{ top: 10, right: 6, left: 0, bottom: 0 }}>
        <CartesianGrid vertical={false} stroke="var(--chart-grid)" strokeDasharray="3 5" />
        <XAxis dataKey="date" tickFormatter={shortDate} axisLine={false} tickLine={false} minTickGap={36} tick={{ fill: 'var(--muted)', fontSize: 11 }} dy={8} />
        <YAxis width={44} axisLine={false} tickLine={false} tick={{ fill: 'var(--muted)', fontSize: 11 }} tickFormatter={v => `${Number(v).toLocaleString('pt-BR', { maximumFractionDigits: 1 })}%`} />
        <ReferenceLine y={0} stroke="var(--border)" />
        {perf.reconstructedUntil && <Reconstructed from={perf.start} to={perf.reconstructedUntil < perf.end ? perf.reconstructedUntil : perf.end} />}
        <Tooltip formatter={(v, n) => [percent(v), n === 'portfolio' ? 'Carteira' : n === 'cdi' ? 'CDI' : perf.benchmarkLabel]} labelFormatter={shortDate} contentStyle={{ background: 'var(--panel)', border: '1px solid var(--border)', borderRadius: 12, fontSize: 12 }} />
        <Line dataKey="benchmark" stroke={SERIES.benchmark} strokeWidth={1.8} dot={false} animationDuration={800} />
        {currency === 'BRL' && <Line dataKey="cdi" stroke={SERIES.cdi} strokeWidth={1.8} strokeDasharray="4 4" dot={false} connectNulls={false} animationDuration={800} />}
        <Line dataKey="portfolio" stroke={SERIES.portfolio} strokeWidth={2.6} dot={false} animationDuration={1000} />
      </LineChart></ResponsiveContainer></div>}
  </>;
}

export function Evolution({ data, hidden }) {
  const account = data.meta.account;
  const currencies = data.totals.map(t => t.currency);
  const [currency, setCurrency] = useState(currencies[0] || 'BRL');
  const [version, setVersion] = useState(0);
  const { data: evo, error } = useEvolution(account, version);
  useEffect(() => { if (!currencies.includes(currency)) setCurrency(currencies[0] || 'BRL'); }, [account]);
  const rows = evo?.snapshots?.[currency] || [];
  const perf = evo?.performance?.[currency];
  return <section className="panel evolution-panel">
    <div className="panel-heading"><div><div className="section-kicker">EVOLUÇÃO REAL</div><h2>Seu patrimônio ao longo do tempo</h2></div>
      {currencies.length > 1 && <div className="segment">{currencies.map(c => <button key={c} className={currency === c ? 'active' : ''} onClick={() => setCurrency(c)}>{c === 'BRL' ? 'Reais · BRL' : 'Dólares · USD'}</button>)}</div>}
    </div>
    {error ? <div className="evo-empty"><AlertCircle size={22} /><div><strong>{error}</strong><button className="btn btn-outline" onClick={() => setVersion(v => v + 1)}>Tentar novamente</button></div></div>
      : !evo ? <div className="evo-loading" role="status"><span className="loader" /> Consultando a evolução…</div>
      : <div className="evolution-grid">
        <div><h3 className="evo-title">Patrimônio em {currency}</h3><PatrimonyChart rows={rows} currency={currency} hidden={hidden} />
          {rows.length > 1 && <div className="evo-legend"><span><i style={{ background: SERIES.portfolio }} />Valor de mercado</span><span><i className="dash" />Capital investido</span></div>}</div>
        <div><h3 className="evo-title"><TrendingUp size={15} /> Rentabilidade comparada</h3>
          {perf ? <PerformanceChart perf={perf} currency={currency} /> : <div className="evo-empty"><Info size={22} /><div><p>{evo.performanceNote || 'Sem posições nesta moeda.'}</p></div></div>}
        </div>
      </div>}
    <div className="table-footnote"><Info size={13} /><p>Somente dados reais: o portal fotografa o patrimônio uma vez por dia; os dias anteriores ao primeiro registro (faixa cinza) foram reconstruídos com as quantidades e valores aplicados do histórico de movimentações do BTG e os fechamentos reais de cada ativo. A rentabilidade desconta compras e vendas e soma proventos (Modified Dietz). Ibovespa e S&amp;P 500 são medidos pelos ETFs BOVA11 e SPY; o CDI vem da série diária do Banco Central. Sem taxas não registradas ou variação cambial.</p></div>
  </section>;
}
