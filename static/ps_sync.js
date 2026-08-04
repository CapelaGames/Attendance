/*
 * PeopleSoft attendance sync — source for the bookmarklet served by /classes/<id>/sync.
 *
 * This file is minified by joining its lines with a space, so every statement must
 * end in a semicolon and there must be no // line comments anywhere below.
 * __API__ is substituted with the class's sync endpoint base before serving.
 */
(function () {
  var BRIDGE = '__BRIDGE__';
  var APP = BRIDGE.split('/').slice(0, 3).join('/');
  var EMPLID = 'RX_AT_ROST_GRID_EMPLID$';
  var ATTEND = 'RX_AT_ROST_GRID_RX_ATTENDANCE$';
  var DATE = 'CLASS_ATTENDNCE_CLASS_ATTEND_DT$';
  var JUNK = /^(expand|collapse|select|clear|save|cancel|present|absent|excused|yes|no|satisfactory|unsatisfactory|ungraded)$/i;

  /* PeopleSoft renders the component inside a same-origin content iframe, so the
     grid usually lives in a child document rather than the one we were clicked on. */
  function allDocs() {
    var docs = [];
    function walk(w, depth) {
      if (depth > 4) { return; }
      try {
        if (w.document) { docs.push(w.document); }
        for (var i = 0; i < w.frames.length; i++) { walk(w.frames[i], depth + 1); }
      } catch (e) { }
    }
    try { walk(window.top, 0); } catch (e) { walk(window, 0); }
    if (docs.indexOf(document) === -1) { docs.push(document); }
    return docs;
  }

  function gridDoc(docs) {
    for (var i = 0; i < docs.length; i++) {
      try {
        if (docs[i].querySelector('[id^="' + EMPLID + '"]')) { return docs[i]; }
      } catch (e) { }
    }
    return null;
  }

  function sampleIds(docs) {
    var ids = [];
    for (var i = 0; i < docs.length; i++) {
      var els;
      try { els = docs[i].querySelectorAll('[id*="$0"]'); } catch (e) { continue; }
      for (var j = 0; j < els.length && ids.length < 20; j++) {
        if (ids.indexOf(els[j].id) === -1) { ids.push(els[j].id); }
      }
    }
    return ids;
  }

  function cellText(el) {
    var t = (el.value !== undefined && el.value !== null && el.value !== '')
      ? el.value : (el.textContent || '');
    return String(t).trim();
  }

  function readRows(doc) {
    var out = [];
    var ids = doc.querySelectorAll('[id^="' + EMPLID + '"]');
    for (var i = 0; i < ids.length; i++) {
      var el = ids[i];
      var row = el.id.split('$').pop();
      var emplid = cellText(el);
      if (!emplid) { continue; }
      var cells = [];
      var tr = el.closest ? el.closest('tr') : null;
      if (tr) {
        var nodes = tr.querySelectorAll('td, div, span, a');
        for (var j = 0; j < nodes.length && cells.length < 4; j++) {
          /* Skip anything wrapping a form control — a cell holding the attendance
             dropdown reads as its concatenated option labels, not a name. */
          if (nodes[j].querySelector && nodes[j].querySelector('select, textarea, input')) {
            continue;
          }
          var t = cellText(nodes[j]);
          /* Keep name-ish cells only: the payload travels in a URL, and times,
             dates, IDs and button labels are all noise for matching. */
          if (t.length >= 2 && t.length <= 40 && /[A-Za-z]/.test(t) && !/\d/.test(t) &&
              !JUNK.test(t) && cells.indexOf(t) === -1) {
            cells.push(t);
          }
        }
      }
      out.push({ row: row, emplid: emplid, cells: cells });
    }
    return out;
  }

  /* Which PeopleSoft class this roster is. The page publishes it as
     PIA_KEYSTRUCT; the URL carries it too if that isn't there. */
  function classKeys(doc) {
    var win = doc.defaultView || window;
    var keys = null, out = {}, href = '';
    try { keys = win.PIA_KEYSTRUCT; } catch (e) { keys = null; }
    try { href = win.location.href; } catch (e) { href = ''; }

    if (keys && keys.CLASS_NBR) {
      out.class_nbr = String(keys.CLASS_NBR);
      if (keys.STRM) { out.strm = String(keys.STRM); }
    } else {
      var m = /[?&]CLASS_NBR=(\w+)/i.exec(href) || /[?&]CLASS_NBR=(\w+)/i.exec(doc.referrer || '');
      if (m) { out.class_nbr = m[1]; }
    }
    var head = doc.getElementById('win0divRX_AT_HEADER_HTMLAREA1');
    if (head) {
      var label = (head.textContent || '').replace(/\s+/g, ' ').trim();
      if (label) { out.label = label.slice(0, 120); }
    }
    return out;
  }

  function meetingDate(doc) {
    var d = doc.querySelectorAll('[id^="' + DATE + '"]');
    for (var i = 0; i < d.length; i++) {
      var p = cellText(d[i]).split('/');
      if (p.length === 3) { return p[2] + '-' + p[0] + '-' + p[1]; }
    }
    return null;
  }

  /* PeopleSoft codes attendance as a dropdown whose value we can't assume: take
     "Y" if it's there, else whatever is labelled Present, else a "P" code. */
  function presentOption(f) {
    var i, o;
    for (i = 0; i < f.options.length; i++) {
      if (String(f.options[i].value).trim().toUpperCase() === 'Y') { return f.options[i].value; }
    }
    for (i = 0; i < f.options.length; i++) {
      o = f.options[i];
      if (String(o.text || '').trim().toUpperCase() === 'PRESENT') { return o.value; }
    }
    for (i = 0; i < f.options.length; i++) {
      if (String(f.options[i].value).trim().toUpperCase() === 'P') { return f.options[i].value; }
    }
    return null;
  }

  function optionSummary(f) {
    var bits = [];
    for (var i = 0; i < f.options.length && i < 8; i++) {
      bits.push(JSON.stringify(String(f.options[i].value)) + '=' +
                JSON.stringify(String(f.options[i].text || '').trim()));
    }
    return bits.length ? bits.join(' ') : 'dropdown has no options';
  }

  /* Set the field the way the page's own handlers expect, so PeopleSoft runs its
     usual row logic (participation, date) instead of us reconstructing it. */
  function mark(doc, row) {
    var f = doc.getElementById(ATTEND + row);
    if (!f) { return 'no field'; }
    var win = doc.defaultView || window;

    if (f.tagName === 'SELECT') {
      var wanted = presentOption(f);
      if (wanted === null) { return 'no Present option [' + optionSummary(f) + ']'; }
      if (f.value === wanted) { return 'already'; }
      f.value = wanted;
      if (f.selectedIndex < 0) { return 'select rejected it [' + optionSummary(f) + ']'; }
    } else if (f.type === 'checkbox') {
      if (f.checked) { return 'already'; }
      f.click();
      return 'ok';
    } else {
      if (f.value === 'Y') { return 'already'; }
      f.value = 'Y';
    }

    if (win.addchg_win0) { win.addchg_win0(f); }
    var Ev = win.Event || Event;
    f.dispatchEvent(new Ev('change', { bubbles: true }));
    if (f.onchange) { try { f.onchange(); } catch (e) { } }
    return 'ok';
  }

  /* PeopleSoft redraws a row when its attendance changes — that's where the
     Participated dropdown comes from. Setting the whole class in a tight loop
     therefore loses every change after the first, so go one at a time and wait
     for the page to settle in between. */
  function isBusy(doc) {
    var el = doc.getElementById('WAIT_win0') || doc.getElementById('processing');
    if (!el) { return false; }
    if (el.style && el.style.display === 'none') { return false; }
    return (el.offsetWidth > 0 || el.offsetHeight > 0);
  }

  function banner(doc) {
    var el = doc.createElement('div');
    el.style.cssText = 'position:fixed;top:14px;right:14px;z-index:2147483647;' +
      'background:#1a1a1a;color:#fff;font:13px system-ui,sans-serif;padding:10px 16px;' +
      'border-radius:8px;box-shadow:0 2px 12px rgba(0,0,0,.35);';
    (doc.body || doc.documentElement).appendChild(el);
    return el;
  }

  function apply(doc, rows, emplids, day, note) {
    var byId = {}, i;
    for (i = 0; i < rows.length; i++) { byId[rows[i].emplid] = rows[i].row; }

    var queue = [], missing = [];
    for (i = 0; i < emplids.length; i++) {
      var id = String(emplids[i]);
      if (byId[id] === undefined) { missing.push(id); } else { queue.push([id, byId[id]]); }
    }

    var marked = 0, already = 0, failed = [], at = 0, waits = 0;
    var tag = banner(doc);

    function finish() {
      if (tag.parentNode) { tag.parentNode.removeChild(tag); }
      var msg = 'Marked ' + marked + ' present for ' + day +
        ' (' + rows.length + ' students on this roster).';
      if (already) { msg += '\n' + already + ' were already marked.'; }
      if (missing.length) { msg += '\n\nNot on this roster: ' + missing.join(', '); }
      if (failed.length) {
        msg += '\n\nCould not set ' + failed.length + ' row(s). First: ' + failed[0];
      }
      if (note) { msg += '\n\n' + note; }
      msg += '\n\nCheck the grid, then click Save yourself.';
      alert(msg);
    }

    function step() {
      if (at >= queue.length) { finish(); return; }
      if (isBusy(doc) && waits < 80) { waits++; window.setTimeout(step, 250); return; }
      waits = 0;
      tag.textContent = 'Marking ' + (at + 1) + ' of ' + queue.length + '…';
      var entry = queue[at++];
      var res = mark(doc, entry[1]);
      if (res === 'ok') { marked++; }
      else if (res === 'already') { already++; }
      else { failed.push(entry[0] + ' (' + res + ')'); }
      window.setTimeout(step, 450);
    }

    step();
  }

  function noteFrom(msg) {
    var learn = msg.learn || {};
    var note = '';
    if (learn.unknown && learn.unknown.length) {
      note += learn.unknown.length + ' student(s) on this roster are not in your app.';
    }
    if (learn.conflicts && learn.conflicts.length) {
      note += (note ? '\n' : '') + 'ID conflicts: ' + learn.conflicts.join(', ');
    }
    if (msg.missing_emplid && msg.missing_emplid.length) {
      note += (note ? '\n' : '') + 'No ID yet for: ' + msg.missing_emplid.join(', ');
    }
    return note;
  }

  /* TAFE's CSP blocks fetch() to another origin, so the work happens in a popup on
     the app's own domain. It posts the answer back when it can — but the roster
     page's COOP may sever the link between the windows, in which case the popup
     shows the list for copying and we ask for a paste instead. */
  function viaBridge(doc, rows, day) {
    /* Only emplid and names travel — row indexes stay here, where they're used. */
    var sending = [];
    for (var i = 0; i < rows.length; i++) {
      sending.push({ emplid: rows[i].emplid, cells: rows[i].cells });
    }
    var keys = classKeys(doc);
    var url = BRIDGE + '?p=' + encodeURIComponent(JSON.stringify({
      date: day, rows: sending,
      class_nbr: keys.class_nbr || '', strm: keys.strm || '', label: keys.label || ''
    }));
    var win = window.open(url, 'attendance_bridge', 'width=520,height=440');
    var settled = false;
    function onMessage(e) {
      if (e.origin !== APP || settled) { return; }
      var msg = e.data || {};
      if (msg.type === 'result') {
        settled = true;
        window.removeEventListener('message', onMessage);
        try { win.close(); } catch (err) { }
        apply(doc, rows, msg.emplids || [], day, noteFrom(msg));
      } else if (msg.type === 'error') {
        settled = true;
        window.removeEventListener('message', onMessage);
        alert('The attendance app reported: ' + msg.message + '\n\nNothing was changed.');
      }
    }
    window.addEventListener('message', onMessage);

    /* Ask straight away rather than waiting on a reply that TAFE's settings
       usually prevent. The prompt only blocks this tab, so the popup keeps
       working behind it — copy from there whenever you're ready. */
    var lead = win
      ? 'Paste the ID list for ' + day + ' and press OK.'
        + '\n\nIt is in the window that just opened, already copied to your clipboard'
        + " — so Ctrl+V should do it. It's also on the class's PeopleSoft page"
        + ' in your attendance app.'
      : 'The popup was blocked — allow popups for this site, or copy the list from'
        + " the class's PeopleSoft page in your attendance app."
        + '\n\nPaste the ID list for ' + day + ' here:';

    var raw = window.prompt(lead, '');
    if (raw) {
      settled = true;
      window.removeEventListener('message', onMessage);
      var ids = raw.split(/[^0-9A-Za-z]+/).filter(function (s) { return s.length > 0; });
      apply(doc, rows, ids, day, 'Pasted manually.');
      try { if (win) { win.close(); } } catch (e) { }
      return;
    }

    /* Cancelled — leave the listener up briefly in case the windows can talk. */
    window.setTimeout(function () {
      if (!settled) { window.removeEventListener('message', onMessage); }
    }, 15000);
  }

  var docs = allDocs();
  var doc = gridDoc(docs);
  if (!doc) {
    var found = sampleIds(docs);
    alert('No attendance grid found (searched ' + docs.length + ' frame(s)).\n\n' +
      'Open a class roster, wait for it to finish loading, then click this bookmark.\n\n' +
      (found.length ? 'Fields seen:\n' + found.join('\n') : 'No grid fields seen at all.'));
    return;
  }

  var rows = readRows(doc);
  if (!rows.length) { alert('Found the grid but read no student IDs from it.'); return; }

  var day = meetingDate(doc);
  if (!day) { day = window.prompt('Could not read the meeting date. Enter it as YYYY-MM-DD:', ''); }
  if (!day) { return; }

  viaBridge(doc, rows, day);
})();
