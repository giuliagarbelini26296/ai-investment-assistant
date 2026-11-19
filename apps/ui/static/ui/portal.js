/* Portal de Ações — JS sem dependências externas além do Chart.js local. Nada sai para a rede fora do portal. */
(function () {
  "use strict";

  // estado dos gráficos (declarado antes de tudo: o tema redesenha ao alternar)
  var graficos = { evolucao: null, alocacao: null, historico: null, comparacao: null };
  var diasEvolucao = 90;

  // --- Tema (claro/escuro) ---------------------------------------------------------------------
  var raiz = document.documentElement;
  function temaAtual() { return raiz.getAttribute("data-theme") || "dark"; }
  function aplicarTema(t, redesenhar) {
    raiz.setAttribute("data-theme", t);
    try { localStorage.setItem("portal-tema", t); } catch (e) { /* armazenamento indisponível */ }
    var b = document.getElementById("alternar-tema");
    if (b) { b.textContent = t === "dark" ? "☀︎" : "☾"; b.setAttribute("aria-label", t === "dark" ? "mudar para tema claro" : "mudar para tema escuro"); }
    if (redesenhar) { redesenharGraficos(); }
  }
  var botaoTema = document.getElementById("alternar-tema");
  if (botaoTema) {
    aplicarTema(temaAtual(), false);
    botaoTema.addEventListener("click", function () { aplicarTema(temaAtual() === "dark" ? "light" : "dark", true); });
  }
  function cor(nome) { return getComputedStyle(raiz).getPropertyValue(nome).trim(); }
  function paleta() { return [cor("--primaria"), cor("--alta"), cor("--aviso"), cor("--queda"), cor("--roxo"), cor("--ciano"), cor("--texto-2")]; }
  function opcoesBase() {
    if (!window.Chart) { return; }
    Chart.defaults.color = cor("--texto-2");
    Chart.defaults.borderColor = cor("--borda");
    Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  }

  // --- Utilidades --------------------------------------------------------------------------------
  var fmt = function (v, moeda) {
    return (moeda === "BRL" ? "R$ " : moeda === "USD" ? "US$ " : moeda + " ") + Number(v).toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  };
  var fmtCurto = function (v, moeda) {
    var n = Number(v), s = moeda === "BRL" ? "R$ " : "US$ ";
    if (Math.abs(n) >= 1e6) { return s + (n / 1e6).toLocaleString("pt-BR", { maximumFractionDigits: 2 }) + "M"; }
    if (Math.abs(n) >= 1e3) { return s + (n / 1e3).toLocaleString("pt-BR", { maximumFractionDigits: 1 }) + "k"; }
    return s + n.toLocaleString("pt-BR", { maximumFractionDigits: 0 });
  };
  function csrf() {
    var m = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
    if (m) { return decodeURIComponent(m[1]); }
    var i = document.querySelector("input[name=csrfmiddlewaretoken]");
    return i ? i.value : "";
  }
  function toast(texto, tipo) {
    var area = document.getElementById("toasts");
    if (!area) { return; }
    var el = document.createElement("div");
    el.className = "toast " + (tipo || "");
    el.textContent = texto;
    area.appendChild(el);
    setTimeout(function () { el.classList.add("sumindo"); setTimeout(function () { el.remove(); }, 400); }, 6000);
  }

  // --- Ordenação de tabelas .ordenavel -----------------------------------------------------------
  function ativarOrdenacao(escopo) {
    (escopo || document).querySelectorAll("table.ordenavel").forEach(function (tabela) {
      tabela.querySelectorAll("th[data-sort]").forEach(function (th, idx) {
        th.style.cursor = "pointer";
        th.addEventListener("click", function () {
          var tipo = th.getAttribute("data-sort");
          var asc = th.getAttribute("data-dir") !== "asc";
          tabela.querySelectorAll("th").forEach(function (o) { o.removeAttribute("data-dir"); });
          th.setAttribute("data-dir", asc ? "asc" : "desc");
          var colIdx = Array.prototype.indexOf.call(th.parentNode.children, th);
          var linhas = Array.prototype.slice.call(tabela.tBodies[0].rows);
          linhas.sort(function (a, b) {
            var ca = a.cells[colIdx], cb = b.cells[colIdx];
            var va = ca ? (ca.getAttribute("data-value") || ca.textContent.trim()) : "";
            var vb = cb ? (cb.getAttribute("data-value") || cb.textContent.trim()) : "";
            if (tipo === "num") { va = parseFloat(va) || 0; vb = parseFloat(vb) || 0; return asc ? va - vb : vb - va; }
            return asc ? va.localeCompare(vb, "pt-BR") : vb.localeCompare(va, "pt-BR");
          });
          linhas.forEach(function (r) { tabela.tBodies[0].appendChild(r); });
        });
      });
    });
  }

  // --- Gráficos da carteira ----------------------------------------------------------------------
  function destruir(chave) { if (graficos[chave]) { graficos[chave].destroy(); graficos[chave] = null; } }
  var evolucaoDados = null;      // último JSON da evolução (para seleção e sparkline do card)
  var selecaoEvolucao = [];      // índices clicados (A, B)

  // Plugin: sombreia o trecho selecionado no gráfico de evolução.
  var pluginSelecao = {
    id: "selecao",
    afterDatasetsDraw: function (chart) {
      if (selecaoEvolucao.length !== 2 || !chart.scales.x) { return; }
      var a = Math.min.apply(null, selecaoEvolucao), b = Math.max.apply(null, selecaoEvolucao);
      var x = chart.scales.x, area = chart.chartArea, ctx = chart.ctx;
      var x1 = x.getPixelForValue(a), x2 = x.getPixelForValue(b);
      ctx.save(); ctx.fillStyle = cor("--primaria") + "22"; ctx.fillRect(x1, area.top, x2 - x1, area.bottom - area.top);
      ctx.strokeStyle = cor("--primaria"); ctx.setLineDash([3, 3]);
      [x1, x2].forEach(function (px) { ctx.beginPath(); ctx.moveTo(px, area.top); ctx.lineTo(px, area.bottom); ctx.stroke(); });
      ctx.restore();
    }
  };

  function mostrarSelecao() {
    var el = document.getElementById("evolucao-selecao");
    if (!el || !evolucaoDados) { return; }
    if (selecaoEvolucao.length < 2) { el.textContent = selecaoEvolucao.length === 1 ? "clique no ponto final…" : ""; return; }
    var a = Math.min.apply(null, selecaoEvolucao), b = Math.max.apply(null, selecaoEvolucao);
    var pa = evolucaoDados.pontos[a], pb = evolucaoDados.pontos[b];
    var va = parseFloat(pa.valor), vb = parseFloat(pb.valor);
    var pct = va ? ((vb - va) / va) * 100 : 0;
    var moeda = evolucaoDados.moeda;
    el.textContent = pa.data.slice(8, 10) + "/" + pa.data.slice(5, 7) + " → " + pb.data.slice(8, 10) + "/" + pb.data.slice(5, 7) + ": " + (vb - va >= 0 ? "+" : "") + fmt(vb - va, moeda) + " (" + (pct >= 0 ? "+" : "") + pct.toFixed(2).replace(".", ",") + "%)";
    el.className = "mini " + (vb - va >= 0 ? "alta" : "queda");
  }

  function desenharSparkCard() {
    var canvas = document.getElementById("spark-patrimonio");
    if (!canvas || !evolucaoDados || !window.Chart) { return; }
    var pontos = evolucaoDados.pontos.slice(-30);
    if (pontos.length < 2) { canvas.hidden = true; return; }
    var valores = pontos.map(function (p) { return parseFloat(p.valor); });
    var c = valores[valores.length - 1] >= valores[0] ? cor("--alta") : cor("--queda");
    if (graficos.sparkCard) { graficos.sparkCard.destroy(); }
    graficos.sparkCard = new Chart(canvas, {
      type: "line",
      data: { labels: pontos.map(function (p) { return p.data; }), datasets: [{ data: valores, borderColor: c, borderWidth: 1.5, pointRadius: 0, fill: false, tension: 0.3 }] },
      options: { responsive: true, maintainAspectRatio: false, animation: false, plugins: { legend: { display: false }, tooltip: { enabled: false } }, scales: { x: { display: false }, y: { display: false } }, events: [] }
    });
  }

  function desenharEvolucao() {
    var canvas = document.getElementById("grafico-evolucao");
    if (!canvas || !window.Chart) { return; }
    var nota = document.getElementById("grafico-evolucao-nota");
    var moeda = canvas.getAttribute("data-moeda");
    fetch(canvas.getAttribute("data-url") + "?dias=" + diasEvolucao, { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        evolucaoDados = d; selecaoEvolucao = []; mostrarSelecao();
        destruir("evolucao");
        opcoesBase();
        var labels = d.pontos.map(function (p) { return p.data.slice(8, 10) + "/" + p.data.slice(5, 7); });
        var valores = d.pontos.map(function (p) { return parseFloat(p.valor); });
        var investido = d.pontos.map(function (p) { return parseFloat(p.investido); });
        var sobe = valores.length > 1 && valores[valores.length - 1] >= valores[0];
        var c = sobe ? cor("--alta") : cor("--queda");
        graficos.evolucao = new Chart(canvas, {
          type: "line",
          data: { labels: labels, datasets: [
            { label: "Patrimônio", data: valores, borderColor: c, backgroundColor: c + "22", fill: true, pointRadius: 0, pointHitRadius: 12, tension: 0.2, borderWidth: 2, order: 1 },
            { label: "Investido", data: investido, borderColor: cor("--texto-2") + "66", backgroundColor: cor("--texto-2") + "14", fill: true, pointRadius: 0, pointHitRadius: 0, borderWidth: 1, tension: 0, order: 2 }
          ] },
          options: {
            responsive: true, interaction: { mode: "index", intersect: false },
            onClick: function (ev, els, chart) {
              var itens = chart.getElementsAtEventForMode(ev, "index", { intersect: false }, true);
              if (!itens.length) { return; }
              var idx = itens[0].index;
              if (selecaoEvolucao.length >= 2) { selecaoEvolucao = []; }
              selecaoEvolucao.push(idx);
              mostrarSelecao(); chart.update("none");
            },
            plugins: { legend: { display: true, labels: { boxWidth: 10 } }, tooltip: { callbacks: { label: function (x) { return x.dataset.label + ": " + fmt(x.parsed.y, moeda); } } } },
            scales: { x: { ticks: { maxTicksLimit: 8 } }, y: { ticks: { callback: function (v) { return fmtCurto(v, moeda); } } } }
          },
          plugins: [pluginSelecao]
        });
        var texto = d.pontos.length + " dia(s) · " + d.metodo;
        if (d.avisos && d.avisos.length) { texto += " · " + d.avisos.join("; "); }
        if (!d.pontos.length) { texto = "Sem histórico suficiente ainda: carregue o histórico (manage.py carregar_historico) ou aguarde os fechamentos."; }
        nota.textContent = texto;
        desenharSparkCard();
      })
      .catch(function () { if (nota) { nota.textContent = "Não foi possível carregar a evolução."; } });
  }

  // Plugin: total no centro da rosca.
  function pluginCentro(texto, sub) {
    return {
      id: "centro",
      afterDraw: function (chart) {
        var meta = chart.getDatasetMeta(0); if (!meta || !meta.data.length) { return; }
        var arco = meta.data[0], ctx = chart.ctx;
        ctx.save(); ctx.textAlign = "center"; ctx.textBaseline = "middle";
        ctx.fillStyle = cor("--texto"); ctx.font = "600 14px " + Chart.defaults.font.family;
        ctx.fillText(texto, arco.x, arco.y - 7);
        ctx.fillStyle = cor("--texto-2"); ctx.font = "11px " + Chart.defaults.font.family;
        ctx.fillText(sub, arco.x, arco.y + 10);
        ctx.restore();
      }
    };
  }

  function realcarLinha(simbolo) {
    document.querySelectorAll("#tabela-carteira tr.linha-ativo").forEach(function (tr) {
      tr.classList.toggle("realce", !!simbolo && tr.getAttribute("data-simbolo") === simbolo);
    });
    document.querySelectorAll(".legenda-alocacao li").forEach(function (li) {
      li.classList.toggle("realce", !!simbolo && li.getAttribute("data-simbolo") === simbolo);
    });
  }

  function desenharAlocacao() {
    var canvas = document.getElementById("grafico-alocacao");
    var dados = document.getElementById("dados-alocacao");
    if (!canvas || !dados || !window.Chart) { return; }
    var itens = [];
    try { itens = JSON.parse(dados.textContent); } catch (e) { return; }
    destruir("alocacao");
    opcoesBase();
    var cores = paleta();
    var moeda = (document.querySelector("#carteira .fragmento-meta") || { getAttribute: function () { return "BRL"; } }).getAttribute("data-moeda") || "BRL";
    var total = itens.reduce(function (s, i) { return s + i.valor; }, 0);
    document.querySelectorAll(".legenda-alocacao .cor").forEach(function (el) { el.style.background = cores[parseInt(el.getAttribute("data-idx"), 10) % cores.length]; });
    graficos.alocacao = new Chart(canvas, {
      type: "doughnut",
      data: { labels: itens.map(function (i) { return i.simbolo; }), datasets: [{ data: itens.map(function (i) { return i.valor; }), backgroundColor: itens.map(function (_, i) { return cores[i % cores.length]; }), borderColor: cor("--superficie"), borderWidth: 2, hoverOffset: 8 }] },
      options: {
        responsive: true, maintainAspectRatio: false, cutout: "66%",
        onHover: function (ev, els) { realcarLinha(els.length ? itens[els[0].index].simbolo : null); ev.native.target.style.cursor = els.length ? "pointer" : "default"; },
        onClick: function (ev, els) { if (els.length) { aplicarFiltroRapido("simbolo:" + itens[els[0].index].simbolo, true); } },
        plugins: { legend: { display: false }, tooltip: { callbacks: { label: function (x) { var it = itens[x.dataIndex]; return it.simbolo + ": " + fmt(it.valor, moeda) + " (" + it.pct.toFixed(1).replace(".", ",") + "%)"; } } } }
      },
      plugins: [pluginCentro(fmtCurto(total, moeda), itens.length + " ativo(s)")]
    });
    document.querySelectorAll(".legenda-alocacao li").forEach(function (li) {
      li.addEventListener("mouseenter", function () { realcarLinha(li.getAttribute("data-simbolo")); });
      li.addEventListener("mouseleave", function () { realcarLinha(null); });
      li.addEventListener("click", function () { aplicarFiltroRapido("simbolo:" + li.getAttribute("data-simbolo"), true); });
    });
  }

  function ligarChipsEvolucao(escopo) {
    (escopo || document).querySelectorAll("[data-dias]").forEach(function (b) {
      b.addEventListener("click", function () {
        diasEvolucao = parseInt(b.getAttribute("data-dias"), 10);
        document.querySelectorAll("[data-dias]").forEach(function (o) { o.classList.toggle("ativo", o === b); });
        desenharEvolucao();
      });
      b.classList.toggle("ativo", parseInt(b.getAttribute("data-dias"), 10) === diasEvolucao);
    });
  }

  // --- Tabela interativa: filtros, colunas, expansão, seleção/comparação, simulador --------------
  var filtroAtual = "";
  var buscaAtual = "";
  var colunasExtras = false;
  var selecionados = {};
  var expandidos = {};
  try { filtroAtual = localStorage.getItem("portal-filtro") || ""; colunasExtras = localStorage.getItem("portal-colunas") === "1"; } catch (e) { /* sem armazenamento */ }

  // O atalho Binance foi retirado; não manter um filtro antigo invisível.
  if (filtroAtual === "custodia:BINANCE") {
    filtroAtual = "";
    try { localStorage.removeItem("portal-filtro"); } catch (e) { /* sem armazenamento */ }
  }

  function linhaPassa(tr) {
    var ok = true;
    if (filtroAtual) {
      var partes = filtroAtual.split(":"), chave = partes[0], valor = partes[1];
      ok = (tr.getAttribute("data-" + chave) || "") === valor;
    }
    if (ok && buscaAtual) {
      var alvo = (tr.getAttribute("data-simbolo") + " " + tr.getAttribute("data-nome")).toLowerCase();
      ok = alvo.indexOf(buscaAtual.toLowerCase()) !== -1;
    }
    return ok;
  }

  function aplicarFiltros() {
    var tabela = document.getElementById("tabela-carteira");
    if (!tabela) { return; }
    var total = 0, visiveis = 0;
    tabela.querySelectorAll("tr.linha-ativo").forEach(function (tr) {
      total += 1;
      var passa = linhaPassa(tr);
      tr.hidden = !passa;
      var det = tr.nextElementSibling;
      if (det && det.classList.contains("linha-detalhe")) { det.hidden = !passa || !expandidos[tr.getAttribute("data-id")]; }
      if (passa) { visiveis += 1; }
    });
    var cont = document.getElementById("contagem-carteira");
    if (cont) { cont.textContent = visiveis === total ? total + " ativo(s)" : visiveis + " de " + total; }
    var rolagem = document.getElementById("posicoes-rolagem"), vazio = document.getElementById("posicoes-vazio");
    if (rolagem) { rolagem.hidden = visiveis === 0; }
    if (vazio) { vazio.hidden = visiveis !== 0; }
    atualizarTotaisTabela(tabela);
    document.querySelectorAll("#filtros-rapidos .chip").forEach(function (b) {
      b.classList.toggle("ativo", (b.getAttribute("data-filtro") || "") === filtroAtual);
    });
    // filtro por símbolo (vindo da rosca) aparece como chip temporário
    var temp = document.getElementById("chip-simbolo");
    if (filtroAtual.indexOf("simbolo:") === 0) {
      if (!temp) {
        temp = document.createElement("button"); temp.type = "button"; temp.id = "chip-simbolo"; temp.className = "chip ativo";
        temp.addEventListener("click", function () { aplicarFiltroRapido("", true); });
        var grupo = document.getElementById("filtros-rapidos"); grupo.appendChild(temp);
      }
      temp.textContent = filtroAtual.split(":")[1] + " ✕";
    } else if (temp) { temp.remove(); }
  }

  // Totais dos ativos visíveis: retorno ponderado, com moedas separadas quando necessário.
  function numeroTotal(tr, campo) {
    var texto = tr.getAttribute("data-total-" + campo);
    if (texto === null || texto.trim() === "") { return null; }
    var valor = Number(texto);
    return Number.isFinite(valor) ? valor : null;
  }

  function somarTotaisVisiveis(tabela) {
    var grupos = {};
    tabela.querySelectorAll("tbody tr[data-total-moeda]").forEach(function (tr) {
      if (tr.hidden) { return; }
      var moeda = tr.getAttribute("data-total-moeda");
      var custo = numeroTotal(tr, "investido");
      if (custo === null) { return; }
      var g = grupos[moeda] || (grupos[moeda] = { moeda: moeda, investido: 0, investidoAvaliado: 0,
        valor: 0, resultado: 0, dia: 0, diaBase: 0, posicoes: 0, avaliadas: 0, comDia: 0, simulado: false });
      var valorSimulado = numeroTotal(tr, "simulado");
      var valor = valorSimulado !== null ? valorSimulado : numeroTotal(tr, "valor");
      var resultado = valorSimulado !== null ? valorSimulado - custo : numeroTotal(tr, "resultado");
      g.posicoes += 1; g.investido += custo;
      if (valor !== null && resultado !== null) {
        g.avaliadas += 1; g.investidoAvaliado += custo; g.valor += valor; g.resultado += resultado;
      }
      if (valorSimulado !== null) { g.simulado = true; }
      var dia = numeroTotal(tr, "dia"), base = numeroTotal(tr, "dia-base");
      if (dia !== null && base !== null) { g.dia += dia; g.diaBase += base; g.comDia += 1; }
    });
    return Object.keys(grupos).sort().map(function (moeda) {
      var g = grupos[moeda];
      g.retorno = g.investidoAvaliado > 0 ? g.resultado / g.investidoAvaliado * 100 : null;
      g.diaPct = g.diaBase > 0 ? g.dia / g.diaBase * 100 : null;
      if (!g.avaliadas) { g.valor = null; g.resultado = null; }
      if (!g.comDia) { g.dia = null; }
      return g;
    });
  }

  function atualizarTotaisTabela(tabela) {
    var rodape = tabela.querySelector("tfoot[data-totais-tabela]");
    if (!rodape) { return; }
    if (!rodape._modeloTotal) {
      var primeira = rodape.querySelector("tr");
      if (!primeira) { return; }
      rodape._modeloTotal = primeira.cloneNode(true);
    }
    var totais = somarTotaisVisiveis(tabela);
    rodape.replaceChildren();
    if (!totais.length) { totais = [{ moeda: "", posicoes: 0, avaliadas: 0, comDia: 0 }]; }
    totais.forEach(function (g) {
      var tr = rodape._modeloTotal.cloneNode(true);
      tr.setAttribute("data-total-moeda", g.moeda);
      tr.querySelector("[data-total-label]").textContent = g.posicoes ? "Total em " + g.moeda + (g.simulado ? " · simulado" : "") : "Nenhuma posição exibida";
      tr.querySelector("[data-total-cobertura]").textContent = g.posicoes ? g.posicoes + " ativo(s) · " + g.avaliadas + " com cotação" : "";
      tr.querySelector("[data-total-base]").textContent = g.posicoes ? "Investido: " + fmt(g.investido, g.moeda) : "";
      tr.querySelectorAll("[data-total-campo]").forEach(function (celula) {
        var campo = celula.getAttribute("data-total-campo"), valor = g[campo];
        celula.classList.remove("alta", "queda");
        if (campo === "dia" || campo === "resultado" || campo === "retorno") {
          if (valor < 0) { celula.classList.add("queda"); } else if (valor > 0) { celula.classList.add("alta"); }
        }
        var disponivel = valor !== null && valor !== undefined;
        celula.textContent = !disponivel ? "—" : campo === "retorno" ?
          (valor > 0 ? "+" : "") + valor.toFixed(2).replace(".", ",") + "%" :
          (valor < 0 ? "-" : "") + fmt(Math.abs(valor), g.moeda);
        if (campo === "dia") {
          celula.title = "Variação sobre o fechamento anterior · " + g.comDia + " de " + g.posicoes + " com variação disponível";
          if (g.diaPct !== null && g.diaPct !== undefined) {
            var percentual = document.createElement("small"); percentual.className = "total-cobertura";
            percentual.textContent = (g.diaPct > 0 ? "+" : "") + g.diaPct.toFixed(2).replace(".", ",") + "%";
            celula.appendChild(percentual);
          }
        }
      });
      rodape.appendChild(tr);
    });
  }

  function aplicarFiltroRapido(filtro, alternar) {
    filtroAtual = (alternar && filtroAtual === filtro) ? "" : filtro;
    try { localStorage.setItem("portal-filtro", filtroAtual.indexOf("simbolo:") === 0 ? "" : filtroAtual); } catch (e) { /* sem armazenamento */ }
    aplicarFiltros();
  }

  function aplicarColunas() {
    var tabela = document.getElementById("tabela-carteira");
    if (!tabela) { return; }
    tabela.classList.toggle("com-extras", colunasExtras);
    var b = document.getElementById("alternar-colunas");
    if (b) { b.textContent = colunasExtras ? "menos colunas" : "mais colunas"; b.setAttribute("aria-pressed", colunasExtras ? "true" : "false"); }
  }

  function expandir(tr, abrir) {
    var id = tr.getAttribute("data-id");
    var det = tr.nextElementSibling;
    var botao = tr.querySelector(".expandir");
    if (!abrir) {
      if (det && det.classList.contains("linha-detalhe")) { det.remove(); }
      delete expandidos[id]; tr.classList.remove("aberta");
      if (botao) { botao.textContent = "▸"; botao.setAttribute("aria-expanded", "false"); }
      return;
    }
    expandidos[id] = true; tr.classList.add("aberta");
    if (botao) { botao.textContent = "▾"; botao.setAttribute("aria-expanded", "true"); }
    if (det && det.classList.contains("linha-detalhe")) { det.hidden = false; return; }
    det = document.createElement("tr"); det.className = "linha-detalhe";
    var td = document.createElement("td"); td.colSpan = tr.cells.length; td.innerHTML = '<p class="nota">Carregando…</p>';
    det.appendChild(td); tr.parentNode.insertBefore(det, tr.nextSibling);
    fetch(tr.getAttribute("data-painel"), { credentials: "same-origin", headers: { "X-Requested-With": "fetch" } })
      .then(function (r) { return r.text(); })
      .then(function (html) { td.innerHTML = html; iniciarPainel(td); })
      .catch(function () { td.innerHTML = '<p class="nota">Não foi possível carregar os detalhes.</p>'; });
  }

  function iniciarPainel(td) {
    var canvas = td.querySelector(".grafico-painel");
    if (!canvas || !window.Chart) { return; }
    var nota = td.querySelector(".grafico-painel-nota");
    var moeda = canvas.getAttribute("data-moeda");
    var niveis = [];
    try { niveis = JSON.parse(td.querySelector(".linhas-painel").textContent); } catch (e) { /* sem linhas */ }
    var chart = null;
    function carregar(periodo) {
      fetch(canvas.getAttribute("data-url") + "?periodo=" + periodo, { credentials: "same-origin" })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (chart) { chart.destroy(); }
          opcoesBase();
          var c = cor("--primaria");
          chart = new Chart(canvas, {
            type: "line",
            data: { labels: d.pontos.map(function (p) { return p.data.slice(5); }), datasets: [{ data: d.pontos.map(function (p) { return parseFloat(p.nativo); }), borderColor: c, backgroundColor: c + "22", fill: true, pointRadius: 0, tension: 0.1, borderWidth: 1.5 }] },
            options: { responsive: true, interaction: { mode: "index", intersect: false }, plugins: { legend: { display: false }, tooltip: { callbacks: { label: function (x) { return fmt(x.parsed.y, moeda); } } } }, scales: { x: { ticks: { maxTicksLimit: 6 } }, y: { ticks: { callback: function (v) { return fmt(v, moeda); } } } } },
            plugins: [pluginNiveis(niveis, moeda)]
          });
          if (nota) { nota.textContent = "Fonte: " + (d.fonte || "—") + " · " + d.pontos.length + " ponto(s)" + (d.avisos && d.avisos.length ? " · " + d.avisos.join("; ") : ""); }
        })
        .catch(function () { if (nota) { nota.textContent = "Não foi possível carregar o histórico."; } });
    }
    td.querySelectorAll("[data-painel-periodo]").forEach(function (b) {
      b.addEventListener("click", function () {
        td.querySelectorAll("[data-painel-periodo]").forEach(function (o) { o.classList.toggle("ativo", o === b); });
        carregar(b.getAttribute("data-painel-periodo"));
      });
    });
    carregar("1m");
  }

  function atualizarSelecao() {
    var ids = Object.keys(selecionados).filter(function (k) { return selecionados[k]; });
    var b = document.getElementById("comparar-selecionados");
    if (b) { b.disabled = ids.length < 2; b.textContent = ids.length >= 2 ? "Comparar " + ids.length + " selecionados" : "Comparar selecionados"; }
    document.querySelectorAll("#tabela-carteira .sel-comparar").forEach(function (cb) { cb.checked = !!selecionados[cb.value]; });
    var todos = document.getElementById("sel-todos");
    if (todos) { var cbs = document.querySelectorAll("#tabela-carteira .sel-comparar"); todos.checked = cbs.length > 0 && ids.length === cbs.length; }
  }

  var compPeriodo = "1m";
  function desenharComparacaoInline() {
    var caixa = document.getElementById("comparacao-inline"), canvas = document.getElementById("grafico-comparacao-inline");
    if (!caixa || !canvas || !window.Chart) { return; }
    var ids = Object.keys(selecionados).filter(function (k) { return selecionados[k]; });
    if (ids.length < 2) { caixa.hidden = true; return; }
    caixa.hidden = false;
    var notaC = document.getElementById("comparacao-inline-nota");
    Promise.all(ids.map(function (id) { return fetch("/ativos/" + id + "/historico.json?periodo=" + compPeriodo, { credentials: "same-origin" }).then(function (r) { return r.json(); }); }))
      .then(function (series) {
        var datas = {}; series.forEach(function (d) { d.pontos.forEach(function (p) { datas[p.data] = true; }); });
        var labels = Object.keys(datas).sort(), cores = paleta();
        var datasets = series.map(function (d, i) {
          var porData = {}; d.pontos.forEach(function (p) { porData[p.data] = parseFloat(p.nativo); });
          var base = d.pontos.length ? parseFloat(d.pontos[0].nativo) : null;
          return { label: d.simbolo, data: labels.map(function (l) { return base && porData[l] !== undefined ? (porData[l] / base) * 100 : null; }), borderColor: cores[i % cores.length], spanGaps: true, pointRadius: 0, tension: 0.1, borderWidth: 1.5 };
        });
        destruir("compInline"); opcoesBase();
        graficos.compInline = new Chart(canvas, { type: "line", data: { labels: labels.map(function (l) { return l.slice(5); }), datasets: datasets }, options: { responsive: true, interaction: { mode: "index", intersect: false }, plugins: { legend: { labels: { boxWidth: 10 } }, tooltip: { callbacks: { label: function (x) { return x.dataset.label + ": " + x.parsed.y.toFixed(1).replace(".", ","); } } } }, scales: { x: { ticks: { maxTicksLimit: 8 } }, y: { ticks: { callback: function (v) { return v.toFixed(0); } } } } } });
        notaC.textContent = "Base 100 no primeiro ponto de cada série, na moeda nativa de cada ativo · " + series.map(function (d) { return d.simbolo + ": " + (d.fonte || "sem histórico"); }).join(" · ");
        caixa.scrollIntoView({ behavior: "smooth", block: "nearest" });
      })
      .catch(function () { if (notaC) { notaC.textContent = "Não foi possível carregar o histórico."; } });
  }

  // Simulador "e se": recalcula valor/resultado por linha e os cards, só no navegador.
  var simAtivo = false, simPreco = 0, simCambio = 0, simPorAtivo = {};
  function aplicarSimulacao() {
    var meta = document.querySelector("#carteira .fragmento-meta");
    if (!meta) { return; }
    var moeda = meta.getAttribute("data-moeda"), outra = meta.getAttribute("data-outra"), taxaOutra = parseFloat(meta.getAttribute("data-taxa-outra") || "0");
    var totalValor = 0, totalInvestido = 0, algum = false;
    document.querySelectorAll("#tabela-carteira tr.linha-ativo").forEach(function (tr) {
      var valor = parseFloat(tr.getAttribute("data-valor")), investido = parseFloat(tr.getAttribute("data-investido"));
      var tdValor = tr.querySelector("[data-sim-valor]"), tdRes = tr.querySelector("[data-sim-resultado]"), tdRet = tr.querySelector("[data-sim-retorno]");
      if (isNaN(valor) || isNaN(investido) || !tdValor) { return; }
      var fator = 1;
      if (simAtivo) {
        var varPreco = simPorAtivo[tr.getAttribute("data-simbolo")] !== undefined ? simPorAtivo[tr.getAttribute("data-simbolo")] : simPreco;
        fator *= 1 + varPreco / 100;
        var nativa = tr.getAttribute("data-moeda-nativa");
        if (nativa !== moeda) { fator *= (nativa === "USD" && moeda === "BRL") ? (1 + simCambio / 100) : 1 / (1 + simCambio / 100); }
      }
      var novoValor = valor * fator, novoRes = novoValor - investido, novoRet = investido ? (novoRes / investido) * 100 : 0;
      if (simAtivo) { tr.setAttribute("data-total-simulado", String(novoValor)); }
      else { tr.removeAttribute("data-total-simulado"); }
      totalValor += novoValor; totalInvestido += investido; algum = true;
      if (!tr.dataset.origValor) { tr.dataset.origValor = tdValor.innerHTML; tr.dataset.origRes = tdRes ? tdRes.innerHTML : ""; tr.dataset.origRet = tdRet ? tdRet.innerHTML : ""; }
      if (simAtivo) {
        tdValor.innerHTML = '<span class="simulado">' + fmt(novoValor, moeda) + "</span>";
        if (tdRes) { tdRes.innerHTML = '<span class="simulado ' + (novoRes < 0 ? "queda" : "alta") + '">' + fmt(novoRes, moeda) + "</span>"; }
        if (tdRet) { tdRet.innerHTML = '<span class="simulado ' + (novoRet < 0 ? "queda" : "alta") + '">' + (novoRet >= 0 ? "+" : "") + novoRet.toFixed(2).replace(".", ",") + "%</span>"; }
      } else {
        tdValor.innerHTML = tr.dataset.origValor; if (tdRes) { tdRes.innerHTML = tr.dataset.origRes; } if (tdRet) { tdRet.innerHTML = tr.dataset.origRet; }
      }
    });
    var cards = { patrimonio: document.querySelector("[data-sim=patrimonio]"), outra: document.querySelector("[data-sim=patrimonio-outra]"), resultado: document.querySelector("[data-sim=resultado]"), retorno: document.querySelector("[data-sim=retorno]") };
    Object.keys(cards).forEach(function (k) { var el = cards[k]; if (el && !el.dataset.orig) { el.dataset.orig = el.innerHTML; } });
    if (simAtivo && algum) {
      var res = totalValor - totalInvestido;
      if (cards.patrimonio) { cards.patrimonio.innerHTML = '<span class="simulado">' + fmt(totalValor, moeda) + "</span>"; }
      if (cards.outra && taxaOutra) { cards.outra.innerHTML = '<span class="simulado">' + fmt(totalValor * taxaOutra, outra) + "</span>"; }
      if (cards.resultado) { cards.resultado.innerHTML = '<span class="simulado ' + (res < 0 ? "queda" : "alta") + '">' + fmt(res, moeda) + "</span>"; }
      if (cards.retorno) { cards.retorno.innerHTML = '<span class="simulado">' + (totalInvestido ? ((res / totalInvestido) * 100).toFixed(2).replace(".", ",") : "0") + "%</span>"; }
    } else {
      Object.keys(cards).forEach(function (k) { var el = cards[k]; if (el && el.dataset.orig) { el.innerHTML = el.dataset.orig; } });
    }
    document.body.classList.toggle("simulando", simAtivo);
    var tabela = document.getElementById("tabela-carteira");
    if (tabela) { atualizarTotaisTabela(tabela); }
  }

  function montarSimulador() {
    var caixa = document.getElementById("simulador");
    if (!caixa) { return; }
    var lista = document.getElementById("sim-ativos"); lista.innerHTML = "";
    document.querySelectorAll("#tabela-carteira tr.linha-ativo").forEach(function (tr) {
      var sim = tr.getAttribute("data-simbolo");
      var wrap = document.createElement("label"); wrap.className = "sim-ativo";
      wrap.innerHTML = "<span>" + sim + "</span> <output>" + (simPorAtivo[sim] !== undefined ? simPorAtivo[sim] : "—") + "</output><input type=\"range\" min=\"-50\" max=\"50\" step=\"1\" value=\"" + (simPorAtivo[sim] !== undefined ? simPorAtivo[sim] : 0) + "\" data-sim-ativo=\"" + sim + "\">";
      lista.appendChild(wrap);
    });
    lista.querySelectorAll("input[data-sim-ativo]").forEach(function (inp) {
      inp.addEventListener("input", function () { simPorAtivo[inp.getAttribute("data-sim-ativo")] = parseFloat(inp.value); inp.previousElementSibling.textContent = (inp.value > 0 ? "+" : "") + inp.value + "%"; aplicarSimulacao(); });
    });
    var sp = document.getElementById("sim-preco"), sc = document.getElementById("sim-cambio");
    sp.value = simPreco; sc.value = simCambio;
    document.getElementById("sim-preco-out").textContent = (simPreco > 0 ? "+" : "") + simPreco + "%";
    document.getElementById("sim-cambio-out").textContent = (simCambio > 0 ? "+" : "") + simCambio + "%";
    sp.oninput = function () { simPreco = parseFloat(sp.value); document.getElementById("sim-preco-out").textContent = (simPreco > 0 ? "+" : "") + simPreco + "%"; aplicarSimulacao(); };
    sc.oninput = function () { simCambio = parseFloat(sc.value); document.getElementById("sim-cambio-out").textContent = (simCambio > 0 ? "+" : "") + simCambio + "%"; aplicarSimulacao(); };
    document.getElementById("simulador-zerar").onclick = function () { simPreco = 0; simCambio = 0; simPorAtivo = {}; montarSimulador(); aplicarSimulacao(); };
    caixa.hidden = !simAtivo;
    var b = document.getElementById("alternar-simulador"); if (b) { b.classList.toggle("ativo", simAtivo); b.setAttribute("aria-pressed", simAtivo ? "true" : "false"); }
  }

  function iniciarTabelaInterativa(escopo) {
    var tabela = escopo.querySelector("#tabela-carteira");
    if (!tabela) { return; }
    escopo.querySelectorAll("#filtros-rapidos .chip[data-filtro]").forEach(function (b) {
      b.addEventListener("click", function () { aplicarFiltroRapido(b.getAttribute("data-filtro") || "", false); });
    });
    var busca = escopo.querySelector("#busca-carteira");
    if (busca) { busca.value = buscaAtual; busca.addEventListener("input", function () { buscaAtual = busca.value.trim(); aplicarFiltros(); }); }
    var alt = escopo.querySelector("#alternar-colunas");
    if (alt) { alt.addEventListener("click", function () { colunasExtras = !colunasExtras; try { localStorage.setItem("portal-colunas", colunasExtras ? "1" : "0"); } catch (e) { /* */ } aplicarColunas(); }); }
    tabela.querySelectorAll("tr.linha-ativo").forEach(function (tr) {
      tr.addEventListener("click", function (ev) {
        if (ev.target.closest("a, input, form, button, .expandir")) { return; }
        expandir(tr, !expandidos[tr.getAttribute("data-id")]);
      });
      var botao = tr.querySelector(".expandir");
      if (botao) { botao.addEventListener("click", function (ev) { ev.stopPropagation(); expandir(tr, !expandidos[tr.getAttribute("data-id")]); }); }
      if (expandidos[tr.getAttribute("data-id")]) { expandir(tr, true); }
      tr.addEventListener("mouseenter", function () { realcarLinha(tr.getAttribute("data-simbolo")); });
      tr.addEventListener("mouseleave", function () { realcarLinha(null); });
    });
    tabela.querySelectorAll(".sel-comparar").forEach(function (cb) {
      cb.addEventListener("change", function () { selecionados[cb.value] = cb.checked; atualizarSelecao(); if (!document.getElementById("comparacao-inline").hidden) { desenharComparacaoInline(); } });
    });
    var todos = escopo.querySelector("#sel-todos");
    if (todos) { todos.addEventListener("change", function () { tabela.querySelectorAll(".sel-comparar").forEach(function (cb) { selecionados[cb.value] = todos.checked; }); atualizarSelecao(); }); }
    var comparar = escopo.querySelector("#comparar-selecionados");
    if (comparar) { comparar.addEventListener("click", desenharComparacaoInline); }
    escopo.querySelectorAll("[data-comp-periodo]").forEach(function (b) {
      b.addEventListener("click", function () { compPeriodo = b.getAttribute("data-comp-periodo"); escopo.querySelectorAll("[data-comp-periodo]").forEach(function (o) { o.classList.toggle("ativo", o === b); }); desenharComparacaoInline(); });
    });
    var fechar = escopo.querySelector("#fechar-comparacao");
    if (fechar) { fechar.addEventListener("click", function () { document.getElementById("comparacao-inline").hidden = true; destruir("compInline"); }); }
    var altSim = escopo.querySelector("#alternar-simulador");
    if (altSim) { altSim.addEventListener("click", function () { simAtivo = !simAtivo; montarSimulador(); aplicarSimulacao(); }); }
    aplicarFiltros(); aplicarColunas(); atualizarSelecao(); montarSimulador(); aplicarSimulacao();
  }

  function iniciarCarteira(escopo) {
    ativarOrdenacao(escopo);
    ligarChipsEvolucao(escopo);
    iniciarTabelaInterativa(escopo);
    desenharEvolucao();
    desenharAlocacao();
  }

  function redesenharGraficos() {
    if (document.getElementById("grafico-evolucao")) { desenharEvolucao(); desenharAlocacao(); }
    if (document.getElementById("grafico-historico")) { carregarHistorico(); }
    if (document.getElementById("grafico-comparacao")) { desenharComparacao(); }
  }

  // --- Atualização ao vivo da carteira ------------------------------------------------------------
  var carteira = document.getElementById("carteira");
  if (carteira) {
    iniciarCarteira(carteira);
    var ultimaISO = carteira.querySelector(".fragmento-meta") ? carteira.querySelector(".fragmento-meta").getAttribute("data-ultima") : "";
    var alertasAtuais = parseInt(carteira.getAttribute("data-alertas") || "0", 10);
    function atualizarRelogio() {
      var el = document.getElementById("ao-vivo-texto");
      var meta = carteira.querySelector(".fragmento-meta");
      if (!el || !meta) { return; }
      var iso = meta.getAttribute("data-ultima");
      if (!iso) { el.textContent = "sem coleta ainda"; return; }
      var s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
      var txt = s < 60 ? "atualizado há " + s + " s" : s < 3600 ? "atualizado há " + Math.round(s / 60) + " min" : "atualizado há " + Math.round(s / 3600) + " h";
      el.textContent = txt + " · " + meta.getAttribute("data-ultima-texto");
    }
    function recarregar() {
      if (document.hidden || simAtivo) { return; }  // não interrompe uma simulação em curso
      fetch(carteira.getAttribute("data-url"), { credentials: "same-origin", headers: { "X-Requested-With": "fetch" } })
        .then(function (r) { if (!r.ok) { throw new Error(r.status); } return r.text(); })
        .then(function (html) {
          var tmp = document.createElement("div");
          tmp.innerHTML = html;
          var meta = tmp.querySelector(".fragmento-meta");
          var novaISO = meta ? meta.getAttribute("data-ultima") : "";
          var alertas = meta ? parseInt(meta.getAttribute("data-alertas") || "0", 10) : alertasAtuais;
          var naoVistos = meta ? meta.getAttribute("data-nao-vistos") : null;
          destruir("evolucao"); destruir("alocacao"); destruir("compInline"); if (graficos.sparkCard) { graficos.sparkCard.destroy(); graficos.sparkCard = null; }
          carteira.innerHTML = html;
          iniciarCarteira(carteira);
          if (novaISO && novaISO !== ultimaISO) { toast("Cotações atualizadas.", "ok"); ultimaISO = novaISO; }
          if (alertas > alertasAtuais) { toast("Novo alerta disparado — veja em Alertas.", "alerta"); }
          alertasAtuais = alertas;
          var contador = document.querySelector(".menu .contador");
          var linkAlertas = document.querySelector(".menu a[href$='/alertas/']");
          if (naoVistos !== null && linkAlertas) {
            if (parseInt(naoVistos, 10) > 0) {
              if (!contador) { contador = document.createElement("span"); contador.className = "contador"; linkAlertas.appendChild(document.createTextNode(" ")); linkAlertas.appendChild(contador); }
              contador.textContent = naoVistos;
            } else if (contador) { contador.remove(); }
          }
          atualizarRelogio();
        })
        .catch(function () { /* mantém a tela anterior; tenta no próximo ciclo */ });
    }
    atualizarRelogio();
    setInterval(atualizarRelogio, 5000);
    setInterval(recarregar, 60000);
    document.addEventListener("visibilitychange", function () { if (!document.hidden) { recarregar(); } });

    var formColetar = document.getElementById("form-coletar");
    if (formColetar) {
      formColetar.addEventListener("submit", function (ev) {
        ev.preventDefault();
        var botao = formColetar.querySelector("button");
        botao.disabled = true;
        fetch(formColetar.getAttribute("action"), { method: "POST", credentials: "same-origin", headers: { "X-CSRFToken": csrf(), "X-Requested-With": "fetch", "Accept": "application/json" } })
          .then(function (r) { return r.json(); })
          .then(function () {
            toast("Coleta solicitada; o coletor roda em até 30 s.", "ok");
            setTimeout(recarregar, 20000); setTimeout(recarregar, 45000); setTimeout(recarregar, 75000);
          })
          .catch(function () { toast("Não foi possível solicitar a coleta.", "erro"); })
          .then(function () { setTimeout(function () { botao.disabled = false; }, 30000); });
      });
    }

    // atalhos de teclado: / busca · u/r moeda · a novo alerta
    document.addEventListener("keydown", function (ev) {
      if (ev.target.closest("input, textarea, select") || ev.metaKey || ev.ctrlKey || ev.altKey) { return; }
      if (ev.key === "/") { var b = document.getElementById("busca-carteira"); if (b) { ev.preventDefault(); b.focus(); } }
      if (ev.key === "u" || ev.key === "r") { var botaoMoeda = document.querySelector(".seletor-moeda button[value=" + (ev.key === "u" ? "USD" : "BRL") + "]"); if (botaoMoeda) { botaoMoeda.click(); } }
      if (ev.key === "a") { var link = document.querySelector("a[href$='/alertas/regras/nova/']"); window.location = link ? link.getAttribute("href") : "/alertas/regras/nova/"; }
    });
  }

  // --- Gráfico de histórico do ativo ----------------------------------------------------------
  var canvasHist = document.getElementById("grafico-historico");
  function linhasDeNivel() {
    var el = document.getElementById("linhas-grafico");
    if (!el) { return []; }
    try { return JSON.parse(el.textContent); } catch (e) { return []; }
  }
  // Plugin: linhas horizontais (preço médio e limites de alertas de preço) com rótulo.
  function pluginNiveis(niveis, moeda) {
    return {
      id: "niveis",
      afterDatasetsDraw: function (chart) {
        if (!niveis.length) { return; }
        var ctx = chart.ctx, area = chart.chartArea, y = chart.scales.y;
        ctx.save();
        niveis.forEach(function (n) {
          var v = parseFloat(n.valor);
          if (isNaN(v) || v < y.min || v > y.max) { return; }
          var py = y.getPixelForValue(v);
          var corN = n.tipo === "medio" ? cor("--texto-2") : n.tipo === "abaixo" ? cor("--queda") : cor("--alta");
          ctx.strokeStyle = corN; ctx.fillStyle = corN; ctx.lineWidth = 1;
          ctx.setLineDash(n.tipo === "medio" ? [6, 4] : [2, 3]);
          ctx.beginPath(); ctx.moveTo(area.left, py); ctx.lineTo(area.right, py); ctx.stroke();
          ctx.setLineDash([]);
          ctx.font = "11px " + Chart.defaults.font.family; ctx.textBaseline = "bottom"; ctx.textAlign = "right";
          ctx.fillText(n.tipo === "medio" ? n.rotulo + " " + fmt(v, moeda) : n.rotulo, area.right - 4, py - 2);
        });
        ctx.restore();
      }
    };
  }
  // Clique no gráfico: abre o mini-formulário de alerta de preço com o valor do ponto clicado.
  function cliqueNoGrafico(ev, elementos, chart) {
    var form = document.getElementById("alerta-rapido");
    if (!form || !chart) { return; }
    var y = chart.scales.y;
    var pos = Chart.helpers ? Chart.helpers.getRelativePosition(ev, chart) : { y: ev.y };
    var valor = y.getValueForPixel(pos.y);
    if (!isFinite(valor)) { return; }
    var atual = graficos.historico && graficos.historico.data.datasets[0].data;
    var ultimo = atual && atual.length ? atual[atual.length - 1] : null;
    form.hidden = false;
    form.querySelector("[name=valor]").value = valor.toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    form.querySelector("[name=tipo]").value = ultimo !== null && valor < ultimo ? "abaixo" : "acima";
    form.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
  var fecharRapido = document.getElementById("alerta-rapido-fechar");
  if (fecharRapido) { fecharRapido.addEventListener("click", function () { document.getElementById("alerta-rapido").hidden = true; }); }
  var periodoHist = canvasHist ? (canvasHist.getAttribute("data-periodo") || "1m") : "1m";
  var serieHist = "nativo";
  function carregarHistorico() {
    if (!canvasHist || !window.Chart) { return; }
    var nota = document.getElementById("grafico-nota");
    fetch(canvasHist.getAttribute("data-url") + "?periodo=" + periodoHist, { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var usarConvertido = serieHist === "convertido" && d.moeda_visao;
        var moeda = usarConvertido ? d.moeda_visao : d.moeda_nativa;
        var pontos = d.pontos.filter(function (p) { return !usarConvertido || p.convertido !== null; });
        var labels = pontos.map(function (p) { return p.data; });
        var valores = pontos.map(function (p) { return parseFloat(usarConvertido ? p.convertido : p.nativo); });
        destruir("historico");
        opcoesBase();
        var c = cor("--primaria");
        var niveis = usarConvertido ? [] : linhasDeNivel();
        graficos.historico = new Chart(canvasHist, {
          type: "line",
          data: { labels: labels, datasets: [{ label: d.simbolo + " (" + moeda + ")", data: valores, borderColor: c, backgroundColor: c + "22", fill: true, pointRadius: pontos.length > 60 ? 0 : 2, tension: 0.1 }] },
          options: { responsive: true, interaction: { mode: "index", intersect: false }, onClick: cliqueNoGrafico, plugins: { legend: { display: false }, tooltip: { callbacks: { label: function (x) { return fmt(x.parsed.y, moeda); } } } }, scales: { x: { ticks: { maxTicksLimit: 8 } }, y: { ticks: { callback: function (v) { return fmt(v, moeda); } } } } },
          plugins: [pluginNiveis(niveis, moeda)]
        });
        var texto = "Fonte: " + (d.fonte || "—") + " · base: " + (d.base_tempo === "dia_utc" ? "dia UTC" : "sessão local") + " · " + pontos.length + " ponto(s)";
        if (d.inicio_real) { texto += " · de " + d.inicio_real + " a " + labels[labels.length - 1]; }
        if (usarConvertido) { texto += " · convertido pelo PTAX de cada data"; }
        if (d.avisos && d.avisos.length) { texto += " · " + d.avisos.join("; "); }
        nota.textContent = texto;
      })
      .catch(function () { if (nota) { nota.textContent = "Não foi possível carregar o histórico."; } });
  }
  if (canvasHist && window.Chart) {
    document.querySelectorAll("[data-periodo-botao]").forEach(function (b) {
      b.addEventListener("click", function () {
        periodoHist = b.getAttribute("data-periodo-botao");
        document.querySelectorAll("[data-periodo-botao]").forEach(function (o) { o.classList.remove("ativo"); });
        b.classList.add("ativo");
        carregarHistorico();
      });
    });
    document.querySelectorAll("[data-serie-botao]").forEach(function (b) {
      b.addEventListener("click", function () {
        serieHist = b.getAttribute("data-serie-botao");
        document.querySelectorAll("[data-serie-botao]").forEach(function (o) { o.classList.remove("ativo"); });
        b.classList.add("ativo");
        carregarHistorico();
      });
    });
    carregarHistorico();
  }

  // --- Gráfico de comparação (base 100) ----------------------------------------------------------
  function desenharComparacao() {
    var comp = document.getElementById("grafico-comparacao");
    if (!comp || !window.Chart || !window.COMPARACAO) { return; }
    var notaC = document.getElementById("grafico-comparacao-nota");
    Promise.all(window.COMPARACAO.series.map(function (s) {
      return fetch("/ativos/" + s.id + "/historico.json?periodo=" + window.COMPARACAO.periodo, { credentials: "same-origin" }).then(function (r) { return r.json(); });
    })).then(function (series) {
      var datas = {};
      series.forEach(function (d) { d.pontos.forEach(function (p) { datas[p.data] = true; }); });
      var labels = Object.keys(datas).sort();
      var cores = paleta();
      var datasets = series.map(function (d, i) {
        var porData = {}; d.pontos.forEach(function (p) { porData[p.data] = parseFloat(p.nativo); });
        var base = d.pontos.length ? parseFloat(d.pontos[0].nativo) : null;
        return { label: d.simbolo, data: labels.map(function (l) { return base && porData[l] !== undefined ? (porData[l] / base) * 100 : null; }), borderColor: cores[i % cores.length], spanGaps: true, pointRadius: 0, tension: 0.1 };
      });
      destruir("comparacao");
      opcoesBase();
      graficos.comparacao = new Chart(comp, { type: "line", data: { labels: labels, datasets: datasets }, options: { responsive: true, interaction: { mode: "index", intersect: false }, scales: { x: { ticks: { maxTicksLimit: 8 } }, y: { ticks: { callback: function (v) { return v.toFixed(0); } } } } } });
      notaC.textContent = series.map(function (d) { return d.simbolo + ": " + (d.fonte || "sem histórico") + (d.avisos.length ? " (" + d.avisos.join("; ") + ")" : ""); }).join(" · ");
    }).catch(function () { if (notaC) { notaC.textContent = "Não foi possível carregar o histórico."; } });
  }
  desenharComparacao();

  // --- Painel de alertas ao vivo + ações rápidas ------------------------------------------------
  var painel = document.getElementById("painel-alertas");
  if (painel) {
    var ativosAtuais = parseInt(painel.getAttribute("data-ativos") || "0", 10);
    function recarregarPainel() {
      if (document.hidden) { return; }
      fetch(painel.getAttribute("data-url"), { credentials: "same-origin", headers: { "X-Requested-With": "fetch" } })
        .then(function (r) { if (!r.ok) { throw new Error(r.status); } return r.text(); })
        .then(function (html) {
          var tmp = document.createElement("div"); tmp.innerHTML = html;
          var meta = tmp.querySelector(".fragmento-meta");
          var ativos = meta ? parseInt(meta.getAttribute("data-ativos") || "0", 10) : ativosAtuais;
          // não interrompe quem está digitando um limite
          if (painel.querySelector("details[open]")) { return; }
          painel.innerHTML = html;
          ligarAcoes(painel);
          if (ativos > ativosAtuais) { toast("Novo alerta disparado.", "alerta"); }
          ativosAtuais = ativos;
          var contador = document.querySelector(".menu .contador");
          var naoVistos = meta ? meta.getAttribute("data-nao-vistos") : null;
          var linkAlertas = document.querySelector(".menu a[href$='/alertas/']");
          if (naoVistos !== null && linkAlertas) {
            if (parseInt(naoVistos, 10) > 0) {
              if (!contador) { contador = document.createElement("span"); contador.className = "contador"; linkAlertas.appendChild(document.createTextNode(" ")); linkAlertas.appendChild(contador); }
              contador.textContent = naoVistos;
            } else if (contador) { contador.remove(); }
          }
        })
        .catch(function () { /* tenta no próximo ciclo */ });
    }
    function postar(url, dados) {
      var corpo = new URLSearchParams(dados || {});
      return fetch(url, { method: "POST", credentials: "same-origin", body: corpo, headers: { "X-CSRFToken": csrf(), "X-Requested-With": "fetch", "Content-Type": "application/x-www-form-urlencoded" } })
        .then(function (r) { return r.json().then(function (j) { return { ok: r.ok && j.ok !== false, msg: j.mensagem || (r.ok ? "Feito." : "Falhou.") }; }); });
    }
    function ligarAcoes(escopo) {
      escopo.querySelectorAll("form.acao-rapida").forEach(function (f) {
        f.addEventListener("submit", function (ev) {
          ev.preventDefault();
          var params = new URLSearchParams();
          new FormData(f).forEach(function (v, k) { if (k !== "csrfmiddlewaretoken") { params.append(k, v); } });
          fetch(f.getAttribute("action"), { method: "POST", credentials: "same-origin", body: params, headers: { "X-CSRFToken": csrf(), "X-Requested-With": "fetch" } })
            .then(function (r) { return r.json(); })
            .then(function (j) { toast(j.mensagem || "Feito.", j.ok === false ? "erro" : "ok"); recarregarPainel(); })
            .catch(function () { toast("Não foi possível concluir.", "erro"); });
        });
      });
      escopo.querySelectorAll(".acao-js").forEach(function (b) {
        b.addEventListener("click", function () {
          var confirmar = b.getAttribute("data-confirmar");
          if (confirmar && !window.confirm(confirmar)) { return; }
          var dados = {};
          if (b.getAttribute("data-campo")) { dados[b.getAttribute("data-campo")] = b.getAttribute("data-valor"); }
          b.disabled = true;
          postar(b.getAttribute("data-url"), dados)
            .then(function (res) { toast(res.msg, res.ok ? "ok" : "erro"); recarregarPainel(); })
            .catch(function () { toast("Não foi possível concluir.", "erro"); b.disabled = false; });
        });
      });
      escopo.querySelectorAll(".acao-ajuste").forEach(function (b) {
        b.addEventListener("click", function () {
          var caixa = b.closest(".ajuste-form");
          var valor = caixa.querySelector("input").value;
          postar(caixa.getAttribute("data-url"), { valor: valor })
            .then(function (res) { toast(res.msg, res.ok ? "ok" : "erro"); if (res.ok) { caixa.closest("details").open = false; recarregarPainel(); } })
            .catch(function () { toast("Não foi possível ajustar.", "erro"); });
        });
      });
    }
    ligarAcoes(painel);
    setInterval(recarregarPainel, 30000);
    document.addEventListener("visibilitychange", function () { if (!document.hidden) { recarregarPainel(); } });
  }

  if (!carteira) { ativarOrdenacao(document); }
  // API mínima para scripts de outras telas (mercado.js)
  window.Portal = { ativarOrdenacao: ativarOrdenacao, toast: toast, csrf: csrf, cor: cor, paleta: paleta, opcoesBase: opcoesBase, fmt: fmt };
})();
