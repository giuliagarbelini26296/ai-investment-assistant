import Decimal from 'decimal.js-light';
const d=x=>new Decimal(String(x));
export function inputDecimal(value,{zero=false,integer=false}={}){
 const text=String(value??'').trim();
 if(!/^\d+(?:[.,]\d+)?$/.test(text)||text.length>28)throw Error('Preencha os campos com números, sem separador de milhares.');
 const n=d(text.replace(',','.'));
 if(n.lt(0)||(!zero&&n.eq(0))||n.gt('1000000000')||n.decimalPlaces()>10||(integer&&!n.isInteger()))throw Error(integer?'A quantidade na B3 deve ser inteira e não negativa.':'Use um valor válido, com até 10 casas decimais.');
 return n;
}
export function simulate(position,inputs){
 try{
  const qty=d(position.quantity),cost=d(position.costBasis);
  if(qty.lte(0)||cost.lt(0))throw Error('Posição sem base de custo válida.');
  const reference=inputDecimal(inputs.reference),buyQty=inputDecimal(inputs.quantity,{zero:true,integer:position.market==='B3'});
  const buyPrice=buyQty.eq(0)?reference:inputDecimal(inputs.price);
  const fees=inputDecimal(inputs.fees,{zero:true}),saleFees=inputDecimal(inputs.saleFees,{zero:true});
  const target=inputDecimal(inputs.target),cash=buyQty.times(buyPrice).plus(fees),newQty=qty.plus(buyQty),newCost=cost.plus(cash);
  const result=price=>{
   const p=d(price),before=p.times(qty).minus(cost).minus(saleFees),after=p.times(newQty).minus(newCost).minus(saleFees);
   return {price:p.toString(),before:before.toString(),after:after.toString(),increment:after.minus(before).toString(),value:p.times(newQty).toString(),returnPct:newCost.gt(0)?after.div(newCost).times(100).toString():null};
  };
  return {ok:true,cash:cash.toString(),newQuantity:newQty.toString(),newCost:newCost.toString(),average:newCost.div(newQty).toString(),oldAverage:cost.div(qty).toString(),breakEven:newCost.plus(saleFees).div(newQty).toString(),reference:reference.toString(),targetChange:target.div(reference).minus(1).times(100).toString(),current:result(reference),future:result(target),scenarios:[-10,-5,0,5,10].map(percent=>({percent,...result(reference.times(d(1).plus(d(percent).div(100))))})),curve:Array.from({length:13},(_,i)=>{const percent=-30+i*5;return {percent,...result(reference.times(d(1).plus(d(percent).div(100))))};})};
 }catch(e){return {ok:false,error:e.message||'Confira os valores informados.'};}
}
export function targetFromPercent(reference,percent){try{return inputDecimal(reference).times(d(1).plus(d(percent).div(100))).toFixed(4);}catch{return '';}}
