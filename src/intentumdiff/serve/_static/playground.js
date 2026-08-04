'use strict';

const PREF_KEY = 'intentumdiff.preferredPlugins.v1';

let languageGroups = [];
let languageById = {};
let preferredPlugins = readPreferredPlugins();
let oldEditor, newEditor;
let oldTextArea, newTextArea;
let oldDecColl = null, newDecColl = null;
let currentChanges = [];
let detectRequestId = 0;

const exampleCache = {};

const BADGE_COLORS = {
  ADDITION:          '#3fb950',
  DELETION:          '#f85149',
  MODIFICATION:      '#e3b341',
  MOVE:              '#58a6ff',
  STYLE_ONLY:        '#484f58',
  REFACTORING:       '#bc8cff',
  MOVE_TO_MODULE:    '#39d353',
  CROSS_FILE_RENAME: '#ffa657',
  REORDER:           '#388bfd',
};

function readPreferredPlugins() {
  try {
    const raw = localStorage.getItem(PREF_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch {
    return {};
  }
}

function writePreferredPlugins() {
  try {
    localStorage.setItem(PREF_KEY, JSON.stringify(preferredPlugins));
  } catch {
    /* localStorage can be unavailable in private or embedded contexts */
  }
}

function selectedLanguage() {
  return document.getElementById('lang-select')?.value || '';
}

function groupFor(lang) {
  return lang ? languageById[lang] || null : null;
}

function selectedPluginFor(lang) {
  const group = groupFor(lang);
  if (!group || !Array.isArray(group.plugins) || group.plugins.length === 0) return null;
  const preferred = preferredPlugins[lang];
  return (
    group.plugins.find(p => p.pluginId === preferred) ||
    group.plugins.find(p => p.pluginId === group.selectedPluginId) ||
    group.plugins[0]
  );
}

function preferredPluginPayload() {
  const payload = {};
  Object.entries(preferredPlugins).forEach(([lang, pluginId]) => {
    const group = groupFor(lang);
    if (group && group.plugins.some(p => p.pluginId === pluginId)) {
      payload[lang] = pluginId;
    }
  });
  return payload;
}

function pluginQuery(plugin) {
  return plugin ? `?plugin_id=${encodeURIComponent(plugin.pluginId)}` : '';
}

function filenameFor(lang) {
  return selectedPluginFor(lang)?.defaultFilename || (lang ? `code.${lang}` : 'code.txt');
}

function monacoLanguageFor(lang) {
  return selectedPluginFor(lang)?.monacoLanguage || 'plaintext';
}

async function fetchLanguageInfo() {
  const r = await fetch('/language-info');
  if (!r.ok) throw new Error('language-info failed');
  const data = await r.json();
  if (!Array.isArray(data.languages)) throw new Error('language-info malformed');
  return data.languages;
}

async function fetchLegacyLanguages() {
  const r = await fetch('/languages');
  if (!r.ok) return [];
  const data = await r.json();
  if (!Array.isArray(data.languages)) return [];
  return data.languages.map(lang => ({
    language: lang,
    selectedPluginId: lang,
    plugins: [{
      languageId: lang,
      languageName: lang,
      languageShortName: lang,
      monacoLanguage: 'plaintext',
      defaultFilename: `code.${lang}`,
      languageFileExtensions: [],
      author: '',
      pluginVersion: '',
      lastUpdated: '',
      pluginId: lang,
      grammarId: lang,
      priority: 0,
      isTrusted: false,
      provenance: '',
    }],
  }));
}

async function populateLanguageSelector() {
  try {
    languageGroups = await fetchLanguageInfo();
  } catch {
    languageGroups = await fetchLegacyLanguages();
  }
  languageById = Object.fromEntries(languageGroups.map(group => [group.language, group]));

  const sel = document.getElementById('lang-select');
  if (!sel) return;
  const current = sel.value;
  while (sel.options.length > 1) sel.remove(1);
  languageGroups.forEach(group => {
    const plugin = selectedPluginFor(group.language) || group.plugins[0] || {};
    const opt = document.createElement('option');
    opt.value = group.language;
    opt.textContent = plugin.languageName || group.language;
    sel.appendChild(opt);
  });
  const languages = languageGroups.map(group => group.language);
  sel.value = current && languages.includes(current)
    ? current
    : (languages.includes('python') ? 'python' : '');
  updatePluginSelector();
}

function updatePluginSelector() {
  const label = document.getElementById('plugin-label');
  const sel = document.getElementById('plugin-select');
  if (!sel) return;

  const lang = selectedLanguage();
  const group = groupFor(lang);
  const plugins = group?.plugins || [];
  sel.innerHTML = '';

  if (!lang || plugins.length <= 1) {
    sel.style.display = 'none';
    if (label) label.style.display = 'none';
    return;
  }

  plugins.forEach(plugin => {
    const opt = document.createElement('option');
    opt.value = plugin.pluginId;
    opt.textContent = plugin.provenance
      ? `${plugin.languageShortName || lang} - ${plugin.provenance}`
      : `${plugin.languageShortName || lang} - ${plugin.pluginId}`;
    sel.appendChild(opt);
  });
  sel.value = selectedPluginFor(lang)?.pluginId || plugins[0].pluginId;
  sel.style.display = '';
  if (label) label.style.display = '';
}

async function fetchExample(lang) {
  if (!lang) return null;
  const plugin = selectedPluginFor(lang);
  const cacheKey = `${lang}:${plugin?.pluginId || ''}`;
  if (exampleCache[cacheKey]) return exampleCache[cacheKey];
  try {
    const r = await fetch(`/example/${encodeURIComponent(lang)}${pluginQuery(plugin)}`);
    if (!r.ok) return null;
    const ex = await r.json();
    if (ex.old == null && ex.new == null) return null;
    exampleCache[cacheKey] = ex;
    return ex;
  } catch {
    return null;
  }
}

function getOldValue() {
  return oldEditor ? oldEditor.getValue() : (oldTextArea?.value || '');
}

function getNewValue() {
  return newEditor ? newEditor.getValue() : (newTextArea?.value || '');
}

function setOldValue(value) {
  if (oldEditor) oldEditor.setValue(value);
  if (oldTextArea) oldTextArea.value = value;
}

function setNewValue(value) {
  if (newEditor) newEditor.setValue(value);
  if (newTextArea) newTextArea.value = value;
}

function setEditorLanguage(lang) {
  if (!oldEditor || !newEditor || typeof monaco === 'undefined') return;
  const monacoLang = monacoLanguageFor(lang);
  monaco.editor.setModelLanguage(oldEditor.getModel(), monacoLang);
  monaco.editor.setModelLanguage(newEditor.getModel(), monacoLang);
}

function triggerDetectIfAuto() {
  if (selectedLanguage() !== '') return;
  clearTimeout(triggerDetectIfAuto.timer);
  triggerDetectIfAuto.timer = setTimeout(() => {
    serverDetect(`${getNewValue()}\n${getOldValue()}`);
  }, 500);
}

function createFallbackEditor(id, label) {
  const wrap = document.getElementById(id);
  if (!wrap) return null;
  wrap.innerHTML = '';
  const textarea = document.createElement('textarea');
  textarea.className = 'fallback-editor';
  textarea.setAttribute('aria-label', label);
  textarea.spellcheck = false;
  textarea.addEventListener('input', triggerDetectIfAuto);
  wrap.appendChild(textarea);
  return textarea;
}

function initFallbackEditors() {
  oldTextArea = createFallbackEditor('old-wrap', 'Old code');
  newTextArea = createFallbackEditor('new-wrap', 'New code');
  loadExample(selectedLanguage() || 'python');
}

function initEditors() {
  const initialLanguage = monacoLanguageFor(selectedLanguage() || 'python');
  const common = {
    theme: 'vs-dark',
    fontSize: 13,
    minimap: { enabled: false },
    scrollBeyondLastLine: false,
    wordWrap: 'on',
    automaticLayout: true,
    language: initialLanguage,
    glyphMargin: true,
    overviewRulerLanes: 3,
  };
  oldEditor = monaco.editor.create(
    document.getElementById('old-wrap'),
    { ...common, value: '' }
  );
  newEditor = monaco.editor.create(
    document.getElementById('new-wrap'),
    { ...common, value: '' }
  );
  oldEditor.onDidChangeModelContent(triggerDetectIfAuto);
  newEditor.onDidChangeModelContent(triggerDetectIfAuto);
  loadExample(selectedLanguage() || 'python');
}

function startEditors() {
  if (typeof require === 'function' && typeof require.config === 'function') {
    require.config({ paths: { vs: '/static/vendor/min/vs' } });
    require(['vs/editor/editor.main'], initEditors);
  } else {
    console.warn('Monaco loader unavailable; using textarea editors.');
    initFallbackEditors();
  }
}

async function loadExample(lang) {
  const ex = await fetchExample(lang);
  if (!ex) return;
  setOldValue(ex.old);
  setNewValue(ex.new);
}

function showDetected(lang) {
  const span = document.getElementById('detected-lang');
  const name = document.getElementById('detected-lang-name');
  if (lang) {
    name.textContent = lang;
    span.style.display = 'inline';
    setEditorLanguage(lang);
  } else {
    span.style.display = 'none';
  }
}

async function detectOnServer(code) {
  if (!code || code.trim().length < 10) return null;
  try {
    const preferred = preferredPluginPayload();
    const body = { content: code.slice(0, 8192) };
    if (Object.keys(preferred).length) body.preferred_plugins = preferred;
    const r = await fetch('/detect', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!r.ok) return null;
    const { language } = await r.json();
    return language || null;
  } catch {
    return null;
  }
}

async function serverDetect(code) {
  const requestId = ++detectRequestId;
  const language = await detectOnServer(code);
  if (requestId !== detectRequestId) return;
  showDetected(language);
}

function clearDecorations() {
  oldDecColl?.clear(); newDecColl?.clear();
  oldDecColl = newDecColl = null;
}

function applyDecorations(changes) {
  if (!oldEditor || !newEditor) return;
  const oldDecs = [], newDecs = [];
  changes.forEach(c => {
    const ct = c.change_type;
    const color = BADGE_COLORS[ct] || '#888';
    const makeOpts = label => ({
      isWholeLine: false,
      className: 'dec-' + ct,
      glyphMarginClassName: 'dec-' + ct + '-glyph',
      hoverMessage: {
        value: '**' + ct + '**' + (label ? ': ' + label : ''),
        isTrusted: false,
      },
      overviewRuler: { color, position: monaco.editor.OverviewRulerLane.Right },
      stickiness: monaco.editor.TrackedRangeStickiness.NeverGrowsWhenTypingAtEdges,
    });
    if (c.old_node?.position) {
      const p = c.old_node.position;
      oldDecs.push({
        range: new monaco.Range(p.start_line + 1, p.start_col + 1, p.end_line + 1, p.end_col + 1),
        options: makeOpts(c.old_node.label),
      });
    }
    if (c.new_node?.position) {
      const p = c.new_node.position;
      newDecs.push({
        range: new monaco.Range(p.start_line + 1, p.start_col + 1, p.end_line + 1, p.end_col + 1),
        options: makeOpts(c.new_node.label),
      });
    }
  });
  oldDecColl = oldEditor.createDecorationsCollection(oldDecs);
  newDecColl = newEditor.createDecorationsCollection(newDecs);
}

document.getElementById('lang-select').addEventListener('change', e => {
  const lang = e.target.value;
  updatePluginSelector();
  setEditorLanguage(lang);
  if (lang) {
    loadExample(lang);
    showDetected(null);
  }
});

document.getElementById('plugin-select')?.addEventListener('change', e => {
  const lang = selectedLanguage();
  if (!lang) return;
  preferredPlugins[lang] = e.target.value;
  writePreferredPlugins();
  setEditorLanguage(lang);
  loadExample(lang);
});

document.getElementById('results-toggle').addEventListener('click', () => {
  const panel = document.getElementById('results');
  const btn = document.getElementById('results-toggle');
  btn.textContent = panel.classList.toggle('collapsed') ? 'Changes >' : 'Changes v';
});

document.getElementById('results').addEventListener('click', e => {
  const li = e.target.closest('[data-idx]');
  if (!li) return;
  const c = currentChanges[+li.dataset.idx];
  if (!c) return;
  if (c.old_node?.position && oldEditor) {
    oldEditor.revealLineInCenter(c.old_node.position.start_line + 1);
  }
  if (c.new_node?.position && newEditor) {
    newEditor.revealLineInCenter(c.new_node.position.start_line + 1);
  }
});

document.getElementById('compare-btn').addEventListener('click', async () => {
  const btn = document.getElementById('compare-btn');
  const spinner = document.getElementById('spinner');
  const results = document.getElementById('results');

  btn.disabled = true;
  spinner.style.display = 'inline';
  results.innerHTML = '';
  clearDecorations();

  let lang = selectedLanguage();
  if (!lang) {
    lang = await detectOnServer(`${getNewValue()}\n${getOldValue()}`) || '';
    if (lang) showDetected(lang);
  }

  const plugin = selectedPluginFor(lang);
  try {
    const res = await fetch('/diff', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        old: getOldValue(),
        new: getNewValue(),
        filename: filenameFor(lang),
        language: lang || null,
        plugin_id: plugin?.pluginId || null,
      }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      const detail = typeof err.detail === 'string' ? err.detail : JSON.stringify(err.detail);
      results.innerHTML = '<div class="error-msg">Error ' + res.status + ': ' + escHtml(detail) + '</div>';
      return;
    }

    const diff = await res.json();
    renderResults(diff);
    if (results.classList.contains('collapsed')) {
      results.classList.remove('collapsed');
      document.getElementById('results-toggle').textContent = 'Changes v';
    }
  } catch (ex) {
    results.innerHTML = '<div class="error-msg">Network error: ' + escHtml(ex.message) + '</div>';
  } finally {
    btn.disabled = false;
    spinner.style.display = 'none';
  }
});

function renderResults(diff) {
  const results = document.getElementById('results');
  const frag = document.createDocumentFragment();

  currentChanges = diff.changes || [];
  applyDecorations(currentChanges);

  (diff.parse_errors || []).forEach(msg => {
    const div = document.createElement('div');
    div.className = 'parse-error';
    div.textContent = '! ' + msg;
    frag.appendChild(div);
  });

  const changes = diff.changes || [];
  const summary = document.createElement('p');
  summary.className = 'summary';
  if (!diff.has_semantic_changes && !diff.is_style_only) {
    summary.innerHTML = '<strong>No changes</strong> detected.';
  } else if (diff.is_style_only) {
    summary.innerHTML = '<strong>Style-only</strong> - no semantic differences.';
  } else {
    const counts = {};
    changes.forEach(c => { counts[c.change_type] = (counts[c.change_type] || 0) + 1; });
    const parts = Object.entries(counts).map(
      ([k, v]) => v + ' ' + escHtml(k.toLowerCase().replace(/_/g, ' '))
    );
    const n = changes.length;
    summary.innerHTML =
      '<strong>' + n + ' change' + (n !== 1 ? 's' : '') + '</strong> - ' + parts.join(', ') + '.';
  }
  frag.appendChild(summary);

  if (changes.length > 0) {
    const ul = document.createElement('ul');
    ul.className = 'change-list';
    changes.forEach((c, idx) => {
      const li = document.createElement('li');
      const ct = c.change_type;
      li.className = 'change-item ci-' + ct;
      li.dataset.idx = idx;
      const node = c.new_node || c.old_node;
      const label = node ? node.label : '-';
      const desc = normaliseDescription(c);
      const metaParts = [
        node ? node.node_type : null,
        formatNodePosition(node),
      ].filter(Boolean);
      li.innerHTML =
        '<span class="badge badge-' + escHtml(ct) + '">' + escHtml(formatChangeType(ct)) + '</span>' +
        '<div class="change-detail">' +
          '<span class="change-label">' + escHtml(label) + '</span>' +
          (desc
            ? '<span class="change-desc">' + escHtml(desc) + '</span>'
            : '') +
          (metaParts.length
            ? '<span class="change-meta">' + escHtml(metaParts.join(' · ')) + '</span>'
            : '') +
        '</div>';
      ul.appendChild(li);
    });
    frag.appendChild(ul);
  }

  const details = document.createElement('details');
  details.className = 'json-toggle';
  const summary2 = document.createElement('summary');
  summary2.textContent = 'Raw JSON';
  const pre = document.createElement('pre');
  pre.textContent = JSON.stringify(diff, null, 2);
  details.appendChild(summary2);
  details.appendChild(pre);
  frag.appendChild(details);

  results.appendChild(frag);
}

function formatChangeType(value) {
  return String(value || '')
    .toLowerCase()
    .split('_')
    .map(part => part ? part[0].toUpperCase() + part.slice(1) : part)
    .join(' ');
}

function normaliseDescription(change) {
  const parts = [];
  if (change.description) parts.push(change.description);
  if (change.refactoring_kind) {
    const kind = change.refactoring_kind.replace(/_/g, ' ').toLowerCase();
    if (!parts.some(part => part.toLowerCase().includes(kind))) parts.push(kind);
  }
  return parts.join(' · ');
}

function formatNodePosition(node) {
  const pos = node?.position;
  if (!pos || typeof pos.start_line !== 'number') return null;
  return 'line ' + (pos.start_line + 1);
}

function escHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

populateLanguageSelector().then(startEditors);
