import { api, state, $ } from '/web/app.js';

export default async function (root) {
  const cur = await api('/api/plan');
  const claudePlans  = Object.entries(cur.pricing.plans        || {});
  const codexPlans   = Object.entries(cur.pricing.codex_plans  || {});
  const _opts = (entries, selected) => entries.map(([k,v]) =>
    `<option value="${k}" ${k===selected?'selected':''}>${v.label}${v.monthly?` — $${v.monthly}/mo`:''}</option>`
  ).join('');
  root.innerHTML = `
    <div class="card">
      <h2>Settings</h2>
      <h3 style="margin-top:16px">Claude Code Plan</h3>
      <p class="muted" style="margin:0 0 12px">Sets how Claude token costs are displayed.</p>
      <div class="flex">
        <select id="plan">${_opts(claudePlans, cur.plan)}</select>
        <button class="primary" id="save-claude">Save</button>
        <span id="msg-claude" class="muted"></span>
      </div>

      <h3 style="margin-top:20px">Codex Plan</h3>
      <p class="muted" style="margin:0 0 12px">Sets how Codex token costs are displayed.</p>
      <div class="flex">
        <select id="codex-plan">${_opts(codexPlans, cur.codex_plan)}</select>
        <button class="primary" id="save-codex">Save</button>
        <span id="msg-codex" class="muted"></span>
      </div>

      <hr class="divider">

      <h3>Pricing table</h3>
      <p class="muted" style="margin:0 0 12px">Edit <code>pricing.json</code> in the project root to change rates. Reload the page after editing.</p>
      <table>
        <thead><tr><th>model</th><th class="num">input</th><th class="num">output</th><th class="num">cache read</th><th class="num">cache 5m</th><th class="num">cache 1h</th></tr></thead>
        <tbody>
          ${Object.entries(cur.pricing.models).map(([k,v]) => `
            <tr><td><span class="badge ${v.tier}">${k}</span></td>
              <td class="num">$${v.input.toFixed(2)}</td>
              <td class="num">$${v.output.toFixed(2)}</td>
              <td class="num">$${v.cache_read.toFixed(2)}</td>
              <td class="num">$${v.cache_create_5m.toFixed(2)}</td>
              <td class="num">$${v.cache_create_1h.toFixed(2)}</td>
            </tr>`).join('')}
        </tbody>
      </table>
      <p class="muted" style="margin-top:8px;font-size:11px">Rates per 1M tokens, USD.</p>

      <hr class="divider">

      <h3>Privacy</h3>
      <p class="muted">Press <code>Cmd/Ctrl + B</code> anywhere to blur prompt text and other sensitive content for screenshots.</p>
    </div>`;

  const _save = async (selectId, msgId, source) => {
    const plan = $(selectId).value;
    await fetch('/api/plan', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ plan, source }) });
    if (source === 'claude') { state.plan = plan; document.getElementById('plan-pill').textContent = plan; }
    $(msgId).textContent = 'Saved.';
    $(msgId).style.color = 'var(--good)';
  };
  $('#save-claude').addEventListener('click', () => _save('#plan', '#msg-claude', 'claude'));
  $('#save-codex').addEventListener('click',  () => _save('#codex-plan', '#msg-codex', 'codex'));
}
