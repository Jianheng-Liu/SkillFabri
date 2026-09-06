/* Fills the published landing page with live data.
 *
 * The page itself is the Figma build, untouched apart from its static copy. Everything that is a
 * number or a skill is written in here instead of into the bundle, so the corpus can grow without
 * anyone re-editing React. Selectors go by structure and text, never by Tailwind hashes. */
(function () {
  var A = '#2847E0', T = '#14B8A6', G = '#8B8B8B';
  var fmt = function (n) { return n >= 1000 ? (n / 1000).toFixed(n >= 10000 ? 0 : 1).replace(/\.0$/, '') + 'k' : String(n); };
  var esc = function (s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]; }); };
  var get = function (u) { return fetch(u).then(function (r) { return r.json(); }); };

  /* icons come from /icons.js so the landing page and the explorer share one set */
  var ICON = window.SF_ICONS || {}, FALLBACK = window.SF_ICON_FALLBACK || '';
  var ACTCOL = window.SF_ACTCOL || {}, ACTFALL = window.SF_ACTCOL_FALLBACK || '#8a8f9c';
  var actCol = function (a) { return ACTCOL[a] || ACTFALL; };
  var actIcon = function (a, px) {
    return '<svg viewBox="0 0 20 20" fill="none" width="' + (px || 18) + '" height="' + (px || 18) + '">' +
      (ICON[a] || FALLBACK) + '</svg>';
  };

  var GH_MARK='<svg viewBox="0 0 16 16" width="15" height="15" fill="currentColor" aria-hidden="true">'+
    '<path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 '+
    '0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 '+
    '1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 '+
    '0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82a7.42 7.42 0 0 1 2-.27c.68 0 1.36.09 '+
    '2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 '+
    '3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8Z"/></svg>';

  function byText(sel, re) {
    return Array.prototype.filter.call(document.querySelectorAll(sel), function (e) { return re.test((e.textContent || '').trim()); });
  }

  function hydrate(o) {
    /* hero stats */
    var stats = [[o.n_skills.toLocaleString(), 'Skills'], [o.dup_pct + '%', 'Near-duplicate'], [fmt(o.intersect), 'Typed relations']];
    var holder = byText('div', /^—Skills—Near-duplicate—Typed relations$/)[0];
    if (holder) Array.prototype.forEach.call(holder.children, function (c, i) {
      if (stats[i] && c.firstChild) c.firstChild.textContent = stats[i][0];
    });

    /* relation legend: real edge counts under each label */
    var legend = { 'Contains': o.contain, 'Same As': o.same, 'Intersects': o.intersect };
    Object.keys(legend).forEach(function (k) {
      var row = byText('div', new RegExp('^' + k + '$'))[0];
      if (row && row.nextElementSibling) row.nextElementSibling.textContent =
        row.nextElementSibling.textContent.replace(/\s*·.*$/, '') + ' · ' + legend[k].toLocaleString() + ' edges';
    });



  /* categories → the eight biggest activities, then everything else in Browse */
    var grid = document.querySelector('.grid.grid-cols-2');
    if (grid && o.activities) {
      grid.innerHTML = o.activities.slice(0, 8).map(function (a) {
        var c = actCol(a.name);
        return '<div class="card-hover rounded-xl p-4 cursor-pointer" data-act="' + esc(a.name) + '" ' +
          'style="background:var(--sf-card);border:1px solid var(--sf-border);box-shadow:0 1px 3px rgba(0,0,0,0.04)">' +
          '<div style="color:' + c + ';margin-bottom:10px">' + actIcon(a.name) + '</div>' +
          '<div style="font-weight:600;font-size:13px;color:var(--sf-ink);margin-bottom:3px">' + esc(a.name) + '</div>' +
          '<div style="font-family:\'JetBrains Mono\',monospace;font-size:9.5px;color:var(--sf-ink3);letter-spacing:.04em">' +
          a.count.toLocaleString() + ' skills</div></div>';
      }).join('') +
        '<div class="card-hover rounded-xl p-4 cursor-pointer" data-act="" style="background:transparent;border:1px dashed var(--sf-borderS);' +
        'display:flex;align-items:center;justify-content:center;font-size:12.5px;color:var(--sf-ink2)">All ' +
        o.activities.length + ' activities →</div>';
      grid.querySelectorAll('[data-act]').forEach(function (el) {
        el.onclick = function () { location.href = '/app#browse' + (el.dataset.act ? '=' + encodeURIComponent(el.dataset.act) : ''); };
      });
    }
  }

  /* featured cards — real skills, under whichever lens the chips select */
  var LENS = { trending: 'pop', duplicated: 'dups', connected: 'shared', newest: 'updated' };
  var EXTRA = { duplicated: '&has=dups' };
  function card(s) {
    var rel = s.rel || {}, badges = '';
    if (rel.same) badges += '<span style="color:' + G + '">≡ ' + rel.same + '</span>';
    if (rel.intersect) badges += '<span style="color:' + T + '">⊓ ' + rel.intersect + '</span>';
    if (rel.contain) badges += '<span style="color:' + A + '">⊑ ' + rel.contain + '</span>';
    var isInstall = s.pop_src === 'clawhub_downloads';
    return '<div class="card-hover rounded-2xl p-5 cursor-pointer" data-id="' + s.id + '" ' +
      'style="background:var(--sf-card);border:1px solid var(--sf-border);box-shadow:0 1px 4px rgba(0,0,0,0.04)">' +
      '<div style="display:flex;align-items:flex-start;gap:12px;margin-bottom:10px">' +
        '<div style="width:38px;height:38px;border-radius:10px;background:' + actCol(s.activity) + '14;' +
        'border:1px solid ' + actCol(s.activity) + '26;display:flex;align-items:center;justify-content:center;' +
        'flex-shrink:0;color:' + actCol(s.activity) + '">' + actIcon(s.activity) + '</div>' +
        '<div style="min-width:0"><div style="font-weight:600;font-size:14px;color:var(--sf-ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' +
        esc(s.name) + '</div><span style="font-family:\'JetBrains Mono\',monospace;font-size:9px;letter-spacing:.1em;color:' + actCol(s.activity) + ';text-transform:uppercase">' +
        esc(s.activity) + '</span></div></div>' +
      '<p style="font-size:12.5px;line-height:1.65;color:var(--sf-ink2);margin:0 0 .9rem;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden">' +
        esc(s.summary) + '</p>' +
      '<div style="display:flex;align-items:center;justify-content:space-between;font-family:\'JetBrains Mono\',monospace;font-size:10px;color:var(--sf-ink3)">' +
        '<span title="' + (isInstall ? 'installs of this skill' : 'stars of the repository this skill lives in') + '">' +
        (isInstall ? '⤓ ' : '★ ') + (s.pop || 0).toLocaleString() + '</span>' +
        '<span style="display:flex;gap:10px">' + badges + '</span></div>' +
      '<div style="font-family:\'JetBrains Mono\',monospace;font-size:9px;color:var(--sf-ink3);margin-top:8px">' +
        esc(s.author) + (s.updated ? ' · ' + s.updated.slice(0, 7) : '') + '</div></div>';
  }

  function loadFeatured(lens) {
    var grid = document.querySelector('.grid.grid-cols-1.sm\\:grid-cols-2.lg\\:grid-cols-3') ||
               document.querySelectorAll('.grid')[1];
    if (!grid) return;
    get('/api/list?sort=' + (LENS[lens] || 'pop') + (EXTRA[lens] || '')).then(function (d) {
      grid.innerHTML = (d.items || []).slice(0, 6).map(card).join('');
      grid.querySelectorAll('[data-id]').forEach(function (el) {
        el.onclick = function () { location.href = '/app#graph=' + el.dataset.id; };
      });
    });
  }

  function wireChips() {
    var chips = byText('button', /^(trending|newest)$/);
    chips.forEach(function (c) {
      c.addEventListener('click', function () { loadFeatured(c.textContent.trim()); }, true);
    });
    return chips.length > 0;
  }



  /* ── drag, layered on top of the design's own rendering ───────────────────
   * The section graph stays exactly as designed — glow, hover dimming, the two-tier
   * labels. This only adds movement: each node keeps an offset, and after every React
   * update (hover re-renders constantly) the offsets are re-applied, because React
   * rewrites cx/cy and the line endpoints from its own static data every time. */
  function clamp(v, lo, hi) { return v < lo ? lo : v > hi ? hi : v; }

  function makeDraggable() {
    var svg = document.querySelector('svg[viewBox="0 0 780 530"]');
    if (!svg) return false;
    var groups = [].slice.call(svg.querySelectorAll('g')).filter(function (g) {
      return g.querySelector('circle') && g.querySelector('text');
    });
    if (groups.length < 3) return false;

    var nodes = groups.map(function (g) {
      var body = g.querySelectorAll('circle')[1] || g.querySelector('circle');
      return { g: g, x0: +body.getAttribute('cx'), y0: +body.getAttribute('cy'),
               r: +body.getAttribute('r'), dx: 0, dy: 0 };
    });
    var lines = [].slice.call(svg.querySelectorAll('line'));
    // an edge is matched to its endpoints by where React drew it: the clipped ends sit on the
    // node circles, so the nearest centre on each side identifies the pair
    lines.forEach(function (l) {
      var p = ['x1', 'y1', 'x2', 'y2'].map(function (k) { return +l.getAttribute(k); });
      l._a = nearest(p[0], p[1]); l._b = nearest(p[2], p[3]);
      l._gap = Math.hypot(p[2] - l._b.x0, p[3] - l._b.y0);   // preserve the arrowhead's gap
    });
    function nearest(x, y) {
      var best = nodes[0], bd = Infinity;
      nodes.forEach(function (n) {
        var d = Math.hypot(n.x0 - x, n.y0 - y);
        if (d < bd) { bd = d; best = n; }
      });
      return best;
    }

    var obs = null;
    function watch(on) {
      if (!obs) return;
      if (on) obs.observe(svg, { attributes: true, subtree: true, childList: true });
      else obs.disconnect();
    }
    function setAttr(el, k, v) {                 // only touch the DOM when the value really moves
      v = String(Math.round(v * 100) / 100);
      if (el.getAttribute(k) !== v) el.setAttribute(k, v);
    }
    function apply() {
      // a MutationObserver callback is a microtask, so a plain re-entrancy flag is already
      // cleared by the time it fires — the observer has to be off while we write, or it
      // retriggers itself forever and starves the page
      watch(false);
      nodes.forEach(function (n) {
        var t = n.g.style.transform || '';
        var scale = (t.match(/scale\([^)]*\)/) || ['scale(1)'])[0];
        var want = 'translate(' + n.dx + 'px,' + n.dy + 'px) ' + scale;
        if (t !== want) n.g.style.transform = want;
      });
      lines.forEach(function (l) {
        var a = l._a, b = l._b;
        var ax = a.x0 + a.dx, ay = a.y0 + a.dy, bx = b.x0 + b.dx, by = b.y0 + b.dy;
        var vx = bx - ax, vy = by - ay, L = Math.hypot(vx, vy) || 1;
        setAttr(l, 'x1', ax + vx / L * a.r); setAttr(l, 'y1', ay + vy / L * a.r);
        setAttr(l, 'x2', bx - vx / L * l._gap); setAttr(l, 'y2', by - vy / L * l._gap);
      });
      watch(true);
    }

    var cur = null;
    nodes.forEach(function (n) {
      n.g.style.cursor = 'grab';
      n.g.addEventListener('pointerdown', function (ev) {
        ev.preventDefault();
        var r = svg.getBoundingClientRect();
        cur = { n: n, sx: (ev.clientX - r.left) / r.width * 780 - n.dx,
                       sy: (ev.clientY - r.top) / r.height * 530 - n.dy };
        n.g.setPointerCapture(ev.pointerId); n.g.style.cursor = 'grabbing';
      });
      n.g.addEventListener('pointermove', function (ev) {
        if (!cur || cur.n !== n) return;
        var r = svg.getBoundingClientRect();
        var dx = (ev.clientX - r.left) / r.width * 780 - cur.sx;
        var dy = (ev.clientY - r.top) / r.height * 530 - cur.sy;
        // Keep the node inside the viewBox. Its own radius is the margin on three sides; the
        // bottom needs more because the label sits under the circle and would clip first.
        var pad = n.r + 3;
        n.dx = clamp(n.x0 + dx, pad, 780 - pad) - n.x0;
        n.dy = clamp(n.y0 + dy, pad, 530 - pad - 14) - n.y0;
        apply();
      });
      ['pointerup', 'pointercancel'].forEach(function (t) {
        n.g.addEventListener(t, function () { cur = null; n.g.style.cursor = 'grab'; });
      });
    });

    // React redraws on every hover, wiping the offsets — put them back each time
    // The hint belongs under the graph. The graph card is a direct child of the two-column grid,
    // so appending the hint as a sibling makes it a third grid item and it wraps to column one —
    // it has to share the card's cell instead.
    var hint = byText('p', /drag any node/)[0];
    var box = svg.closest('.rounded-2xl') || svg.parentNode;
    if (hint && box && box.parentNode && !box.parentNode.dataset.sfWrapped) {
      var wrap = document.createElement('div');
      wrap.dataset.sfWrapped = '1';
      box.parentNode.insertBefore(wrap, box);
      wrap.appendChild(box);
      hint.style.margin = '14px 0 0';
      hint.style.textAlign = 'center';
      wrap.appendChild(hint);
    }

    obs = new MutationObserver(function () { apply(); });
    watch(true);
    apply();
    return true;
  }

  /* React renders after this script parses, so wait for the page to exist */
  var tries = 0;
  (function boot() {
    if (++tries > 60) return;
    if (!document.querySelector('.grid') || !byText('button', /^trending$/).length) return setTimeout(boot, 100);
    get('/api/overview').then(hydrate).catch(function () {});
    // the toggle lives outside React so a re-render cannot drop it
    if(!document.querySelector('.sf-theme')){
      var tb=document.createElement('button');tb.className='sf-theme';tb.title='Light / dark';
      var cur=function(){return document.documentElement.getAttribute('data-theme')||'dark';};
      tb.textContent=cur()==='dark'?'☾':'☀';
      tb.onclick=function(){var t=cur()==='dark'?'light':'dark';
        document.documentElement.setAttribute('data-theme',t);
        try{localStorage.setItem('sf-theme',t);}catch(e){}
        location.reload();};   // the bundle read its palette once, at load
      var gh=document.createElement('a');gh.className='sf-gh';gh.title='Source on GitHub';
      gh.href='https://github.com/Jianheng-Liu/SkillFabri';gh.target='_blank';gh.rel='noopener';
      gh.innerHTML=GH_MARK;
      // the pair hangs off <body>, not off the nav: the nav is React-owned and the next render
      // discards anything appended into it. .sf-tools re-creates the header's own container
      // geometry so they line up with the wordmark rather than with the viewport edge.
      // The four nav links do not fit beside the wordmark on a phone. Below 680px the row hides
      // and this opens in its place. The items are real <a>s carrying the same labels, so the
      // page's existing click router resolves them — no second copy of the routing table.
      var mb=document.createElement('button');mb.className='sf-burger';mb.setAttribute('aria-label','Menu');
      mb.setAttribute('aria-expanded','false');
      mb.innerHTML='<svg width="17" height="17" viewBox="0 0 20 20" fill="none" stroke="currentColor"'+
        ' stroke-width="1.9" stroke-linecap="round"><path d="M3 5.5h14M3 10h14M3 14.5h14"/></svg>';
      var menu=document.createElement('div');menu.className='sf-menu';
      menu.innerHTML=['Browse','Graph','Add My Skill','My Skills']
        .map(function(t){return '<a href="#">'+t+'</a>';}).join('');
      var shut=function(){menu.classList.remove('open');mb.setAttribute('aria-expanded','false');};
      mb.onclick=function(e){e.stopPropagation();
        var on=!menu.classList.contains('open');
        menu.classList.toggle('open',on);mb.setAttribute('aria-expanded',String(on));};
      menu.addEventListener('click',shut);          // the router handles the navigation itself
      document.addEventListener('click',function(e){if(!menu.contains(e.target)&&e.target!==mb)shut();});
      document.addEventListener('keydown',function(e){if(e.key==='Escape')shut();});

      // The account only does anything inside the explorer — favourites and My Skills — so the
      // landing page shows its state and hands the actual sign-in over rather than carrying a
      // second copy of the Google integration and its dialog.
      var who=document.createElement('span');who.className='sf-who';
      fetch('/api/me').then(function(r){return r.json();}).then(function(m){
        if(!m||!m.login)return;                      // sign-in not configured: show nothing
        if(m.user){
          who.innerHTML='<a class="sf-avatar" href="/app#mine" title="'+esc(m.user.email||'')+'">'+
            (m.user.picture?'<img src="'+esc(m.user.picture)+'" alt="">'
                           :'<span>'+esc((m.user.name||'?')[0])+'</span>')+'</a>';
        } else {
          who.innerHTML='<a class="sf-signin" href="/app#signin">Sign in</a>';
        }
      }).catch(function(){});

      var tools=document.createElement('div');tools.className='sf-tools';
      tools.appendChild(mb);tools.appendChild(gh);tools.appendChild(tb);tools.appendChild(who);   // sign-in last = rightmost
      document.body.appendChild(tools);document.body.appendChild(menu);
    }
    wireChips();
    var dtries = 0;
    (function armDrag(){ if (makeDraggable() || ++dtries > 40) return; setTimeout(armDrag, 150); })();
    loadFeatured('trending');
  })();
})();
