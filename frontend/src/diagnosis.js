import Decimal from 'decimal.js-light';
const dec=v=>v===null||v===undefined||v===''?null:new Decimal(v);
const assetClass=p=>p.market==='CRIPTO'?'Cripto':p.market==='B3'&&p.symbol.endsWith('34')?'BDRs na B3':p.market==='B3'?'Ações brasileiras':'Ações no exterior';
export function diagnosis(positions,currency){
 const rows=positions.filter(p=>p.currency===currency&&dec(p.quantity)?.gt(0));
 const priced=rows.filter(p=>dec(p.value)!==null&&dec(p.price)?.gt(0));
 const complete=priced.length===rows.length&&rows.length>0;
 const value=priced.reduce((s,p)=>s.plus(p.value),new Decimal(0));
 const byValue=[...priced].sort((a,b)=>dec(b.value).cmp(a.value));
 const gains=rows.filter(p=>dec(p.totalGain)!==null).sort((a,b)=>dec(b.totalGain).cmp(a.totalGain));
 const classes=new Map();for(const p of priced){const k=assetClass(p);classes.set(k,(classes.get(k)||new Decimal(0)).plus(p.value));}
 return {rows,complete,priced:priced.length,missing:rows.length-priced.length,
  allocation:complete&&value.gt(0)?byValue.map(p=>({symbol:p.symbol,value:Number(p.value),weight:dec(p.value).div(value).times(100).toNumber()})):[],
  classes:complete&&value.gt(0)?[...classes].map(([name,v])=>({name,value:v.toNumber(),weight:v.div(value).times(100).toNumber()})):[],
  top:complete&&value.gt(0)?{symbol:byValue[0].symbol,weight:dec(byValue[0].value).div(value).times(100).toNumber()}:null,
  winners:gains.filter(p=>dec(p.totalGain).gt(0)),losers:gains.filter(p=>dec(p.totalGain).lt(0)).reverse(),
  delayed:rows.filter(p=>p.quoteStatus==='atrasado').length,
  closed:rows.filter(p=>p.quoteStatus==='mercado_fechado').length,
  unspecified:rows.filter(p=>p.quoteStatus==='nao_declarado').length,
  invalid:rows.filter(p=>!['atual','atrasado','nao_declarado','mercado_fechado','spot'].includes(p.quoteStatus)).length,
 };
}
