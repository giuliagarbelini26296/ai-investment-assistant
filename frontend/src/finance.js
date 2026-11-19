import Decimal from 'decimal.js-light';
export function currencyTotals(rows) {
  return ['BRL','USD'].flatMap(currency=>{
    const group=rows.filter(p=>p.currency===currency);
    if(!group.length)return [];
    const priced=group.filter(p=>p.value!=null&&p.totalGain!=null);
    const sum=(items,key)=>items.reduce((s,p)=>s.plus(p[key]??0),new Decimal(0));
    const invested=sum(group,'costBasis'),cost=sum(priced,'costBasis');
    const value=priced.length?sum(priced,'value'):null,gain=priced.length?sum(priced,'totalGain'):null;
    const daily=group.filter(p=>p.dayGain!=null&&p.dayBase!=null),base=sum(daily,'dayBase');
    const day=daily.length?sum(daily,'dayGain'):null;
    return [{currency,count:group.length,quoted:priced.length,invested:invested.toString(),value:value?.toString()??null,gain:gain?.toString()??null,returnPct:gain&&cost.gt(0)?gain.div(cost).times(100).toString():null,day:day?.toString()??null,dayPct:day&&base.gt(0)?day.div(base).times(100).toString():null}];
  });
}
export const number = value => value === null || value === undefined ? null : Number(value);

export const money = (value, currency, hidden = false) => hidden ? '••••••' : value === null || value === undefined ? '—' : `${Number(value) < 0 ? '−' : ''}${currency === 'USD' ? 'US$' : 'R$'} ${Math.abs(Number(value)).toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
export const quoteMoney = (position, hidden = false) => (!hidden && position?.currency === 'USD' && position?.brokerCode === 'AVENUE' && position?.price != null ? '≈ ' : '') + money(position?.price, position?.currency, hidden);
export const percent = value => value === null || value === undefined ? '—' : `${Number(value) > 0 ? '+' : Number(value) < 0 ? '−' : ''}${Math.abs(Number(value)).toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}%`;
export const quantity = value => Number(value).toLocaleString('pt-BR', { maximumFractionDigits: 10 });
export const tone = value => Number(value) > 0 ? 'positive' : Number(value) < 0 ? 'negative' : 'muted';
export const dateTime = value => value ? new Date(value).toLocaleString('pt-BR', { timeZone: 'America/Sao_Paulo', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }) : 'Não informado';
export const shortDate = value => new Date(`${value}T12:00:00`).toLocaleDateString('pt-BR', { day: '2-digit', month: 'short' });
export function historyFor(position, period = 90) {
  const history = position?.history || [];
  if (!history.length) return [];
  const end = new Date(`${history.at(-1).date}T12:00:00`);
  const start = new Date(end); start.setDate(start.getDate() - period);
  return history.filter(p => new Date(`${p.date}T12:00:00`) >= start).map(p => ({ ...p, close: Number(p.close) }));
}
export function comparisonHistory(positions, period = 90) {
  if (!positions.length) return [];
  const series = positions.map(p => ({ position: p, bars: historyFor(p, period) }));
  const commonDates = series[0].bars.map(b => b.date).filter(date =>
    series.every(s => s.bars.some(b => b.date === date && b.close > 0)));
  const start = commonDates.sort()[0];
  if (!start) return [];
  const byDate = new Map();
  for (const { position, bars } of series) {
    const base = bars.find(b => b.date === start).close;
    for (const bar of bars.filter(b => b.date >= start)) {
      const row = byDate.get(bar.date) || { date: bar.date };
      row[position.symbol] = bar.close / base * 100;
      byDate.set(bar.date, row);
    }
  }
  return [...byDate.values()].sort((a,b) => a.date.localeCompare(b.date));
}
export function csv(positions) {
  const cells = v => `"${String(v ?? '').replaceAll('"', '""')}"`;
  const header = ['Ativo','Corretora','Moeda','Quantidade','Preço médio','Cotação','Investido','Valor atual','Resultado','Retorno (%)','Resultado do dia','Variação do dia (%)','Coleta da cotação'];
  const rows = positions.map(p => [p.symbol,p.broker,p.currency,p.quantity,p.averagePrice,p.price,p.costBasis,p.value,p.totalGain,p.totalReturn,p.dayGain,p.dayPercent,p.collectedAt]);
  return '\uFEFF' + [header,...rows].map(row => row.map(cells).join(';')).join('\r\n');
}
