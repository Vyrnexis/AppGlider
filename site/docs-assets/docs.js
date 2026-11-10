(function () {
  'use strict';
  document.documentElement.classList.add('js');

  var toggle = document.querySelector('.menu-toggle');
  var side = document.getElementById('sidebar');
  if (toggle && side) {
    toggle.addEventListener('click', function () {
      var open = side.classList.toggle('open');
      toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
  }

  // Sidebar search: queries /docs/search.json, fetched on first focus.
  var input = document.getElementById('doc-search');
  if (input && input.form) {
    var list = document.createElement('ul');
    list.className = 'search-results';
    list.id = 'search-results';
    list.setAttribute('role', 'listbox');
    list.setAttribute('aria-label', 'Search results');
    list.hidden = true;
    input.form.insertAdjacentElement('afterend', list);
    input.setAttribute('role', 'combobox');
    input.setAttribute('aria-controls', 'search-results');
    input.setAttribute('aria-expanded', 'false');
    input.setAttribute('aria-autocomplete', 'list');

    var index = null;
    var loading = false;
    var items = [];
    var active = -1;
    var MAX = 8;

    var load = function () {
      if (index || loading) return;
      loading = true;
      fetch('/docs/search.json')
        .then(function (r) { return r.ok ? r.json() : []; })
        .then(function (data) { index = data; loading = false; if (input.value.trim()) run(); })
        .catch(function () { loading = false; });
    };

    var close = function () {
      list.hidden = true;
      list.textContent = '';
      items = [];
      active = -1;
      input.setAttribute('aria-expanded', 'false');
      input.removeAttribute('aria-activedescendant');
    };

    var setActive = function (n) {
      if (!items.length) return;
      if (active >= 0) {
        items[active].classList.remove('active');
        items[active].removeAttribute('aria-selected');
      }
      active = (n + items.length) % items.length;
      items[active].classList.add('active');
      items[active].setAttribute('aria-selected', 'true');
      input.setAttribute('aria-activedescendant', items[active].id);
    };

    var score = function (entry, terms) {
      var total = 0;
      for (var i = 0; i < terms.length; i++) {
        var t = terms[i];
        var h = entry.heading.toLowerCase().indexOf(t) !== -1;
        var p = entry.page.toLowerCase().indexOf(t) !== -1;
        var x = entry.text.toLowerCase().indexOf(t) !== -1;
        if (!h && !p && !x) return 0;
        total += (h ? 3 : 0) + (p ? 2 : 0) + (x ? 1 : 0);
      }
      return total;
    };

    var span = function (cls, text) {
      var s = document.createElement('span');
      s.className = cls;
      s.textContent = text;
      return s;
    };

    var run = function () {
      var q = input.value.trim().toLowerCase();
      if (!q) { close(); return; }
      if (!index) { load(); return; }
      var terms = q.split(/\s+/);
      var hits = [];
      index.forEach(function (entry, i) {
        var s = score(entry, terms);
        if (s) hits.push({ entry: entry, score: s, i: i });
      });
      hits.sort(function (a, b) { return b.score - a.score || a.i - b.i; });
      list.textContent = '';
      items = [];
      active = -1;
      hits.slice(0, MAX).forEach(function (hit, n) {
        var li = document.createElement('li');
        li.setAttribute('role', 'presentation');
        var a = document.createElement('a');
        a.href = hit.entry.url;
        a.id = 'search-result-' + n;
        a.setAttribute('role', 'option');
        a.appendChild(span('r-page', hit.entry.page));
        if (hit.entry.heading) a.appendChild(span('r-head', hit.entry.heading));
        a.appendChild(span('r-text', hit.entry.text));
        li.appendChild(a);
        list.appendChild(li);
        items.push(a);
      });
      if (!items.length) {
        var none = document.createElement('li');
        none.className = 'r-none';
        none.textContent = 'No pages match.';
        list.appendChild(none);
      }
      list.hidden = false;
      input.setAttribute('aria-expanded', 'true');
    };

    input.addEventListener('focus', load);
    input.addEventListener('input', run);
    input.form.addEventListener('submit', function (e) {
      e.preventDefault();
      if (active >= 0) window.location.href = items[active].href;
      else if (items.length) window.location.href = items[0].href;
    });
    input.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowDown') { e.preventDefault(); setActive(active + 1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); setActive(active < 0 ? -1 : active - 1); }
      else if (e.key === 'Escape') { if (!list.hidden) { e.preventDefault(); close(); } }
    });
    document.addEventListener('click', function (e) {
      if (!input.form.contains(e.target) && !list.contains(e.target)) close();
    });
  }

  // Highlight the current heading in "On this page".
  var links = Array.prototype.slice.call(document.querySelectorAll('.toc a'));
  if (links.length && 'IntersectionObserver' in window) {
    var map = {};
    links.forEach(function (a) { map[a.getAttribute('href').slice(1)] = a; });
    var obs = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) {
          links.forEach(function (a) { a.classList.remove('active'); });
          map[e.target.id].classList.add('active');
        }
      });
    }, { rootMargin: '-80px 0px -70% 0px' });
    Object.keys(map).forEach(function (id) {
      var el = document.getElementById(id);
      if (el) obs.observe(el);
    });
  }
})();
