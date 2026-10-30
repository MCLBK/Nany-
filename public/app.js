/* ============================================================
   NANYÉ — Application Logic — app.js
   Les données (catalogue, compte client, commandes) viennent de l'API
   configurée dans config.js. L'espace de gestion est une application
   distincte (admin/), sans aucun lien depuis le site public. Prix, stocks et totaux sont
   toujours recalculés par le serveur : le navigateur n'affiche qu'une estimation.
   ============================================================ */

const API = ((window.NANYE_CONFIG || {}).apiBaseUrl || 'http://localhost:8000/api').replace(/\/$/, '');

// Catalogue affiché si l'API est injoignable (consultation seule, achat désactivé)
const FALLBACK_PRODUCTS = [
  { slug: 'moutarde-naturelle', name: 'Moutarde Naturelle', category: 'naturelle', status: 'available',
    availability: 'in_stock', max_quantity: 0, price_xof: 2500, format: '200 g',
    tagline: "L'essence de l'afitin, sublimée.",
    description: "L'authenticité de l'afitin sublimée. Notes profondes, texture onctueuse, caractère sans compromis.",
    long_description: "Notre moutarde phare. Produite à partir de graines de néré sélectionnées, fermentées naturellement pendant 72 heures selon les méthodes ancestrales du Bénin. Profil aromatique exceptionnel — umami, terreux, légèrement fumé.",
    images: ['images/product-hero.jpg', 'images/product-ingredients.jpg', 'images/lifestyle.jpg'] },
  { slug: 'moutarde-epicee', name: 'Moutarde Épicée', category: 'epice', status: 'coming_soon',
    availability: 'coming_soon', max_quantity: 0, price_xof: 0, format: '200 g', tagline: '',
    description: 'Chaleur progressive, piment akwi du Bénin. Une intensité maîtrisée.', long_description: '', images: [] },
  { slug: 'moutarde-gingembre', name: 'Moutarde Gingembre', category: 'herbes', status: 'coming_soon',
    availability: 'coming_soon', max_quantity: 0, price_xof: 0, format: '200 g', tagline: '',
    description: "La vivacité du gingembre et la profondeur de l'afitin. Fraîcheur et caractère.", long_description: '', images: [] }
];

const STATUS_LABELS = {
  pending_payment: 'En attente de paiement', paid: 'Payée', preparing: 'En préparation',
  shipped: 'Expédiée', delivered: 'Livrée', cancelled: 'Annulée', refunded: 'Remboursée'
};
const STATUS_CLASS = {
  pending_payment: 's-pending', paid: 's-delivered', preparing: 's-transit',
  shipped: 's-transit', delivered: 's-delivered', cancelled: 's-cancelled', refunded: 's-cancelled'
};
const PAYMENT_LABELS = {
  none: 'Non payée', initiated: 'Paiement en cours', pending: 'Paiement en cours',
  succeeded: 'Paiement reçu', failed: 'Paiement refusé', cancelled: 'Paiement abandonné'
};
// Étapes affichées au client pour suivre sa commande
const ORDER_STEPS = [['paid', 'Payée'], ['preparing', 'En préparation'], ['shipped', 'Expédiée'], ['delivered', 'Livrée']];
const ACTIVE_STATUSES = ['pending_payment', 'paid', 'preparing', 'shipped'];
const state = {
  products: FALLBACK_PRODUCTS, online: false, catalogLoaded: false,
  user: null, csrf: null, sessionChecked: false,
  shop: { shipping_fee_xof: null, payment_enabled: false, payment_simulated: false, max_quantity_per_item: 20 }
};
let cart = [];
let detailQty = 1;
let currentProductSlug = 'moutarde-naturelle';
let resetToken = null;

/* --- UTILITAIRES ------------------------------------------- */
const $ = id => document.getElementById(id);
const fmt = n => Number(n || 0).toLocaleString('fr-FR');
function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
// N'accepte que les images du site ou en https (jamais javascript:, data:…)
function safeImg(url) {
  return /^(images\/[\w.-]+|https:\/\/[\w.-]+\/[\w.~/%-]+)$/.test(url || '') ? url : 'images/product-hero.jpg';
}
const parseDate = iso => new Date(/[zZ]|[+-]\d\d:\d\d$/.test(iso) ? iso : iso + 'Z');
const fmtDate = iso => parseDate(iso).toLocaleDateString('fr-FR', { day: 'numeric', month: 'short', year: 'numeric' });
const productBySlug = slug => state.products.find(p => p.slug === slug);
const isBuyable = p => p && state.online && p.status === 'available' && p.max_quantity > 0;
function newKey() {
  if (window.crypto && crypto.randomUUID) return crypto.randomUUID().replace(/-/g, '');
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2, '0')).join('');
}
function storageGet(store, key) { try { return store.getItem(key); } catch { return null; } }
function storageSet(store, key, value) { try { value === null ? store.removeItem(key) : store.setItem(key, value); } catch { /* stockage indisponible */ } }

/* --- API --------------------------------------------------- */
class ApiError extends Error {
  constructor(status, message, data) { super(message); this.status = status; this.data = data; }
}
const STATUS_MESSAGES = {
  0: 'Le service est momentanément indisponible. Vérifiez votre connexion et réessayez.',
  401: 'Votre session a expiré. Connectez-vous à nouveau.',
  403: 'Action non autorisée.',
  404: 'Élément introuvable.',
  429: 'Trop de tentatives. Patientez quelques minutes.',
  500: 'Une erreur est survenue de notre côté. Réessayez dans un instant.'
};

async function api(path, { method = 'GET', body, headers = {}, raw = false } = {}) {
  const opts = { method, credentials: 'include', headers: { Accept: 'application/json', ...headers } };
  if (body !== undefined) { opts.body = JSON.stringify(body); opts.headers['Content-Type'] = 'application/json'; }
  if (method !== 'GET' && state.csrf) opts.headers['X-CSRF-Token'] = state.csrf;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 15000);
  opts.signal = ctrl.signal;
  let res;
  try { res = await fetch(API + path, opts); }
  catch { throw new ApiError(0, STATUS_MESSAGES[0]); }
  finally { clearTimeout(timer); }
  if (raw && res.ok) return res;
  let data = null;
  if (res.status !== 204) { try { data = await res.json(); } catch { data = null; } }
  if (!res.ok) {
    if (res.status === 401 && state.user) { state.user = null; state.csrf = null; updateAuthUI(); }
    let message = data && typeof data.detail === 'string' ? data.detail : (STATUS_MESSAGES[res.status] || STATUS_MESSAGES[500]);
    if (res.status === 422 && data && Array.isArray(data.errors) && data.errors.length) {
      message = 'Vérifiez les champs : ' + data.errors.map(e => e.field.split('.').pop()).filter(Boolean).join(', ') + '.';
    }
    throw new ApiError(res.status, message, data);
  }
  return data;
}

/* --- PAGE ROUTING ------------------------------------------ */
// Les pages sont adressées par l'URL : #shop, #product/<slug>, #order/<référence>…
function parseHash() {
  const [page, ...rest] = location.hash.slice(1).split('/');
  return { page, param: rest.join('/') };
}
function pageExists(id) { return !!id && !!document.getElementById('page-' + id); }

function showPage(pageId) {
  if (location.hash.slice(1) !== pageId) { location.hash = pageId; return; }
  renderPage(pageId);
}

function renderPage(pageId, param = '') {
  if (!pageExists(pageId)) pageId = 'home';
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  const target = document.getElementById('page-' + pageId);
  target.classList.add('active');
  document.body.dataset.page = pageId;
  if (target.dataset.title) document.title = target.dataset.title;

  document.querySelectorAll('[data-nav]').forEach(a => {
    const match = a.dataset.nav.split(' ').includes(pageId);
    a.classList.toggle('active', match);
    if (match) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  });

  window.scrollTo(0, 0);
  closeMobileMenu();
  const onEnter = PAGE_HANDLERS[pageId];
  if (onEnter) onEnter(param);
}

const PAGE_HANDLERS = {
  product: slug => renderProduct(slug || currentProductSlug),
  account: () => renderAccount(),
  checkout: () => renderCheckout(),
  order: ref => renderOrder(ref),
  reset: token => {
    if (token) {
      resetToken = token;
      // Le jeton ne reste ni dans la barre d'adresse ni dans l'historique
      history.replaceState(null, '', location.pathname + location.search + '#reset');
    }
  }
};

function route() {
  const { page, param } = parseHash();
  if (pageExists(page)) renderPage(page, param);
}
window.addEventListener('hashchange', route);

document.addEventListener('click', e => {
  const a = e.target.closest('a[href^="#"]');
  if (!a) return;
  const id = a.getAttribute('href').slice(1);
  if (!id) { e.preventDefault(); return; }
  if (id === location.hash.slice(1)) { e.preventDefault(); route(); }
});

/* --- MOBILE MENU ------------------------------------------- */
function toggleMobileMenu() {
  const menu = $('mobileMenu');
  const btn = $('mobileMenuBtn');
  const open = menu.classList.toggle('open');
  btn.setAttribute('aria-expanded', open);
  btn.innerHTML = open ? '<i class="ti ti-x"></i>' : '<i class="ti ti-menu-2"></i>';
}
function closeMobileMenu() {
  const menu = $('mobileMenu');
  if (!menu.classList.contains('open')) return;
  menu.classList.remove('open');
  const btn = $('mobileMenuBtn');
  btn.setAttribute('aria-expanded', 'false');
  btn.innerHTML = '<i class="ti ti-menu-2"></i>';
}

/* --- CATALOGUE ---------------------------------------------- */
async function loadCatalog() {
  try {
    const [products, shop] = await Promise.all([api('/products'), api('/shop-config')]);
    state.products = products;
    state.shop = shop;
    state.online = true;
  } catch {
    state.products = FALLBACK_PRODUCTS;
    state.online = false;
  }
  state.catalogLoaded = true;
  $('demoBanner').hidden = !(state.online && state.shop.payment_simulated);
  renderPayMethods();
  renderCatalog();
  sanitizeCart();
  renderCart();
  // Rafraîchit la page courante si elle dépend du catalogue (sans remonter en haut)
  const { page, param } = parseHash();
  if (page === 'product' || page === 'checkout') PAGE_HANDLERS[page](param);
}

// N'affiche que ce qui est réellement disponible : rien hors ligne, la simulation en démonstration
function renderPayMethods() {
  const chip = (icon, label) => `<span class="pay-chip"><i class="ti ${icon}" aria-hidden="true"></i>${label}</span>`;
  const html = !(state.online && state.shop.payment_enabled) ? ''
    : state.shop.payment_simulated ? chip('ti-flask', 'Paiement simulé, aucun débit')
    : chip('ti-lock', 'Paiement sécurisé') + chip('ti-device-mobile', 'Mobile Money') + chip('ti-credit-card', 'Carte bancaire');
  document.querySelectorAll('[data-pay-methods]').forEach(el => { el.innerHTML = html; el.hidden = !html; });
}

function jarIllustration(p) {
  const accent = p.category === 'herbes' ? 'pj-accent-ginger' : 'pj-accent-spicy';
  const body = p.category === 'herbes' ? 'pj-ginger' : 'pj-spicy';
  const short = esc(p.name.replace(/^Moutarde\s+/i, ''));
  return `<div class="p-jar-img" aria-hidden="true"><div class="pj-lid"></div><div class="pj-body ${body}">
    <div class="pj-accent ${accent}"></div><div class="pj-brand">NANYÉ</div><div class="pj-div"></div>
    <div class="pj-sub">${short} · ${esc(p.format)}</div></div></div>`;
}

function productCard(p) {
  const slug = esc(p.slug);
  if (p.status === 'coming_soon') {
    return `<article class="p-card unavailable" data-cat="${esc(p.category)}">
      <div class="p-visual p-visual-soon">${jarIllustration(p)}<span class="p-badge badge-soon">Bientôt</span></div>
      <h3 class="p-name p-name-muted">${esc(p.name)}</h3>
      <p class="p-desc p-desc-muted">${esc(p.description)}</p>
      <div class="p-footer"><div class="p-price p-price-muted">Prix à venir</div>
        <button class="p-notify-btn" onclick="showNotify('${slug}')"><i class="ti ti-bell" aria-hidden="true"></i>M'avertir</button></div>
    </article>`;
  }
  const soldOut = p.availability === 'out_of_stock';
  const badge = soldOut ? '<span class="p-badge badge-soon">Épuisé</span>' : '<span class="p-badge badge-available">Disponible</span>';
  const cta = isBuyable(p)
    ? `<button class="p-cta" onclick="addToCart(event,'${slug}')"><i class="ti ti-plus" aria-hidden="true"></i>Ajouter</button>`
    : `<button class="p-notify-btn" disabled>${soldOut ? 'Épuisé' : 'Indisponible'}</button>`;
  return `<article class="p-card" data-cat="${esc(p.category)}">
    <a class="p-visual" href="#product/${slug}" aria-label="Voir ${esc(p.name)}">
      <img src="${esc(safeImg(p.images[0]))}" alt="Pot de ${esc(p.name)} NANYÉ" class="p-real-img" loading="lazy">${badge}</a>
    <h3 class="p-name"><a href="#product/${slug}">${esc(p.name)}</a></h3>
    <p class="p-desc">${esc(p.description)}</p>
    <div class="p-footer"><div class="p-price">${fmt(p.price_xof)} <span class="p-currency">FCFA</span></div>${cta}</div>
  </article>`;
}

function renderCatalog() {
  const html = state.products.map(productCard).join('');
  $('homeProducts').innerHTML = state.products.slice(0, 3).map(productCard).join('');
  $('shopGrid').innerHTML = html;
  $('homeProducts').removeAttribute('aria-busy');
  $('shopGrid').removeAttribute('aria-busy');
  const notice = $('shopNotice');
  if (notice) {
    notice.hidden = state.online;
    notice.textContent = 'La boutique est momentanément hors ligne : vous pouvez consulter le catalogue, les commandes reprendront dans un instant.';
  }
  const active = document.querySelector('.f-btn.active');
  if (active) setFilter(active);
}

/* --- CART -------------------------------------------------- */
let cartReturnFocus = null;

function loadCart() {
  try {
    const saved = JSON.parse(storageGet(localStorage, 'nanye_cart') || '[]');
    cart = Array.isArray(saved) ? saved.filter(i => typeof i.slug === 'string' && Number.isInteger(i.qty) && i.qty > 0) : [];
  } catch { cart = []; }
}
function saveCart() { storageSet(localStorage, 'nanye_cart', JSON.stringify(cart)); }

// Retire du panier ce qui n'est plus vendable et respecte les plafonds du serveur
function sanitizeCart() {
  if (!state.online) return;
  cart = cart.filter(i => isBuyable(productBySlug(i.slug)))
             .map(i => ({ slug: i.slug, qty: Math.min(i.qty, productBySlug(i.slug).max_quantity) }));
  saveCart();
  updateCartCount();
}

function openCart() {
  const overlay = $('cartOverlay');
  cartReturnFocus = document.activeElement;
  renderCart();
  overlay.classList.add('open');
  overlay.setAttribute('aria-hidden', 'false');
  document.body.classList.add('no-scroll');
  closeMobileMenu();
  setTimeout(() => $('cartClose').focus(), 50);
}
function closeCart() {
  const overlay = $('cartOverlay');
  if (!overlay.classList.contains('open')) return;
  overlay.classList.remove('open');
  overlay.setAttribute('aria-hidden', 'true');
  document.body.classList.remove('no-scroll');
  if (cartReturnFocus) cartReturnFocus.focus();
}
function handleOverlay(e) {
  if (e.target === $('cartOverlay')) closeCart();
}

function addToCart(e, slug, qty = 1) {
  if (e) e.stopPropagation();
  const product = productBySlug(slug);
  if (!state.online) { toast('Boutique momentanément hors ligne', 'Réessayez dans un instant.'); return; }
  if (!isBuyable(product)) { toast('Produit indisponible', 'Il ne peut pas être commandé pour le moment.'); return; }
  const existing = cart.find(i => i.slug === slug);
  const wanted = (existing ? existing.qty : 0) + qty;
  const allowed = Math.min(wanted, product.max_quantity);
  if (existing) existing.qty = allowed; else cart.push({ slug, qty: allowed });
  saveCart();
  updateCartCount();
  if (allowed < wanted) toast('Quantité maximale atteinte', `${product.max_quantity} pots au plus pour ce produit.`);
  else showNotif(product.name, false, qty);
}

function cartQty(idx, delta) {
  const item = cart[idx];
  const max = (productBySlug(item.slug) || {}).max_quantity || item.qty;
  item.qty = Math.min(item.qty + delta, max);
  if (item.qty <= 0) cart.splice(idx, 1);
  saveCart();
  updateCartCount();
  renderCart();
}

function cartLines() {
  return cart.map(i => ({ ...i, product: productBySlug(i.slug) })).filter(l => l.product);
}
const cartSubtotal = () => cartLines().reduce((s, l) => s + l.product.price_xof * l.qty, 0);

function updateCartCount() {
  const count = cart.reduce((s, i) => s + i.qty, 0);
  const badge = $('cartCount');
  badge.textContent = count;
  badge.classList.toggle('has-items', count > 0);
}

function renderCart() {
  const el = $('cartItems');
  const lines = cartLines();
  $('checkoutBtn').disabled = !lines.length || !state.online;
  if (!lines.length) {
    el.innerHTML = `
      <div class="cart-empty">
        <p class="cart-empty-title">Votre panier est vide</p>
        <p class="cart-empty-sub">La Moutarde Naturelle est disponible dès maintenant.</p>
        <button class="btn-primary" onclick="closeCart();showPage('shop')">Voir la boutique</button>
      </div>`;
    $('cartTotal').textContent = '0 FCFA';
    return;
  }
  el.innerHTML = lines.map((l, i) => `
    <div class="cart-item">
      <div class="c-img"><img src="${esc(safeImg(l.product.images[0]))}" alt=""></div>
      <div class="c-info">
        <div class="c-name">${esc(l.product.name)}</div>
        <div class="c-var">${esc(l.product.format)} · ${fmt(l.product.price_xof)} FCFA</div>
        <div class="c-qty">
          <button class="qty-b" onclick="cartQty(${i},-1)" aria-label="Retirer un ${esc(l.product.name)}"><i class="ti ti-${l.qty === 1 ? 'trash' : 'minus'}"></i></button>
          <span class="qty-n">${l.qty}</span>
          <button class="qty-b" onclick="cartQty(${i},1)" aria-label="Ajouter un ${esc(l.product.name)}" ${l.qty >= l.product.max_quantity ? 'disabled' : ''}><i class="ti ti-plus"></i></button>
        </div>
      </div>
      <span class="c-price">${fmt(l.product.price_xof * l.qty)} F</span>
    </div>`).join('');
  $('cartTotal').textContent = fmt(cartSubtotal()) + ' FCFA';
}

function goToCheckout() {
  closeCart();
  showPage('checkout');
}

/* --- NOTIFICATIONS ----------------------------------------- */
let notifTimer = null;
function toast(title, subHtml) {
  const n = $('notif');
  $('notif-txt').textContent = title;
  $('notif-sub').innerHTML = subHtml;
  n.classList.add('show');
  clearTimeout(notifTimer);
  notifTimer = setTimeout(() => n.classList.remove('show'), 4000);
}
function showNotif(name, isNotify = false, qty = 1) {
  if (isNotify) {
    toast('Demande enregistrée pour ' + name, 'Nous vous écrirons dès l\'ouverture des ventes.');
  } else {
    toast((qty > 1 ? qty + ' × ' : '') + name + ' ajouté au panier',
      '<button class="notif-link" onclick="openCart()">Voir le panier</button>');
  }
}

function showNotify(slug) {
  const p = productBySlug(slug);
  if (!p) return;
  $('notifySlug').value = p.slug;
  $('notifyTitle').textContent = 'Être averti — ' + p.name;
  if (state.user && !$('notifyEmail').value) $('notifyEmail').value = state.user.email;
  openDialog('notifyDialog');
}

function openDialog(id) {
  const d = $(id);
  d.querySelectorAll('.form-error, .form-success').forEach(m => { m.hidden = true; });
  if (typeof d.showModal === 'function') d.showModal(); else d.setAttribute('open', '');
}
function closeDialog(id) {
  const d = $(id);
  if (typeof d.close === 'function') d.close(); else d.removeAttribute('open');
}

/* --- FORMULAIRES ------------------------------------------- */
// Gère chargement, double clic, erreurs et succès de façon uniforme
function bindForm(formId, handler) {
  const form = $(formId);
  if (!form) return;
  form.addEventListener('submit', async e => {
    e.preventDefault();
    const btn = form.querySelector('button[type="submit"]');
    const err = form.querySelector('.form-error');
    const ok = form.querySelector('.form-success');
    if (btn.disabled) return;
    if (err) err.hidden = true;
    if (ok) ok.hidden = true;
    if (!form.checkValidity()) {
      const firstInvalid = form.querySelector(':invalid');
      if (err) { err.textContent = invalidMessage(firstInvalid); err.hidden = false; }
      if (firstInvalid) firstInvalid.focus();
      return;
    }
    const data = Object.fromEntries(new FormData(form).entries());
    btn.disabled = true;
    btn.classList.add('is-loading');
    try {
      const message = await handler(data, form);
      if (message && ok) { ok.textContent = message; ok.hidden = false; }
    } catch (ex) {
      if (err) { err.textContent = ex.message || STATUS_MESSAGES[500]; err.hidden = false; }
    } finally {
      btn.disabled = false;
      btn.classList.remove('is-loading');
    }
  });
}

function invalidMessage(input) {
  if (!input) return 'Vérifiez le formulaire.';
  const label = (input.labels && input.labels[0] ? input.labels[0].textContent : 'Ce champ').trim();
  if (input.validity.valueMissing) return `${label} : champ obligatoire.`;
  if (input.validity.typeMismatch) return `${label} : format invalide.`;
  if (input.validity.tooShort) return `${label} : ${input.minLength} caractères minimum.`;
  if (input.validity.patternMismatch) return `${label} : format invalide.`;
  return `${label} : valeur invalide.`;
}

/* --- AUTHENTIFICATION -------------------------------------- */
function setSession(data) {
  state.user = data ? data.user : null;
  state.csrf = data ? data.csrf_token : null;
  updateAuthUI();
}

let sessionPromise = null;
function loadSession() {
  if (!sessionPromise) {
    sessionPromise = api('/auth/me')
      .then(setSession, () => setSession(null))
      .finally(() => { state.sessionChecked = true; });
  }
  return sessionPromise;
}

function updateAuthUI() {
  const label = document.querySelector('.nav-account span');
  if (label) label.textContent = state.user ? state.user.full_name.split(' ')[0] : 'Mon compte';
}

function toggleForgot(show) {
  $('loginForm').hidden = show;
  $('forgotForm').hidden = !show;
  (show ? $('forgotEmail') : $('loginEmail')).focus();
}

function afterLogin() {
  const next = storageGet(sessionStorage, 'nanye_after_login');
  storageSet(sessionStorage, 'nanye_after_login', null);
  $('authNotice').hidden = true;
  if (next && pageExists(next)) showPage(next); else renderAccount();
}

async function logout(e) {
  if (e) e.preventDefault();
  try { await api('/auth/logout', { method: 'POST' }); } catch { /* session déjà close */ }
  setSession(null);
  toast('Vous êtes déconnecté', 'À bientôt chez NANYÉ.');
  showPage('home');
}

async function logoutAll() {
  try {
    await api('/auth/logout-all', { method: 'POST' });
    setSession(null);
    toast('Tous vos appareils sont déconnectés', 'Reconnectez-vous pour continuer.');
    showPage('account');
  } catch (ex) { toast('Action impossible', esc(ex.message)); }
}

/* --- MON COMPTE -------------------------------------------- */
async function renderAccount() {
  if (!state.sessionChecked) await loadSession();
  const member = !!state.user;
  $('accGuest').hidden = member;
  $('accMember').hidden = !member;
  if (!member) return;

  const u = state.user;
  $('accAvatar').textContent = u.full_name.split(/\s+/).map(w => w[0]).slice(0, 2).join('').toUpperCase();
  $('accName').textContent = u.full_name;
  $('accEmail').textContent = u.email;
  $('accHello').textContent = 'Bonjour, ' + u.full_name.split(' ')[0];
  $('accSince').textContent = 'Client NANYÉ depuis ' + parseDate(u.created_at).toLocaleDateString('fr-FR', { month: 'long', year: 'numeric' });
  fillProfileForm(u);
  showAccountView(storageGet(sessionStorage, 'nanye_acc_view') || 'orders');

  const box = $('accOrders');
  box.innerHTML = '<p class="loading">Chargement…</p>';
  try {
    const orders = await api('/account/orders');
    const paid = orders.filter(o => ['paid', 'preparing', 'shipped', 'delivered'].includes(o.status));
    $('accStatOrders').textContent = paid.length;
    $('accStatSpent').textContent = fmt(paid.reduce((s, o) => s + o.total_xof, 0));
    $('accStatActive').textContent = orders.filter(o => ACTIVE_STATUSES.includes(o.status)).length;
    box.innerHTML = orders.length ? orders.map(orderRow).join('') : `
      <div class="acc-empty">
        <p class="acc-empty-title">Votre première commande vous attend.</p>
        <p class="acc-empty-text">Vos achats apparaîtront ici, avec leur suivi de préparation et de livraison.</p>
        <a class="btn-primary" href="#shop">Découvrir la boutique</a>
      </div>`;
  } catch (ex) {
    box.innerHTML = `<p class="form-error">${esc(ex.message)}</p>`;
  }
}

function orderRow(o) {
  const count = o.items.reduce((s, i) => s + i.quantity, 0);
  const label = o.items.length === 1 ? `${esc(o.items[0].product_name)} × ${o.items[0].quantity}` : `${count} articles`;
  const hint = o.status === 'pending_payment' ? `<span class="o-hint">${esc(PAYMENT_LABELS[o.payment_status] || '')} — à finaliser</span>` : '';
  return `<a class="order-item order-link" href="#order/${esc(o.reference)}">
    <div class="o-img"><img src="images/product-hero.jpg" alt=""></div>
    <div class="o-main"><div class="o-name">${label}</div><div class="o-meta">${esc(o.reference)} · ${fmtDate(o.created_at)}</div>${hint}</div>
    <span class="o-status ${STATUS_CLASS[o.status] || ''}">${esc(STATUS_LABELS[o.status] || o.status)}</span>
    <span class="o-price">${fmt(o.total_xof)} F</span>
  </a>`;
}

const ACCOUNT_VIEWS = { orders: 'accOrdersView', profile: 'accProfileView', security: 'accSecurityView' };
function showAccountView(view) {
  if (!ACCOUNT_VIEWS[view]) view = 'orders';
  storageSet(sessionStorage, 'nanye_acc_view', view);
  Object.entries(ACCOUNT_VIEWS).forEach(([k, id]) => { $(id).hidden = k !== view; });
  document.querySelectorAll('.acc-nav a[data-acc]').forEach(a => {
    const on = a.dataset.acc === view;
    a.classList.toggle('active', on);
    if (on) a.setAttribute('aria-current', 'true'); else a.removeAttribute('aria-current');
  });
}

// Le sélecteur de pays du profil reprend la liste de la page de commande
function fillProfileForm(u) {
  const sel = $('pfCountry');
  if (sel.options.length < 2) {
    Array.from($('shCountry').options).forEach(o => sel.add(new Option(o.textContent, o.value)));
  }
  $('pfName').value = u.full_name;
  $('pfPhone').value = u.phone || '';
  $('pfEmail').value = u.email;
  $('pfAddress').value = u.default_address || '';
  $('pfCity').value = u.default_city || '';
  sel.value = u.default_country || '';
}

/* --- FICHE PRODUIT ----------------------------------------- */
function renderProduct(slug) {
  const p = productBySlug(slug);
  $('pdMissing').hidden = !!p || !state.catalogLoaded;
  $('pdGrid').hidden = !p;
  if (!p) return;
  currentProductSlug = p.slug;
  detailQty = 1;
  $('detailQty').textContent = 1;
  document.title = p.name + ' — NANYÉ';
  $('pdCrumb').textContent = p.name;
  $('pdTitle').textContent = p.name;
  $('pdTagline').textContent = p.tagline || '';
  $('pdTagline').hidden = !p.tagline;
  $('pdDesc').textContent = p.long_description || p.description;
  $('pdFormat').textContent = p.format;
  $('pdPrice').innerHTML = p.status === 'coming_soon'
    ? '<span class="pd-soon">Prix à venir</span>'
    : `${fmt(p.price_xof)} <span class="pd-curr">FCFA</span>`;

  const stock = {
    in_stock: ['', 'En stock · Expédition 48 h'],
    low_stock: ['', 'Plus que quelques pots'],
    out_of_stock: ['is-out', 'Épuisé pour le moment'],
    coming_soon: ['is-out', 'Bientôt disponible']
  }[p.availability] || ['is-out', ''];
  $('pdStock').className = 'pd-stock ' + stock[0];
  $('pdStock').innerHTML = `<span class="dot" aria-hidden="true"></span>${stock[1]}`;

  const images = (p.images.length ? p.images : ['images/product-hero.jpg']).map(safeImg);
  const photo = $('pdMainPhoto');
  photo.src = images[0];
  photo.alt = p.name + ' NANYÉ';
  $('pdThumbs').innerHTML = images.length > 1 ? images.map((src, i) => `
    <button class="thumb${i === 0 ? ' active' : ''}" onclick="switchThumb(this,'${esc(src)}')" aria-label="Photo ${i + 1}" aria-pressed="${i === 0}">
      <img src="${esc(src)}" alt=""></button>`).join('') : '';

  const btn = $('pdAdd');
  const buyable = isBuyable(p);
  btn.disabled = !buyable && p.status !== 'coming_soon';
  btn.querySelector('span').textContent = buyable ? 'Ajouter au panier'
    : p.status === 'coming_soon' ? 'M\'avertir de la sortie' : (state.online ? 'Épuisé' : 'Boutique hors ligne');
  btn.onclick = p.status === 'coming_soon' ? () => showNotify(p.slug) : e => addToCart(e, p.slug, detailQty);
  document.querySelector('#pdBuy .qty-ctrl').hidden = !buyable;
}

function changeQty(delta) {
  const p = productBySlug(currentProductSlug);
  const max = p && p.max_quantity > 0 ? p.max_quantity : 1;
  detailQty = Math.min(max, Math.max(1, detailQty + delta));
  $('detailQty').textContent = detailQty;
}

function switchThumb(btn, src) {
  const mainPhoto = $('pdMainPhoto');
  if (mainPhoto) {
    mainPhoto.style.opacity = '0';
    setTimeout(() => { mainPhoto.src = src; mainPhoto.style.opacity = '1'; }, 150);
  }
  btn.closest('.pd-thumbs').querySelectorAll('.thumb').forEach(t => {
    t.classList.remove('active');
    t.setAttribute('aria-pressed', 'false');
  });
  btn.classList.add('active');
  btn.setAttribute('aria-pressed', 'true');
}

/* --- FILTERS ----------------------------------------------- */
function setFilter(btn) {
  document.querySelectorAll('.f-btn').forEach(b => {
    b.classList.remove('active');
    b.setAttribute('aria-pressed', 'false');
  });
  btn.classList.add('active');
  btn.setAttribute('aria-pressed', 'true');

  const filter = btn.dataset.filter || 'all';
  let shown = 0;
  document.querySelectorAll('#shopGrid .p-card').forEach(card => {
    const visible = filter === 'all' || card.dataset.cat === filter;
    card.hidden = !visible;
    if (visible) shown++;
  });
  $('shopGrid').hidden = shown === 0;
  $('shopEmpty').hidden = shown !== 0;
  $('shopCount').textContent = shown + (shown > 1 ? ' produits' : ' produit');
}

/* --- COMMANDE ---------------------------------------------- */
async function renderCheckout() {
  const lines = cartLines();
  $('checkoutEmpty').hidden = lines.length > 0;
  $('checkoutGrid').hidden = lines.length === 0;
  if (!lines.length) return;

  $('checkoutItems').innerHTML = lines.map(l => `
    <div class="summary-line"><span>${esc(l.product.name)} <span class="muted">× ${l.qty}</span></span><span>${fmt(l.product.price_xof * l.qty)} F</span></div>`).join('');
  const subtotal = cartSubtotal();
  const shipping = state.shop.shipping_fee_xof;
  $('sumSubtotal').textContent = fmt(subtotal) + ' FCFA';
  $('sumShipping').textContent = shipping === null ? '—' : shipping === 0 ? 'Offerte' : fmt(shipping) + ' FCFA';
  $('sumTotal').textContent = fmt(subtotal + (shipping || 0)) + ' FCFA';

  const notice = $('checkoutPayNotice');
  notice.hidden = state.shop.payment_enabled && !state.shop.payment_simulated;
  $('checkoutPayHint').hidden = !notice.hidden;
  notice.textContent = !state.online ? 'La boutique est momentanément hors ligne. Réessayez dans un instant.'
    : !state.shop.payment_enabled ? 'Le paiement en ligne n\'est pas encore ouvert : votre commande sera enregistrée et réservée, et nous vous recontacterons pour le règlement.'
    : 'Mode démonstration : le paiement est simulé, aucun montant ne sera débité.';
  $('checkoutSubmit').querySelector('span').textContent = state.shop.payment_enabled ? 'Valider et payer' : 'Valider la commande';

  if (!state.sessionChecked) await loadSession();
  if (!state.user) {
    storageSet(sessionStorage, 'nanye_after_login', 'checkout');
    $('authNotice').textContent = 'Connectez-vous ou créez un compte pour finaliser votre commande. Votre panier est conservé.';
    $('authNotice').hidden = false;
    showPage('account');
    return;
  }
  const u = state.user;
  const prefill = (id, v) => { if (!$(id).value && v) $(id).value = v; };
  prefill('shName', u.full_name);
  prefill('shPhone', u.phone);
  prefill('shAddress', u.default_address);
  prefill('shCity', u.default_city);
  if (u.default_country) $('shCountry').value = u.default_country;
  // Rien à mémoriser si l'adresse enregistrée est déjà celle du formulaire
  $('shRemember').closest('.check').hidden = !!(u.default_address && u.default_address === $('shAddress').value);
}

// Une clé d'idempotence par contenu de panier : un double clic ou un rechargement
// renvoie la même commande au lieu d'en créer une seconde.
function checkoutKey() {
  const signature = JSON.stringify(cart);
  let saved = null;
  try { saved = JSON.parse(storageGet(sessionStorage, 'nanye_checkout') || 'null'); } catch { saved = null; }
  if (saved && saved.signature === signature) return saved.key;
  const key = newKey();
  storageSet(sessionStorage, 'nanye_checkout', JSON.stringify({ signature, key }));
  return key;
}

async function submitCheckout(data) {
  if (!state.user) { showPage('account'); return; }
  const payload = {
    items: cart.map(i => ({ product_slug: i.slug, quantity: i.qty })),
    shipping: { name: data.name, phone: data.phone, address: data.address, city: data.city, country: data.country }
  };
  const order = await api('/orders', { method: 'POST', body: payload, headers: { 'Idempotency-Key': checkoutKey() } });
  if ($('shRemember').checked && !$('shRemember').closest('.check').hidden) {
    // Mémorisation facultative : un échec ici ne bloque pas la commande
    api('/account/profile', { method: 'PATCH', body: {
      phone: data.phone, default_address: data.address, default_city: data.city, default_country: data.country
    } }).then(u => { state.user = u; }).catch(() => {});
  }
  cart = [];
  saveCart();
  updateCartCount();
  storageSet(sessionStorage, 'nanye_checkout', null);
  if (state.shop.payment_enabled) {
    try {
      const pay = await api(`/orders/${encodeURIComponent(order.reference)}/pay`, { method: 'POST' });
      location.href = pay.payment_url;  // page de paiement du prestataire
      return;
    } catch (ex) {
      toast('Commande enregistrée', esc(ex.message));
    }
  }
  location.hash = 'order/' + order.reference;
}

/* --- SUIVI D'UNE COMMANDE ---------------------------------- */
let orderPoll = null;
async function renderOrder(reference, attempt = 0) {
  clearTimeout(orderPoll);
  const view = $('orderView');
  if (!reference) { view.innerHTML = '<p class="loading">Commande introuvable.</p>'; return; }
  if (!state.sessionChecked) await loadSession();
  if (!state.user) {
    storageSet(sessionStorage, 'nanye_after_login', 'account');
    $('authNotice').textContent = 'Connectez-vous pour consulter votre commande.';
    $('authNotice').hidden = false;
    showPage('account');
    return;
  }
  if (attempt === 0) view.innerHTML = '<p class="loading">Chargement…</p>';
  let order;
  try {
    const path = `/orders/${encodeURIComponent(reference)}`;
    order = await api(path);
    // Retour du prestataire : on demande au serveur de vérifier le paiement auprès de lui
    if (order.status === 'pending_payment' && attempt < 6) {
      order = await api(path + '/verify-payment', { method: 'POST' }).catch(() => order);
    }
  } catch (ex) {
    view.innerHTML = `<div class="empty-state"><p class="empty-title">${esc(ex.message)}</p><a class="btn-ghost" href="#account">Mes commandes</a></div>`;
    return;
  }
  if (parseHash().page !== 'order') return;
  view.innerHTML = orderDetail(order);
  if (order.status === 'pending_payment' && attempt < 5 && state.shop.payment_enabled) {
    orderPoll = setTimeout(() => renderOrder(reference, attempt + 1), 5000);
  }
}

function orderDetail(o) {
  const pending = o.status === 'pending_payment';
  const intro = {
    pending_payment: state.shop.payment_enabled
      ? 'Votre commande est réservée. Si vous venez de payer, la confirmation arrive en quelques secondes.'
      : 'Votre commande est enregistrée et réservée. Nous vous recontactons pour le règlement.',
    paid: 'Paiement confirmé. Merci ! Nous préparons votre colis.',
    preparing: 'Votre colis est en préparation.',
    shipped: 'Votre colis est en route.',
    delivered: 'Votre commande a été livrée. Bonne dégustation !',
    cancelled: 'Cette commande a été annulée. Les produits réservés ont été remis en vente.',
    refunded: 'Cette commande a été annulée et remboursée.'
  }[o.status] || '';
  const reached = ORDER_STEPS.findIndex(([s]) => s === o.status);
  const tracker = reached >= 0 ? `<ol class="order-steps" aria-label="Suivi de la commande">${ORDER_STEPS.map(([s, l], i) =>
    `<li class="${i < reached ? 'done' : i === reached ? 'current' : ''}"${i === reached ? ' aria-current="step"' : ''}><span class="os-dot" aria-hidden="true"></span><span class="os-label">${l}</span></li>`).join('')}</ol>` : '';
  const payment = `<p class="pay-line"><span class="muted">Paiement :</span> ${esc(PAYMENT_LABELS[o.payment_status] || o.payment_status)}${o.paid_at ? ` le ${fmtDate(o.paid_at)}` : ''}${o.payment_simulated ? ' <span class="muted">(démonstration, aucune somme débitée)</span>' : ''}</p>`;
  const expires = pending && o.expires_at
    ? `<p class="form-hint">Réservation valable jusqu'à ${parseDate(o.expires_at).toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' })}.</p>` : '';
  return `
    <header class="page-head order-head">
      <div class="eyebrow"><span class="eline"></span><span class="etxt">Commande ${esc(o.reference)}</span></div>
      <h1 class="page-title">${esc(STATUS_LABELS[o.status] || o.status)}</h1>
      <p class="page-lead">${intro}</p>${expires}
    </header>
    ${tracker}
    <section class="acc-card">
      <div class="acc-card-hdr"><h2 class="acc-card-title">Articles</h2><span class="o-status ${STATUS_CLASS[o.status] || ''}">${esc(STATUS_LABELS[o.status] || o.status)}</span></div>
      <div class="acc-card-body">
        ${o.items.map(i => `<div class="summary-line"><span>${esc(i.product_name)} <span class="muted">× ${i.quantity}</span></span><span>${fmt(i.line_total_xof)} F</span></div>`).join('')}
        <dl class="summary-rows">
          <div><dt>Sous-total</dt><dd>${fmt(o.subtotal_xof)} FCFA</dd></div>
          <div><dt>Livraison</dt><dd>${o.shipping_fee_xof ? fmt(o.shipping_fee_xof) + ' FCFA' : 'Offerte'}</dd></div>
          <div class="summary-total"><dt>Total</dt><dd>${fmt(o.total_xof)} FCFA</dd></div>
        </dl>
        ${payment}
        <p class="form-hint">Commandée le ${fmtDate(o.created_at)}</p>
      </div>
    </section>
    <section class="acc-card">
      <div class="acc-card-hdr"><h2 class="acc-card-title">Livraison</h2></div>
      <div class="acc-card-body"><p class="address">${esc(o.ship_name)}<br>${esc(o.ship_address)}<br>${esc(o.ship_city)}, ${esc(o.ship_country)}<br>${esc(o.ship_phone)}</p></div>
    </section>
    <div class="btn-row">
      ${pending && state.shop.payment_enabled ? `<button class="btn-primary" onclick="payOrder(this,'${esc(o.reference)}')">Payer ${fmt(o.total_xof)} FCFA</button>` : ''}
      ${pending ? `<button class="btn-ghost" onclick="cancelOrder(this,'${esc(o.reference)}')">Annuler la commande</button>` : ''}
      <a class="btn-ghost" href="#account">Toutes mes commandes</a>
    </div>`;
}

async function payOrder(btn, reference) {
  btn.disabled = true;
  try {
    const pay = await api(`/orders/${encodeURIComponent(reference)}/pay`, { method: 'POST' });
    location.href = pay.payment_url;
  } catch (ex) {
    toast('Paiement impossible', esc(ex.message));
    btn.disabled = false;
  }
}

async function cancelOrder(btn, reference) {
  if (!confirm('Annuler cette commande ? Les produits réservés seront remis en vente.')) return;
  btn.disabled = true;
  try {
    await api(`/orders/${encodeURIComponent(reference)}/cancel`, { method: 'POST' });
    toast('Commande annulée', '');
    renderOrder(reference);
  } catch (ex) {
    toast('Annulation impossible', esc(ex.message));
    btn.disabled = false;
  }
}

/* --- INIT -------------------------------------------------- */
document.addEventListener('DOMContentLoaded', () => {
  // Account nav
  document.querySelectorAll('.acc-nav a[data-acc]').forEach(a => {
    a.addEventListener('click', e => {
      e.preventDefault();
      if (a.dataset.acc === 'logout') logout();
      else showAccountView(a.dataset.acc);
    });
  });

  bindForm('loginForm', async d => {
    setSession(await api('/auth/login', { method: 'POST', body: { email: d.email, password: d.password } }));
    $('loginForm').reset();
    afterLogin();
  });
  bindForm('registerForm', async d => {
    setSession(await api('/auth/register', { method: 'POST', body: { email: d.email, password: d.password, full_name: d.full_name } }));
    $('registerForm').reset();
    toast('Bienvenue chez NANYÉ', 'Votre compte est créé.');
    afterLogin();
  });
  bindForm('forgotForm', async d => {
    const r = await api('/auth/password/forgot', { method: 'POST', body: { email: d.email } });
    return r.detail;
  });
  bindForm('resetForm', async (d, form) => {
    if (d.new_password !== d.confirm) throw new Error('Les deux mots de passe ne correspondent pas.');
    if (!resetToken) throw new Error('Lien invalide. Faites une nouvelle demande depuis « Mot de passe oublié ».');
    await api('/auth/password/reset', { method: 'POST', body: { token: resetToken, new_password: d.new_password } });
    resetToken = null;
    form.reset();
    setSession(null);
    toast('Mot de passe modifié', 'Connectez-vous avec votre nouveau mot de passe.');
    showPage('account');
  });
  bindForm('profileForm', async d => {
    // Champ vide = information effacée (null), jamais une chaîne vide invalide
    const opt = v => (v && v.trim()) || null;
    const body = {
      full_name: d.full_name, phone: opt(d.phone), default_address: opt(d.default_address),
      default_city: opt(d.default_city), default_country: opt(d.default_country)
    };
    state.user = await api('/account/profile', { method: 'PATCH', body });
    updateAuthUI();
    $('accName').textContent = state.user.full_name;
    $('accHello').textContent = 'Bonjour, ' + state.user.full_name.split(' ')[0];
    return 'Informations enregistrées.';
  });
  bindForm('passwordForm', async (d, form) => {
    await api('/auth/password/change', { method: 'POST', body: { current_password: d.current_password, new_password: d.new_password } });
    form.reset();
    return 'Mot de passe modifié. Vos autres appareils ont été déconnectés.';
  });
  bindForm('deleteForm', async d => {
    if (!confirm('Supprimer définitivement votre compte ?')) return '';
    await api('/account', { method: 'DELETE', body: { password: d.password } });
    setSession(null);
    toast('Compte supprimé', 'Vos données personnelles ont été effacées.');
    showPage('home');
  });
  bindForm('checkoutForm', submitCheckout);
  bindForm('notifyForm', async d => {
    await api('/notify', { method: 'POST', body: { email: d.email, product_slug: d.product_slug } });
    closeDialog('notifyDialog');
    const p = productBySlug(d.product_slug);
    showNotif(p ? p.name : 'ce produit', true);
  });
  // Escape closes the cart and the mobile menu
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') { closeCart(); closeMobileMenu(); }
  });

  // Nav: hairline + denser background once the page scrolls
  const nav = $('mainNav');
  const onScroll = () => nav.classList.toggle('scrolled', window.scrollY > 20);
  window.addEventListener('scroll', onScroll, { passive: true });
  onScroll();

  loadCart();
  updateCartCount();
  renderCatalog();
  renderCart();
  const { page, param } = parseHash();
  renderPage(pageExists(page) ? page : 'home', param);
  loadSession();
  loadCatalog();
});
