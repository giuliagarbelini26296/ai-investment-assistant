import Decimal from 'decimal.js-light';
import {inputDecimal} from './simulation.js';
export function calculatePurchase(position,inputs,market='B3'){
 try{
  if(position&&Number(position.quantity)>0&&position.costBasis==null)throw Error('A posição está sem base de custo válida.');
  const oldQty=new Decimal(String(position?.quantity??0)),oldCost=new Decimal(String(position?.costBasis??0));
  if(oldQty.lt(0)||oldCost.lt(0)||(oldQty.eq(0)&&!oldCost.eq(0)))throw Error('A posição está sem base de custo válida.');
  const qty=inputDecimal(inputs.quantity,{integer:market==='B3'}),price=inputDecimal(inputs.price);
  const fees=inputDecimal(inputs.fees||'0',{zero:true}),saleFees=inputDecimal(inputs.saleFees||'0',{zero:true});
  const purchase=qty.times(price).plus(fees),newQty=oldQty.plus(qty),newCost=oldCost.plus(purchase);
  let maxQuantity=null;
  if(String(inputs.budget??'').trim()){
   const available=inputDecimal(inputs.budget,{zero:true}).minus(fees);
   maxQuantity=available.lt(0)?'0':available.div(price).toDecimalPlaces(market==='B3'?0:8,Decimal.ROUND_DOWN).toString();
  }
  let future=null;
  if(String(inputs.target??'').trim()){
   const target=inputDecimal(inputs.target),net=target.times(newQty).minus(saleFees),gain=net.minus(newCost);
   future={gain:gain.toString(),net:net.toString(),returnPct:gain.div(newCost).times(100).toString()};
  }
  return {ok:true,purchase:purchase.toString(),newQuantity:newQty.toString(),newCost:newCost.toString(),average:newCost.div(newQty).toString(),breakEven:newCost.plus(saleFees).div(newQty).toString(),maxQuantity,future};
 }catch(e){return {ok:false,error:e.message};}
}
