(() => {
  const _links = Reader.prototype.links;
  Reader.prototype.links = async function (i, page, vp) {
    await _links.call(this, i, page, vp);
    let an = []; try { an = await page.getAnnotations({ intent: 'display' }); } catch (e) {}
    const els = [...this.pg[i].querySelectorAll('.lnk a')];
    an.filter(a => a.subtype === 'Link' && a.rect && (a.url || a.dest)).forEach((a, k) => { if (els[k]) { els[k].__dest = a.dest; els[k].__page = i; } });
  };

})();

function makeCite(reader) {
  let R = null, refs = null, building = null, refStart = 0, builtKey = null, card = null, hideT = null, showT = null, pinned = false, curDest = null, backBtn = null;

  const DIA = { '˘': '̆', '¸': '̧', '´': '́', '`': '̀', 'ˆ': '̂', '˜': '̃', '¨': '̈', '˚': '̊', 'ˇ': '̌', '˝': '̋', '˛': '̨' };
  function fixAccents(t) {
    return t.replace(/([˘´`ˆ˜¨˚ˇ˝])\s?([A-Za-z])/g, (m, d, c) => c + DIA[d])
            .replace(/([A-Za-z])\s?([¸˛])/g, (m, c, d) => c + DIA[d]).normalize('NFC');
  }
  async function lines(i) {
    const tc = await R.text(i), rows = [];
    for (const it of tc.items) {
      if (!it.str || !it.str.trim()) continue;
      const y = it.transform[5], x = it.transform[4];
      let row = rows.find(r => Math.abs(r.y - y) < 2.2);
      if (!row) { row = { y, parts: [] }; rows.push(row); }
      row.parts.push({ x, s: it.str });
    }
    rows.sort((a, b) => b.y - a.y);
    return rows.map(r => ({ y: r.y, x: Math.min(...r.parts.map(p => p.x)), text: fixAccents(r.parts.sort((a, b) => a.x - b.x).map(p => p.s).join(' ').replace(/\s+/g, ' ').trim()) }));
  }

  const LABEL = /^\s*\[([^\]]{1,14})\]\s*(.*)$/, NUM = /^\s*(\d{1,3})\.\s+(.*)$/;
  async function build() {
    if (!R.pdf) return {};
    if (builtKey !== R.key) { refs = null; building = null; refStart = 0; builtKey = R.key; }
    if (refs) return refs;
    if (building) return building;
    building = (async () => {
      const map = {}, n = R.n;
      let start = null;
      for (let i = n; i >= Math.max(1, n - 12) && !start; i--) {
        const L = await lines(i);
        const k = L.findIndex(l => /^\s*(\d+\.?\s*)?(references|bibliography|références|bibliographie|literatur|literature cited)\s*$/i.test(l.text));
        if (k >= 0) { start = { page: i, idx: k + 1 }; refStart = i; }
      }
      const all = [];
      if (start) for (let i = start.page; i <= n; i++) { const L = await lines(i); all.push(...(i === start.page ? L.slice(start.idx) : L)); }
      const lab = all.filter(l => LABEL.test(l.text)).length, num = all.filter(l => NUM.test(l.text)).length;
      const re = lab >= num ? LABEL : NUM;
      let cur = null;
      for (const l of all) {
        const m = l.text.match(re);
        if (m) { cur = m[1].trim(); map[cur] = m[2]; }
        else if (cur && map[cur].length < 900) map[cur] += ' ' + l.text;
      }
      refs = map; return map;
    })();
    return building;
  }

  async function destPage(dest) {
    try { let d = dest; if (typeof d === 'string') d = await R.pdf.getDestination(d); if (!Array.isArray(d)) return 0;
      return ((typeof d[0] === 'object' && d[0]) ? await R.pdf.getPageIndex(d[0]) : d[0]) + 1; } catch (e) { return 0; }
  }
  async function fromDest(dest) {
    try {
      let d = dest; if (typeof d === 'string') d = await R.pdf.getDestination(d);
      if (!Array.isArray(d)) return '';
      const idx = (typeof d[0] === 'object' && d[0]) ? await R.pdf.getPageIndex(d[0]) : d[0];
      const top = d[1] && d[1].name === 'XYZ' ? d[3] : null;
      const L = await lines(idx + 1);
      let k = top == null ? 0 : L.findIndex(l => l.y <= top + 3);
      if (k < 0) k = 0;
      const out = [L[k] && L[k].text];
      for (let j = k + 1; j < L.length && out.length < 7; j++) {
        if (LABEL.test(L[j].text) || NUM.test(L[j].text)) break;
        out.push(L[j].text);
      }
      return out.filter(Boolean).join(' ');
    } catch (e) { return ''; }
  }

  const ESCH = s => String(s || '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  function show(x, y, items, pin) {
    if (!items.length) return;
    pinned = !!pin;
    card = document.getElementById('vlCite');
    if (!card) { card = document.createElement('div'); card.id = 'vlCite'; }
    const host = document.fullscreenElement || document.body;
    if (card.parentNode !== host) host.appendChild(card);
    card.onmouseenter = () => clearTimeout(hideT); card.onmouseleave = () => { if (!pinned) hide(250); };
    card.innerHTML = items.map(({ label, text }) => {
      const t = text.replace(/\s+/g, ' ').trim();
      const ax = (t.match(/arXiv[:\s]*([0-9]{4}\.[0-9]{4,5}|[a-z\-]+\/[0-9]{7})/i) || [])[1];
      const doi = (t.match(/\b(10\.\d{4,9}\/[^\s,;]+[^\s,;.])/) || [])[1];
      const q = encodeURIComponent(t.replace(/^[^A-Za-zÀ-ÿ]*/, '').slice(0, 220));
      return `<div class="vc-ref"><div class="vc-l">${label ? '[' + ESCH(label) + ']' : '↳'}</div><div class="vc-t">${ESCH(t)}</div>
        <div class="vc-a"><a class="vc-open" target="_blank" href="${doi ? 'https://doi.org/' + ESCH(doi) : ax ? 'https://arxiv.org/abs/' + ESCH(ax) : 'https://scholar.google.com/scholar?q=' + q}">Open the paper ↗</a>
        ${ax ? `<a href="#" data-read="${ESCH(ax)}">Read in VanoLib</a>` : ''}
        <a target="_blank" href="https://scholar.google.com/scholar?q=${q}">Google Scholar</a>
        ${ax ? `<a target="_blank" href="https://arxiv.org/abs/${ESCH(ax)}">arXiv:${ESCH(ax)}</a>` : `<a target="_blank" href="https://arxiv.org/a/?searchtype=all&query=${q}">arXiv</a>`.replace('https://arxiv.org/a/?','https://arxiv.org/search/?')}
        ${doi ? `<a target="_blank" href="https://doi.org/${ESCH(doi)}">DOI</a>` : ''}
        <a href="#" data-copy="${ESCH(t)}">Copy</a>
        <a href="#" data-bib="${ESCH(t)}" data-doi="${ESCH(doi || '')}" data-ax="${ESCH(ax || '')}">BibTeX</a></div></div>`;
    }).join('');
    card.querySelectorAll('[data-copy]').forEach(a => a.onclick = e => { e.preventDefault(); navigator.clipboard.writeText(a.dataset.copy); a.textContent = 'Copied ✓'; });
    card.querySelectorAll('[data-bib]').forEach(a => a.onclick = e => { e.preventDefault(); Cite.bib({ text: a.dataset.bib, doi: a.dataset.doi, arxiv: a.dataset.ax }, a); });
    card.querySelectorAll('[data-read]').forEach(a => a.onclick = e => { e.preventDefault(); window.open((typeof VL !== 'undefined' ? VL.viewer('https://arxiv.org/pdf/' + a.dataset.read) : 'https://arxiv.org/pdf/' + a.dataset.read), '_blank'); });
    if (pin) {
      const bar = document.createElement('div'); bar.className = 'vc-bar';
      bar.innerHTML = (curDest ? '<a href="#" class="vc-go">Go to the reference list ↓</a>' : '<span></span>') + '<a href="#" class="vc-x">Close ✕</a>';
      card.appendChild(bar);
      const go = bar.querySelector('.vc-go');
      if (go) go.onclick = e => { e.preventDefault(); const d = curDest; hide(0); jumpWithBack(d); };
      bar.querySelector('.vc-x').onclick = e => { e.preventDefault(); hide(0); };
    }
    card.style.display = 'block';
    const w = Math.min(460, innerWidth - 20); card.style.width = w + 'px';
    const h = card.offsetHeight;
    card.style.left = Math.max(10, Math.min(x - w / 2, innerWidth - w - 10)) + 'px';
    card.style.top = (y + 18 + h < innerHeight ? y + 18 : Math.max(10, y - h - 12)) + 'px';
  }
  function hide(ms) { clearTimeout(hideT); hideT = setTimeout(() => { if (card) card.style.display = 'none'; pinned = false; }, ms || 0); }

  function jumpWithBack(dest) {
    const v = R.view, top = v.scrollTop, left = v.scrollLeft;
    R.dest(dest);
    backBtn = document.getElementById('vlBack');
    if (!backBtn) { backBtn = document.createElement('button'); backBtn.id = 'vlBack'; }
    const host = document.fullscreenElement || document.body;
    if (backBtn.parentNode !== host) host.appendChild(backBtn);
    backBtn.textContent = '↩ Back to where I was reading';
    backBtn.style.display = 'block';
    backBtn.onclick = () => { v.scrollTo({ top, left, behavior: 'smooth' }); backBtn.style.display = 'none'; };
    clearTimeout(backBtn._t); backBtn._t = setTimeout(() => { backBtn.style.display = 'none'; }, 60000);
  }

  async function itemsForLink(a) {
    const map = await build();
    const dp = await destPage(a.__dest);
    if (refStart && dp && dp < refStart) return [];
    const pg = a.closest('.pg'), rr = a.getBoundingClientRect();
    let txt = '';
    pg.querySelectorAll('.textLayer span').forEach(s => {
      const b = s.getBoundingClientRect(), t = s.textContent;
      if (!(b.right > rr.left && b.left < rr.right && b.bottom > rr.top && b.top < rr.bottom) || !t) return;
      const i0 = Math.max(0, Math.floor((rr.left - b.left) / b.width * t.length) - 1);
      const i1 = Math.min(t.length, Math.ceil((rr.right - b.left) / b.width * t.length) + 1);
      txt += t.slice(i0, i1);
    });
    const labs = (txt.match(/[A-Za-z]*\d+[a-z]?/g) || []).filter(l => map[l]);
    if (labs.length) return [{ label: labs[0], text: map[labs[0]] }];
    const lab = (txt.match(/[A-Za-z]*\d+[a-z]?/) || [])[0];
    const t = await fromDest(a.__dest);
    if (t && !/^(\d+(\.\d+)*\s+)?[A-Z][a-z]+ \d/.test(t) && looksLikeRef(t)) return [{ label: lab || '', text: t }];
    return [];
  }
  function looksLikeRef(t) {
    return /(19|20)\d\d/.test(t) && (/[A-Z][a-z]+,?\s+[A-Z]\./.test(t) || /arXiv|doi|Journal|J\.|Ann\.|Math|Press|Proc|vol\.|pp\./i.test(t)) && !/^(Theorem|Lemma|Proposition|Definition|Corollary|Remark|Example|Section|Figure|Table)\b/i.test(t);
  }

  async function onClick(e) {
    const a = e.target.closest && e.target.closest('.lnk a');
    if (a && a.__dest) {
      e.preventDefault(); e.stopPropagation();
      const x = e.clientX, y = e.clientY;
      clearTimeout(showT);
      const items = await itemsForLink(a);
      if (items.length) { curDest = a.__dest; show(x, y, items, true); }
      else R.dest(a.__dest);
      return;
    }
    const span = e.target.closest && e.target.closest('.textLayer span');
    if (span && getSelection().isCollapsed && !(R.rdr.dataset.tool)) {
      const map = await build();
      const labs = labelsIn(span.textContent).filter(l => map[l]).slice(0, 4);
      if (labs.length) { curDest = null; show(e.clientX, e.clientY, labs.map(l => ({ label: l, text: map[l] })), true); }
    }
  }

  function labelsIn(text) {
    const out = [];
    for (const m of text.matchAll(/\[([^\]]{1,40})\]/g)) {
      for (const part of m[1].split(/[,;]/)) {
        const p = part.trim(), r = p.match(/^(\d+)\s*[–-]\s*(\d+)$/);
        if (r) { for (let k = +r[1]; k <= +r[2] && k - r[1] < 12; k++) out.push(String(k)); } else if (p) out.push(p);
      }
    }
    return out;
  }

  async function onOver(e) {
    const a = e.target.closest && e.target.closest('.lnk a');
    const span = !a && e.target.closest && e.target.closest('.textLayer span');
    if (!a && !span) return;
    clearTimeout(showT); clearTimeout(hideT);
    const x = e.clientX, y = e.clientY;
    showT = setTimeout(async () => {
      const map = await build();
      if (pinned) return;
      if (a && a.__dest) { curDest = null; const it = await itemsForLink(a); if (it.length) show(x, y, it); return; }
      if (span) {
        if (pinned) return;
        const labs = labelsIn(span.textContent).filter(l => map[l]).slice(0, 4);
        if (labs.length) show(x, y, labs.map(l => ({ label: l, text: map[l] })));
      }
    }, 280);
  }
  function onOut(e) {
    if (pinned) return;
    if (e.target.closest && (e.target.closest('.lnk a') || e.target.closest('.textLayer span'))) { clearTimeout(showT); hide(350); }
  }

  (function init() {
      R = reader; refs = null; building = null; refStart = 0;
      reader.view.addEventListener('mouseover', onOver);
      reader.view.addEventListener('mouseout', onOut);
      reader.view.addEventListener('click', onClick, true);
      document.addEventListener('mousedown', e => { if (pinned && card && !card.contains(e.target) && !(e.target.closest && e.target.closest('.lnk a'))) hide(0); });
      document.addEventListener('keydown', e => { if (e.key === 'Escape' && card && card.style.display === 'block') hide(0); });
      reader.view.addEventListener('scroll', () => { if (!pinned) hide(0); }, { passive: true });
      document.addEventListener('fullscreenchange', () => hide(0));
      setTimeout(build, 1500);
  })();
}
const Cite = { attach: reader => { if (reader.__cite) return; reader.__cite = true; makeCite(reader); } };
if (typeof READERS !== 'undefined') READERS.forEach(r => Cite.attach(r));


Cite.bibFetch = async function ({ text, doi, arxiv }) {
  const T = (u, o) => fetch(u, o).then(r => { if (!r.ok) throw new Error(r.status); return r.text(); });
  const clean = x => x.trim().replace(/^\s+@/m, '@');
  const byDoi = async d => {
    try { return clean(await T('https://api.crossref.org/works/' + encodeURIComponent(d) + '/transform/application/x-bibtex')); } catch (e) {}
    return clean(await T('https://doi.org/' + d, { headers: { Accept: 'application/x-bibtex' } }));
  };
  if (doi) { try { return await byDoi(doi); } catch (e) {} }
  if (arxiv) {
    try { return await byDoi('10.48550/arXiv.' + arxiv); } catch (e) {}
    const x = await T('https://export.arxiv.org/api/query?id_list=' + encodeURIComponent(arxiv));
    const d = new DOMParser().parseFromString(x, 'text/xml'), en = d.querySelector('entry');
    if (en) {
      const g = k => (en.querySelector(k) || {}).textContent || '';
      const au = [...en.querySelectorAll('author > name')].map(n => n.textContent.trim());
      const yr = g('published').slice(0, 4), ln = (au[0] || 'anon').split(' ').pop().toLowerCase().replace(/[^a-z]/g, '');
      return `@misc{${ln}${yr},\n  title = {${g('title').replace(/\s+/g, ' ').trim()}},\n  author = {${au.join(' and ')}},\n  year = {${yr}},\n  eprint = {${arxiv}},\n  archivePrefix = {arXiv},\n  url = {https://arxiv.org/abs/${arxiv}}\n}`;
    }
  }
  const q = (text || '').replace(/\s+/g, ' ').replace(/^[^A-Za-zÀ-ÿ]*/, '').slice(0, 300);
  if (!q) throw new Error('nothing to search');
  const j = JSON.parse(await T('https://api.crossref.org/works?rows=1&select=DOI,title&query.bibliographic=' + encodeURIComponent(q)));
  const it = j.message && j.message.items && j.message.items[0];
  if (!it) throw new Error('no match found');
  return await byDoi(it.DOI);
};
Cite.bib = async function (src, btn) {
  const old = btn && btn.textContent; if (btn) btn.textContent = 'BibTeX…';
  let out, err;
  try { out = await Cite.bibFetch(src); } catch (e) { err = e; }
  if (btn) btn.textContent = old;
  let box = document.getElementById('vlBib');
  if (!box) { box = document.createElement('div'); box.id = 'vlBib'; }
  const host = document.fullscreenElement || document.body;
  if (box.parentNode !== host) host.appendChild(box);
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  box.innerHTML = out
    ? `<b>BibTeX</b><textarea readonly>${esc(out)}</textarea><div><button class="ok">Copy</button><button class="x">Close</button></div>`
    : `<b>BibTeX</b><p>No BibTeX found (${esc(err && err.message || 'offline?')}). Try Google Scholar → Cite → BibTeX.</p><div><button class="x">Close</button></div>`;
  box.style.display = 'block';
  const ta = box.querySelector('textarea'), ok = box.querySelector('.ok');
  if (ta) { ta.select(); try { await navigator.clipboard.writeText(out); ok.textContent = 'Copied ✓'; } catch (e) {} ok.onclick = () => { navigator.clipboard.writeText(out); ok.textContent = 'Copied ✓'; }; }
  box.querySelector('.x').onclick = () => { box.style.display = 'none'; };
};
Cite.paper = async function (R, fileUrl, btn) {
  let doi = '', arxiv = '', text = R.title || '';
  const u = decodeURIComponent(fileUrl || '');
  arxiv = (u.match(/arxiv\.org\/(?:pdf|abs)\/([0-9]{4}\.[0-9]{4,5}|[a-z\-]+\/[0-9]{7})/i) || [])[1] || '';
  doi = (u.match(/\b(10\.\d{4,9}\/[^\s?#&]+)/) || [])[1] || '';
  try {
    const md = await R.pdf.getMetadata();
    const sub = ((md.info && (md.info.doi || md.info.Subject)) || '') + ' ' + ((md.metadata && md.metadata.get && md.metadata.get('prism:doi')) || '');
    doi = doi || (sub.match(/\b(10\.\d{4,9}\/[^\s,;]+[^\s,;.])/) || [])[1] || '';
    for (let i = 1; !doi && !arxiv && i <= Math.min(2, R.pdf.numPages); i++) {
      const tx = (await (await R.pdf.getPage(i)).getTextContent()).items.map(x => x.str).join(' ');
      doi = (tx.match(/\b(10\.\d{4,9}\/[^\s,;]+[^\s,;.])/) || [])[1] || '';
      arxiv = arxiv || (tx.match(/arXiv[:\s]*([0-9]{4}\.[0-9]{4,5}(?:v\d+)?)/i) || [])[1] || '';
    }
    if (arxiv) arxiv = arxiv.replace(/v\d+$/, '');
  } catch (e) {}
  return Cite.bib({ text, doi, arxiv }, btn);
};
