/* Portal de Ações — telas do Mercado e dos Favoritos. Usa Chart.js local e window.Portal (portal.js). */
(function () {
  "use strict";
  var P = window.Portal || {};
  var graficos = {};

  function csrf() { return P.csrf ? P.csrf() : ""; }
  function toast(t, tipo) { if (P.toast) { P.toast(t, tipo); } }
  function cor(n) { return getComputedStyle(document.documentElement).getPropertyValue(n).trim(); }
  function paleta() { return P.paleta ? P.paleta() : ["#5aa9ff", "#3ddc84", "#f5c451", "#ff6b6b", "#b48cff", "#4fd1d9"]; }
  function num(v, casas) { return Number(v).toLocaleString("pt-BR", { minimumFractionDigits: casas, maximumFractionDigits: casas }); }
  function post(url, dados) {
    var corpo = new URLSearchParams(dados || {});
    return fetch(url, {
      method: "POST", credentials: "same-origin", body: corpo,
      headers: { "X-CSRFToken": csrf(), "X-Requested-With": "fetch", "Content-Type": "application/x-www-form-urlencoded" }
    }).then(function (r) { if (!r.ok) { throw new Error("HTTP " + r.status); } return r.json(); });
  }
  function destruir(k) { if (graficos[k]) { graficos[k].destroy(); graficos[k] = null; } }
  function lerJSON(id) {
    var el = document.getElementById(id);
    if (!el) { return null; }
    try { return JSON.parse(el.textContent || "null"); } catch (e) { return null; }
  }

  // --- Estrela (favoritos) e grupos: delegação no documento -------------------------------------
  document.addEventListener("click", function (ev) {
    var b = ev.target.closest("[data-favorito]");
    if (b) {
      ev.preventDefault();
      post(b.getAttribute("data-favorito")).then(function (r) {
        document.querySelectorAll('[data-favorito="' + b.getAttribute("data-favorito") + '"]').forEach(function (o) {
          o.classList.toggle("ligada", r.favorito);
          o.textContent = r.favorito ? "★" : "☆";
          o.setAttribute("aria-pressed", r.favorito ? "true" : "false");
        });
        toast(r.simbolo + (r.favorito ? " adicionado aos favoritos" : " removido dos favoritos"), "ok");
      }).catch(function () { toast("Não foi possível alterar o favorito.", "erro"); });
      return;
    }
    var g = ev.target.closest("[data-grupo-url]");
    if (g) {
      ev.preventDefault();
      post(g.getAttribute("data-grupo-url"), { grupo: g.getAttribute("data-grupo") })
        .then(function (r) { g.classList.toggle("ativo", r.no_grupo); })
        .catch(function () { toast("Não foi possível alterar o grupo.", "erro"); });
    }
  });

  // --- Critérios do score: dentro de tabela, abrem numa linha inteira logo abaixo -------------------
  document.addEventListener("toggle", function (ev) {
    var d = ev.target;
    if (!d.matches || !d.matches("details.score")) { return; }
    var tr = d.closest("tr");
    if (!tr) { return; }
    var detalhe = d.querySelector(".score-detalhe") || (d._linha && d._linha.querySelector(".score-detalhe"));
    if (d.open && detalhe) {
      var linha = document.createElement("tr");
      linha.className = "linha-score";
      var td = document.createElement("td");
      td.colSpan = tr.cells.length;
      td.appendChild(detalhe);
      linha.appendChild(td);
      tr.parentNode.insertBefore(linha, tr.nextSibling);
      d._linha = linha;
    } else if (!d.open && d._linha) {
      if (detalhe) { d.appendChild(detalhe); }
      d._linha.remove();
      d._linha = null;
    }
  }, true);

  // --- Busca dentro das tabelas -----------------------------------------------------------------
  function ligarBusca(escopo) {
    escopo.querySelectorAll("input[data-busca]").forEach(function (inp) {
      inp.addEventListener("input", function () {
        var q = inp.value.trim().toLowerCase();
        escopo.querySelectorAll("table[data-busca-alvo] tbody tr[data-busca]").forEach(function (tr) {
          tr.classList.toggle("oculta", q !== "" && tr.getAttribute("data-busca").indexOf(q) === -1);
        });
      });
    });
  }

  // --- Sparklines (SVG inline) -------------------------------------------------------------------
  function sparklines(escopo) {
    escopo.querySelectorAll("[data-spark]").forEach(function (td) {
      var v; try { v = JSON.parse(td.getAttribute("data-spark")); } catch (e) { v = []; }
      if (!v || v.length < 2) { td.innerHTML = '<span class="texto-2 mini">—</span>'; return; }
      var min = Math.min.apply(null, v), max = Math.max.apply(null, v), w = 72, h = 22;
      var pts = v.map(function (y, i) { return (i * w / (v.length - 1)).toFixed(1) + "," + (max === min ? h / 2 : h - (y - min) / (max - min) * h).toFixed(1); });
      var classe = v[v.length - 1] >= v[0] ? "alta" : "queda";
      td.innerHTML = '<svg class="sparkline ' + classe + '" viewBox="0 0 ' + w + " " + h + '" width="' + w + '" height="' + h + '" aria-hidden="true"><polyline fill="none" stroke="currentColor" stroke-width="1.5" points="' + pts.join(" ") + '"/></svg>';
    });
  }

  // --- Gráficos do dólar --------------------------------------------------------------------------
  function linha(id, rotulos, valores, opts) {
    var canvas = document.getElementById(id);
    if (!canvas || !window.Chart) { return; }
    if (P.opcoesBase) { P.opcoesBase(); }
    destruir(id);
    var c = cor("--primaria");
    graficos[id] = new Chart(canvas, {
      type: "line",
      data: { labels: rotulos, datasets: [{ data: valores, borderColor: c, backgroundColor: c + "22", fill: true, pointRadius: 0, borderWidth: 2, tension: 0.15 }] },
      options: {
        responsive: true, maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
        plugins: { legend: { display: false }, tooltip: { callbacks: { label: function (ctx) { return (opts && opts.prefixo || "R$ ") + num(ctx.parsed.y, (opts && opts.casas) || 4); } } } },
        scales: { x: { ticks: { maxTicksLimit: 8 } }, y: { ticks: { callback: function (v) { return num(v, 2); } } } }
      }
    });
  }
  function desenharDolar() {
    var serie = lerJSON("dados-dolar");
    if (serie && serie.length) {
      linha("grafico-dolar", serie.map(function (p) { return p.data.slice(8, 10) + "/" + p.data.slice(5, 7) + "/" + p.data.slice(2, 4); }), serie.map(function (p) { return Number(p.valor); }));
    }
    var intra = lerJSON("dados-dolar-intradia");
    if (intra && intra.length) {
      linha("grafico-dolar-intradia", intra.map(function (p) { return new Date(p.t).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" }); }), intra.map(function (p) { return Number(p.valor); }));
    }
    var focus = lerJSON("dados-focus");
    if (focus && focus.length) {
      linha("grafico-focus", focus.map(function (p) { return p.data.slice(8, 10) + "/" + p.data.slice(5, 7); }), focus.map(function (p) { return Number(p.mediana); }), { casas: 2 });
    }
  }

  // --- Comparação dos favoritos (base 100) -------------------------------------------------------
  var periodoComp = "6m";
  function desenharComparacaoFav() {
    var canvas = document.getElementById("grafico-comparacao-fav");
    if (!canvas || !window.Chart) { return; }
    var modelo = canvas.getAttribute("data-url-modelo");
    var marcados = Array.prototype.slice.call(document.querySelectorAll(".sel-comparar-fav:checked")).slice(0, 6);
    destruir("comparacao-fav");
    if (!marcados.length) { return; }
    Promise.all(marcados.map(function (cb) {
      return fetch(modelo.replace("/0/", "/" + cb.value + "/") + "?periodo=" + periodoComp, { credentials: "same-origin" })
        .then(function (r) { return r.json(); })
        .then(function (d) { return { simbolo: cb.getAttribute("data-simbolo"), pontos: d.pontos || [] }; })
        .catch(function () { return { simbolo: cb.getAttribute("data-simbolo"), pontos: [] }; });
    })).then(function (series) {
      var datas = {};
      series.forEach(function (s) { s.pontos.forEach(function (p) { datas[p.data] = true; }); });
      var eixo = Object.keys(datas).sort();
      var cores = paleta();
      var conjuntos = series.filter(function (s) { return s.pontos.length; }).map(function (s, i) {
        var base = Number(s.pontos[0].nativo), mapa = {};
        s.pontos.forEach(function (p) { mapa[p.data] = Number(p.nativo) / base * 100; });
        return { label: s.simbolo, data: eixo.map(function (d) { return mapa[d] === undefined ? null : mapa[d]; }), borderColor: cores[i % cores.length], pointRadius: 0, borderWidth: 2, spanGaps: true, tension: 0.15 };
      });
      if (P.opcoesBase) { P.opcoesBase(); }
      destruir("comparacao-fav");
      graficos["comparacao-fav"] = new Chart(canvas, {
        type: "line",
        data: { labels: eixo.map(function (d) { return d.slice(8, 10) + "/" + d.slice(5, 7); }), datasets: conjuntos },
        options: {
          responsive: true, maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
          plugins: { tooltip: { callbacks: { label: function (ctx) { return ctx.dataset.label + ": " + num(ctx.parsed.y, 1) + " (" + (ctx.parsed.y >= 100 ? "+" : "") + num(ctx.parsed.y - 100, 1) + "%)"; } } } },
          scales: { x: { ticks: { maxTicksLimit: 8 } }, y: { ticks: { callback: function (v) { return num(v, 0); } } } }
        }
      });
    });
  }
  function ligarComparacao(escopo) {
    escopo.querySelectorAll("[data-comp-fav]").forEach(function (b) {
      b.addEventListener("click", function () {
        periodoComp = b.getAttribute("data-comp-fav");
        escopo.querySelectorAll("[data-comp-fav]").forEach(function (o) { o.classList.toggle("ativo", o === b); });
        desenharComparacaoFav();
      });
    });
    escopo.querySelectorAll(".sel-comparar-fav").forEach(function (cb) {
      cb.addEventListener("change", function () {
        if (document.querySelectorAll(".sel-comparar-fav:checked").length > 6) { cb.checked = false; toast("Compare até 6 favoritos.", "erro"); return; }
        desenharComparacaoFav();
      });
    });
  }

  // --- Comparar mercados (base 100, com ou sem câmbio) -------------------------------------------
  var serieComparar = "moeda";
  function desenharComparar() {
    var canvas = document.getElementById("grafico-comparar");
    if (!canvas || !window.Chart) { return; }
    var series = lerJSON(serieComparar === "moeda" ? "dados-comparar" : "dados-comparar-nativo") || [];
    var datas = {};
    series.forEach(function (s) { s.pontos.forEach(function (p) { datas[p.data] = true; }); });
    var eixo = Object.keys(datas).sort();
    var cores = paleta();
    var conjuntos = series.filter(function (s) { return s.pontos.length; }).map(function (s, i) {
      var mapa = {};
      s.pontos.forEach(function (p) { mapa[p.data] = p.valor; });
      return { label: s.simbolo, data: eixo.map(function (d) { return mapa[d] === undefined ? null : mapa[d]; }), borderColor: cores[i % cores.length], pointRadius: 0, borderWidth: 2, spanGaps: true, tension: 0.15 };
    });
    if (P.opcoesBase) { P.opcoesBase(); }
    destruir("comparar");
    graficos.comparar = new Chart(canvas, {
      type: "line",
      data: { labels: eixo.map(function (d) { return d.slice(8, 10) + "/" + d.slice(5, 7) + "/" + d.slice(2, 4); }), datasets: conjuntos },
      options: {
        responsive: true, maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
        plugins: { tooltip: { callbacks: { label: function (ctx) { return ctx.dataset.label + ": " + num(ctx.parsed.y, 1) + " (" + (ctx.parsed.y >= 100 ? "+" : "") + num(ctx.parsed.y - 100, 1) + "%)"; } } } },
        scales: { x: { ticks: { maxTicksLimit: 8 } }, y: { ticks: { callback: function (v) { return num(v, 0); } } } }
      }
    });
  }
  function ligarComparar(escopo) {
    escopo.querySelectorAll("[data-comp-serie]").forEach(function (b) {
      b.addEventListener("click", function () {
        serieComparar = b.getAttribute("data-comp-serie");
        escopo.querySelectorAll("[data-comp-serie]").forEach(function (o) { o.classList.toggle("ativo", o === b); });
        desenharComparar();
      });
    });
    escopo.querySelectorAll(".form-comparar input[type=radio]").forEach(function (r) {
      r.addEventListener("change", function () { r.form.submit(); });
    });
  }

  // --- Inicialização e atualização automática -----------------------------------------------------
  function iniciar(escopo, novoConteudo) {
    // na carga da página o portal.js já liga a ordenação; só o conteúdo recarregado precisa religar
    if (novoConteudo && P.ativarOrdenacao) { P.ativarOrdenacao(escopo); }
    ligarBusca(escopo);
    sparklines(escopo);
    ligarComparacao(escopo);
    ligarComparar(escopo);
    desenharDolar();
    desenharComparacaoFav();
    desenharComparar();
  }

  var painel = document.getElementById("painel-mercado");
  function ocupado() {
    if (!painel) { return true; }
    if (painel.querySelector("details[open]")) { return true; }  // leitura de critérios aberta
    var foco = document.activeElement;
    return foco && painel.contains(foco) && /INPUT|SELECT|TEXTAREA/.test(foco.tagName);
  }
  function recarregar(forcar) {
    if (!painel || (!forcar && (document.hidden || ocupado()))) { return Promise.resolve(); }
    return fetch(painel.getAttribute("data-recarregar"), { credentials: "same-origin" })
      .then(function (r) { if (!r.ok) { throw new Error(); } return r.text(); })
      .then(function (html) {
        Object.keys(graficos).forEach(destruir);
        painel.innerHTML = html;
        iniciar(painel, true);
      })
      .catch(function () { /* mantém o conteúdo atual; tenta na próxima */ });
  }
  if (painel) {
    iniciar(painel);
    var intervalo = parseInt(painel.getAttribute("data-intervalo") || "120", 10) * 1000;
    setInterval(recarregar, intervalo);
    document.addEventListener("visibilitychange", function () { if (!document.hidden) { recarregar(); } });
  } else {
    iniciar(document);
  }
  var botao = document.getElementById("mercado-atualizar");
  if (botao) {
    botao.addEventListener("click", function () {
      botao.disabled = true;
      post(botao.getAttribute("data-url"))
        .then(function () { return recarregar(true); })
        .then(function () { toast("Leituras recalculadas com os dados mais recentes do banco.", "ok"); })
        .catch(function () { toast("Não foi possível atualizar agora.", "erro"); })
        .then(function () { botao.disabled = false; });
    });
  }
  var tema = document.getElementById("alternar-tema");
  if (tema) { tema.addEventListener("click", function () { setTimeout(function () { desenharDolar(); desenharComparacaoFav(); desenharComparar(); }, 30); }); }
})();
