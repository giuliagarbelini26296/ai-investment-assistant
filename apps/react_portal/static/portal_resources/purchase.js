(function () {
  'use strict';
  var form = document.getElementById('quick-purchase-form');
  var area = document.getElementById('purchase-live');
  var controller, timer, generation = 0;
  function money(value, currency) {
    if (value === null || value === undefined) return '—';
    return (currency === 'USD' ? 'US$ ' : 'R$ ') + Number(value).toLocaleString('pt-BR',{minimumFractionDigits:2,maximumFractionDigits:2});
  }
  function qty(value) { return Number(value).toLocaleString('pt-BR',{maximumFractionDigits:10}); }
  function text(tag, value, cls) {
    var node = document.createElement(tag); node.textContent = value;
    if (cls) node.className = cls;
    return node;
  }
  function render(data) {
    area.replaceChildren();
    area.appendChild(text('p',data.symbol+' · '+data.broker+' · '+data.currency,'purchase-symbol'));
    [['Quantidade atual',qty(data.before.quantity)],['Preço médio atual',money(data.before.averagePrice,data.currency)],
     ['Total desta compra',money(data.purchase.total,data.currency)],['Quantidade após a compra',qty(data.after.quantity)],
     ['Novo preço médio',money(data.after.averagePrice,data.currency)]].forEach(function (row,index) {
      var line=text('div','',index===4?'purchase-live-row purchase-live-result':'purchase-live-row');
      line.append(text('span',row[0]),text('strong',row[1]));area.appendChild(line);
    });
    area.appendChild(text('p','A quantidade atual já inclui o saldo conciliado. Não repita compras que já estejam lançadas.','nota'));
  }
  function load() {
    if (!form) return;
    var key = ++generation;
    if (controller) controller.abort();
    var params = new URLSearchParams();
    ['asset_choice','quantity','unit_price','trade_date','confirmar_valores','currency'].forEach(function (name) {
      var el=form.elements[name];
      if (el && (el.type!=='checkbox' || el.checked)) params.set(name,el.value);
    });
    if (!params.get('asset_choice') || !params.get('quantity') || !params.get('unit_price')) {
      area.replaceChildren(text('p','Escolha o ativo e preencha quantidade e preço pago.','nota'));return;
    }
    controller = new AbortController();
    area.replaceChildren(text('p','Calculando com sua posição atual…','nota'));
    fetch(form.dataset.previewUrl+'?'+params.toString(),{credentials:'same-origin',cache:'no-store',signal:controller.signal})
      .then(function (response) { if(response.status===401)throw Error('Sua sessão expirou. Entre novamente no portal.');return response.json(); })
      .then(function (data) {
        if(key!==generation)return;
        if(data.valid)render(data);
        else {var errors=[];Object.values(data.errors||{}).forEach(function(rows){rows.forEach(function(e){errors.push(e.message);});});area.replaceChildren(text('p',errors.join(' ')||'Confira os campos informados.','purchase-preview-error'));}
      }).catch(function (error) { if(error.name!=='AbortError'&&key===generation)area.replaceChildren(text('p',error.message||'Não foi possível calcular. Use Conferir compra para tentar novamente.','purchase-preview-error')); });
  }
  if(form) {
    form.addEventListener('input',function(){clearTimeout(timer);timer=setTimeout(load,220);});
    form.addEventListener('change',function(){clearTimeout(timer);load();});
    load();
  }
  var confirm = document.getElementById('purchase-confirm-form');
  if(confirm)confirm.addEventListener('submit',function(event){
    if(event.submitter&&event.submitter.value==='confirm'){
      // Keep the submitted button's value while preventing repeated clicks.
      setTimeout(function(){confirm.querySelectorAll('button').forEach(function(b){b.disabled=true;});},0);
    }
  });
})();
