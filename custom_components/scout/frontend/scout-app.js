const app = (() => {
  let token = null;
  let data = null;          // /status payload
  let detail = null;        // /items/{id} payload when the detail view is open
  let view = 'deals';       // deals | watchlist | detail
  let refreshing = false;
  let retryTimer = null;
  let flash = null;         // {text, err} shown under the header after a check
  let flashTimer = null;

  const VERDICTS = {
    match: ['Match', 'green'],
    accessory: ['Accessory', 'red'],
    incomplete: ['Incomplete', 'orange'],
    wrong_item: ['Wrong item', 'red'],
    suspect: ['Suspect', 'red'],
    unsure: ['Check by eye', 'grey'],
  };

  // ── Helpers ────────────────────────────────────────────────

  function esc(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  function money(n) {
    if (n === null || n === undefined) return '—';
    return '$' + Number(n).toLocaleString('en-US', { maximumFractionDigits: n < 100 ? 2 : 0 });
  }

  function ago(iso) {
    if (!iso) return 'never';
    const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return `${mins} min ago`;
    const hrs = Math.round(mins / 60);
    if (hrs < 24) return `${hrs} hr ago`;
    return `${Math.round(hrs / 24)} days ago`;
  }

  function fmtDate(iso) {
    if (!iso) return '';
    const d = iso.length === 10 ? new Date(iso + 'T12:00:00') : new Date(iso);
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
  }

  function sourceLabel(key) {
    return data?.link_sources?.[key] || (key === 'other' ? 'Other' : key);
  }

  async function api(method, path, body) {
    const res = await fetch(`/api/scout${path}`, {
      method,
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    let json = null;
    try { json = await res.json(); } catch (e) { /* empty body */ }
    if (!res.ok) throw new Error(json?.error || `HTTP ${res.status}`);
    return json;
  }

  function priceTone(total, target, max) {
    if (total <= target) return 'green';
    if (total <= max) return 'orange';
    return 'grey';
  }

  function verdictPill(l) {
    if (!l.verdict) return '<span class="pill grey">Not reviewed yet</span>';
    const [label, tone] = VERDICTS[l.verdict] || ['?', 'grey'];
    return `<span class="pill ${tone}">${label}</span>`;
  }

  function shipText(l) {
    if (l.source !== 'ebay' && (l.shipping === null || l.shipping === undefined)) return '';
    return l.shipping === null ? ' + shipping' : (l.shipping === 0 ? ' shipped free' : ` incl. ${money(l.shipping)} ship`);
  }

  // ── Header / chrome ────────────────────────────────────────

  function header() {
    // eBay is the only source Scout checks itself; the other sites are
    // quick-search links on each item.
    const ebayOn = !data || data.config.ebay;
    const btn = ebayOn
      ? `<button class="btn" onclick="app.refresh()" ${refreshing ? 'disabled' : ''}>${refreshing ? 'Checking eBay…' : 'Check eBay'}</button>`
      : `<button class="btn" disabled title="Add eBay keys to turn on automatic checks. Other sites are the quick-search links on each item.">eBay off</button>`;
    return `
      <header>
        <div>
          <h1>Scout</h1>
          <div class="sub">Used-gear watchlist</div>
        </div>
        <div class="hdr-actions">${btn}</div>
      </header>
      ${flash ? `<div class="flash ${flash.err ? 'err' : ''}">${esc(flash.text)}</div>` : ''}`;
  }

  function tabs() {
    const n = data.deals.length;
    return `<div class="tabs">
      <button class="tab ${view === 'deals' ? 'on' : ''}" onclick="app.show('deals')">Deals<span class="n">${n}</span></button>
      <button class="tab ${view === 'watchlist' ? 'on' : ''}" onclick="app.show('watchlist')">Watchlist<span class="n">${data.items.length}</span></button>
    </div>`;
  }

  function banners() {
    const out = [];
    if (!data.config.ebay) {
      out.push(`<div class="banner"><strong>eBay checks are off.</strong> Add your eBay keys to the
        <code>scout:</code> block (see the README) to turn on automatic searches. Until then the
        watchlist, quick-search links and logging finds by hand all work. Scout never checks the
        other sites itself; their links open a saved search for you.</div>`);
    }
    if (data.config.ebay && !data.config.model) {
      out.push(`<div class="banner"><strong>Listing review is off.</strong> Add <code>ollama_host</code>
        to have forge check each listing. Without it, every listing under target alerts with only the
        automatic flags.</div>`);
    }
    return out.join('');
  }

  function footer() {
    if (data.last_error) return `<div class="status err">Last check had errors: ${esc(data.last_error)}</div>`;
    const alerts = data.config.notify ? `alerts → ${esc(data.config.notify)}` : 'alerts → HA notifications';
    return `<div class="status">Checked ${ago(data.last_refresh)} · ${alerts}</div>`;
  }

  // ── Deals ──────────────────────────────────────────────────

  function dealCard(d) {
    const tone = priceTone(d.total, d.target_price, d.max_price);
    const under = d.total <= d.target_price ? 'at or under target' : `${money(d.total - d.target_price)} over target`;
    const meta = [
      esc(d.item_name),
      sourceLabel(d.source),
      d.condition ? esc(d.condition) : '',
      d.seller ? `${esc(d.seller)}${d.seller_pct != null ? ` · ${d.seller_pct}%` : ''}` : '',
      `seen ${ago(d.first_seen)}`,
    ].filter(Boolean).join(' · ');
    return `<div class="card deal">
      ${d.image_url ? `<img class="thumb" src="${esc(d.image_url)}" alt="" loading="lazy" onerror="this.remove()">` : ''}
      <div class="deal-body">
        <div class="deal-top">
          <div class="deal-title">${d.url ? `<a href="${esc(d.url)}" target="_blank" rel="noopener">${esc(d.title)}</a>` : esc(d.title)}</div>
          <div class="deal-price">${money(d.total)}</div>
        </div>
        <div class="deal-meta">${meta}</div>
        <div style="margin-top:6px">${verdictPill(d)} <span class="pill ${tone}">${under}</span>
          <span class="deal-meta">${esc(shipText(d))}</span></div>
        ${d.verdict_notes ? `<div class="deal-notes">${esc(d.verdict_notes)}</div>` : ''}
        ${d.flags.length ? `<div class="flags">${d.flags.map(f => `<span class="flag">${esc(f)}</span>`).join('')}</div>` : ''}
        <div class="deal-actions">
          <button class="btn sm" onclick="app.setStatus(${d.id}, 'dismissed')">Dismiss</button>
          <button class="btn sm" onclick="app.setStatus(${d.id}, 'wrong')" title="Also drops it from price history">Not this item</button>
          <button class="btn sm" onclick="app.openItem(${d.item_id})">Item history</button>
        </div>
      </div>
    </div>`;
  }

  function dealsView() {
    if (!data.deals.length) {
      const why = data.config.ebay
        ? 'Nothing in range right now. Scout checks eBay every 3 hours.'
        : 'No deals yet. Once eBay keys are added, listings in each item\'s price range show up here. Finds you log by hand from the quick links show up too.';
      return `<div class="empty">${why}</div>`;
    }
    return data.deals.map(dealCard).join('');
  }

  // ── Watchlist ──────────────────────────────────────────────

  function itemCard(it) {
    const s = it.stats;
    const best = it.best
      ? `<div class="best">Best now: <a href="${esc(it.best.url || '#')}" target="_blank" rel="noopener">${money(it.best.total)}</a>
          <span class="pill ${priceTone(it.best.total, it.target_price, it.max_price)}">${sourceLabel(it.best.source)}</span></div>`
      : '';
    return `<div class="card">
      <div class="item-head">
        <div>
          <div class="item-name ${it.active ? '' : 'paused'}">${esc(it.name)}${it.active ? '' : ' <span class="pill grey">Paused</span>'}</div>
          <div class="item-prices">Target ${money(it.target_price)} · max ${money(it.max_price)}${it.ebay_enabled ? '' : ' · eBay off'}</div>
        </div>
      </div>
      <div class="stats">
        <div class="stat"><div class="k">In range now</div><div class="v">${s.live}</div></div>
        <div class="stat"><div class="k">Typical</div><div class="v">${s.median ? money(s.median) : '—'}</div></div>
        <div class="stat"><div class="k">Lowest seen</div><div class="v">${money(s.low)}</div></div>
        <div class="stat"><div class="k">Prices logged</div><div class="v">${s.points}</div></div>
      </div>
      ${best}
      <div class="links">${it.links.map(l => `<a class="link" href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.label)} ↗</a>`).join('')}</div>
      <div class="item-actions">
        <button class="btn sm" onclick="app.openItem(${it.id})">History</button>
        <button class="btn sm" onclick="app.logFind(${it.id})">Log a find</button>
        <button class="btn sm" onclick="app.editItem(${it.id})">Edit</button>
        <button class="btn sm" onclick="app.togglePause(${it.id}, ${!it.active})">${it.active ? 'Pause' : 'Resume'}</button>
      </div>
    </div>`;
  }

  function watchlistView() {
    const groups = {};
    for (const it of data.items) (groups[it.grp || 'Other'] ||= []).push(it);
    const body = Object.keys(groups).sort().map(g =>
      `<div class="section-label">${esc(g)}</div>` + groups[g].map(itemCard).join('')).join('');
    return `<div style="display:flex;justify-content:flex-end;margin-bottom:10px">
        <button class="btn primary" onclick="app.editItem(null)">+ Add item</button>
      </div>` + (body || '<div class="empty">Nothing on the watchlist yet.</div>');
  }

  // ── Item detail ────────────────────────────────────────────

  function chart(it) {
    const pts = it.listings.filter(l => l.verdict !== 'accessory' && l.verdict !== 'incomplete'
      && l.verdict !== 'wrong_item' && l.user_status !== 'wrong');
    if (pts.length < 2) {
      return '<div class="empty" style="padding:24px">The price chart appears after two or more prices are logged.</div>';
    }
    const W = 760, H = 240, L = 46, R = 12, T = 12, B = 26;
    const xs = pts.map(p => new Date(p.first_seen).getTime());
    const ys = pts.map(p => p.total).concat([it.target_price, it.max_price]);
    let x0 = Math.min(...xs), x1 = Math.max(...xs);
    if (x1 - x0 < 86400000) { x0 -= 43200000; x1 += 43200000; }
    const y0 = Math.floor(Math.min(...ys) * 0.9 / 10) * 10;
    const y1 = Math.ceil(Math.max(...ys) * 1.05 / 10) * 10;
    const X = t => L + (t - x0) / (x1 - x0) * (W - L - R);
    const Y = v => T + (1 - (v - y0) / (y1 - y0)) * (H - T - B);

    const ticks = [];
    for (let i = 0; i <= 4; i++) {
      const v = y0 + (y1 - y0) * i / 4;
      ticks.push(`<line x1="${L}" x2="${W - R}" y1="${Y(v)}" y2="${Y(v)}" stroke="var(--border)" stroke-width="0.5"/>
        <text x="${L - 6}" y="${Y(v) + 4}" text-anchor="end">${money(Math.round(v))}</text>`);
    }
    const xl = [x0, (x0 + x1) / 2, x1].map((t, i) =>
      `<text x="${X(t)}" y="${H - 6}" text-anchor="${['start', 'middle', 'end'][i]}">${fmtDate(new Date(t).toISOString())}</text>`).join('');
    const ref = (v, color, label) => `
      <line x1="${L}" x2="${W - R}" y1="${Y(v)}" y2="${Y(v)}" stroke="${color}" stroke-width="1.2" stroke-dasharray="5 4"/>
      <text x="${W - R}" y="${Y(v) - 4}" text-anchor="end" style="fill:${color}">${label} ${money(v)}</text>`;
    const dots = pts.map(p => {
      const color = p.source === 'ebay' ? 'var(--accent)' : 'var(--purple)';
      const fill = p.state === 'active' ? color : 'none';
      return `<circle cx="${X(new Date(p.first_seen).getTime())}" cy="${Y(p.total)}" r="4.5" fill="${fill}" stroke="${color}" stroke-width="1.5">
        <title>${esc(p.title)} — ${money(p.total)} (${sourceLabel(p.source)}, ${fmtDate(p.first_seen)})</title></circle>`;
    }).join('');
    return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Asking prices over time">
        ${ticks.join('')}${xl}
        ${ref(it.max_price, 'var(--orange)', 'Max')}${ref(it.target_price, 'var(--green)', 'Target')}
        ${dots}
      </svg>
      <div class="legend">
        <span><i style="background:var(--accent)"></i>eBay</span>
        <span><i style="background:var(--purple)"></i>Logged by hand</span>
        <span><i style="border:1.5px solid var(--muted)"></i>Hollow = gone or sold</span>
      </div>`;
  }

  function listingRow(l) {
    const dim = l.state !== 'active' || l.user_status;
    const state = l.state === 'sold' ? '<span class="pill purple">Sold</span>'
      : l.state === 'gone' ? '<span class="pill grey">Gone</span>' : '';
    const user = l.user_status === 'wrong' ? '<span class="pill red">Not this item</span>'
      : l.user_status === 'dismissed' ? '<span class="pill grey">Dismissed</span>' : '';
    const actions = l.user_status
      ? `<button class="btn sm" onclick="app.setStatus(${l.id}, null)">Restore</button>`
      : `<button class="btn sm" onclick="app.setStatus(${l.id}, 'wrong')">Not this item</button>`;
    const del = l.source !== 'ebay'
      ? ` <button class="btn sm danger" onclick="app.deleteFind(${l.id})">Delete</button>` : '';
    return `<tr class="${dim ? 'dim' : ''}">
      <td class="num">${money(l.total)}</td>
      <td>
        <div class="t-title">${l.url ? `<a href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.title)}</a>` : esc(l.title)}</div>
        <div class="t-sub">${sourceLabel(l.source)} · ${fmtDate(l.first_seen)}${l.condition ? ' · ' + esc(l.condition) : ''}${l.note ? ' · ' + esc(l.note) : ''}</div>
        ${l.verdict_notes && l.source === 'ebay' ? `<div class="t-sub">${esc(l.verdict_notes)}</div>` : ''}
      </td>
      <td class="hide-sm">${l.source === 'ebay' ? verdictPill(l) : ''} ${state} ${user}</td>
      <td style="white-space:nowrap">${actions}${del}</td>
    </tr>`;
  }

  function detailView() {
    const it = detail;
    const s = it.stats;
    return `<button class="btn sm back" onclick="app.show('watchlist')">← Watchlist</button>
      <div class="card">
        <div class="item-head">
          <div>
            <div class="item-name">${esc(it.name)}</div>
            <div class="item-prices">Target ${money(it.target_price)} · max ${money(it.max_price)}
              ${s.median ? ` · typical ${money(s.median)}` : ''} · searching “${esc(it.query)}”</div>
          </div>
          <button class="btn sm" onclick="app.editItem(${it.id})">Edit</button>
        </div>
        ${it.notes ? `<div class="notes-box">${esc(it.notes)}</div>` : ''}
        <div class="links" style="margin-top:12px">${it.links.map(l => `<a class="link" href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.label)} ↗</a>`).join('')}</div>
      </div>
      <div class="card">${chart(it)}</div>
      <div class="section-label" style="display:flex;justify-content:space-between;align-items:center">
        <span>Every listing seen (${it.listings.length})</span>
        <button class="btn sm" onclick="app.logFind(${it.id})">Log a find</button>
      </div>
      <div class="card" style="padding:4px 8px">
        ${it.listings.length
          ? `<table><thead><tr><th>Total</th><th>Listing</th><th class="hide-sm">Status</th><th></th></tr></thead>
             <tbody>${it.listings.map(listingRow).join('')}</tbody></table>`
          : '<div class="empty" style="padding:24px">No listings yet.</div>'}
      </div>`;
  }

  // ── Render ─────────────────────────────────────────────────

  function render() {
    const main = document.getElementById('main');
    if (!data) {
      main.innerHTML = header() + '<div class="empty">Loading…</div>';
      return;
    }
    let body;
    if (view === 'detail' && detail) body = detailView();
    else body = tabs() + (view === 'watchlist' ? watchlistView() : dealsView());
    main.innerHTML = header() + banners() + body + footer();
  }

  // ── Modals ─────────────────────────────────────────────────

  function closeModal() {
    document.getElementById('modal').innerHTML = '';
  }

  function modal(html) {
    document.getElementById('modal').innerHTML =
      `<div class="modal-bg" onclick="if (event.target === this) app.closeModal()"><div class="modal">${html}</div></div>`;
  }

  function formError(msg) {
    const el = document.getElementById('form-err');
    if (el) el.textContent = msg;
  }

  function val(id) {
    return document.getElementById(id).value.trim();
  }

  function editItem(id) {
    const it = id ? data.items.find(i => i.id === id) : {
      name: '', grp: '', query: '', exclude_terms: 'for parts, parts only, broken, not working, as-is',
      min_price: null, target_price: '', max_price: '', condition: 'any', notes: '',
      link_sources: 'ebay', ebay_enabled: true, active: true,
    };
    const chosen = new Set(it.link_sources.split(',').filter(Boolean));
    const linkChecks = Object.entries(data.link_sources).map(([k, label]) =>
      `<label><input type="checkbox" name="links" value="${k}" ${chosen.has(k) ? 'checked' : ''}> ${esc(label)}</label>`).join('');
    const groups = [...new Set(data.items.map(i => i.grp).filter(Boolean))];
    modal(`
      <h2>${id ? 'Edit item' : 'Add item'}</h2>
      <div class="row2">
        <div class="field"><label>Name</label><input id="f-name" value="${esc(it.name)}"></div>
        <div class="field"><label>Group</label><input id="f-grp" value="${esc(it.grp)}" list="f-groups" placeholder="Astro, Bike…">
          <datalist id="f-groups">${groups.map(g => `<option value="${esc(g)}">`).join('')}</datalist></div>
      </div>
      <div class="field"><label>Search terms</label><input id="f-query" value="${esc(it.query)}">
        <div class="hint">Used for the eBay search and the quick-search links.</div></div>
      <div class="field"><label>Skip titles containing</label><input id="f-excl" value="${esc(it.exclude_terms)}">
        <div class="hint">Comma-separated. Case doesn't matter.</div></div>
      <div class="row3">
        <div class="field"><label>Min price</label><input id="f-min" inputmode="decimal" value="${it.min_price ?? ''}">
          <div class="hint">Filters out caps and parts</div></div>
        <div class="field"><label>Target</label><input id="f-target" inputmode="decimal" value="${it.target_price}">
          <div class="hint">Alerts at or under</div></div>
        <div class="field"><label>Max</label><input id="f-max" inputmode="decimal" value="${it.max_price}">
          <div class="hint">Shown as a deal up to</div></div>
      </div>
      <div class="row2">
        <div class="field"><label>Condition</label><select id="f-cond">
          ${['any', 'used', 'new'].map(c => `<option value="${c}" ${it.condition === c ? 'selected' : ''}>${c[0].toUpperCase() + c.slice(1)}</option>`).join('')}
        </select></div>
        <div class="field"><label>Automatic checks</label><div class="checks" style="padding-top:8px">
          <label><input type="checkbox" id="f-ebay" ${it.ebay_enabled ? 'checked' : ''}> Search eBay</label></div></div>
      </div>
      <div class="field"><label>What counts as the right item</label><textarea id="f-notes">${esc(it.notes)}</textarea>
        <div class="hint">Forge reads this to judge each listing: exact model, mount or size, what must be included, what's an accessory.</div></div>
      <div class="field"><label>Quick-search links</label><div class="checks">${linkChecks}</div></div>
      <div id="form-err" class="form-err"></div>
      <div class="modal-actions">
        ${id ? `<button class="btn danger" onclick="app.deleteItem(${id})">Delete</button>` : ''}
        <div class="right">
          <button class="btn" onclick="app.closeModal()">Cancel</button>
          <button class="btn primary" onclick="app.saveItem(${id || 'null'})">Save</button>
        </div>
      </div>`);
  }

  async function saveItem(id) {
    const body = {
      name: val('f-name'), grp: val('f-grp'), query: val('f-query'), exclude_terms: val('f-excl'),
      min_price: val('f-min'), target_price: val('f-target'), max_price: val('f-max'),
      condition: val('f-cond'), notes: val('f-notes'),
      ebay_enabled: document.getElementById('f-ebay').checked,
      link_sources: [...document.querySelectorAll('input[name=links]:checked')].map(c => c.value),
    };
    try {
      const res = id ? await api('PUT', `/items/${id}`, body) : await api('POST', '/items', body);
      closeModal();
      if (view === 'detail') detail = res;
      await load();
    } catch (e) {
      formError(e.message);
    }
  }

  async function deleteItem(id) {
    const it = data.items.find(i => i.id === id);
    if (!confirm(`Delete “${it.name}” and every listing logged for it?`)) return;
    try {
      await api('DELETE', `/items/${id}`);
      closeModal();
      if (view === 'detail') { view = 'watchlist'; detail = null; }
      await load();
    } catch (e) {
      formError(e.message);
    }
  }

  function logFind(itemId) {
    const it = data.items.find(i => i.id === itemId);
    const today = new Date().toLocaleDateString('en-CA');
    const sources = data.manual_sources.map(k => `<option value="${k}">${esc(sourceLabel(k))}</option>`).join('');
    modal(`
      <h2>Log a find · ${esc(it.name)}</h2>
      <div class="row2">
        <div class="field"><label>Where</label><select id="m-source">${sources}</select></div>
        <div class="field"><label>Date seen</label><input id="m-date" type="date" value="${today}"></div>
      </div>
      <div class="field"><label>Title</label><input id="m-title" placeholder="As listed"></div>
      <div class="field"><label>Link</label><input id="m-url" placeholder="https://…"></div>
      <div class="row3">
        <div class="field"><label>Price</label><input id="m-price" inputmode="decimal"></div>
        <div class="field"><label>Shipping</label><input id="m-ship" inputmode="decimal" placeholder="blank = local"></div>
        <div class="field"><label>Status</label><select id="m-state">
          <option value="active">Still listed</option><option value="sold">Sold (sold price)</option><option value="gone">Gone</option>
        </select></div>
      </div>
      <div class="row2">
        <div class="field"><label>Condition</label><input id="m-cond" placeholder="e.g. Excellent"></div>
        <div class="field"><label>Note</label><input id="m-note" placeholder="Optional"></div>
      </div>
      <div id="form-err" class="form-err"></div>
      <div class="modal-actions"><div class="right">
        <button class="btn" onclick="app.closeModal()">Cancel</button>
        <button class="btn primary" onclick="app.saveFind(${itemId})">Save</button>
      </div></div>`);
  }

  async function saveFind(itemId) {
    const body = {
      source: val('m-source'), seen_at: val('m-date'), title: val('m-title'), url: val('m-url'),
      price: val('m-price'), shipping: val('m-ship'), state: val('m-state'),
      condition: val('m-cond'), note: val('m-note'),
    };
    try {
      const res = await api('POST', `/items/${itemId}/listings`, body);
      closeModal();
      if (view === 'detail') detail = res;
      await load();
    } catch (e) {
      formError(e.message);
    }
  }

  // ── Actions ────────────────────────────────────────────────

  async function load() {
    try {
      data = await api('GET', '/status');
      if (view === 'detail' && detail) detail = await api('GET', `/items/${detail.id}`);
    } catch (e) {
      data = data || { items: [], deals: [], config: { ebay: false, model: false, notify: '' },
        link_sources: {}, manual_sources: [], last_refresh: null, last_error: e.message };
    }
    render();
    clearTimeout(retryTimer);
    if (!data.last_refresh) retryTimer = setTimeout(load, 40000);
  }

  async function refresh() {
    if (refreshing) return;
    refreshing = true;
    render();
    try {
      data = await api('POST', '/refresh');
      if (view === 'detail' && detail) detail = await api('GET', `/items/${detail.id}`);
      flash = data.last_error
        ? { text: 'Check finished with errors. See the bottom of the page.', err: true }
        : { text: `Checked eBay · ${data.last_summary.replace(/^Searched /, '')}` };
    } catch (e) {
      if (data) data.last_error = e.message;
      flash = { text: `Check failed: ${e.message}`, err: true };
    }
    refreshing = false;
    render();
    clearTimeout(flashTimer);
    flashTimer = setTimeout(() => { flash = null; render(); }, 8000);
  }

  function show(v) {
    view = v;
    if (v !== 'detail') detail = null;
    render();
    window.scrollTo(0, 0);
  }

  async function openItem(id) {
    try {
      detail = await api('GET', `/items/${id}`);
      show('detail');
    } catch (e) {
      alert(e.message);
    }
  }

  async function setStatus(id, status) {
    try {
      await api('POST', `/listings/${id}/status`, { user_status: status });
      await load();
    } catch (e) {
      alert(e.message);
    }
  }

  async function togglePause(id, active) {
    try {
      await api('PUT', `/items/${id}`, { active });
      await load();
    } catch (e) {
      alert(e.message);
    }
  }

  async function deleteFind(id) {
    if (!confirm('Delete this logged find?')) return;
    try {
      await api('DELETE', `/listings/${id}`);
      await load();
    } catch (e) {
      alert(e.message);
    }
  }

  // ── Boot ───────────────────────────────────────────────────

  let initialized = false;
  window.addEventListener('message', e => {
    if (e.data?.type === 'auth' && e.data.token) {
      token = e.data.token;
      if (!initialized) {
        initialized = true;
        load();
      }
    }
  });

  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });

  return {
    refresh, show, openItem, setStatus, togglePause, editItem, saveItem, deleteItem,
    logFind, saveFind, deleteFind, closeModal,
  };
})();
