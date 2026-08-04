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
        var nodes = tr.querySelectorAll('td, div, span, a, input');
        for (var j = 0; j < nodes.length; j++) {
          var t = cellText(nodes[j]);
          if (t.length >= 2 && t.length <= 60 && /[A-Za-z]/.test(t) && cells.indexOf(t) === -1) {
            cells.push(t);
          }
        }
      }
      out.push({ row: row, emplid: emplid, cells: cells.slice(0, 16) });
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

  /* Set the field the way the page's own handlers expect, so PeopleSoft runs its
     usual row logic (participation, date) instead of us reconstructing it. */
  function mark(doc, row) {
    var f = doc.getElementById(ATTEND + row);
    if (!f) { return 'no field'; }
    var win = doc.defaultView || window;

    if (f.tagName === 'SELECT') {
      var wanted = null;
      for (var i = 0; i < f.options.length; i++) {
        if (String(f.options[i].value).toUpperCase() === 'Y') { wanted = f.options[i].value; }
      }
      if (wanted === null) { return 'no "Y" option'; }
      if (f.value === wanted) { return 'already'; }
      f.value = wanted;
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

  function apply(doc, rows, emplids, day, note) {
    var byId = {};
    for (var i = 0; i < rows.length; i++) { byId[rows[i].emplid] = rows[i].row; }
    var marked = 0, already = 0, missing = [], failed = [];
    for (var k = 0; k < emplids.length; k++) {
      var id = String(emplids[k]);
      if (byId[id] === undefined) { missing.push(id); continue; }
      var res = mark(doc, byId[id]);
      if (res === 'ok') { marked++; }
      else if (res === 'already') { already++; }
      else { failed.push(id + ' (' + res + ')'); }
    }
    var msg = 'Marked ' + marked + ' present for ' + day + '.';
    if (already) { msg += '\n' + already + ' were already marked.'; }
    if (missing.length) { msg += '\n\nNot on this roster: ' + missing.join(', '); }
    if (failed.length) { msg += '\n\nCould not set: ' + failed.join(', '); }
    if (note) { msg += '\n\n' + note; }
    msg += '\n\nCheck the grid, then click Save yourself.';
    alert(msg);
  }

  function pasteFallback(doc, rows, day, why) {
    var raw = window.prompt(
      'Could not reach the attendance app (' + why + ').\n\n' +
      'Open your attendance app, copy the ID list for ' + day + ', and paste it here:', '');
    if (!raw) { return; }
    var ids = raw.split(/[^0-9A-Za-z]+/).filter(function (s) { return s.length > 0; });
    apply(doc, rows, ids, day, 'Pasted manually.');
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

  /* The TAFE page's CSP blocks fetch() to another origin, so we hand the work to
     a popup on the app's own domain and exchange postMessage, which CSP allows. */
  function viaBridge(doc, rows, day) {
    var win = window.open(BRIDGE, 'attendance_bridge', 'width=460,height=300');
    if (!win) {
      pasteFallback(doc, rows, day, 'popup blocked — allow popups for this site');
      return;
    }

    var settled = false;
    function finish() {
      settled = true;
      window.removeEventListener('message', onMessage);
      try { win.close(); } catch (e) { }
    }
    function onMessage(e) {
      if (e.origin !== APP || settled) { return; }
      var msg = e.data || {};
      if (msg.type === 'ready') {
        win.postMessage({ type: 'sync', rows: rows, date: day }, APP);
      } else if (msg.type === 'result') {
        finish();
        apply(doc, rows, msg.emplids || [], day, noteFrom(msg));
      } else if (msg.type === 'error') {
        finish();
        alert('The attendance app reported: ' + msg.message + '\n\nNothing was changed.');
      }
    }
    window.addEventListener('message', onMessage);
    window.setTimeout(function () {
      if (settled) { return; }
      finish();
      pasteFallback(doc, rows, day, 'no reply from the app');
    }, 20000);
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
