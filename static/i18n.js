// Minimal i18n: dictionaries in /static/i18n/<lang>.json, markup tagged with data-i18n[-title|-placeholder].
// French is the default for French browsers, English otherwise; the choice is remembered.
(function () {
  const SUPPORTED = ['fr', 'en'];
  const dict = {};
  let lang = 'fr';

  function detect() {
    const saved = localStorage.getItem('clipmaster_lang');
    if (SUPPORTED.includes(saved)) return saved;
    return (navigator.language || 'en').toLowerCase().startsWith('fr') ? 'fr' : 'en';
  }

  window.t = function (key, vars) {
    let s = (dict[lang] && dict[lang][key]) || (dict.fr && dict.fr[key]) || key;
    if (vars) for (const k of Object.keys(vars)) s = s.split('{' + k + '}').join(vars[k]);
    return s;
  };

  window.getLang = () => lang;

  // Tell the backend which language to use for its error messages, and go back to the login page when
  // the server answers 401 (token required / expired).
  const _fetch = window.fetch.bind(window);
  window.fetch = function (input, init) {
    init = init || {};
    const url = typeof input === 'string' ? input : (input && input.url) || '';
    if (url.startsWith('/') || url.startsWith(location.origin)) {
      const h = new Headers(init.headers || (typeof input !== 'string' && input.headers) || {});
      if (!h.has('X-Lang')) h.set('X-Lang', lang);
      init.headers = h;
    }
    return _fetch(input, init).then(res => {
      if (res.status === 401 && url.startsWith('/api/')) location.href = '/';
      return res;
    });
  };

  window.applyI18n = function (root) {
    const scope = root || document;
    scope.querySelectorAll('[data-i18n]').forEach(el => { el.textContent = t(el.dataset.i18n); });
    scope.querySelectorAll('[data-i18n-title]').forEach(el => { el.title = t(el.dataset.i18nTitle); });
    scope.querySelectorAll('[data-i18n-placeholder]').forEach(el => { el.placeholder = t(el.dataset.i18nPlaceholder); });
    document.documentElement.lang = lang;
    const btn = document.getElementById('btnLang');
    if (btn) btn.textContent = t('top.lang');
  };

  window.setLang = async function (next) {
    if (!SUPPORTED.includes(next)) return;
    lang = next;
    localStorage.setItem('clipmaster_lang', lang);
    applyI18n();
    document.dispatchEvent(new CustomEvent('langchange', { detail: lang }));
  };

  window.toggleLang = () => setLang(lang === 'fr' ? 'en' : 'fr');

  window.i18nReady = (async function () {
    await Promise.all(SUPPORTED.map(async l => {
      try { dict[l] = await (await fetch('/static/i18n/' + l + '.json')).json(); } catch (e) { dict[l] = {}; }
    }));
    lang = detect();
    applyI18n();
  })();
})();
