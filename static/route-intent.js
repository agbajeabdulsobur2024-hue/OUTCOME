(function (root) {
  "use strict";

  /* Remembers where a visitor was heading when they had to sign in first, so the
     intended record and tab can be restored afterwards. It stores only a validated
     in-app hash such as #commitments/<id>/<tab>: never a full URL, never a token. */

  var KEY = "outcome-intended-route";
  var MAX_AGE_MS = 30 * 60 * 1000;
  var TABS = ["decision", "timeline", "amendments", "payments"];
  var MAX_ID = 128;

  function decode(value) {
    try { return decodeURIComponent(value); } catch (error) { return null; }
  }

  /* Returns { view, id, tab, hash } for a restorable route, or null. */
  function parse(hash, tabs) {
    var known = tabs || TABS;
    var text = String(hash || "");
    if (text.charAt(0) !== "#" || text.length > 400) return null;
    var parts = text.slice(1).split("/");
    var head = parts[0];
    if (head === "patterns-section" && parts.length === 1) return { view: "signals", id: null, tab: "decision", hash: "#patterns-section" };
    if (head === "new-commitment" && parts.length === 1) return { view: "new", id: null, tab: "decision", hash: "#new-commitment" };
    if (head !== "commitments" || parts.length > 3) return null;
    if (parts.length === 1) return { view: "commitments", id: null, tab: "decision", hash: "#commitments" };
    var id = decode(parts[1]);
    if (id === null || !id || id.length > MAX_ID || /[\u0000-\u001f\u007f\s/\\<>"'`]/.test(id)) return null;
    var tab = "decision";
    if (parts.length === 3) {
      // An unknown tab opens the record on its default tab, as the live router does.
      var wanted = decode(parts[2]);
      if (wanted !== null && known.indexOf(wanted) >= 0) tab = wanted;
    }
    var clean = "#commitments/" + encodeURIComponent(id) + (tab !== "decision" ? "/" + tab : "");
    return { view: "commitments", id: id, tab: tab, hash: clean };
  }

  function isRecordRoute(hash, tabs) {
    var route = parse(hash, tabs);
    return Boolean(route && route.id);
  }

  function save(storage, hash, now, tabs) {
    var route = parse(hash, tabs);
    if (!route || route.view === "overview") return null;
    try {
      storage.setItem(KEY, JSON.stringify({ hash: route.hash, at: Number(now) || Date.now() }));
    } catch (error) { return null; }
    return route;
  }

  function read(storage, now, tabs) {
    var raw;
    try { raw = storage.getItem(KEY); } catch (error) { return null; }
    if (!raw) return null;
    var stored;
    try { stored = JSON.parse(raw); } catch (error) { clear(storage); return null; }
    var at = Number(stored && stored.at);
    var age = (Number(now) || Date.now()) - at;
    var route = stored && typeof stored.hash === "string" ? parse(stored.hash, tabs) : null;
    if (!route || !Number.isFinite(at) || age < 0 || age > MAX_AGE_MS) { clear(storage); return null; }
    return route;
  }

  function clear(storage) {
    try { storage.removeItem(KEY); } catch (error) { /* nothing to clear */ }
  }

  /* A short, honest description for a notice. It never reveals record content. */
  function describe(route) {
    if (!route) return "";
    if (route.id) return "a commitment link";
    if (route.view === "signals") return "the repeated signals page";
    if (route.view === "new") return "the new commitment form";
    return "your commitments";
  }

  root.OutcomeRouteIntent = {
    KEY: KEY,
    MAX_AGE_MS: MAX_AGE_MS,
    TABS: TABS,
    parse: parse,
    isRecordRoute: isRecordRoute,
    save: save,
    read: read,
    clear: clear,
    describe: describe
  };
})(typeof window !== "undefined" ? window : globalThis);
