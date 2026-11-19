import React,{useState} from 'react';
import {Calculator,RefreshCw} from 'lucide-react';
import {money,quantity,percent,dateTime,tone} from './finance';
import {calculatePurchase} from './purchaseMath.js';
export function PurchaseCalculator({payload,hidden}){
 const [symbol,setSymbol]=useState(payload.positions[0]?.symbol||payload.assets[0]?.symbol||'');
 const [forms,setForms]=useState({});
 const held=payload.positions.find(p=>p.symbol===symbol),asset=payload.assets.find(a=>a.symbol===symbol);
 const currency=held?.currency||asset?.native_currency||'BRL',market=held?.market||asset?.market;
 const initial={quantity:'',price:held?.price==null?'':String(held.price),fees:'0',saleFees:'0',budget:'',target:''};
 const form=forms[symbol]||initial;
 const change=(name,value)=>setForms(f=>({...f,[symbol]:{...form,[name]:value}}));
 const result=calculatePurchase(held,form,market);
 const field=(name,label,placeholder='0')=><label>{label}<input aria-label={label} inputMode="decimal" autoComplete="off" placeholder={placeholder} value={form[name]} onChange={e=>change(name,e.target.value)}/></label>;
 if(hidden)return <p className="workbook-empty">Mostre os valores para usar a calculadora.</p>;
 return <div><div className="workbook-intro"><div><h2><Calculator size={20}/> Calculadora de compra</h2><p>Veja o custo de uma nova compra e o preço médio resultante. Cálculos instantâneos para a carteira de {payload.owner}.</p></div></div>
 <div className="workbook-settings"><label>Ativo<select aria-label="Ativo da calculadora" value={symbol} onChange={e=>setSymbol(e.target.value)}>{payload.assets.map(a=><option key={a.symbol} value={a.symbol}>{a.symbol} · {a.native_currency}</option>)}</select></label><article><span>Posição atual · {currency}</span><strong>{quantity(held?.quantity||0)} unidades</strong><small>Investido: {money(held?.costBasis||0,currency)} · PM: {held?money(held.averagePrice,currency):'Sem posição'}</small></article></div>
 <div className="workbook-settings">{field('quantity','Quantidade a comprar')}{field('price',`Preço por unidade (${currency})`)}{field('fees',`Taxas da compra (${currency})`)}{field('budget',`Orçamento livre (${currency}, opcional)`,'Opcional')}{field('target',`Preço de venda hipotético (${currency}, opcional)`,'Opcional')}{field('saleFees',`Taxas estimadas da venda (${currency})`)}</div>
 {held?.price!=null&&<div className="workbook-intro"><button className="btn btn-outline" onClick={()=>change('price',String(held.price))}><RefreshCw size={15}/>Usar cotação do portal</button><p>{money(held.price,currency)} · {held.quoteSource||'Fonte não informada'} · {dateTime(held.quoteAt||held.collectedAt)}. A cotação é uma referência; não garante execução.</p></div>}
 {result.ok?<><div className="workbook-settings"><article><span>Desembolso da compra</span><strong>{money(result.purchase,currency)}</strong><small>Inclui as taxas informadas.</small></article><article><span>Novo preço médio</span><strong>{money(result.average,currency)}</strong><small>{quantity(result.newQuantity)} unidades · custo total {money(result.newCost,currency)}</small></article><article><span>Preço de equilíbrio</span><strong>{money(result.breakEven,currency)}</strong><small>Considera as taxas de venda informadas, antes dos impostos.</small></article>{result.maxQuantity!==null&&<article><span>Quantidade que cabe no orçamento</span><strong>{quantity(result.maxQuantity)}</strong><small>Estimativa matemática; não é uma indicação de compra.</small></article>}{result.future&&<article><span>Resultado hipotético da posição inteira</span><strong className={tone(result.future.gain)}>{money(result.future.gain,currency)}</strong><small>{percent(result.future.returnPct)} · após taxas informadas, antes dos impostos.</small></article>}</div></>:<p className="workbook-note" role="status">{form.quantity?result.error:'Informe a quantidade e o preço para calcular.'}</p>}
 <p className="workbook-note">Novo preço médio = (custo atual + quantidade comprada × preço + taxas da compra) ÷ quantidade total. Use vírgula ou ponto decimal, sem separador de milhares. Valores calculados na moeda do ativo. Esta calculadora não salva lançamentos, não altera posições e não envia ordens.</p>
 </div>;
}
