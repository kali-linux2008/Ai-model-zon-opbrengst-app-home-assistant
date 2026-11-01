'use strict';
// Bestel-app. Alle gegevens worden met textContent in de pagina gezet (nooit innerHTML),
// zodat tekst van gebruikers of de PLC geen code kan uitvoeren.

const PRODUCTEN = {1: ['Base blauw', '#185FA5'], 2: ['Lid blauw', '#85B7EB'], 11: ['Base groen', '#3B6D11'], 12: ['Lid groen', '#97C459']};
const STATUS = {
  nieuw: ['In wachtrij', '#80868C'], in_plc: ['In PLC', '#5A6068'], wachtend: ['Wachtend', '#80868C'],
  bezig: ['Bezig', '#2A71B8'], gepauzeerd: ['Gepauzeerd', '#B08C00'], gereed: ['Gereed', '#3B6D11'],
  annuleren: ['Wordt geannuleerd', '#B08C00'], geannuleerd: ['Geannuleerd', '#5A6068'], geweigerd: ['Geweigerd', '#C62828']
};
const PRIO = {1: 'Hoog', 2: 'Normaal', 3: 'Laag'};
const $ = id => document.getElementById(id);
let ik = null, csrf = null, soort = 1, timer = null;

function el(tag, props = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === 'style') Object.assign(e.style, v);
    else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
    else if (k in e) e[k] = v; else e.setAttribute(k, v);
  }
  for (const k of kids) if (k != null) e.append(k);
  return e;
}

async function api(pad, opties = {}) {
  const headers = {'Content-Type': 'application/json'};
  if (csrf) headers['X-CSRF-Token'] = csrf;
  const r = await fetch(pad, {credentials: 'same-origin', headers, ...opties});
  let data = {};
  try { data = await r.json(); } catch (e) { data = {ok: false, fout: 'onverwacht antwoord van de server'}; }
  if (r.status === 401 && pad !== '/api/login') { toonLogin(); }
  return {status: r.status, ...data};
}
const post = (pad, body) => api(pad, {method: 'POST', body: JSON.stringify(body || {})});

function melding(id, tekst, ok) { const m = $(id); m.textContent = tekst || ''; m.className = 'melding ' + (ok ? 'ok' : 'fout'); }
function toast(tekst, isFout) {
  const t = $('toast'); t.textContent = tekst; t.className = isFout ? 'fout' : ''; t.hidden = false;
  clearTimeout(toast.t); toast.t = setTimeout(() => { t.hidden = true; }, 3000);
}
const tijd = s => s ? new Date(s * 1000).toLocaleString('nl-NL', {day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'}) : '—';

/* ---------------- inloggen ---------------- */
function toonLogin() {
  ik = null; csrf = null; clearInterval(timer);
  $('app').hidden = true; $('login').hidden = false; $('uitloggen').hidden = true; $('plcPil').hidden = true;
}
async function start() {
  const r = await api('/api/ik');
  if (r.ok) ingelogd(r); else toonLogin();
}
function ingelogd(r) {
  ik = r.gebruiker; csrf = r.csrf;
  $('login').hidden = true; $('app').hidden = false; $('uitloggen').hidden = false; $('plcPil').hidden = false;
  $('plannerVelden').hidden = ik.rol === 'klant';
  $('aantal').max = ik.rol === 'klant' ? 50 : 999;
  $('accountInfo').textContent = `${ik.naam} · ${ik.rol}${ik.klantnaam ? ' · ' + ik.klantnaam : ''}`;
  bouwTabs(); ververs(); clearInterval(timer); timer = setInterval(ververs, 3000);
}
$('loginForm').addEventListener('submit', async e => {
  e.preventDefault();
  const d = Object.fromEntries(new FormData(e.target));
  const knop = e.target.querySelector('button'); knop.disabled = true;
  const r = await post('/api/login', d);
  knop.disabled = false;
  if (r.ok) { e.target.reset(); melding('loginMelding', ''); ingelogd(r); }
  else melding('loginMelding', r.fout);
});
$('uitloggen').addEventListener('click', async () => { await post('/api/logout'); toonLogin(); });

/* ---------------- tabs ---------------- */
let tab = 'bestellen';
function bouwTabs() {
  const tabs = [['bestellen', 'Bestellen'], ['orders', ik.rol === 'klant' ? 'Mijn orders' : 'Orders']];
  if (ik.rol === 'beheerder') tabs.push(['beheer', 'Beheer']);
  tabs.push(['account', 'Account']);
  $('tabs').replaceChildren(...tabs.map(([id, naam]) =>
    el('button', {type: 'button', role: 'tab', 'data-id': id, 'aria-selected': String(id === tab), onclick: () => kiesTab(id)}, naam)));
  kiesTab(tabs.some(t => t[0] === tab) ? tab : 'bestellen');
}
function kiesTab(id) {
  tab = id;
  document.querySelectorAll('.view').forEach(v => { v.hidden = v.id !== 'v-' + id; });
  [...$('tabs').children].forEach(b => b.setAttribute('aria-selected', String(b.dataset.id === id)));
  ververs();
}

/* ---------------- bestellen ---------------- */
function bouwProducten() {
  $('producten').replaceChildren(...Object.entries(PRODUCTEN).map(([s, [naam, kleur]]) =>
    el('button', {type: 'button', className: 'product', 'aria-pressed': String(+s === soort), onclick: () => { soort = +s; bouwProducten(); }},
      el('span', {className: 'blok', style: {background: kleur}}), naam)));
}
$('min').addEventListener('click', () => { const a = $('aantal'); a.value = Math.max(1, (+a.value || 1) - 1); });
$('plus').addEventListener('click', () => { const a = $('aantal'); a.value = Math.min(+a.max, (+a.value || 0) + 1); });
$('orderForm').addEventListener('submit', async e => {
  e.preventDefault();
  const d = Object.fromEntries(new FormData(e.target));
  const body = {soort, aantal: +d.aantal};
  if (ik.rol !== 'klant') { body.klant = d.klant; body.prio = +d.prio; if (d.nr) body.nr = d.nr; }
  const knop = e.target.querySelector('.hoofd'); knop.disabled = true;
  const r = await post('/api/orders', body);
  knop.disabled = false;
  if (r.ok) { melding('orderMelding', `Order ${r.nr} geplaatst`, true); toast(`Order ${r.nr} geplaatst`); e.target.nr && (e.target.nr.value = ''); }
  else melding('orderMelding', r.fout);
});

/* ---------------- orders ---------------- */
async function laadOrders() {
  const r = await api('/api/orders'); if (!r.ok) return;
  const lijst = $('orderLijst');
  if (!r.orders.length) { lijst.replaceChildren(el('p', {className: 'leeg'}, 'Nog geen orders.')); return; }
  lijst.replaceChildren(...r.orders.map(o => {
    const [stTekst, stKleur] = STATUS[o.status] || [o.status, '#80868C'];
    const [pNaam, pKleur] = PRODUCTEN[o.soort] || ['?', '#999'];
    const pc = Math.round(o.gepickt / o.aantal * 100);
    const kanAnnuleren = (ik.rol === 'klant' ? ['nieuw', 'in_plc', 'wachtend'] : ['nieuw', 'in_plc', 'wachtend', 'bezig', 'gepauzeerd']).includes(o.status);
    return el('article', {className: 'order', style: {borderLeftColor: stKleur}},
      el('div', {className: 'r1'},
        el('span', {className: 'nr'}, o.nr),
        el('span', {className: 'chip', style: {background: stKleur}}, stTekst.toUpperCase()),
        el('span', {className: 'spacer'}),
        kanAnnuleren ? el('button', {type: 'button', className: 'klein', onclick: () => annuleer(o.nr)}, 'Annuleren') : null),
      el('div', {className: 'r1'},
        el('span', {className: 'blok', style: {background: pKleur, width: '22px', height: '14px'}}),
        el('span', {}, `${o.aantal} × ${pNaam}`),
        el('span', {className: 'sub'}, ik.rol === 'klant' ? `· dock ${o.dock}` : `· ${o.klant} · dock ${o.dock} · ${PRIO[o.prio]}`)),
      el('div', {className: 'voortgang', title: `${o.gepickt} van ${o.aantal}`}, el('i', {style: {width: pc + '%', background: o.status === 'gereed' ? '#3B6D11' : ''}})),
      el('div', {className: 'sub'}, `${o.gepickt} van ${o.aantal} pallets · geplaatst ${tijd(o.aangemaakt)}${o.fout ? ' · ' + o.fout : ''}`));
  }));
}
async function annuleer(nr) {
  if (!confirm(`Order ${nr} annuleren?`)) return;
  const r = await post(`/api/orders/${encodeURIComponent(nr)}/annuleren`);
  toast(r.ok ? `Order ${nr} wordt geannuleerd` : r.fout, !r.ok); laadOrders();
}

/* ---------------- beheer ---------------- */
async function laadBeheer() {
  const [g, l] = await Promise.all([api('/api/gebruikers'), api('/api/logboek')]);
  if (g.ok) $('gebruikers').replaceChildren(...g.gebruikers.map(u => el('tr', {},
    el('td', {}, el('b', {}, u.naam)), el('td', {}, u.rol), el('td', {}, u.klantnaam || '—'), el('td', {}, tijd(u.laatst_ingelogd)),
    el('td', {}, u.actief ? 'actief' : 'geblokkeerd'),
    el('td', {}, u.naam === ik.naam ? '' : el('button', {type: 'button', className: 'klein', onclick: async () => {
      const r = await post(`/api/gebruikers/${u.id}/actief`, {actief: !u.actief}); toast(r.ok ? 'Opgeslagen' : r.fout, !r.ok); laadBeheer();
    }}, u.actief ? 'Blokkeren' : 'Vrijgeven')))));
  if (l.ok) $('logboek').replaceChildren(...l.logboek.map(x => el('li', {}, el('time', {}, tijd(x.tijd)), el('b', {}, x.wie), ' — ', x.actie)));
}
$('gebruikerForm').addEventListener('submit', async e => {
  e.preventDefault();
  const r = await post('/api/gebruikers', Object.fromEntries(new FormData(e.target)));
  if (r.ok) { e.target.reset(); melding('gebruikerMelding', 'Gebruiker aangemaakt', true); laadBeheer(); }
  else melding('gebruikerMelding', r.fout);
});

/* ---------------- account ---------------- */
$('wwForm').addEventListener('submit', async e => {
  e.preventDefault();
  const r = await post('/api/wachtwoord', Object.fromEntries(new FormData(e.target)));
  if (r.ok) { e.target.reset(); melding('wwMelding', 'Wachtwoord gewijzigd; andere sessies zijn uitgelogd', true); }
  else melding('wwMelding', r.fout);
});

/* ---------------- verversen ---------------- */
async function ververs() {
  if (!ik) return;
  const s = await api('/api/status');
  if (s.ok) {
    $('plcLed').style.background = s.plc_verbonden ? '#3FC24A' : '#D93636';
    $('plcTekst').textContent = s.plc_verbonden ? 'PLC online' : 'PLC offline';
  }
  if (tab === 'orders') laadOrders();
  if (tab === 'beheer') laadBeheer();
}

bouwProducten();
start();
