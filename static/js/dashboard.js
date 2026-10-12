/* FrontDesk owner dashboard */
const $ = s => document.querySelector(s);
const biz = () => $('#bizSel').value;
const headers = () => ({'Content-Type': 'application/json', 'X-Owner-Token': $('#tokenIn').value});
function toast(msg){ const t = $('#toast'); t.textContent = msg; t.style.display = 'block'; setTimeout(()=>t.style.display='none', 2600); }
async function api(path, opts = {}){
  const r = await fetch(path, {...opts, headers: {...headers(), ...(opts.headers||{})}});
  if (r.status === 401){ toast('Wrong owner token — check the field in the top bar.'); throw new Error('auth'); }
  if (!r.ok){ const e = await r.text(); toast('Error: ' + e.slice(0,120)); throw new Error(e); }
  return r.json();
}
$('#tokenIn').value = localStorage.getItem('fd_token') || '';
$('#tokenIn').addEventListener('change', e => localStorage.setItem('fd_token', e.target.value));

document.querySelectorAll('.tab').forEach(t => t.onclick = () => {
  document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(x => x.classList.remove('active'));
  t.classList.add('active'); $('#panel-' + t.dataset.tab).classList.add('active');
  load(t.dataset.tab);
});
$('#bizSel').addEventListener('change', () => load(document.querySelector('.tab.active').dataset.tab));

/* ── inbox ── */
async function loadInbox(){
  const items = await api(`/api/owner/escalations?business=${biz()}&status=open`);
  const c = $('#inboxCount'); c.style.display = items.length ? '' : 'none'; c.textContent = items.length;
  const box = $('#inbox');
  if (!items.length){ box.innerHTML = '<div class="empty">🎉 All clear — nothing needs you right now.</div>'; return; }
  box.innerHTML = items.map(e => `
    <div class="brief" data-id="${e.id}">
      <div class="brief-head">
        <span class="reason ${e.reason}">${e.reason.replace(/_/g,' ')}</span>
        <h3>Chat #${e.conversation_id}</h3>
        <span class="time">${new Date(e.created_at).toLocaleString()}</span>
      </div>
      <div class="brief-grid">
        <div class="brief-box"><h4>What happened</h4><p>${esc(e.brief_summary)}</p></div>
        <div class="brief-box"><h4>What the AI already tried</h4>${e.ai_tried.length ? '<ul>'+e.ai_tried.map(x=>'<li>'+esc(x)+'</li>').join('')+'</ul>' : '<p>—</p>'}</div>
        <div class="brief-box"><h4>What you must decide</h4><p>${esc(e.needs_decision)}</p></div>
      </div>
      <div class="brief-actions">
        <input class="reply-in" placeholder="Reply to the customer in this chat…" style="flex:1;border:2px solid var(--ink);border-radius:10px;padding:8px 12px;font:inherit">
        <button class="btn small" data-act="reply">Send reply</button>
        <button class="btn small ghost" data-act="claim">Claim</button>
        <button class="btn small leaf" data-act="resolve">Resolve</button>
        <button class="btn small ghost" data-act="thread">View thread</button>
      </div>
    </div>`).join('');
  box.querySelectorAll('.brief').forEach(card => {
    const id = card.dataset.id;
    card.querySelector('[data-act="claim"]').onclick = async () => { await api(`/api/owner/escalations/${id}/claim`, {method:'POST'}); toast('Claimed.'); loadInbox(); };
    card.querySelector('[data-act="resolve"]').onclick = async () => {
      const note = prompt('Resolution note (optional):') || '';
      await api(`/api/owner/escalations/${id}/resolve`, {method:'POST', body: JSON.stringify({resolution_note: note})});
      toast('Resolved.'); loadInbox();
    };
    card.querySelector('[data-act="reply"]').onclick = async () => {
      const text = card.querySelector('.reply-in').value.trim(); if(!text) return toast('Type a reply first.');
      await api(`/api/owner/escalations/${id}/reply`, {method:'POST', body: JSON.stringify({text})});
      toast('Sent into the customer chat.'); card.querySelector('.reply-in').value = '';
    };
    card.querySelector('[data-act="thread"]').onclick = () => showThread(card.querySelector('h3').textContent.match(/#(\d+)/)[1]);
  });
}
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

/* ── chats ── */
async function loadChats(){
  const rows = await api(`/api/owner/conversations?business=${biz()}`);
  $('#chatList').innerHTML = rows.length ? rows.map(c => `
    <div class="card" style="cursor:pointer" data-id="${c.id}">
      <div class="num">${c.channel} · ${c.status}</div>
      <h3>Chat #${c.id}</h3><p>${esc(c.last_message) || '—'}</p>
    </div>`).join('') : '<div class="empty">No chats yet — talk to the demo on the landing page.</div>';
  $('#chatList').querySelectorAll('.card').forEach(el => el.onclick = () => showThread(el.dataset.id));
}
async function showThread(id){
  document.querySelector('[data-tab="chats"]').click();
  const msgs = await api(`/api/owner/conversations/${id}/messages`);
  $('#chatDetail').style.display = '';
  $('#threadBody').innerHTML = msgs.map(m =>
    `<div class="msg ${m.role}"><span class="who">${m.role}${m.intent ? ' · ' + m.intent : ''}${m.meta && m.meta.action ? ' → ' + m.meta.action : ''}</span>${esc(m.text)}</div>`).join('');
  $('#threadBody').scrollTop = 1e6;
}

/* ── catalog ── */
async function loadCatalog(){
  const items = await api(`/api/catalog?business=${biz()}`);
  $('#catalogList').innerHTML = items.length ? `<table class="data"><tr><th>Item</th><th>Price</th><th>Stock</th><th></th></tr>` +
    items.map(i => `<tr><td><b>${esc(i.name)}</b><br><small>${esc(i.category)}</small></td>
      <td>₹${(i.price/100).toLocaleString('en-IN')}</td><td>${i.in_stock ? '✓' : '✗'}</td>
      <td><button class="btn small clay" data-del="${i.id}">Delete</button></td></tr>`).join('') + '</table>'
    : '<div class="empty">No items yet.</div>';
  $('#catalogList').querySelectorAll('[data-del]').forEach(b => b.onclick = async () => {
    await api(`/api/catalog/${b.dataset.del}?business=${biz()}`, {method:'DELETE'}); loadCatalog();
  });
}
$('#ci-add').onclick = async () => {
  await api(`/api/catalog?business=${biz()}`, {method:'POST', body: JSON.stringify({
    name: $('#ci-name').value, description: $('#ci-desc').value,
    price: parseInt($('#ci-price').value || '0', 10), category: $('#ci-cat').value})});
  $('#ci-name').value = $('#ci-desc').value = $('#ci-cat').value = ''; toast('Added.'); loadCatalog();
};

/* ── bookings ── */
async function loadBookings(){
  const rows = await api(`/api/bookings?business=${biz()}`);
  $('#bookingList').innerHTML = rows.length ? `<table class="data"><tr><th>When</th><th>Customer</th><th>Service</th><th>Status</th><th></th></tr>` +
    rows.map(b => `<tr><td>${new Date(b.starts_at).toLocaleString()}</td><td>${esc(b.customer_name)}<br><small>${esc(b.customer_phone)}</small></td>
      <td>${esc(b.service)}</td><td>${b.status}</td>
      <td>${b.status==='confirmed' ? `<button class="btn small clay" data-c="${b.id}">Cancel</button>` : ''}</td></tr>`).join('') + '</table>'
    : '<div class="empty">No bookings yet.</div>';
  $('#bookingList').querySelectorAll('[data-c]').forEach(x => x.onclick = async () => {
    await api(`/api/bookings/${x.dataset.c}?business=${biz()}`, {method:'DELETE'}); toast('Cancelled.'); loadBookings();
  });
}

/* ── analytics ── */
async function loadAnalytics(){
  const a = await api(`/api/owner/analytics?business=${biz()}`);
  $('#statGrid').innerHTML = `
    <div class="stat"><div class="v">${a.chats_7d}</div><div class="l">Chats · 7 days</div></div>
    <div class="stat"><div class="v">${a.bookings_7d}</div><div class="l">Bookings · 7 days</div></div>
    <div class="stat"><div class="v">${a.escalations_waiting}</div><div class="l">Waiting on you</div></div>
    <div class="stat"><div class="v">${a.escalations_resolved_7d}</div><div class="l">Resolved · 7 days</div></div>`;
  $('#plainList').innerHTML = a.plain_language_summary.map(s => `<li>${esc(s)}</li>`).join('');
}

/* ── setup wizard ── */
const WZ = ['Business', 'Language', 'Catalog', 'Hours', 'WhatsApp'];
let wzBiz = '';
function renderWz(step){
  $('#wzSteps').innerHTML = WZ.map((t, i) =>
    `<div class="wz ${i + 1 < step ? 'done' : ''}"><div class="d">${i + 1 < step ? '✓' : i + 1}</div>${t}</div>`).join('');
  const B = $('#wzBody');
  const input = (id, label, val='', ph='') => `<label>${label}</label><input id="${id}" value="${val}" placeholder="${ph}">`;
  if (step === 1) B.innerHTML = input('w-name','Business name','','Demo Boutique') + input('w-tag','Tagline','') + input('w-phone','Phone','') +
    `<p style="margin-top:14px"><button class="btn" id="w-next">Continue →</button></p>`;
  if (step === 2) B.innerHTML = `<label>Language</label><select id="w-lang"><option value="en">English</option><option value="hi">Hindi (हिन्दी)</option></select>
    <p style="margin-top:14px"><button class="btn" id="w-next">Continue →</button></p>`;
  if (step === 3) B.innerHTML = `<label>Items (one per line: name | price in rupees)</label><textarea id="w-items" placeholder="Silver necklace | 2499&#10;Jhumka earrings | 1299"></textarea>
    <p style="margin-top:14px"><button class="btn" id="w-next">Continue →</button></p>`;
  if (step === 4) B.innerHTML = `<label>Open days & hours (e.g. mon–sat 10:00–20:00)</label>
    <input id="w-days" value="mon,tue,wed,thu,fri,sat"><input id="w-hrs" value="10:00-20:00" style="margin-top:8px">
    <p style="margin-top:14px"><button class="btn" id="w-next">Continue →</button></p>`;
  if (step === 5) B.innerHTML = `<p>WhatsApp connects later via <b>.env</b> keys — the web chat works right now.</p>
    <p style="margin-top:14px"><button class="btn" id="w-next">Finish — we're live 🎉</button></p>`;
  $('#w-next').onclick = () => wizardNext(step);
}
async function wizardNext(step){
  let data = {};
  if (step === 1) data = {name: $('#w-name').value, tagline: $('#w-tag').value, phone: $('#w-phone').value};
  if (step === 2) data = {language: $('#w-lang').value};
  if (step === 3) data = {items: $('#w-items').value.split('\n').map(l => { const [n, p] = l.split('|'); return {name: (n||'').trim(), price: Math.round(parseFloat(p || '0') * 100)}; }).filter(x => x.name)};
  if (step === 4){ const days = $('#w-days').value.split(',').map(s => s.trim()); const [a, b2] = $('#w-hrs').value.split('-');
    data = {hours: Object.fromEntries(days.map(d => [d, [a.trim(), b2.trim()]])), slot_minutes: 30}; }
  if (step === 5) data = {};
  const r = await fetch('/api/setup/wizard', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({business: wzBiz, step, data})});
  const j = await r.json(); wzBiz = j.business; toast(j.message);
  if (j.done){ $('#wzBody').innerHTML = `<h3>🎉 You're live!</h3><p>Shop slug: <b>${esc(wzBiz)}</b></p>
    <p style="margin-top:12px"><a class="btn small" href="/embed/${esc(wzBiz)}">Open the shop chat →</a></p>`; renderWz(6); return; }
  renderWz(j.next_step);
}
function loadSetup(){ wzBiz = ''; renderWz(1); }

/* ── settings ── */
async function loadSettings(){
  const s = await api(`/api/owner/settings?business=${biz()}`);
  $('#settingsForm').innerHTML = `
    <label>Business name</label><input id="s-name" value="${esc(s.name)}">
    <label>Tagline</label><input id="s-tag" value="${esc(s.tagline)}">
    <label>Phone</label><input id="s-phone" value="${esc(s.phone)}">
    <label>Language</label><select id="s-lang">${s.available_languages.map(l => `<option ${l===s.language?'selected':''}>${l}</option>`).join('')}</select>
    <label>AI assistant name</label><input id="s-bot" value="${esc(s.bot_name)}">
    <label><input type="checkbox" id="s-disc" ${s.require_approval_for.includes('discount')?'checked':''} style="width:auto"> Require my approval for <b>discounts</b></label>
    <label><input type="checkbox" id="s-ref" ${s.require_approval_for.includes('refund')?'checked':''} style="width:auto"> Require my approval for <b>refunds</b></label>
    <p style="margin-top:16px"><button class="btn" id="s-save">Save settings</button></p>
    <p style="margin-top:12px;color:var(--ink2);font-size:13px">Engine: ${s.demo_mode ? '<b>Demo AI</b> (set LLM_API_KEY to upgrade)' : '<b>LLM connected</b>'} · WhatsApp: ${s.whatsapp_configured ? 'connected' : 'not configured'}</p>`;
  $('#s-save').onclick = async () => {
    const req = [];
    if ($('#s-disc').checked) req.push('discount');
    if ($('#s-ref').checked) req.push('refund');
    await api(`/api/owner/settings?business=${biz()}`, {method:'PUT', body: JSON.stringify({
      name: $('#s-name').value, tagline: $('#s-tag').value, phone: $('#s-phone').value,
      language: $('#s-lang').value, bot_name: $('#s-bot').value, require_approval_for: req})});
    toast('Saved.');
  };
}

/* ── ledger ── */
async function loadLedger(){
  const d = await api(`/api/owner/ledger?business=${biz()}`);
  $('#ledgerPlain').textContent = d.plain_language;
  $('#ledgerProblems').innerHTML = d.problems.length ? `<table class="data"><tr><th>Issue</th><th>Amount</th><th>Ref</th><th>Order</th><th>Source</th></tr>` +
    d.problems.map(p => `<tr><td><span class="reason">${p.status.replace('_',' ')}</span></td><td>${p.money}</td>
      <td><small>${esc(p.ref)}</small></td><td>${esc(p.order_id)}</td><td>${p.source}</td></tr>`).join('') + '</table>'
    : '<div class="empty">No problems — every payment matched cleanly. 🎉</div>';
  $('#ledgerOrders').innerHTML = d.orders.length ? `<table class="data"><tr><th>Order</th><th>Customer</th><th>Items</th><th>Amount</th><th>Status</th></tr>` +
    d.orders.map(o => `<tr><td><b>${o.order_id}</b></td><td>${esc(o.customer)}</td><td><small>${esc(o.items)}</small></td><td>${o.money}</td>
      <td>${o.status === 'confirmed' ? '<span class="reason" style="background:var(--leaf)">confirmed ✓</span>' : '<span class="reason">waiting on payment</span>'}</td></tr>`).join('') + '</table>'
    : '<div class="empty">No orders yet.</div>';
}

function load(tab){ ({inbox: loadInbox, chats: loadChats, catalog: loadCatalog, bookings: loadBookings, ledger: loadLedger, analytics: loadAnalytics, setup: loadSetup, settings: loadSettings})[tab]().catch(e => { if (e.message !== 'auth') console.error(e); }); }
load('inbox');
