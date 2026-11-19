/* Atualiza a lista armazenada, mantendo filtros, leitura do original e posição da página. */
(function () {
  "use strict";
  var feed = document.getElementById("noticias-feed");
  if (!feed) { return; }
  var loading = false;
  setInterval(function () {
    if (loading || document.hidden || document.querySelector(".noticia-original[open]") ||
        (document.activeElement && document.activeElement.closest(".noticias-filtros"))) { return; }
    loading = true;
    fetch(location.pathname + location.search, {
      credentials: "same-origin", headers: { "X-Requested-With": "fetch" }, cache: "no-store"
    }).then(function (r) {
      if (!r.ok) { throw new Error("Atualização indisponível"); }
      return r.text();
    }).then(function (html) {
      var doc = new DOMParser().parseFromString(html, "text/html");
      var next = doc.getElementById("noticias-feed");
      if (!next) { return; }
      var y = scrollY;
      feed.innerHTML = next.innerHTML;
      [".noticias-status", ".noticias-estatisticas", ".noticias-fontes"].forEach(function (selector) {
        var current = document.querySelector(selector), updated = doc.querySelector(selector);
        if (current && updated && !current.open) { current.innerHTML = updated.innerHTML; }
      });
      scrollTo(0, y);
    }).catch(function () {
      /* As notícias já carregadas continuam legíveis durante uma falha de conexão. */
    }).finally(function () { loading = false; });
  }, 60000);
})();
