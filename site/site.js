(function () {
  'use strict';

  var REPO = 'Vyrnexis/AppGlider';
  var API = 'https://api.github.com/repos/' + REPO + '/releases/latest';
  var CACHE_KEY = 'appglider-latest-release';

  // Screenshot tabs: click, arrow keys, Home and End.
  var tabs = Array.prototype.slice.call(document.querySelectorAll('[role="tab"]'));
  function select(tab, focus) {
    tabs.forEach(function (t) {
      var on = t === tab;
      t.setAttribute('aria-selected', on ? 'true' : 'false');
      t.tabIndex = on ? 0 : -1;
      document.getElementById(t.getAttribute('aria-controls')).hidden = !on;
    });
    if (focus) tab.focus();
  }
  tabs.forEach(function (tab, i) {
    tab.addEventListener('click', function () { select(tab, false); });
    tab.addEventListener('keydown', function (e) {
      var next = null;
      if (e.key === 'ArrowRight') next = tabs[(i + 1) % tabs.length];
      else if (e.key === 'ArrowLeft') next = tabs[(i - 1 + tabs.length) % tabs.length];
      else if (e.key === 'Home') next = tabs[0];
      else if (e.key === 'End') next = tabs[tabs.length - 1];
      if (next) { e.preventDefault(); select(next, true); }
    });
  });

  // Copy buttons: shown only when scripting is available.
  var status = document.createElement('div');
  status.className = 'sr-status';
  status.setAttribute('role', 'status');
  document.body.appendChild(status);

  function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text);
    }
    return new Promise(function (resolve, reject) {
      var area = document.createElement('textarea');
      area.value = text;
      area.setAttribute('readonly', '');
      area.className = 'sr-status';
      document.body.appendChild(area);
      area.select();
      var ok = false;
      try { ok = document.execCommand('copy'); } catch (err) { ok = false; }
      document.body.removeChild(area);
      if (ok) resolve(); else reject(new Error('copy failed'));
    });
  }

  Array.prototype.forEach.call(document.querySelectorAll('.copy'), function (button) {
    button.hidden = false;
    var label = button.textContent;
    button.addEventListener('click', function () {
      var code = document.getElementById(button.getAttribute('data-copy'));
      if (!code) return;
      copyText(code.textContent).then(function () {
        button.textContent = 'Copied';
        status.textContent = 'Copied to the clipboard.';
      }, function () {
        button.textContent = 'Press Ctrl+C';
        status.textContent = 'Could not copy. Select the text and copy it.';
      });
      window.setTimeout(function () { button.textContent = label; status.textContent = ''; }, 2000);
    });
  });

  // Version: one fallback in the body's data-version, replaced by the latest release when it can be read.
  var fallback = document.body.getAttribute('data-version');
  var downloads = document.querySelectorAll('[data-download]');
  var sums = document.querySelector('[data-sums]');
  var releasePage = document.querySelector('[data-release-page]');
  var versionLine = document.querySelector('[data-version-line]');

  function show(version, appImageUrl, sumsUrl, pageUrl, confirmed) {
    Array.prototype.forEach.call(downloads, function (a) {
      var long = a.hasAttribute('data-download-long');
      a.textContent = long ? 'Download ' + version + ' for Linux' : 'Download';
      if (appImageUrl) a.href = appImageUrl;
    });
    if (sums && sumsUrl) sums.href = sumsUrl;
    if (releasePage && pageUrl) releasePage.href = pageUrl;
    if (versionLine && confirmed) versionLine.textContent = 'Current version: ' + version + '.';
  }

  function apply(release) {
    var version = String(release.tag_name || '').replace(/^v/, '');
    var appImage = null;
    var sumsAsset = null;
    (release.assets || []).forEach(function (asset) {
      if (!appImage && /^AppGlider-.*-x86_64\.AppImage$/.test(asset.name)) appImage = asset;
      if (!sumsAsset && asset.name === 'SHA256SUMS.txt') sumsAsset = asset;
    });
    var prefix = 'https://github.com/' + REPO + '/';
    function ok(asset) { return asset && typeof asset.browser_download_url === 'string' && asset.browser_download_url.indexOf(prefix) === 0; }
    var page = typeof release.html_url === 'string' && release.html_url.indexOf(prefix) === 0 ? release.html_url : null;
    if (!version || !ok(appImage)) return false;
    show(version, appImage.browser_download_url, ok(sumsAsset) ? sumsAsset.browser_download_url : page, page, true);
    return true;
  }

  if (fallback) show(fallback, null, null, null, false);

  var cached = null;
  try { cached = JSON.parse(sessionStorage.getItem(CACHE_KEY) || 'null'); } catch (err) { cached = null; }
  if (cached && apply(cached)) return;

  if (!window.fetch) return;
  fetch(API, { headers: { Accept: 'application/vnd.github+json' } })
    .then(function (response) { if (!response.ok) throw new Error('HTTP ' + response.status); return response.json(); })
    .then(function (release) {
      if (apply(release)) {
        try { sessionStorage.setItem(CACHE_KEY, JSON.stringify({ tag_name: release.tag_name, html_url: release.html_url, assets: (release.assets || []).map(function (a) { return { name: a.name, browser_download_url: a.browser_download_url }; }) })); } catch (err) { /* storage unavailable */ }
      }
    })
    .catch(function () { /* keep the fallback links */ });
}());
