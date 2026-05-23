import { api, fmt } from '/web/app.js';

export default async function (root) {
  const id = decodeURIComponent(location.hash.split('/')[2] || '');
  if (!id) return renderList(root);
  return renderSession(root, id);
}

function cacheRate(s) {
  const total = (s.tokens || 0) + (s.cache_read_tokens || 0) + (s.cache_create_tokens || 0);
  return total > 0 ? s.cache_read_tokens / total : null;
}

async function renderList(root) {
  const list = await api('/api/sessions?limit=100');
  root.innerHTML = `
    <div class="card">
      <h2>Sessions</h2>
      <table>
        <thead><tr><th>started</th><th>project</th><th class="num">turns</th><th class="num">tokens</th><th class="num">cache rate</th><th>session</th></tr></thead>
        <tbody>
          ${list.map(s => `
            <tr>
              <td class="mono">${fmt.ts(s.started)}</td>
              <td title="${fmt.htmlSafe(s.project_slug)}">${fmt.htmlSafe(s.project_name || s.project_slug)}</td>
              <td class="num">${fmt.int(s.turns)}</td>
              <td class="num">${fmt.int(s.tokens)}</td>
              <td class="num good">${fmt.pct(cacheRate(s))}</td>
              <td><a href="#/sessions/${encodeURIComponent(s.session_id)}" class="mono">${fmt.htmlSafe(s.session_id.slice(0,8))}…</a></td>
            </tr>`).join('')}
        </tbody>
      </table>
    </div>`;
}

function renderTurnsTable(turns, sortKey) {
  const sorted = sortKey
    ? [...turns].sort((a, b) => (b[sortKey] || 0) - (a[sortKey] || 0))
    : turns;

  const hdr = (label, key, cls='num') => {
    const active = sortKey === key ? ' sort-active' : '';
    return `<th class="${cls} sortable${active}" data-sort="${key}">${label}${sortKey === key ? ' ▼' : ''}</th>`;
  };

  return `<table>
    <thead><tr>
      <th>time</th><th>type</th><th>model</th>
      <th class="blur-sensitive">prompt / tools</th>
      ${hdr('in', 'input_tokens')}
      ${hdr('out', 'output_tokens')}
      ${hdr('cache rd', 'cache_read_tokens')}
    </tr></thead>
    <tbody>
      ${sorted.map(t => {
        const tools = t.tool_calls_json ? JSON.parse(t.tool_calls_json) : [];
        const summary = t.prompt_text ? fmt.short(t.prompt_text, 110)
          : tools.length ? tools.map(x => x.name).join(' · ')
          : '';
        return `<tr>
          <td class="mono">${(t.timestamp || '').slice(11,19)}</td>
          <td>${t.type}${t.is_sidechain ? ' <span class="badge">side</span>' : ''}</td>
          <td>${t.model ? `<span class="badge ${fmt.modelClass(t.model)}">${fmt.htmlSafe(fmt.modelShort(t.model))}</span>` : ''}</td>
          <td class="blur-sensitive">${fmt.htmlSafe(summary)}</td>
          <td class="num">${fmt.int(t.input_tokens)}</td>
          <td class="num">${fmt.int(t.output_tokens)}</td>
          <td class="num">${fmt.int(t.cache_read_tokens)}</td>
        </tr>`;
      }).join('')}
    </tbody>
  </table>`;
}

async function renderSession(root, id) {
  const turns = await api('/api/sessions/' + encodeURIComponent(id));
  let totalIn = 0, totalOut = 0, totalCacheRd = 0;
  for (const t of turns) {
    if (t.type !== 'assistant') continue;
    totalIn += t.input_tokens || 0;
    totalOut += t.output_tokens || 0;
    totalCacheRd += t.cache_read_tokens || 0;
  }
  const slug = (turns[0] && turns[0].project_slug) || '';
  const cwd = (turns.find(t => t.cwd) || {}).cwd || '';
  const base = cwd ? cwd.replace(/\\/g, '/').replace(/\/+$/, '').split('/').pop() : '';
  const project = base || slug;
  const started = (turns[0] && turns[0].timestamp) || '';
  const ended = (turns[turns.length-1] && turns[turns.length-1].timestamp) || '';

  let sortKey = null;

  const summary = document.createElement('div');
  summary.className = 'card';
  summary.innerHTML = `
    <h2 style="display:flex;align-items:center">
      <span>Session ${fmt.htmlSafe(id.slice(0,8))}…</span>
      <span class="spacer"></span>
      <a href="#/sessions" class="muted">← all sessions</a>
    </h2>
    <div class="flex muted" style="font-family:var(--mono);font-size:12px;flex-wrap:wrap;gap:14px">
      <span>${fmt.htmlSafe(project)}</span>
      <span>${fmt.ts(started)} → ${fmt.ts(ended)}</span>
      <span>${turns.length} records</span>
      <span>${fmt.int(totalIn)} in · ${fmt.int(totalOut)} out · ${fmt.int(totalCacheRd)} cache rd</span>
    </div>`;

  const detail = document.createElement('div');
  detail.className = 'card';
  detail.style.marginTop = '16px';

  const refresh = () => {
    detail.innerHTML = `<h3>Turn-by-turn</h3>` + renderTurnsTable(turns, sortKey);
    detail.querySelectorAll('th.sortable').forEach(th => {
      th.style.cursor = 'pointer';
      th.addEventListener('click', () => {
        sortKey = sortKey === th.dataset.sort ? null : th.dataset.sort;
        refresh();
      });
    });
  };

  root.innerHTML = '';
  root.appendChild(summary);
  root.appendChild(detail);
  refresh();
}
