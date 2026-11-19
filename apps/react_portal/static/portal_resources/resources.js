(function () {
  'use strict';
  var root = document.documentElement;
  var menu = document.getElementById('resource-menu');
  var sidebar = document.getElementById('resource-sidebar');
  var scrim = document.querySelector('.resource-scrim');
  function syncTheme() {
    var theme = root.dataset.theme === 'dark' ? 'dark' : 'light';
    try { localStorage.setItem('id3-theme', theme); localStorage.setItem('portal-tema', theme); } catch(e) {}
    var toggle = document.getElementById('alternar-tema');
    if (toggle) toggle.setAttribute('aria-label', theme === 'dark' ? 'Ativar tema claro' : 'Ativar tema escuro');
  }
  syncTheme();
  new MutationObserver(syncTheme).observe(root, { attributes:true, attributeFilter:['data-theme'] });
  function setMenu(open) {
    sidebar.classList.toggle('is-open', open);
    scrim.hidden = !open;
    menu.setAttribute('aria-expanded', String(open));
    if (open) sidebar.querySelector('a').focus();
    else menu.focus();
  }
  menu.addEventListener('click', function () { setMenu(!sidebar.classList.contains('is-open')); });
  scrim.addEventListener('click', function () { setMenu(false); });
  document.addEventListener('keydown', function (e) {
    if (!sidebar.classList.contains('is-open')) return;
    if (e.key === 'Escape') { setMenu(false); return; }
    if (e.key !== 'Tab') return;
    var links = sidebar.querySelectorAll('a,button');
    var first = links[0], last = links[links.length-1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  });
  window.addEventListener('resize', function () {
    if (innerWidth > 760 && sidebar.classList.contains('is-open')) setMenu(false);
  });
  var content = document.getElementById('resource-content');
  function adaptContent() {
    content.querySelectorAll('table').forEach(function (table) {
      if (table.closest('.resource-table-scroll')) return;
      var wrap = document.createElement('div');
      wrap.className = 'resource-table-scroll';
      wrap.tabIndex = 0;
      wrap.setAttribute('role', 'region');
      wrap.setAttribute('aria-label', 'Tabela com rolagem horizontal');
      table.parentNode.insertBefore(wrap, table);
      wrap.appendChild(table);
    });
    content.querySelectorAll('a[target="_blank"]').forEach(function (link) {
      if (new URL(link.href, location.href).origin === location.origin) link.removeAttribute('target');
    });
  }
  adaptContent();
  var scheduled = false;
  new MutationObserver(function () {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(function () { scheduled = false; adaptContent(); });
  }).observe(content, { childList:true, subtree:true });
})();
