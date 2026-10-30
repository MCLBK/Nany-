/* ============================================================
   NANYÉ — Espace de gestion — admin.js
   Toutes les permissions sont vérifiées par l'API : cette interface ne fait
   qu'afficher ce que le serveur autorise (rôles « staff » et « admin »).
   ============================================================ */

const API = ((window.NANYE_CONFIG || {}).apiBaseUrl || 'http://localhost:8000/api').replace(/\/$/, '');

const ORDER_STATUS = {
  pending_payment: ['En attente de paiement', 'st-wait'], paid: ['Payée — à préparer', 'st-todo'],
  preparing: ['En préparation', 'st-todo'], shipped: ['Expédiée', 'st-move'], delivered: ['Livrée', 'st-done'],
  cancelled: ['Annulée', 'st-off'], refunded: ['Remboursée', 'st-off']
};
const PAYMENT_STATUS = {
  none: 'Aucun paiement', initiated: 'Paiement en cours', pending: 'Paiement en cours',
  succeeded: 'Payé', failed: 'Paiement refusé', cancelled: 'Paiement abandonné'
};
// Miroir d'affichage des règles serveur (le serveur reste seul juge)
const TRANSITIONS = {
  pending_payment: ['cancelled'], paid: ['preparing', 'cancelled'], preparing: ['shipped', 'cancelled'],
  shipped: ['delivered'], delivered: [], cancelled: ['refunded'], refunded: []
};
const STAFF_FORBIDDEN = ['paid>cancelled', 'preparing>cancelled', 'cancelled>refunded'];
const ACTION_LABELS = {
  preparing: 'Passer en préparation', shipped: 'Marquer comme expédiée', delivered: 'Marquer comme livrée',
  cancelled: 'Annuler la commande', refunded: 'Marquer comme remboursée'
};
const CATEGORY = { naturelle: 'Naturelle', epice: 'Épicé', herbes: 'Gingembre' };
const PRODUCT_STATUS = { available: ['En vente', 'st-done'], coming_soon: ['Bientôt', 'st-wait'], archived: ['Retiré', 'st-off'] };
const ROLE_LABEL = { admin: 'Administrateur', staff: 'Équipe' };
const VIEWS = {
  overview: "Vue d'ensemble", orders: 'Commandes', products: 'Produits', stock: 'Stocks',
  customers: 'Clients', settings: 'Réglages'
};
const PAGE_SIZE = 20;

const state = { user: null, csrf: null, ordersQuery: { q: '', status: '', offset: 0 }, customersQuery: { q: '', offset: 0 } };

/* --- Utilitaires ------------------------------------------------------- */
const $ = id => document.getElementById(id);
const fmt = n => Number(n || 0).toLocaleString('fr-FR');
const money = n => `${fmt(n)} <span class="cur">FCFA</span>`;
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const parseDate = iso => new Date(/[zZ]|[+-]\d\d:\d\d$/.test(iso) ? iso : iso + 'Z');
const fmtDate = iso => iso ? parseDate(iso).toLocaleDateString('fr-FR', { day: 'numeric', month: 'short', year: 'numeric' }) : '—';
const fmtDateTime = iso => iso ? parseDate(iso).toLocaleString('fr-FR', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : '—';
const isAdmin = () => state.user && state.user.role === 'admin';
const badge = (map, key) => { const [label, cls] = map[key] || [key, '']; return `<span class="st ${cls}">${esc(label)}</span>`; };
const safeImg = url => /^(images\/[\w.-]+|https:\/\/[\w.-]+\/[\w.~/%-]+)$/.test(url || '') ? (url.startsWith('images/') ? '../' + url : url) : '';
function paymentBadge(o) {
  const cls = o.payment_status === 'succeeded' ? 'pay-ok' : ['failed', 'cancelled'].includes(o.payment_status) ? 'pay-ko' : 'pay-wait';
  return `<span class="pay ${cls}">${esc(PAYMENT_STATUS[o.payment_status] || o.payment_status)}${o.payment_simulated ? ' · simulé' : ''}</span>`;
}
function emptyState(title, text = '', action = '') {
  return `<div class="adm-empty"><p class="adm-empty-title">${title}</p>${text ? `<p class="adm-empty-text">${text}</p>` : ''}${action}</div>`;
}
const loading = () => '<p class="adm-loading">Chargement…</p>';

/* --- API ------------------------------------------------------------------ */
class ApiError extends Error { constructor(status, message) { super(message); this.status = status; } }

async function api(path, { method = 'GET', body, raw = false } = {}) {
  const opts = { method, credentials: 'include', headers: { Accept: 'application/json' } };
  if (body !== undefined) { opts.body = JSON.stringify(body); opts.headers['Content-Type'] = 'application/json'; }
  if (method !== 'GET' && state.csrf) opts.headers['X-CSRF-Token'] = state.csrf;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 15000);
  opts.signal = ctrl.signal;
  let res;
  try { res = await fetch(API + path, opts); }
  catch { throw new ApiError(0, 'Serveur injoignable. Vérifiez votre connexion puis réessayez.'); }
  finally { clearTimeout(timer); }
  if (raw && res.ok) return res;
  let data = null;
  if (res.status !== 204) { try { data = await res.json(); } catch { data = null; } }
  if (res.status === 401 && path !== '/auth/login' && path !== '/auth/me') {
    // Session expirée ou révoquée : retour à l'écran de connexion
    state.user = null; state.csrf = null;
    showGate('login', 'Votre session a expiré. Reconnectez-vous.');
    throw new ApiError(401, 'Session expirée.');
  }
  if (!res.ok) {
    let msg = data && typeof data.detail === 'string' ? data.detail : 'Une erreur est survenue. Réessayez.';
    if (res.status === 403) msg = data && data.detail && data.detail !== 'Accès refusé.' ? data.detail : 'Action non autorisée pour votre compte.';
    if (res.status === 422 && data && Array.isArray(data.errors) && data.errors.length) {
      msg = 'Vérifiez les champs : ' + data.errors.map(e => e.field.split('.').pop()).filter(Boolean).join(', ') + '.';
    }
    if (res.status === 429) msg = 'Trop de tentatives. Patientez quelques minutes.';
    throw new ApiError(res.status, msg);
  }
  return data;
}

/* --- Notifications et confirmations ----------------------------------------- */
let notifTimer = null;
function toast(title, sub = '', isError = false) {
  const n = $('notif');
  n.classList.toggle('is-error', isError);
  n.querySelector('.notif-icon i').className = 'ti ' + (isError ? 'ti-alert-triangle' : 'ti-check');
  $('notif-txt').textContent = title;
  $('notif-sub').textContent = sub;
  n.classList.add('show');
  clearTimeout(notifTimer);
  notifTimer = setTimeout(() => n.classList.remove('show'), isError ? 6000 : 3500);
}

function confirmAction({ title, text, ok = 'Confirmer', danger = false, note = false }) {
  const d = $('confirmDialog');
  $('cfTitle').textContent = title;
  $('cfText').textContent = text;
  $('cfOk').textContent = ok;
  $('cfOk').className = danger ? 'btn-danger' : 'btn-primary';
  $('cfNoteField').hidden = !note;
  $('cfNote').value = '';
  d.returnValue = '';
  d.showModal();
  return new Promise(resolve => {
    d.addEventListener('close', () => resolve(d.returnValue === 'ok' ? { note: $('cfNote').value.trim() } : null), { once: true });
  });
}

/* --- Session -------------------------------------------------------------- */
function showGate(mode, message = '') {
  $('admApp').hidden = true;
  $('admGate').hidden = false;
  $('gateLogin').hidden = mode !== 'login';
  $('gateDenied').hidden = mode !== 'denied';
  $('gateOffline').hidden = !message;
  $('gateOffline').textContent = message;
  closeDrawer();
  if (mode === 'login') setTimeout(() => $('gEmail').focus(), 50);
}

function enterApp() {
  $('admGate').hidden = true;
  $('admApp').hidden = false;
  $('meName').textContent = state.user.full_name;
  $('meRole').textContent = ROLE_LABEL[state.user.role] || '';
  document.querySelectorAll('[data-role="admin"]').forEach(el => { el.hidden = !isAdmin(); });
  route();
}

async function boot() {
  try {
    const s = await api('/auth/me');
    state.user = s.user; state.csrf = s.csrf_token;
  } catch (e) {
    showGate('login', e.status === 0 ? e.message : '');
    return;
  }
  if (!['staff', 'admin'].includes(state.user.role)) { showGate('denied'); return; }
  enterApp();
}

async function logout() {
  try { await api('/auth/logout', { method: 'POST' }); } catch { /* déjà déconnecté */ }
  state.user = null; state.csrf = null;
  location.hash = '';
  showGate('login');
}

/* --- Navigation ------------------------------------------------------------- */
function parseHash() {
  const [view, ...rest] = location.hash.slice(1).split('/');
  return { view: VIEWS[view] ? view : 'overview', param: decodeURIComponent(rest.join('/')) };
}

function route() {
  if (!state.user) return;
  let { view, param } = parseHash();
  if (view === 'customers' && !isAdmin()) view = 'overview';
  document.querySelectorAll('.adm-nav a').forEach(a => {
    const on = a.dataset.view === view;
    a.classList.toggle('active', on);
    if (on) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  });
  $('topTitle').textContent = VIEWS[view];
  document.title = VIEWS[view] + ' — Gestion NANYÉ';
  toggleSide(false);
  const main = $('admView');
  if (main.dataset.view !== view) { main.dataset.view = view; main.innerHTML = loading(); window.scrollTo(0, 0); }
  RENDER[view](param);
}
window.addEventListener('hashchange', route);

function toggleSide(open) {
  $('admSide').classList.toggle('open', open);
  $('admScrim').hidden = !open;
}

const RENDER = {
  overview: renderOverview, orders: renderOrders, products: renderProducts,
  stock: renderStock, customers: renderCustomers, settings: renderSettings
};

function viewError(e) {
  if (e.status === 401) return;
  $('admView').innerHTML = emptyState('Impossible de charger cette page.', esc(e.message),
    '<button class="btn-ghost btn-sm" onclick="route()">Réessayer</button>');
}

function pageHead(title, sub = '', actions = '') {
  return `<header class="adm-head"><div><h1 class="adm-h1">${title}</h1>${sub ? `<p class="adm-sub">${sub}</p>` : ''}</div>${actions ? `<div class="adm-head-actions">${actions}</div>` : ''}</header>`;
}

function updateNavCounts(o) {
  const tp = $('navToProcess');
  tp.hidden = !o.orders.to_process;
  tp.textContent = o.orders.to_process;
  const alerts = o.products.out_of_stock.length + o.products.low_stock.length;
  $('navStock').hidden = !alerts;
  $('navStock').textContent = alerts;
  $('demoFlag').hidden = !o.payment_simulated;
}

/* --- Vue d'ensemble ---------------------------------------------------------------- */
async function renderOverview() {
  let o;
  try { o = await api('/admin/overview'); } catch (e) { return viewError(e); }
  updateNavCounts(o);
  const today = new Date().toLocaleDateString('fr-FR', { weekday: 'long', day: 'numeric', month: 'long' });

  const alerts = [];
  if (o.payment_simulated) alerts.push(['info', 'Paiements simulés actifs : aucune somme n\'est encaissée. Les ventes simulées sont comptées à part.', '']);
  if (o.orders.to_process) alerts.push(['todo', `${o.orders.to_process} commande${o.orders.to_process > 1 ? 's' : ''} payée${o.orders.to_process > 1 ? 's' : ''} à préparer`, '#orders/to_process']);
  o.products.out_of_stock.forEach(p => alerts.push(['warn', `${esc(p.name)} est en rupture de stock`, '#stock']));
  o.products.low_stock.forEach(p => alerts.push(['warn', `${esc(p.name)} : plus que ${p.stock} en stock (seuil ${p.threshold})`, '#stock']));

  const rev = o.revenue;
  const delta = d => d === null || d === undefined ? '<span class="kpi-note">Pas de comparaison possible</span>'
    : `<span class="kpi-trend ${d >= 0 ? 'up' : 'down'}">${d > 0 ? '+' : ''}${String(d).replace('.', ',')} % vs mois précédent</span>`;
  const lead = rev ? `
    <div class="kpi kpi-lead">
      <p class="kpi-label">Chiffre d'affaires encaissé · ${new Date().toLocaleDateString('fr-FR', { month: 'long' })}</p>
      <p class="kpi-value">${money(rev.this_month_xof)}</p>
      ${delta(rev.delta_pct)}
      ${rev.simulated_this_month_xof ? `<p class="kpi-note">+ ${fmt(rev.simulated_this_month_xof)} FCFA de ventes simulées, non encaissées</p>` : ''}
      ${rev.average_basket_xof ? `<p class="kpi-note">Panier moyen : ${fmt(rev.average_basket_xof)} FCFA</p>` : ''}
    </div>` : `
    <div class="kpi kpi-lead">
      <p class="kpi-label">À préparer maintenant</p>
      <p class="kpi-value">${fmt(o.orders.to_process)}</p>
      <a class="kpi-link" href="#orders/to_process">Voir les commandes</a>
    </div>`;
  const kpis = [
    ['Commandes payées ce mois', fmt(o.orders.paid_this_month), delta(o.orders.paid_delta_pct)],
    rev ? ['À préparer', fmt(o.orders.to_process), o.orders.in_transit ? `<span class="kpi-note">${o.orders.in_transit} en cours de livraison</span>` : '<span class="kpi-note">Payées, pas encore expédiées</span>']
        : ['En cours de livraison', fmt(o.orders.in_transit), '<span class="kpi-note">Expédiées, non livrées</span>'],
    ['En attente de paiement', fmt(o.orders.pending_payment), '<span class="kpi-note">Stock réservé, libéré automatiquement</span>'],
    o.customers ? ['Clients inscrits', fmt(o.customers.total), `<span class="kpi-note">${o.customers.new_this_month} nouveau${o.customers.new_this_month > 1 ? 'x' : ''} ce mois</span>`]
                : ['Produits en vente', fmt(o.products.for_sale), `<span class="kpi-note">${o.products.coming_soon} à venir</span>`]
  ];

  const shortcuts = [
    ['#orders/to_process', 'ti-truck-delivery', 'Préparer les commandes'],
    ['#stock', 'ti-stack-2', 'Mettre à jour les stocks'],
    ...(isAdmin() ? [['#products/new', 'ti-plus', 'Ajouter un produit'], ['#export', 'ti-download', 'Exporter les commandes']] : [])
  ];

  $('admView').innerHTML = `
    ${pageHead(`Bonjour, ${esc(state.user.full_name.split(' ')[0])}`, today.charAt(0).toUpperCase() + today.slice(1))}
    ${alerts.length ? `<ul class="adm-alerts">${alerts.map(([t, txt, href]) => `<li class="adm-alert al-${t}">${href ? `<a href="${href}">${txt}<i class="ti ti-arrow-right" aria-hidden="true"></i></a>` : `<span>${txt}</span>`}</li>`).join('')}</ul>` : ''}
    <section class="kpi-grid" aria-label="Indicateurs">
      ${lead}
      ${kpis.map(([l, v, n]) => `<div class="kpi"><p class="kpi-label">${l}</p><p class="kpi-value">${v}</p>${n}</div>`).join('')}
    </section>
    <div class="adm-cols">
      ${rev ? `<section class="panel"><div class="panel-head"><h2 class="panel-title">Ventes des six derniers mois</h2>
        <span class="legend"><i class="lg-real"></i>Encaissé${rev.by_month.some(m => m.simulated_xof) ? '<i class="lg-sim"></i>Simulé' : ''}</span></div>
        ${salesChart(rev.by_month)}</section>` : ''}
      <section class="panel"><div class="panel-head"><h2 class="panel-title">Stock des produits en vente</h2><a class="panel-link" href="#stock">Gérer</a></div>
        <div id="ovStock">${loading()}</div></section>
    </div>
    <section class="panel"><div class="panel-head"><h2 class="panel-title">Commandes récentes</h2><a class="panel-link" href="#orders">Toutes les commandes</a></div>
      ${o.recent_orders.length ? ordersTable(o.recent_orders, true) : emptyState('Aucune commande pour le moment.', 'Les commandes apparaîtront ici dès le premier achat.')}
    </section>
    <nav class="adm-shortcuts" aria-label="Raccourcis">${shortcuts.map(([h, i, l]) => `<a href="${h}"><i class="ti ${i}" aria-hidden="true"></i>${l}</a>`).join('')}</nav>`;

  try {
    const products = (await api('/admin/products')).filter(p => p.status === 'available');
    $('ovStock').innerHTML = products.length ? `<ul class="stock-list">${products.map(stockLine).join('')}</ul>`
      : emptyState('Aucun produit en vente.', 'Passez un produit « En vente » depuis l\'onglet Produits.');
  } catch (e) { if ($('ovStock')) $('ovStock').innerHTML = `<p class="form-error">${esc(e.message)}</p>`; }
}

function stockLine(p) {
  const max = Math.max(p.stock, p.low_stock_threshold * 4, 1);
  const level = p.stock <= 0 ? 'out' : p.stock <= p.low_stock_threshold ? 'low' : 'ok';
  return `<li class="stock-line lv-${level}">
    <span class="stock-name">${esc(p.name)}</span>
    <span class="stock-bar" aria-hidden="true"><span style="width:${Math.min(100, p.stock / max * 100)}%"></span><span class="stock-th" style="left:${Math.min(100, p.low_stock_threshold / max * 100)}%"></span></span>
    <span class="stock-qty">${fmt(p.stock)}<span class="stock-unit"> en stock</span></span></li>`;
}

function salesChart(months) {
  const max = Math.max(...months.map(m => m.revenue_xof + m.simulated_xof), 0);
  if (!max) return emptyState('Aucune vente sur la période.', 'Le graphique se remplira avec les premières commandes payées.');
  return `<div class="chart" role="img" aria-label="Ventes mensuelles sur six mois">
    ${months.map((m, i) => {
      const total = m.revenue_xof + m.simulated_xof;
      return `<div class="chart-col${i === months.length - 1 ? ' is-now' : ''}" title="${esc(m.label)} : ${fmt(m.revenue_xof)} FCFA encaissés${m.simulated_xof ? `, ${fmt(m.simulated_xof)} simulés` : ''} · ${m.orders} commande(s)">
        <span class="chart-val">${total ? (total >= 1e6 ? (total / 1e6).toFixed(1).replace('.', ',') + ' M' : Math.round(total / 1000) + ' k') : ''}</span>
        <span class="chart-bar" style="height:${Math.max(total ? 3 : 0, total / max * 100)}%">
          ${m.simulated_xof ? `<span class="chart-sim" style="height:${m.simulated_xof / total * 100}%"></span>` : ''}
        </span>
        <span class="chart-lbl">${esc(m.label)}</span></div>`;
    }).join('')}</div>`;
}

function ordersTable(rows, compact = false) {
  return `<div class="table-scroll"><table class="tbl tbl-click">
    <thead><tr><th scope="col">Commande</th><th scope="col">Client</th>${compact ? '' : '<th scope="col" class="col-opt">Articles</th>'}
      <th scope="col" class="num">Montant</th><th scope="col" class="col-opt">Paiement</th><th scope="col">Statut</th></tr></thead>
    <tbody>${rows.map(o => `<tr tabindex="0" data-ref="${esc(o.reference)}" onclick="openOrder(this.dataset.ref)" onkeydown="if(event.key==='Enter')openOrder(this.dataset.ref)">
      <td><span class="ref">${esc(o.reference)}</span><span class="sub">${fmtDateTime(o.created_at)}</span></td>
      <td>${esc(o.ship_name)}${o.customer_email ? `<span class="sub">${esc(o.customer_email)}</span>` : ''}</td>
      ${compact ? '' : `<td class="col-opt">${o.items_count} pot${o.items_count > 1 ? 's' : ''}</td>`}
      <td class="num">${fmt(o.total_xof)} F</td>
      <td class="col-opt">${paymentBadge(o)}</td>
      <td>${badge(ORDER_STATUS, o.status)}</td></tr>`).join('')}</tbody></table></div>`;
}

/* --- Commandes -------------------------------------------------------------------- */
const ORDER_FILTERS = [['', 'Toutes'], ['to_process', 'À préparer'], ['pending_payment', 'Attente paiement'],
  ['shipped', 'Expédiées'], ['delivered', 'Livrées'], ['cancelled', 'Annulées'], ['refunded', 'Remboursées']];

async function renderOrders(param) {
  const q = state.ordersQuery;
  if (param && ORDER_FILTERS.some(([v]) => v === param)) { q.status = param; q.offset = 0; }
  else if (param) { openOrder(param); }
  const main = $('admView');
  if (!main.querySelector('#ordersList')) {
    main.innerHTML = `${pageHead('Commandes', 'Suivi, préparation et expédition', isAdmin() ? '<button class="btn-ghost btn-sm" onclick="exportOrders(this)"><i class="ti ti-download" aria-hidden="true"></i>Exporter (CSV)</button>' : '')}
      <div class="toolbar">
        <label class="search"><i class="ti ti-search" aria-hidden="true"></i><span class="sr-only">Rechercher une commande</span>
          <input class="input" id="orderSearch" type="search" placeholder="Référence, nom ou email" maxlength="80" value="${esc(q.q)}"></label>
        <div class="seg" role="group" aria-label="Filtrer par statut">${ORDER_FILTERS.map(([v, l]) => `<button type="button" data-status="${v}" aria-pressed="${q.status === v}">${l}</button>`).join('')}</div>
      </div>
      <section class="panel panel-flush" id="ordersList">${loading()}</section>`;
    let t;
    $('orderSearch').addEventListener('input', e => { clearTimeout(t); t = setTimeout(() => { q.q = e.target.value.trim(); q.offset = 0; loadOrders(); }, 300); });
    main.querySelectorAll('.seg button').forEach(b => b.addEventListener('click', () => {
      q.status = b.dataset.status; q.offset = 0;
      main.querySelectorAll('.seg button').forEach(x => x.setAttribute('aria-pressed', x === b));
      loadOrders();
    }));
  }
  main.querySelectorAll('.seg button').forEach(x => x.setAttribute('aria-pressed', x.dataset.status === q.status));
  loadOrders();
}

async function loadOrders() {
  const q = state.ordersQuery;
  const box = $('ordersList');
  if (!box) return;
  box.setAttribute('aria-busy', 'true');
  const params = new URLSearchParams({ limit: PAGE_SIZE, offset: q.offset });
  if (q.q) params.set('q', q.q);
  if (q.status) params.set('status', q.status);
  try {
    const page = await api('/admin/orders?' + params);
    box.innerHTML = page.items.length ? ordersTable(page.items) + pager(page.total, q.offset, 'ordersPage')
      : emptyState(q.q || q.status ? 'Aucune commande ne correspond à ces critères.' : 'Aucune commande pour le moment.',
          q.q || q.status ? 'Modifiez la recherche ou le filtre.' : 'Les commandes apparaîtront ici dès le premier achat.');
  } catch (e) { if (e.status !== 401) box.innerHTML = `<p class="form-error">${esc(e.message)}</p>`; }
  box.removeAttribute('aria-busy');
}

function pager(total, offset, fn) {
  if (total <= PAGE_SIZE) return `<p class="pager-info">${total} résultat${total > 1 ? 's' : ''}</p>`;
  const end = Math.min(offset + PAGE_SIZE, total);
  return `<div class="pager"><span>${offset + 1}–${end} sur ${total}</span>
    <button class="btn-ghost btn-sm" ${offset ? '' : 'disabled'} onclick="${fn}(-1)">Précédent</button>
    <button class="btn-ghost btn-sm" ${end < total ? '' : 'disabled'} onclick="${fn}(1)">Suivant</button></div>`;
}
function ordersPage(dir) { state.ordersQuery.offset = Math.max(0, state.ordersQuery.offset + dir * PAGE_SIZE); loadOrders(); }

/* --- Détail d'une commande (panneau latéral) ---------------------------------------- */
let drawerReturnFocus = null;
function openDrawer(kicker, title, html) {
  drawerReturnFocus = document.activeElement;
  $('drawerKicker').textContent = kicker;
  $('drawerTitle').innerHTML = title;
  $('drawerBody').innerHTML = html;
  $('drawer').hidden = false;
  $('drawerScrim').hidden = false;
  document.body.classList.add('no-scroll');
  requestAnimationFrame(() => $('drawer').classList.add('open'));
  $('drawer').querySelector('.cart-close').focus();
}
function closeDrawer() {
  const d = $('drawer');
  if (d.hidden) return;
  d.classList.remove('open');
  $('drawerScrim').hidden = true;
  document.body.classList.remove('no-scroll');
  setTimeout(() => { d.hidden = true; }, 200);
  if (drawerReturnFocus) drawerReturnFocus.focus();
}

async function openOrder(ref) {
  openDrawer('Commande', `<span class="num-title">${esc(ref)}</span>`, loading());
  let o;
  try { o = await api('/admin/orders/' + encodeURIComponent(ref)); }
  catch (e) { $('drawerBody').innerHTML = `<p class="form-error">${esc(e.message)}</p>`; return; }
  $('drawerTitle').innerHTML = `<span class="num-title">${esc(o.reference)}</span> ${badge(ORDER_STATUS, o.status)}`;
  const actions = (TRANSITIONS[o.status] || [])
    .filter(s => isAdmin() || !STAFF_FORBIDDEN.includes(`${o.status}>${s}`))
    .filter(s => s !== 'refunded' || o.paid_at);
  const actorLabel = a => a === 'payment' ? 'Paiement' : a === 'system' ? 'Automatique' : a === 'customer' ? 'Client'
    : a.startsWith('admin') ? 'Administrateur' : a.startsWith('staff') ? 'Équipe' : a;
  $('drawerBody').innerHTML = `
    ${actions.length ? `<div class="drawer-actions">${actions.map(s => `<button class="${s === 'cancelled' ? 'btn-danger' : 'btn-primary'} btn-sm" onclick="changeStatus('${esc(o.reference)}','${s}')">${ACTION_LABELS[s]}</button>`).join('')}</div>` : ''}
    <section class="drawer-sec"><h3>Articles</h3>
      ${o.items.map(i => `<div class="line"><span>${esc(i.product_name)} <span class="muted">× ${i.quantity} · ${fmt(i.unit_price_xof)} F</span></span><span>${fmt(i.line_total_xof)} F</span></div>`).join('')}
      <div class="line muted"><span>Livraison</span><span>${o.shipping_fee_xof ? fmt(o.shipping_fee_xof) + ' F' : 'Offerte'}</span></div>
      <div class="line line-total"><span>Total</span><span>${money(o.total_xof)}</span></div></section>
    <section class="drawer-sec"><h3>Paiement</h3><p>${paymentBadge(o)}${o.paid_at ? ` <span class="muted">le ${fmtDateTime(o.paid_at)}</span>` : ''}</p>
      ${o.payment_simulated ? '<p class="muted small">Paiement simulé : aucune somme n\'a été encaissée.</p>' : ''}
      ${o.status === 'pending_payment' && o.expires_at ? `<p class="muted small">Réservation du stock jusqu'au ${fmtDateTime(o.expires_at)}.</p>` : ''}</section>
    <section class="drawer-sec"><h3>Livraison</h3>
      <p class="address">${esc(o.ship_name)}<br>${esc(o.ship_address)}<br>${esc(o.ship_city)} · ${esc(o.ship_country)}<br>
      <a href="tel:${esc(o.ship_phone.replace(/[^+\d]/g, ''))}">${esc(o.ship_phone)}</a></p></section>
    <section class="drawer-sec"><h3>Client</h3><p>${o.customer_email ? esc(o.customer_email) : '<span class="muted">Compte supprimé</span>'}
      ${isAdmin() && o.customer_id ? `<br><a class="panel-link" href="#customers/${o.customer_id}" onclick="closeDrawer()">Voir la fiche client</a>` : ''}</p></section>
    <section class="drawer-sec"><h3>Historique</h3><ol class="timeline">
      ${o.events.slice().reverse().map(e => `<li><span class="tl-status">${esc((ORDER_STATUS[e.to_status] || [e.to_status])[0])}</span>
        <span class="tl-meta">${fmtDateTime(e.created_at)} · ${esc(actorLabel(e.actor))}</span>${e.note ? `<span class="tl-note">${esc(e.note)}</span>` : ''}</li>`).join('')}
    </ol></section>`;
}

async function changeStatus(ref, status) {
  const needsNote = status === 'cancelled' || status === 'refunded';
  const texts = {
    preparing: 'La commande passe en préparation. Le client verra ce statut dans son espace.',
    shipped: 'Confirmez que le colis a été remis au transporteur.',
    delivered: 'Confirmez que le client a bien reçu sa commande.',
    cancelled: 'La commande sera annulée et ses produits remis en stock. Si elle était payée, le remboursement reste à effectuer auprès du prestataire.',
    refunded: 'Confirmez que le remboursement a bien été effectué auprès du prestataire de paiement.'
  };
  const res = await confirmAction({ title: ACTION_LABELS[status] + ' ?', text: texts[status], ok: ACTION_LABELS[status], danger: status === 'cancelled', note: needsNote });
  if (!res) return;
  try {
    await api(`/admin/orders/${encodeURIComponent(ref)}/status`, { method: 'PATCH', body: { status, note: res.note } });
    toast('Commande mise à jour', `${ref} : ${ORDER_STATUS[status][0]}`);
    openOrder(ref);
    if ($('ordersList')) loadOrders();
    if (parseHash().view === 'overview') renderOverview();
  } catch (e) { if (e.status !== 401) toast('Modification refusée', e.message, true); }
}

async function exportOrders(btn) {
  if (btn) btn.disabled = true;
  try {
    const res = await api('/admin/orders.csv', { raw: true });
    const url = URL.createObjectURL(await res.blob());
    const a = Object.assign(document.createElement('a'), { href: url, download: `commandes-nanye-${new Date().toISOString().slice(0, 10)}.csv` });
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    toast('Export téléchargé', 'Fichier CSV compatible Excel.');
  } catch (e) { if (e.status !== 401) toast('Export impossible', e.message, true); }
  finally { if (btn) btn.disabled = false; }
}

/* --- Produits --------------------------------------------------------------------- */
let productsCache = [];
async function renderProducts(param) {
  try { productsCache = await api('/admin/products'); } catch (e) { return viewError(e); }
  const head = pageHead('Produits', `${productsCache.filter(p => p.status === 'available').length} en vente · ${productsCache.filter(p => p.status === 'coming_soon').length} à venir`,
    isAdmin() ? '<button class="btn-primary btn-sm" onclick="editProduct()"><i class="ti ti-plus" aria-hidden="true"></i>Nouveau produit</button>' : '');
  $('admView').innerHTML = head + (productsCache.length ? `<div class="product-list">${productsCache.map(p => {
    const img = safeImg((p.images || [])[0]);
    const level = p.status !== 'available' ? '' : p.stock <= 0 ? '<span class="flag flag-out">Rupture</span>' : p.stock <= p.low_stock_threshold ? '<span class="flag flag-low">Stock bas</span>' : '';
    return `<article class="product-row${p.status === 'archived' ? ' is-archived' : ''}">
      <div class="pr-img">${img ? `<img src="${esc(img)}" alt="" loading="lazy">` : '<span class="pr-noimg" aria-hidden="true">N</span>'}</div>
      <div class="pr-main"><h2 class="pr-name">${esc(p.name)}</h2><p class="pr-meta">${esc(CATEGORY[p.category] || p.category)} · ${esc(p.format)} · <code>${esc(p.slug)}</code></p></div>
      <div class="pr-status">${badge(PRODUCT_STATUS, p.status)}${level}</div>
      <div class="pr-price">${p.price_xof ? money(p.price_xof) : '<span class="muted">Prix à définir</span>'}</div>
      <div class="pr-stock"><strong>${fmt(p.stock)}</strong> <span class="muted">en stock</span></div>
      <div class="pr-actions">${isAdmin() ? `<button class="btn-ghost btn-sm" onclick="editProduct('${esc(p.slug)}')">Modifier</button>` : '<a class="btn-ghost btn-sm" href="#stock">Stock</a>'}</div>
    </article>`;
  }).join('')}</div>` : emptyState('Aucun produit dans le catalogue.', '', isAdmin() ? '<button class="btn-primary btn-sm" onclick="editProduct()">Créer le premier produit</button>' : ''));
  if (param === 'new' && isAdmin()) editProduct();
}

function editProduct(slug) {
  const p = slug ? productsCache.find(x => x.slug === slug) : null;
  const f = $('productForm');
  f.reset();
  f.querySelector('.form-error').hidden = true;
  $('peTitle').textContent = p ? 'Modifier — ' + p.name : 'Nouveau produit';
  f.dataset.slug = p ? p.slug : '';
  const set = (name, v) => { f.elements.namedItem(name).value = v ?? ''; };
  set('name', p?.name); set('slug', p?.slug); set('category', p?.category || 'naturelle');
  set('status', p?.status || 'coming_soon'); set('format', p?.format || '200 g');
  set('price_xof', p?.price_xof ?? ''); set('stock', p?.stock ?? 0); set('low_stock_threshold', p?.low_stock_threshold ?? 5);
  set('tagline', p?.tagline); set('description', p?.description); set('long_description', p?.long_description);
  set('images', (p?.images || []).join('\n'));
  $('peSlug').readOnly = !!p;
  $('peSlugHint').textContent = p ? 'Identifiant fixe : il sert d\'adresse à la fiche produit.' : 'Lettres minuscules, chiffres et tirets. Non modifiable ensuite.';
  $('productEditor').showModal();
}

async function saveProduct(e) {
  e.preventDefault();
  const f = e.target;
  const err = f.querySelector('.form-error');
  const btn = f.querySelector('button[type="submit"]');
  err.hidden = true;
  if (!f.checkValidity()) {
    const bad = f.querySelector(':invalid');
    err.textContent = `${(bad.labels[0] || {}).textContent || 'Champ'} : valeur manquante ou invalide.`;
    err.hidden = false; bad.focus(); return;
  }
  const v = n => f.elements.namedItem(n).value.trim();
  const body = {
    name: v('name'), category: v('category'), status: v('status'), format: v('format') || '200 g',
    price_xof: parseInt(v('price_xof'), 10), stock: parseInt(v('stock'), 10), low_stock_threshold: parseInt(v('low_stock_threshold'), 10),
    tagline: v('tagline'), description: v('description'), long_description: v('long_description'),
    images: v('images').split('\n').map(s => s.trim()).filter(Boolean)
  };
  if (body.status === 'available' && !body.price_xof) { err.textContent = 'Un produit en vente doit avoir un prix.'; err.hidden = false; return; }
  const slug = f.dataset.slug;
  btn.disabled = true;
  try {
    if (slug) await api('/admin/products/' + encodeURIComponent(slug), { method: 'PATCH', body });
    else await api('/admin/products', { method: 'POST', body: { ...body, slug: v('slug') } });
    $('productEditor').close();
    toast(slug ? 'Produit enregistré' : 'Produit créé', 'La boutique affiche déjà la nouvelle version.');
    if (location.hash === '#products/new') history.replaceState(null, '', '#products');
    renderProducts();
  } catch (ex) { if (ex.status !== 401) { err.textContent = ex.message; err.hidden = false; } }
  finally { btn.disabled = false; }
}

/* --- Stocks ----------------------------------------------------------------------- */
async function renderStock() {
  let products, notify;
  try { [products, notify] = await Promise.all([api('/admin/products'), api('/admin/notify-requests')]); }
  catch (e) { return viewError(e); }
  const active = products.filter(p => p.status !== 'archived');
  const alerts = active.filter(p => p.status === 'available' && p.stock <= p.low_stock_threshold).length;
  $('admView').innerHTML = pageHead('Stocks', alerts ? `${alerts} produit${alerts > 1 ? 's' : ''} sous le seuil d'alerte` : 'Tous les produits en vente sont au-dessus de leur seuil')
    + (active.length ? `<section class="panel panel-flush"><div class="table-scroll"><table class="tbl">
      <thead><tr><th scope="col">Produit</th><th scope="col">Niveau</th><th scope="col">Stock</th><th scope="col">Alerte sous</th><th scope="col" class="col-opt">Demandes d'alerte</th><th scope="col"><span class="sr-only">Action</span></th></tr></thead>
      <tbody>${active.map(p => {
        const n = notify.find(x => x.product_slug === p.slug);
        return `<tr data-slug="${esc(p.slug)}"><td><strong>${esc(p.name)}</strong><span class="sub">${esc((PRODUCT_STATUS[p.status] || [p.status])[0])}</span></td>
          <td class="td-level"><ul class="stock-list stock-list-inline">${stockLine(p)}</ul></td>
          <td><input class="input input-sm input-num" type="number" min="0" step="1" name="stock" value="${p.stock}" aria-label="Stock de ${esc(p.name)}"></td>
          <td><input class="input input-sm input-num" type="number" min="0" step="1" name="low_stock_threshold" value="${p.low_stock_threshold}" aria-label="Seuil d'alerte de ${esc(p.name)}"></td>
          <td class="col-opt">${n ? `${n.pending} en attente` : '<span class="muted">—</span>'}</td>
          <td><button class="btn-ghost btn-sm" onclick="saveStock(this)">Enregistrer</button></td></tr>`;
      }).join('')}</tbody></table></div></section>
      <p class="adm-footnote">Le stock est réservé dès qu'un client valide sa commande, puis libéré automatiquement si le paiement n'aboutit pas.</p>`
      : emptyState('Aucun produit à suivre.', 'Créez un produit dans l\'onglet Produits.'));
}

async function saveStock(btn) {
  const row = btn.closest('tr');
  const stock = parseInt(row.querySelector('[name=stock]').value, 10);
  const th = parseInt(row.querySelector('[name=low_stock_threshold]').value, 10);
  if (!Number.isInteger(stock) || stock < 0 || !Number.isInteger(th) || th < 0) { toast('Valeurs invalides', 'Stock et seuil doivent être des nombres entiers positifs.', true); return; }
  btn.disabled = true;
  try {
    await api(`/admin/products/${encodeURIComponent(row.dataset.slug)}/stock`, { method: 'PATCH', body: { stock, low_stock_threshold: th } });
    toast('Stock enregistré', '');
    renderStock();
  } catch (e) { if (e.status !== 401) toast('Enregistrement refusé', e.message, true); btn.disabled = false; }
}

/* --- Clients (administrateurs) ------------------------------------------------------ */
async function renderCustomers(param) {
  if (!isAdmin()) { location.hash = 'overview'; return; }
  const q = state.customersQuery;
  const main = $('admView');
  if (!main.querySelector('#customersList')) {
    main.innerHTML = `${pageHead('Clients', 'Comptes clients actifs et historique d\'achat')}
      <div class="toolbar"><label class="search"><i class="ti ti-search" aria-hidden="true"></i><span class="sr-only">Rechercher un client</span>
        <input class="input" id="customerSearch" type="search" placeholder="Nom ou email" maxlength="80" value="${esc(q.q)}"></label></div>
      <section class="panel panel-flush" id="customersList">${loading()}</section>
      <p class="adm-footnote">Données personnelles affichées au strict nécessaire : coordonnées complètes uniquement dans la fiche d'un client.</p>`;
    let t;
    $('customerSearch').addEventListener('input', e => { clearTimeout(t); t = setTimeout(() => { q.q = e.target.value.trim(); q.offset = 0; loadCustomers(); }, 300); });
  }
  loadCustomers();
  if (param) openCustomer(param);
}

async function loadCustomers() {
  const q = state.customersQuery;
  const box = $('customersList');
  if (!box) return;
  const params = new URLSearchParams({ limit: PAGE_SIZE, offset: q.offset });
  if (q.q) params.set('q', q.q);
  try {
    const page = await api('/admin/customers?' + params);
    box.innerHTML = page.items.length ? `<div class="table-scroll"><table class="tbl tbl-click">
      <thead><tr><th scope="col">Client</th><th scope="col" class="col-opt">Inscription</th><th scope="col" class="num">Commandes payées</th><th scope="col" class="num">Total dépensé</th><th scope="col" class="col-opt">Dernière commande</th></tr></thead>
      <tbody>${page.items.map(c => `<tr tabindex="0" onclick="location.hash='customers/${c.id}'" onkeydown="if(event.key==='Enter')location.hash='customers/${c.id}'">
        <td><strong>${esc(c.full_name)}</strong><span class="sub">${esc(c.email)}</span></td>
        <td class="col-opt">${fmtDate(c.created_at)}</td><td class="num">${c.paid_orders_count}</td>
        <td class="num">${fmt(c.total_spent_xof)} F</td><td class="col-opt">${fmtDate(c.last_order_at)}</td></tr>`).join('')}</tbody></table></div>`
      + pager(page.total, q.offset, 'customersPage')
      : emptyState(q.q ? 'Aucun client ne correspond à cette recherche.' : 'Aucun client inscrit pour le moment.');
  } catch (e) { if (e.status !== 401) box.innerHTML = `<p class="form-error">${esc(e.message)}</p>`; }
}
function customersPage(dir) { state.customersQuery.offset = Math.max(0, state.customersQuery.offset + dir * PAGE_SIZE); loadCustomers(); }

async function openCustomer(id) {
  openDrawer('Client', '…', loading());
  let c;
  try { c = await api('/admin/customers/' + encodeURIComponent(id)); }
  catch (e) { $('drawerBody').innerHTML = `<p class="form-error">${esc(e.message)}</p>`; return; }
  $('drawerTitle').textContent = c.full_name;
  $('drawerBody').innerHTML = `
    <section class="drawer-sec"><h3>Coordonnées</h3><p><a href="mailto:${esc(c.email)}">${esc(c.email)}</a>${c.phone ? `<br><a href="tel:${esc(c.phone.replace(/[^+\d]/g, ''))}">${esc(c.phone)}</a>` : ''}</p>
      <p class="muted small">Client depuis le ${fmtDate(c.created_at)}</p></section>
    <section class="drawer-sec"><h3>Achats</h3><div class="mini-kpis">
      <div><span class="mk-v">${c.paid_orders_count}</span><span class="mk-l">commandes payées</span></div>
      <div><span class="mk-v">${fmt(c.total_spent_xof)}</span><span class="mk-l">FCFA dépensés</span></div></div></section>
    <section class="drawer-sec"><h3>Commandes</h3>
      ${c.orders.length ? c.orders.map(o => `<button class="order-pick" onclick="openOrder('${esc(o.reference)}')">
        <span><span class="ref">${esc(o.reference)}</span><span class="sub">${fmtDate(o.created_at)}</span></span>
        <span>${fmt(o.total_xof)} F</span>${badge(ORDER_STATUS, o.status)}</button>`).join('') : '<p class="muted">Aucune commande.</p>'}
    </section>`;
}

/* --- Réglages -------------------------------------------------------------------- */
async function renderSettings() {
  let s = null;
  if (isAdmin()) { try { s = await api('/admin/settings'); } catch (e) { return viewError(e); } }
  const row = (k, v) => `<div class="set-row"><dt>${k}</dt><dd>${v}</dd></div>`;
  $('admView').innerHTML = pageHead('Réglages', isAdmin() ? 'Boutique, paiements et compte' : 'Votre compte')
    + (s ? `<div class="adm-cols">
      <section class="panel"><div class="panel-head"><h2 class="panel-title">Boutique</h2></div><dl class="set-list">
        ${row('Frais de livraison', s.shipping_fee_xof ? money(s.shipping_fee_xof) : 'Offerts')}
        ${row('Quantité maximale par produit', s.max_quantity_per_item)}
        ${row('Délai de paiement avant libération du stock', s.order_payment_timeout_minutes + ' min')}
        ${row('Durée des sessions', Math.round(s.session_lifetime_hours / 24) + ' jours')}</dl></section>
      <section class="panel"><div class="panel-head"><h2 class="panel-title">Paiements et emails</h2></div><dl class="set-list">
        ${row('Paiement en ligne', esc(s.payment_provider))}
        ${row('Emails transactionnels', s.emails_enabled ? 'Actifs' : '<span class="muted">Non configurés</span>')}
        ${row('Environnement', esc(s.environment))}</dl>
        ${s.payment_simulated ? '<p class="adm-alert al-info"><span>Les paiements sont simulés : passez à CinetPay en renseignant les identifiants marchands dans la configuration du serveur.</span></p>' : ''}</section>
    </div>
    <p class="adm-footnote">Ces réglages se modifient dans la configuration du serveur (fichier <code>.env</code> ou variables de l'hébergeur), jamais depuis le navigateur : aucun secret n'y transite.</p>` : '')
    + `<section class="panel panel-narrow"><div class="panel-head"><h2 class="panel-title">Mon mot de passe</h2></div>
      <form id="pwForm" class="form" novalidate>
        <div class="field"><label for="pwCur">Mot de passe actuel</label><input class="input" id="pwCur" type="password" autocomplete="current-password" required maxlength="128"></div>
        <div class="field"><label for="pwNew">Nouveau mot de passe</label><input class="input" id="pwNew" type="password" autocomplete="new-password" required minlength="10" maxlength="128"><span class="field-hint">10 caractères minimum. Vos autres sessions seront fermées.</span></div>
        <p class="form-error" role="alert" hidden></p><p class="form-success" role="status" hidden></p>
        <button class="btn-primary" type="submit">Changer le mot de passe</button>
      </form></section>`;
  $('pwForm').addEventListener('submit', async e => {
    e.preventDefault();
    const f = e.target, err = f.querySelector('.form-error'), ok = f.querySelector('.form-success'), btn = f.querySelector('button');
    err.hidden = ok.hidden = true;
    if (!f.checkValidity()) { err.textContent = 'Renseignez les deux champs (10 caractères minimum pour le nouveau).'; err.hidden = false; return; }
    btn.disabled = true;
    try {
      await api('/auth/password/change', { method: 'POST', body: { current_password: $('pwCur').value, new_password: $('pwNew').value } });
      f.reset(); ok.textContent = 'Mot de passe modifié.'; ok.hidden = false;
    } catch (ex) { if (ex.status !== 401) { err.textContent = ex.message; err.hidden = false; } }
    finally { btn.disabled = false; }
  });
}

/* --- Démarrage ------------------------------------------------------------------ */
document.addEventListener('DOMContentLoaded', () => {
  $('gateLogin').addEventListener('submit', async e => {
    e.preventDefault();
    const f = e.target, err = f.querySelector('.form-error'), btn = f.querySelector('button');
    err.hidden = true;
    if (!f.checkValidity()) { err.textContent = 'Renseignez votre email et votre mot de passe.'; err.hidden = false; return; }
    btn.disabled = true;
    try {
      const s = await api('/auth/login', { method: 'POST', body: { email: $('gEmail').value, password: $('gPassword').value } });
      state.user = s.user; state.csrf = s.csrf_token;
      f.reset();
      if (!['staff', 'admin'].includes(s.user.role)) showGate('denied');
      else enterApp();
    } catch (ex) { err.textContent = ex.message; err.hidden = false; }
    finally { btn.disabled = false; }
  });
  $('productForm').addEventListener('submit', saveProduct);
  document.addEventListener('keydown', e => { if (e.key === 'Escape') { closeDrawer(); toggleSide(false); } });
  // Raccourci « Exporter » de la vue d'ensemble
  window.addEventListener('hashchange', () => {
    if (location.hash === '#export') { history.replaceState(null, '', '#overview'); exportOrders(); }
  });
  boot();
});
