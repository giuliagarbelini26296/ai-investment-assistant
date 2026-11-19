/* Conferência de compras e seleção acessível de ativos. */
(function () {
  "use strict";
  function numero(texto, fracionaria) {
    var s = (texto || "").replace(/[^\d,.\-]/g, "");
    if (!s) return null;
    if (fracionaria && !s.includes(",") && (s.match(/\./g) || []).length === 1) s = s.replace(".", ",");
    if (s.includes(",") && s.includes(".")) {
      s = s.lastIndexOf(",") > s.lastIndexOf(".") ? s.replace(/\./g, "").replace(",", ".") : s.replace(/,/g, "");
    } else if (s.includes(",")) {
      s = (s.match(/,/g) || []).length === 1 ? s.replace(",", ".") : s.replace(/,/g, "");
    } else if ((s.match(/\./g) || []).length > 1) {
      s = s.replace(/\./g, "");
    } else if (/^-?[1-9]\d*\.\d{3}$/.test(s)) {
      s = s.replace(".", "");
    }
    var n = Number(s);
    return Number.isFinite(n) ? n : null;
  }
  var form = document.getElementById("form-compra");
  if (form) {
    var previa = document.getElementById("previa-compra");
    var formatar = function (n, moeda) { return n.toLocaleString("pt-BR", { style: "currency", currency: moeda }); };
    function atualizarPrevia() {
      var q = numero(form.elements.quantity.value, form.dataset.inteira !== "sim");
      var p = numero(form.elements.unit_price.value, false);
      var fx = form.elements.fx_rate ? numero(form.elements.fx_rate.value, false) : null;
      if (q === null || p === null || q <= 0 || p <= 0) {
        previa.textContent = "Preencha quantidade e preço positivos para conferir o total.";
        return;
      }
      var texto = q.toLocaleString("pt-BR", { maximumFractionDigits: 10 }) + " unidade(s) × " + formatar(p, form.dataset.moeda) + " = " + formatar(q * p, form.dataset.moeda) + ".";
      if (form.dataset.moeda === "USD") {
        if (fx !== null && fx > 0) {
          texto += " Com câmbio de R$ " + fx.toLocaleString("pt-BR", { maximumFractionDigits: 6 }) + ": " + formatar(q * p * fx, "BRL") + ".";
          if (fx < 1 || fx > 15) texto += " Confira o câmbio: valor fora da faixa de conferência.";
        } else texto += " Total em reais depende da resolução do câmbio da compra.";
      }
      previa.textContent = texto;
    }
    form.addEventListener("input", atualizarPrevia);
    atualizarPrevia();
  }
  var selecao = document.getElementById("selecao-comparar");
  if (selecao) {
    var busca = document.getElementById("buscar-comparacao");
    function atualizarSelecao() {
      var termo = busca.value.trim().toLocaleLowerCase("pt-BR");
      var visiveis = 0;
      selecao.querySelectorAll("[data-opcao-ativo]").forEach(function (opcao) {
        opcao.hidden = !opcao.dataset.opcaoAtivo.includes(termo);
        if (!opcao.hidden) visiveis++;
      });
      var n = selecao.querySelectorAll('input[type="checkbox"]:checked').length;
      document.getElementById("contagem-comparacao").textContent = n + " ativo(s) selecionado(s)";
      document.getElementById("comparacao-sem-resultados").hidden = visiveis > 0;
    }
    busca.addEventListener("input", atualizarSelecao);
    selecao.addEventListener("change", atualizarSelecao);
    atualizarSelecao();
  }
})();
