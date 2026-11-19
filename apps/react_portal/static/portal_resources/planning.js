document.addEventListener('DOMContentLoaded', () => {
  if (!window.Chart) return;
  for (const currency of ['BRL','USD']) {
    const source = document.getElementById(currency), canvas = document.getElementById(`comparison-${currency}`);
    if (!source || !canvas) continue;
    const points = JSON.parse(source.textContent);
    const ink = getComputedStyle(document.documentElement).getPropertyValue('--muted').trim() || '#74857f';
    new Chart(canvas, {type:'line',data:{labels:points.map(p=>p.date.split('-').reverse().join('/')),datasets:[
      {label:'Carteira',data:points.map(p=>Number(p.portfolio)),borderColor:'#318677',pointRadius:2,tension:0},
      {label:currency==='BRL'?'BOVA11':'SPY',data:points.map(p=>Number(p.benchmark)),borderColor:'#c09d60',pointRadius:2,tension:0}
    ]},options:{responsive:true,maintainAspectRatio:false,animation:false,plugins:{legend:{labels:{color:ink}},tooltip:{callbacks:{label:ctx=>`${ctx.dataset.label}: ${ctx.parsed.y.toLocaleString('pt-BR',{maximumFractionDigits:2})}%`}}},scales:{x:{ticks:{color:ink,maxTicksLimit:5},grid:{display:false}},y:{ticks:{color:ink,callback:value=>`${value}%`},grid:{color:'rgba(130,145,140,.12)'}}}}});
  }
});
