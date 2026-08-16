/* Tagteam Cockpit (Phase 34) — plain JS, no framework, no build step.
 *
 * Zones: Now strip (state / owed / in-flight / hold / watcher / connection),
 * Needs you (typed cards, one primary action each), Watch (Feed | Diff |
 * Usage | Notes). Live via EventSource(/api/events); polling fallback.
 * Every control: pending → server {ok, message} as a toast; final actions
 * confirm with the exact CLI line the server will run (dry_run).
 */
(function () {
  'use strict';

  // ---------- helpers ----------
  var $ = function (id) { return document.getElementById(id); };
  var tokenMeta = document.querySelector('meta[name="tagteam-token"]');
  var TOKEN = tokenMeta ? tokenMeta.getAttribute('content') : null;

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function fmtAge(s) {
    if (s == null || isNaN(s)) return '?';
    s = Math.max(0, Math.floor(s));
    var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    if (h) return h + 'h' + String(m).padStart(2, '0') + 'm';
    if (m) return m + 'm' + String(sec).padStart(2, '0') + 's';
    return sec + 's';
  }
  function fmtTs(ts) {
    if (!ts) return '';
    var d = new Date(ts);
    if (isNaN(d.getTime())) return String(ts).slice(0, 19);
    return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' });
  }
  function fmtInt(v) { return (typeof v === 'number') ? v.toLocaleString() : '-'; }
  function fmtCost(v) { return (typeof v === 'number') ? '$' + v.toFixed(3) : '-'; }
  function firstLine(s, n) {
    s = String(s || '').trim();
    var line = s.split('\n')[0];
    if (line.length > (n || 160)) line = line.slice(0, (n || 160) - 1) + '…';
    return line;
  }
  function getJSON(url) {
    return fetch(url, { cache: 'no-store' }).then(function (r) {
      return r.json().then(function (b) { return { ok: r.ok, status: r.status, body: b }; });
    });
  }
  function postJSON(url, data) {
    var headers = { 'Content-Type': 'application/json' };
    if (TOKEN) headers['X-Tagteam-Token'] = TOKEN;
    return fetch(url, { method: 'POST', headers: headers, body: JSON.stringify(data || {}) })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (b) {
          return { ok: r.ok && b.ok !== false, status: r.status, body: b };
        });
      });
  }
  function toast(kind, msg, cli) {
    var t = el('div', 'toast ' + kind);
    t.textContent = msg || (kind === 'ok' ? 'Done.' : 'Failed.');
    if (cli) { var c = el('span', 't-cli', cli); t.appendChild(c); }
    $('toasts').appendChild(t);
    setTimeout(function () { t.style.opacity = '0'; t.style.transition = 'opacity .4s'; }, kind === 'err' ? 9000 : 5000);
    setTimeout(function () { t.remove(); }, kind === 'err' ? 9600 : 5600);
  }

  // Run an action with the feedback contract: pending → toast(message).
  // `confirmText` → confirm modal showing the exact CLI first (dry_run).
  function act(btn, url, data, opts) {
    opts = opts || {};
    var run = function () {
      if (btn) { btn.disabled = true; btn.classList.add('pending'); }
      return postJSON(url, data).then(function (r) {
        var msg = (r.body && (r.body.message || r.body.error)) || (r.ok ? 'Done.' : 'Failed (' + r.status + ')');
        toast(r.ok ? 'ok' : 'err', msg, r.body && r.body.cli);
        if (opts.onDone) opts.onDone(r);
        if (r.ok) refreshAll('action'); else if (opts.onError) opts.onError(msg);
        return r;
      }).catch(function (e) {
        toast('err', 'Request failed: ' + e);
      }).then(function (r) {
        if (btn) { btn.disabled = false; btn.classList.remove('pending'); }
        return r;
      });
    };
    if (!opts.confirm) return run();
    // dry-run to get the exact CLI line the server would run
    return postJSON(url, Object.assign({}, data, { dry_run: true })).then(function (r) {
      if (!r.ok) { toast('err', (r.body && (r.body.message || r.body.error)) || 'Cannot prepare action'); return; }
      confirmModal(opts.confirm.title, opts.confirm.body, r.body.cli, run);
    });
  }

  var confirmCb = null;
  function confirmModal(title, body, cli, onOk) {
    $('confirm-title').textContent = title;
    $('confirm-body').textContent = body || '';
    $('confirm-cli').textContent = cli || '';
    confirmCb = onOk;
    $('confirm').classList.remove('hidden');
    $('confirm-ok').focus();
  }
  $('confirm-cancel').addEventListener('click', function () { $('confirm').classList.add('hidden'); confirmCb = null; });
  $('confirm-ok').addEventListener('click', function () { var cb = confirmCb; $('confirm').classList.add('hidden'); confirmCb = null; if (cb) cb(); });
  $('confirm').addEventListener('click', function (e) { if (e.target === $('confirm')) { $('confirm').classList.add('hidden'); confirmCb = null; } });

  // ---------- state ----------
  var NOW = null;              // /api/now payload
  var CYCLE_ID = null;         // "<phase>_<type>"
  var briefCurrent = null;     // /api/brief/current payload
  var lastRoundsKey = null;
  var diffLoaded = false, usageLoaded = false, notesLoaded = false;
  var expandedFeed = {};       // id -> bool
  var expandedFiles = {};      // path -> bool

  // ---------- Now strip ----------
  function renderNow(n) {
    NOW = n;
    var st = n.state || {};
    var phase = st.phase, type = st.type;
    CYCLE_ID = (phase && type) ? phase + '_' + type : null;
    $('now-project').textContent = (n.project_dir || '').split('/').slice(-2).join('/');

    var cyc = $('chip-cycle');
    if (phase) {
      cyc.innerHTML = '<b>' + esc(phase) + '</b> · ' + esc(type) + ' · r' + esc(st.round);
      var cs = n.cycle && n.cycle.state;
      if (cs && cs !== 'in-progress') cyc.innerHTML += ' · <span class="' + (cs === 'approved' ? '' : 'muted') + '">' + esc(cs) + '</span>';
    } else {
      cyc.textContent = 'no active cycle';
    }

    var owed = $('chip-owed');
    owed.className = 'chip';
    if (n.owed) {
      owed.innerHTML = 'turn: <b>' + esc(n.owed.role) + '</b>' + (n.owed.agent ? ' (' + esc(n.owed.agent) + ')' : '') +
        ' · owed ' + esc(fmtAge(n.owed.age_s));
      if (n.owed.age_s > 900 && !n.inflight) owed.classList.add('warn');
    } else if (st.status === 'done') {
      owed.textContent = 'cycle done — ' + (st.result || 'approved');
    } else if (n.cycle && (n.cycle.state === 'escalated' || n.cycle.state === 'needs-human')) {
      owed.innerHTML = 'turn: <b>you</b> (arbiter)';
      owed.classList.add('warn');
    } else {
      owed.textContent = 'no turn owed';
    }

    var inf = $('chip-inflight');
    if (n.inflight) {
      inf.classList.remove('hidden');
      inf.innerHTML = '<span class="pulse"></span> in flight: <b>' + esc(n.inflight.agent || n.inflight.provider) + '</b> (' + esc(n.inflight.role) + ') · ' + esc(fmtAge(n.inflight.age_s)) +
        (n.inflight.pid_alive === false ? ' · <span class="muted">process gone</span>' : '');
      $('btn-cancel-turn').classList.toggle('hidden', !n.inflight);
    } else {
      inf.classList.add('hidden');
      $('btn-cancel-turn').classList.add('hidden');
    }

    var pz = $('chip-paused');
    if (n.paused) {
      pz.classList.remove('hidden');
      pz.textContent = 'paused ' + fmtAge(n.paused.age_s) + (n.paused.by ? ' by ' + n.paused.by : '');
      $('btn-pause').textContent = 'Resume';
      $('btn-pause').title = 'tagteam resume — clear the hold; the watcher re-dispatches the owed turn';
    } else {
      pz.classList.add('hidden');
      $('btn-pause').textContent = 'Pause';
      $('btn-pause').title = 'tagteam pause — hold dispatch in every watcher mode';
    }

    var w = $('chip-watcher');
    var wr = n.watcher || {};
    w.className = 'chip ' + (wr.running ? 'ok' : (n.owed && !n.inflight ? 'warn' : ''));
    if (wr.running) {
      w.textContent = 'watcher ' + (wr.mode ? wr.mode + ' ' : '') + 'pid ' + wr.pid;
      w.title = 'watcher liveness — bound to this project via ' + (wr.source || '?');
    } else {
      w.textContent = wr.stale_pidfile ? 'watcher gone (stale record)' : 'no watcher';
      w.title = wr.stale_pidfile ? 'the recorded watcher process is not running; start a new one' : 'no watcher found for this project';
    }

    var notes = $('chip-notes');
    if (n.pending_notes) { notes.classList.remove('hidden'); notes.textContent = n.pending_notes + ' queued note' + (n.pending_notes > 1 ? 's' : ''); }
    else notes.classList.add('hidden');
    var nc = $('notes-count'); nc.textContent = n.pending_notes ? String(n.pending_notes) : '';

    if (!$('tail-drawer').classList.contains('hidden')) loadTail();
    renderNeeds();
  }

  $('btn-pause').addEventListener('click', function () {
    var btn = this;
    if (NOW && NOW.paused) act(btn, '/api/resume', {});
    else act(btn, '/api/pause', { reason: 'paused from the cockpit' });
  });
  $('btn-tail').addEventListener('click', function () {
    var d = $('tail-drawer'); var open = d.classList.toggle('hidden');
    this.classList.toggle('on', !open);
    if (!open) loadTail();
  });
  function loadTail() {
    getJSON('/api/tail?lines=60').then(function (r) {
      var b = r.body || {};
      $('tail-title').textContent = b.path ? (b.inflight ? 'tagteam tail  ·  ' : 'tagteam tail --no-follow  ·  ') + b.path.split('/').slice(-1)[0] : 'tagteam tail';
      $('tail-body').textContent = b.lines && b.lines.length ? b.lines.join('\n') : (b.message || '(empty)');
      var pre = $('tail-body'); pre.scrollTop = pre.scrollHeight;
    });
  }
  $('btn-cancel-turn').addEventListener('click', function () {
    var inf = NOW && NOW.inflight;
    act(this, '/api/cancel-turn', {}, { confirm: {
      title: 'Cancel the in-flight turn?',
      body: inf ? ('Kills ' + (inf.agent || inf.provider) + ' (' + inf.role + ', pid ' + inf.pid + '); the engine records outcome "cancelled" and pauses dispatch.') : ''
    } });
  });

  // ---------- Needs you ----------
  function briefSections(md) {
    // Split the brief markdown by "## " headings → [{title, text}]
    var out = [], cur = null;
    String(md || '').split('\n').forEach(function (line) {
      var m = /^##\s+(.*)$/.exec(line);
      if (m) { cur = { title: m[1].trim(), text: '' }; out.push(cur); }
      else if (cur) cur.text += line + '\n';
      else if (line.trim() && !/^#\s/.test(line) && !/^<!--/.test(line)) { cur = { title: '', text: line + '\n' }; out.push(cur); }
    });
    return out;
  }

  function cardShell(kind, title, meta) {
    var c = el('div', 'card ' + kind);
    var h = el('div', 'card-head');
    h.appendChild(el('span', 'card-kind', kind === 'hold' ? 'hold' : kind === 'stale' ? 'attention' : kind));
    h.appendChild(el('span', 'card-title', title));
    if (meta) { var m = el('span', 'card-meta', meta); m.style.marginLeft = 'auto'; h.appendChild(m); }
    c.appendChild(h);
    return c;
  }
  function textareaRow(placeholder, rows) {
    var ta = el('textarea'); ta.placeholder = placeholder; ta.rows = rows || 3; return ta;
  }
  function inlineError(card, msg) {
    var e = card.querySelector('.inline-error') || card.appendChild(el('div', 'inline-error'));
    e.textContent = msg || '';
  }

  function renderNeeds() {
    var wrap = $('needs-cards'); wrap.innerHTML = '';
    var cards = 0;
    var n = NOW || {}; var st = n.state || {}; var cs = n.cycle && n.cycle.state;

    // Escalation / question — from the current event (Phase 33 rule)
    if (cs === 'escalated' || cs === 'needs-human') {
      var ev = briefCurrent && briefCurrent.event;
      var brief = briefCurrent && briefCurrent.brief;
      var kind = cs === 'escalated' ? 'escalation' : 'question';
      var card = cardShell(kind, cs === 'escalated' ? 'Escalation — rule on ' + st.phase + '/' + st.type + ' r' + st.round
                                                    : 'Question from ' + (ev ? ev.role : 'the reviewer') + ' — ' + st.phase + '/' + st.type + ' r' + st.round,
                           ev ? fmtTs(ev.ts) : '');
      if (ev) {
        var long = (ev.content || '').length > 420 || (ev.content || '').split('\n').length > 7;
        var body = el('div', 'card-body' + (long ? ' clamp' : ''), ev.content || '');
        card.appendChild(body);
        if (long) {
          var more = el('button', 'link-btn', 'show all'); more.addEventListener('click', function () { body.classList.toggle('clamp'); more.textContent = body.classList.contains('clamp') ? 'show all' : 'show less'; });
          card.appendChild(more);
        }
      }
      // brief
      if (brief) {
        var b = el('div', 'brief');
        var bm = el('div', 'brief-meta', 'Brief #' + brief.id + ' (' + brief.kind + ' a' + brief.attempt + ', ' + brief.status + ') · ' + (brief.path || '').split('/').slice(-1)[0]);
        b.appendChild(bm);
        briefSections(brief.content).forEach(function (s) {
          if (s.title) b.appendChild(el('h4', null, s.title));
          var sec = el('div', 'sec' + (/recommendation/i.test(s.title) ? ' rec' : ''), s.text.trim());
          b.appendChild(sec);
        });
        card.appendChild(b);
      } else if (cs === 'escalated' || cs === 'needs-human') {
        var attempts = (briefCurrent && briefCurrent.attempts) || [];
        var txt = attempts.length ? ('Brief attempts: ' + attempts.map(function (a) { return 'a' + a.attempt + ' ' + a.status; }).join(', ')) : 'No brief yet for this event.';
        var hb = el('div', 'hint'); hb.textContent = txt + ' ';
        if (n.briefer_enabled) {
          var running = attempts.some(function (a) { return a.status === 'running'; });
          var gb = el('button', 'btn btn-small', running ? 'Brief running…' : 'Generate brief');
          gb.disabled = running; gb.title = 'tagteam brief --generate';
          gb.addEventListener('click', function () { act(gb, '/api/brief/generate', {}); });
          hb.appendChild(gb);
        } else {
          var code = el('code', null, 'briefer.enabled: true'); hb.appendChild(document.createTextNode('Enable the briefer in tagteam.yaml (')); hb.appendChild(code); hb.appendChild(document.createTextNode(') to get a decision brief.'));
        }
        card.appendChild(hb);
      }
      // ruling controls
      var ta = textareaRow(cs === 'escalated' ? 'Comment for the lead (required for Request changes; optional for Approve)…' : 'Your answer…', 3);
      card.appendChild(ta);
      var row = el('div', 'row');
      var safe = el('div', 'actions-safe'); var fin = el('div', 'actions-final');
      if (cs === 'escalated') {
        var rc = el('button', 'btn', 'Request changes'); rc.title = 'tagteam rule request-changes --content …';
        rc.addEventListener('click', function () {
          if (!ta.value.trim()) { inlineError(card, 'Request changes needs a comment.'); ta.focus(); return; }
          inlineError(card, '');
          act(rc, '/api/rule', { ruling: 'request-changes', content: ta.value.trim() }, { confirm: { title: 'Request changes?', body: 'The lead gets the turn back with your comment (recorded as an arbiter ruling).' }, onError: function (m) { inlineError(card, m); } });
        });
        safe.appendChild(rc);
        var ap = el('button', 'btn btn-approve', 'Approve'); ap.title = 'tagteam rule approve';
        ap.addEventListener('click', function () {
          inlineError(card, '');
          act(ap, '/api/rule', { ruling: 'approve', content: ta.value.trim() }, { confirm: { title: 'Approve and close the cycle?', body: 'This is final: the cycle is marked approved.' }, onError: function (m) { inlineError(card, m); } });
        });
        fin.appendChild(ap);
      } else {
        var sel = el('select'); ['reviewer', 'lead'].forEach(function (r) { var o = el('option', null, 'to ' + r); o.value = r; sel.appendChild(o); });
        safe.appendChild(sel);
        var an = el('button', 'btn btn-primary', 'Answer'); an.title = 'tagteam rule answer --to … --content …';
        an.addEventListener('click', function () {
          if (!ta.value.trim()) { inlineError(card, 'An answer needs content.'); ta.focus(); return; }
          inlineError(card, '');
          act(an, '/api/rule', { ruling: 'answer', content: ta.value.trim(), to: sel.value }, { confirm: { title: 'Send the answer?', body: 'Delivered as an interjection; the ' + sel.value + ' gets the turn.' }, onError: function (m) { inlineError(card, m); } });
        });
        fin.appendChild(an);
      }
      row.appendChild(safe); row.appendChild(fin); card.appendChild(row);
      wrap.appendChild(card); cards++;
    }

    // Hold
    if (n.paused) {
      var pc = cardShell('hold', 'Dispatch is on hold', fmtTs(n.paused.ts));
      var pb = el('div', 'card-body', (n.paused.reason || 'paused') + (n.paused.by ? '\nby ' + n.paused.by : '') + (n.paused.outcome ? '\nfailed turn: ' + n.paused.outcome + ' — ' + (n.paused.phase || '') + ' ' + (n.paused.type || '') + ' r' + (n.paused.round || '') : '') + (n.paused.log_path ? '\nlog: ' + n.paused.log_path : ''));
      pc.appendChild(pb);
      var pr = el('div', 'row'); var rs = el('button', 'btn btn-primary', 'Resume'); rs.title = 'tagteam resume';
      rs.addEventListener('click', function () { act(rs, '/api/resume', {}); });
      var fill = el('div', 'actions-final'); fill.appendChild(rs); pr.appendChild(fill); pc.appendChild(pr);
      wrap.appendChild(pc); cards++;
    }

    // Stale in-flight / no watcher
    if (n.inflight && n.inflight.pid_alive === false) {
      var sc = cardShell('stale', 'In-flight pointer, but the process is gone', fmtTs(n.inflight.started_at));
      sc.appendChild(el('div', 'card-body', (n.inflight.agent || n.inflight.provider) + ' (' + n.inflight.role + ') · ' + n.inflight.stem + '\nThe engine normally clears this itself; if it stays, cancel-turn removes the stale pointer (nothing is killed).'));
      var sr = el('div', 'row'); var cb = el('button', 'btn btn-danger', 'Cancel turn'); cb.title = 'tagteam cancel-turn';
      cb.addEventListener('click', function () { act(cb, '/api/cancel-turn', {}, { confirm: { title: 'Cancel turn?', body: 'Binds the recorded pid first; a stale pointer is removed without signalling.' } }); });
      var sf = el('div', 'actions-final'); sf.appendChild(cb); sr.appendChild(sf); sc.appendChild(sr);
      wrap.appendChild(sc); cards++;
    } else if (n.owed && !n.inflight && !n.paused && !(n.watcher && n.watcher.running) && n.owed.age_s > 120) {
      var wc = cardShell('stale', 'A turn is owed but nothing is dispatching', 'owed ' + fmtAge(n.owed.age_s));
      var wb = el('div', 'hint'); wb.innerHTML = 'No watcher was found for this project. Start one: <code>tagteam watch --mode headless</code> (or run <code>/handoff</code> in the ' + esc(n.owed.role) + '\'s terminal).';
      wc.appendChild(wb); wrap.appendChild(wc); cards++;
    }

    // First-run states
    if (!n.agents || !n.agents.lead) {
      var fc = cardShell('stale', 'No tagteam.yaml yet', '');
      var fb = el('div', 'hint'); fb.innerHTML = 'Set up the two agents in the <a href="/?theme=saloon">Saloon</a> (the Mayor walks you through it) or run <code>tagteam init</code>.';
      fc.appendChild(fb); wrap.appendChild(fc); cards++;
    }

    $('needs-count').textContent = cards ? String(cards) : '';
    $('needs-count').classList.toggle('hot', cards > 0);
    $('needs-you').classList.toggle('hot', cards > 0 && (cs === 'escalated' || cs === 'needs-human'));
    $('needs-empty').classList.toggle('hidden', cards > 0);
    if (!cards) {
      var eb = $('needs-empty-body');
      if (!st.phase) eb.innerHTML = 'No active cycle. Start one with <code>tagteam cycle init --phase … --type plan …</code> or from the <a href="/?theme=saloon">Saloon</a>.';
      else if (n.owed) eb.innerHTML = esc(n.owed.agent || n.owed.role) + ' is on ' + esc(st.phase) + ' ' + esc(st.type) + ' r' + esc(st.round) + ' (' + esc(fmtAge(n.owed.age_s)) + ')' + (n.inflight ? ' — in flight now' : '') + '. Watch the <button class="link-btn" data-tab-link="feed">Feed</button>.';
      else if (st.status === 'done') eb.innerHTML = esc(st.phase) + ' ' + esc(st.type) + ' is ' + esc(st.result || 'done') + '. Next: the lead opens the next cycle.';
      else eb.textContent = 'Nothing is owed right now.';
      var lb = eb.querySelector('[data-tab-link]'); if (lb) lb.addEventListener('click', function () { showTab('feed'); });
    }
  }

  // ---------- Tabs ----------
  function showTab(name) {
    document.querySelectorAll('.tab').forEach(function (t) { t.classList.toggle('active', t.dataset.tab === name); });
    document.querySelectorAll('.panel').forEach(function (p) { p.classList.toggle('active', p.id === 'panel-' + name); });
    if (name === 'diff' && !diffLoaded) loadDiff();
    if (name === 'usage' && !usageLoaded) loadUsage();
    if (name === 'notes' && !notesLoaded) loadNotes();
    try { localStorage.setItem('tagteam.cockpit.tab', name); } catch (e) { /* ignore */ }
  }
  document.querySelectorAll('.tab').forEach(function (t) { t.addEventListener('click', function () { showTab(t.dataset.tab); }); });
  function activeTab() { var t = document.querySelector('.tab.active'); return t ? t.dataset.tab : 'feed'; }

  // ---------- Feed ----------
  function loadFeed() {
    if (!CYCLE_ID) { $('feed-list').innerHTML = ''; $('feed-empty').classList.remove('hidden'); $('feed-empty').textContent = 'No active cycle.'; return Promise.resolve(); }
    var st = NOW.state;
    return Promise.all([getJSON('/api/rounds/' + encodeURIComponent(CYCLE_ID)), getJSON('/api/briefs?phase=' + encodeURIComponent(st.phase) + '&type=' + encodeURIComponent(st.type))]).then(function (rs) {
      var rounds = (rs[0].body && rs[0].body.rounds) || [];
      var briefs = (rs[1].body && rs[1].body.briefs) || [];
      var items = [];
      rounds.forEach(function (r) {
        (r.entries || []).forEach(function (e, i) {
          var isRuling = /^\[ARBITER RULING by /.test(e.content || '');
          items.push({ id: 'r' + r.round + '-' + i + '-' + e.ts, kind: isRuling ? 'ruling' : e.role, role: isRuling ? 'arbiter' : e.role, action: e.action, ts: e.ts, round: r.round, text: e.content || '', by: e.updated_by });
        });
        (r.rulings || []).forEach(function (e, i) {
          if (!(r.entries || []).some(function (x) { return x.ts === e.ts; })) items.push({ id: 'ru' + r.round + '-' + i, kind: 'ruling', role: 'arbiter', action: e.action, ts: e.ts, round: r.round, text: e.content || '' });
        });
        (r.interjections || []).forEach(function (n) {
          items.push({ id: 'i' + n.id, kind: 'interjection', role: 'note #' + n.id, action: n.retired_ts ? 'retired' : (n.delivered_role ? 'delivered → ' + n.delivered_role + ' r' + n.delivered_round : 'queued'), ts: n.ts, round: r.round, text: n.note, by: n.by });
        });
        if (!r.entries || !r.entries.length) {
          if (r.lead_text) items.push({ id: 'l' + r.round, kind: 'lead', role: 'lead', action: r.lead_action || 'SUBMIT_FOR_REVIEW', ts: '', round: r.round, text: r.lead_text });
          if (r.reviewer_text) items.push({ id: 'v' + r.round, kind: 'reviewer', role: 'reviewer', action: r.action || '', ts: '', round: r.round, text: r.reviewer_text });
        }
      });
      briefs.forEach(function (b) {
        items.push({ id: 'b' + b.id, kind: 'brief', role: 'brief #' + b.id, action: b.kind + ' a' + b.attempt + ' ' + b.status, ts: b.finished_at || b.ts, round: b.round, text: (b.reason || '') + (b.path ? '\n' + b.path : ''), by: b.provider });
      });
      items.sort(function (a, b) { return (b.ts || '').localeCompare(a.ts || '') || (b.round - a.round); });
      var key = items.map(function (i) { return i.id + '|' + i.action; }).join(';');
      $('feed-meta').textContent = rounds.length + ' round' + (rounds.length === 1 ? '' : 's') + ' · ' + items.length + ' entries';
      $('feed-title').textContent = 'Round stream — ' + st.phase + ' ' + st.type;
      if (key === lastRoundsKey) return;
      lastRoundsKey = key;
      var list = $('feed-list'); list.innerHTML = '';
      $('feed-empty').classList.toggle('hidden', items.length > 0);
      items.forEach(function (it) {
        var d = el('div', 'feed-item ' + it.kind);
        d.appendChild(el('span', 'dot'));
        var body = el('div');
        var line = el('div', 'feed-line');
        line.appendChild(el('span', 'tag role', it.role));
        if (it.action) line.appendChild(el('span', 'tag action', it.action));
        line.appendChild(el('span', 'tag', 'r' + it.round));
        if (it.by && it.by !== it.role) line.appendChild(el('span', 'muted', it.by));
        line.appendChild(el('span', 'ts', fmtTs(it.ts)));
        body.appendChild(line);
        var summary = el('div', 'feed-summary', firstLine(it.text, 200));
        body.appendChild(summary);
        var full = String(it.text || '').trim();
        if (full.length > firstLine(full, 200).length) {
          var more = el('button', 'link-btn', expandedFeed[it.id] ? 'less' : 'more');
          var fullEl = el('div', 'feed-full', full);
          if (!expandedFeed[it.id]) fullEl.classList.add('hidden');
          more.addEventListener('click', function () { expandedFeed[it.id] = !expandedFeed[it.id]; fullEl.classList.toggle('hidden', !expandedFeed[it.id]); more.textContent = expandedFeed[it.id] ? 'less' : 'more'; });
          body.appendChild(more); body.appendChild(fullEl);
        }
        d.appendChild(body); list.appendChild(d);
      });
    });
  }

  // ---------- Diff ----------
  function colorize(patch) {
    return String(patch || '').split('\n').map(function (l) {
      var cls = l.startsWith('+++') || l.startsWith('---') || l.startsWith('diff ') || l.startsWith('index ') ? 'l-meta'
        : l.startsWith('@@') ? 'l-hunk' : l.startsWith('+') ? 'l-add' : l.startsWith('-') ? 'l-del' : '';
      return cls ? '<span class="' + cls + '">' + esc(l) + '</span>' : esc(l);
    }).join('\n');
  }
  function loadDiff() {
    if (!CYCLE_ID) { $('diff-list').innerHTML = ''; $('diff-empty').classList.remove('hidden'); return Promise.resolve(); }
    $('diff-title').textContent = 'Loading scope diff…';
    return getJSON('/api/scope-diff/' + encodeURIComponent(CYCLE_ID)).then(function (r) {
      var b = r.body || {}; diffLoaded = true;
      var list = $('diff-list'); list.innerHTML = '';
      var banner = $('diff-banner');
      if (b.error) { banner.textContent = b.error; banner.classList.remove('hidden'); $('diff-title').textContent = 'Scope diff'; return; }
      $('diff-title').textContent = 'Scope diff — ' + (b.paths || []).length + ' path' + ((b.paths || []).length === 1 ? '' : 's') + ' since baseline ' + (b.diff_base || '').slice(0, 10);
      if (b.truncated) { banner.textContent = 'Diff capped: ' + (b.omitted_files ? b.omitted_files + ' file(s) omitted; ' : '') + 'patch text limited to ' + Math.round((b.max_bytes || 0) / 1024) + ' KB — see per-file markers, or run git diff locally.'; banner.classList.remove('hidden'); }
      else banner.classList.add('hidden');
      $('diff-empty').classList.toggle('hidden', (b.files || []).length > 0);
      (b.files || []).forEach(function (f) {
        var box = el('div', 'file');
        var head = el('div', 'file-head');
        head.appendChild(el('span', 'status', f.status + (f.binary ? ' · binary' : '')));
        head.appendChild(el('span', 'path', f.path));
        if (typeof f.additions === 'number') head.appendChild(el('span', 'adds', '+' + f.additions));
        if (typeof f.deletions === 'number') head.appendChild(el('span', 'dels', '−' + f.deletions));
        box.appendChild(head);
        var pre = el('pre'); pre.innerHTML = f.binary ? '<span class="l-meta">(binary file — not diffed)</span>' : (f.patch ? colorize(f.patch) : '<span class="l-meta">(no textual diff)</span>');
        if (!expandedFiles[f.path]) pre.classList.add('hidden');
        head.addEventListener('click', function () { expandedFiles[f.path] = !expandedFiles[f.path]; pre.classList.toggle('hidden', !expandedFiles[f.path]); });
        box.appendChild(pre);
        if (f.truncated) box.appendChild(el('div', 'trunc', 'patch truncated at the size cap'));
        list.appendChild(box);
      });
    });
  }
  $('btn-diff-refresh').addEventListener('click', function () { loadDiff(); });
  $('btn-diff-expand').addEventListener('click', function () {
    var anyHidden = Array.prototype.some.call(document.querySelectorAll('#diff-list .file pre'), function (p) { return p.classList.contains('hidden'); });
    document.querySelectorAll('#diff-list .file').forEach(function (box) {
      var path = box.querySelector('.path').textContent; expandedFiles[path] = anyHidden; box.querySelector('pre').classList.toggle('hidden', !anyHidden);
    });
    this.textContent = anyHidden ? 'Collapse all' : 'Expand all';
  });

  // ---------- Usage ----------
  function bucketTable(buckets) {
    var keys = Object.keys(buckets || {});
    if (!keys.length) return '<div class="hint" style="padding:8px 10px">no rows</div>';
    var h = '<table class="u"><tr><th>bucket</th><th>turns</th><th>ok</th><th>failed</th><th>in</th><th>out</th><th>cache r</th><th>cache w</th><th>cost</th><th>mean</th></tr>';
    keys.forEach(function (k) {
      var b = buckets[k];
      h += '<tr><td>' + esc(k) + '</td><td>' + b.turns + '</td><td>' + b.ok + '</td><td>' + b.failed + '</td><td>' + fmtInt(b.input_tokens) + '</td><td>' + fmtInt(b.output_tokens) + '</td><td>' + fmtInt(b.cache_read_tokens) + '</td><td>' + fmtInt(b.cache_write_tokens) + '</td><td>' + (b.cost_known_turns ? fmtCost(b.cost_usd) : '-') + '</td><td>' + (b.mean_duration_ms != null ? Math.round(b.mean_duration_ms / 1000) + 's' : '-') + '</td></tr>';
    });
    return h + '</table>';
  }
  function drawChurn(series) {
    var svg = $('churn'); svg.innerHTML = '';
    var W = 640, H = 220, padL = 46, padR = 12, padT = 10, padB = 24;
    var pts = (series || []).filter(function (s) { return typeof s.round === 'number'; });
    var byRole = {};
    pts.forEach(function (s) {
      var tot = (s.input || 0) + (s.output || 0);
      (byRole[s.role] = byRole[s.role] || []).push({ x: s.round, y: tot, s: s });
    });
    var maxR = Math.max(10, Math.max.apply(null, pts.map(function (p) { return p.round; }).concat([1])));
    var maxY = Math.max(1, Math.max.apply(null, pts.map(function (p) { return (p.input || 0) + (p.output || 0); }).concat([1])));
    var X = function (r) { return padL + (r - 1) / Math.max(1, maxR - 1) * (W - padL - padR); };
    var Y = function (v) { return H - padB - v / maxY * (H - padT - padB); };
    var ns = 'http://www.w3.org/2000/svg';
    function line(x1, y1, x2, y2, stroke, dash, w) { var l = document.createElementNS(ns, 'line'); l.setAttribute('x1', x1); l.setAttribute('y1', y1); l.setAttribute('x2', x2); l.setAttribute('y2', y2); l.setAttribute('stroke', stroke); l.setAttribute('stroke-width', w || 1); if (dash) l.setAttribute('stroke-dasharray', dash); svg.appendChild(l); }
    function text(x, y, t, anchor, fill) { var e = document.createElementNS(ns, 'text'); e.setAttribute('x', x); e.setAttribute('y', y); e.setAttribute('font-size', '10'); e.setAttribute('fill', fill || '#9aa4b1'); e.setAttribute('text-anchor', anchor || 'start'); e.setAttribute('font-family', 'ui-monospace, Menlo, monospace'); e.textContent = t; svg.appendChild(e); }
    line(padL, H - padB, W - padR, H - padB, '#2b333d'); line(padL, padT, padL, H - padB, '#2b333d');
    for (var r = 1; r <= maxR; r++) { if (maxR <= 12 || r % 2 === 1) text(X(r), H - 8, 'r' + r, 'middle'); }
    text(padL - 4, padT + 8, fmtInt(maxY), 'end'); text(padL - 4, H - padB, '0', 'end');
    // round-10 escalation line
    if (maxR >= 10) {
      line(X(10), padT, X(10), H - padB, '#f85149', '4 3');
      var nearRight = X(10) > W - padR - 110;
      text(X(10) + (nearRight ? -3 : 3), padT + 10, 'r10 auto-escalate', nearRight ? 'end' : 'start', '#f85149');
    }
    var colors = { lead: '#5aa9ff', reviewer: '#c58af9', briefer: '#d29922' };
    Object.keys(byRole).forEach(function (role) {
      var arr = byRole[role].sort(function (a, b) { return a.x - b.x || 0; });
      var d = ''; arr.forEach(function (p, i) { d += (i ? 'L' : 'M') + X(p.x).toFixed(1) + ',' + Y(p.y).toFixed(1) + ' '; });
      var path = document.createElementNS(ns, 'path'); path.setAttribute('d', d); path.setAttribute('fill', 'none'); path.setAttribute('stroke', colors[role] || '#9aa4b1'); path.setAttribute('stroke-width', '2'); svg.appendChild(path);
      arr.forEach(function (p) { var c = document.createElementNS(ns, 'circle'); c.setAttribute('cx', X(p.x)); c.setAttribute('cy', Y(p.y)); c.setAttribute('r', p.s.status === 'ok' ? 3 : 4); c.setAttribute('fill', p.s.status === 'ok' ? (colors[role] || '#9aa4b1') : '#f85149'); var t = document.createElementNS(ns, 'title'); t.textContent = role + ' r' + p.x + ': ' + fmtInt(p.y) + ' tokens (' + p.s.status + (p.s.cost != null ? ', ' + fmtCost(p.s.cost) : '') + ')'; c.appendChild(t); svg.appendChild(c); });
    });
    if (!pts.length) text(W / 2, H / 2, 'no turns yet', 'middle');
  }
  function loadUsage() {
    var st = (NOW && NOW.state) || {};
    var q = st.phase ? '?phase=' + encodeURIComponent(st.phase) + '&type=' + encodeURIComponent(st.type) : '';
    return Promise.all([getJSON('/api/usage' + q), getJSON('/api/usage')]).then(function (rs) {
      usageLoaded = true;
      var cur = rs[0].body || {}, all = rs[1].body || {};
      var lims = all.rate_limits || [];
      var rl = $('rate-line');
      if (!lims.length) rl.innerHTML = '<span class="k">Subscription window:</span> n/a — no rate-limit signal recorded yet (Claude headless turns report it; Codex has no equivalent).';
      else rl.innerHTML = lims.map(function (l) {
        var when = l.resets_at ? new Date(l.resets_at) : null;
        var resets = when && !isNaN(when.getTime()) ? when.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }) + (l.resets_in_s != null ? ' (in ' + fmtAge(l.resets_in_s) + ')' : '') : '?';
        return '<span class="k">' + esc(l.provider) + ' ' + esc(String(l.kind).replace('_', ' ')) + ' window:</span> <b>' + esc(l.status || '?') + '</b>, resets ' + esc(resets) + ' <span class="muted">· seen ' + esc(fmtTs(l.ts)) + '</span>';
      }).join('<br>');
      drawChurn(cur.series || []);
      $('usage-role').innerHTML = bucketTable(all.by_role);
      $('usage-cycle').innerHTML = bucketTable(all.by_cycle);
      $('usage-agent').innerHTML = bucketTable(all.by_agent);
      $('usage-empty').classList.toggle('hidden', !!(all.totals && all.totals.turns));
    });
  }
  $('btn-usage-refresh').addEventListener('click', function () { loadUsage(); });

  // ---------- Notes ----------
  function loadNotes() {
    var st = (NOW && NOW.state) || {};
    var q = st.phase ? '?phase=' + encodeURIComponent(st.phase) + '&type=' + encodeURIComponent(st.type) : '';
    return getJSON('/api/interjections' + q).then(function (r) {
      notesLoaded = true;
      var rows = (r.body && r.body.interjections) || [];
      var list = $('notes-list'); list.innerHTML = '';
      $('notes-empty').classList.toggle('hidden', rows.length > 0);
      rows.slice().reverse().forEach(function (n) {
        var d = el('div', 'note ' + n.status);
        var left = el('div');
        var meta = el('div', 'meta');
        meta.appendChild(el('span', 'st', n.status === 'delivered' ? 'delivered → ' + n.delivered_role + ' r' + n.delivered_round : n.status));
        meta.appendChild(document.createTextNode(' #' + n.id + ' · ' + (n.by || '') + ' → ' + (n.target_role || 'next turn') + ' · ' + (n.phase ? n.phase + '/' + n.type + ' r' + n.round : 'next cycle') + ' · ' + fmtTs(n.ts)));
        left.appendChild(meta); left.appendChild(el('div', 'text', n.note));
        d.appendChild(left);
        var right = el('div');
        if (n.status === 'pending') {
          var rb = el('button', 'btn btn-small', 'Retire'); rb.title = 'tagteam interject --retire ' + n.id;
          rb.addEventListener('click', function () { act(rb, '/api/interject/retire', { id: n.id }); });
          right.appendChild(rb);
        }
        d.appendChild(right); list.appendChild(d);
      });
    });
  }
  $('note-form').addEventListener('submit', function (e) {
    e.preventDefault();
    var ta = $('note-text'); var note = ta.value.trim();
    if (!note) { ta.focus(); return; }
    act($('btn-interject'), '/api/interject', { note: note, to: $('note-to').value }, { onDone: function (r) { if (r && r.ok) ta.value = ''; } });
  });

  // ---------- Refresh orchestration ----------
  var refreshing = false, refreshQueued = false;
  function refreshAll(reason) {
    if (refreshing) { refreshQueued = true; return; }
    refreshing = true;
    getJSON('/api/now').then(function (r) {
      if (!r.ok) throw new Error('now ' + r.status);
      var n = r.body;
      var st = n.state || {}; var cs = n.cycle && n.cycle.state;
      var p = Promise.resolve();
      if ((cs === 'escalated' || cs === 'needs-human') && st.phase) {
        p = getJSON('/api/brief/current?phase=' + encodeURIComponent(st.phase) + '&type=' + encodeURIComponent(st.type)).then(function (b) { briefCurrent = b.body; });
      } else briefCurrent = null;
      return p.then(function () {
        renderNow(n);
        var t = activeTab();
        var ps = [loadFeed()];
        if (t === 'notes' || notesLoaded) ps.push(loadNotes());
        if (t === 'usage' && usageLoaded) ps.push(loadUsage());
        if (t === 'diff' && diffLoaded && reason !== 'tick') ps.push(loadDiff());
        return Promise.all(ps);
      });
    }).catch(function (e) {
      console.error('[cockpit] refresh failed', e);
      if (connMode === 'live') setConn('disconnected');
    }).then(function () {
      refreshing = false;
      if (refreshQueued) { refreshQueued = false; refreshAll('queued'); }
    });
  }

  // ---------- Live connection: SSE with polling fallback ----------
  var connMode = 'connecting', es = null, pollTimer = null, pollDelay = 3000, sseRetry = null, sseAttempts = 0;
  function setConn(mode, text) {
    connMode = mode;
    var c = $('conn'); c.dataset.mode = mode;
    $('conn-text').textContent = text || ({ live: 'Live', polling: 'Polling', disconnected: 'Disconnected — retrying', connecting: 'Connecting…' })[mode];
  }
  function startPolling() {
    if (pollTimer) return;
    setConn('polling');
    var tick = function () {
      refreshAll('tick');
      pollDelay = Math.min(15000, Math.round(pollDelay * 1.25));
      pollTimer = setTimeout(tick, pollDelay);
    };
    pollTimer = setTimeout(tick, pollDelay);
  }
  function stopPolling() { if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; } pollDelay = 3000; }
  function connectSSE() {
    // `?nosse=1` forces the polling path (dogfood / debugging the fallback).
    if (!window.EventSource || /[?&]nosse=1/.test(location.search)) { startPolling(); return; }
    try { es = new EventSource('/api/events'); } catch (e) { startPolling(); return; }
    es.addEventListener('open', function () { sseAttempts = 0; stopPolling(); setConn('live'); });
    es.addEventListener('change', function () { refreshAll('sse'); });
    es.addEventListener('error', function () {
      // EventSource auto-reconnects on transient errors; while it does, poll.
      if (es && es.readyState === EventSource.CLOSED) {
        setConn('disconnected');
        sseAttempts++;
        startPolling();
        if (sseRetry) clearTimeout(sseRetry);
        sseRetry = setTimeout(function () { try { es.close(); } catch (e2) { /* ignore */ } connectSSE(); }, Math.min(30000, 2000 * Math.pow(2, Math.min(sseAttempts, 4))));
      } else {
        setConn('disconnected');
        startPolling();
      }
    });
  }

  // ---------- boot ----------
  try { var saved = localStorage.getItem('tagteam.cockpit.tab'); if (saved && $('tab-' + saved)) showTab(saved); } catch (e) { /* ignore */ }
  refreshAll('boot');
  connectSSE();
  // Ages in the strip tick locally between refreshes.
  setInterval(function () { if (NOW) renderNowAges(); }, 1000);
  // Safety net in live mode: a slow full refresh (process liveness that
  // leaves no file trace, clock drift) — cheap, local.
  setInterval(function () { if (connMode === 'live') refreshAll('tick'); }, 30000);
  function renderNowAges() {
    // Cheap local re-render of the age chips (no fetch).
    if (NOW.owed && NOW.owed.age_s != null) NOW.owed.age_s += 1;
    if (NOW.inflight && NOW.inflight.age_s != null) NOW.inflight.age_s += 1;
    if (NOW.paused && NOW.paused.age_s != null) NOW.paused.age_s += 1;
    var owed = $('chip-owed');
    if (NOW.owed) owed.innerHTML = 'turn: <b>' + esc(NOW.owed.role) + '</b>' + (NOW.owed.agent ? ' (' + esc(NOW.owed.agent) + ')' : '') + ' · owed ' + esc(fmtAge(NOW.owed.age_s));
    if (NOW.inflight) $('chip-inflight').innerHTML = '<span class="pulse"></span> in flight: <b>' + esc(NOW.inflight.agent || NOW.inflight.provider) + '</b> (' + esc(NOW.inflight.role) + ') · ' + esc(fmtAge(NOW.inflight.age_s)) + (NOW.inflight.pid_alive === false ? ' · <span class="muted">process gone</span>' : '');
    if (NOW.paused) $('chip-paused').textContent = 'paused ' + fmtAge(NOW.paused.age_s) + (NOW.paused.by ? ' by ' + NOW.paused.by : '');
  }
})();
