import { test } from 'node:test';
import assert from 'node:assert/strict';
import { money, percent, quantity, comparisonHistory, csv } from '../src/finance.js';

test('Campos indisponíveis não aparecem como zero',()=>{
 assert.equal(money(null,'USD'),'—');assert.equal(percent(null),'—');
 assert.equal(money('0','BRL'),'R$ 0,00');assert.equal(percent('0'),'0,00%');
});
test('Quantidade fracionária e preço médio preservados na apresentação',()=>{
 assert.equal(quantity('100.47592'),'100,47592');assert.equal(money('214.57','USD'),'US$ 214,57');
});
test('Ocultação não expõe o valor monetário',()=>{assert.equal(money('23420.7381544','USD',true),'••••••');});
test('Comparação normaliza na mesma sessão apesar de séries com inícios diferentes',()=>{
 const data=comparisonHistory([
  {symbol:'A',history:[{date:'2026-11-01',close:'200'},{date:'2026-11-02',close:'100'},{date:'2026-11-03',close:'110'}]},
  {symbol:'B',history:[{date:'2026-11-02',close:'50'},{date:'2026-11-03',close:'60'}]}
 ]);
 assert.equal(data[0].date,'2026-11-02');assert.equal(data[0].A,100);assert.equal(data[0].B,100);
 assert(Math.abs(data[1].A-110)<1e-10);assert.equal(data[1].B,120);
});
test('Sem sessão comum a comparação não inventa uma base',()=>{
 assert.deepEqual(comparisonHistory([{symbol:'A',history:[{date:'2026-11-01',close:'100'}]},
  {symbol:'B',history:[{date:'2026-11-02',close:'100'}]}]),[]);
});
test('CSV mantém fração e moeda sem converter para BRL',()=>{
 const result=csv([{symbol:'TTWO',broker:'Avenue',currency:'USD',quantity:'100.47592',averagePrice:'214.57',costBasis:'21559.1181544'}]);
 assert(result.includes('"USD"'));assert(result.includes('"100.47592"'));assert(result.includes('"214.57"'));
 assert.equal(result.split('\r\n').length,2);
});
