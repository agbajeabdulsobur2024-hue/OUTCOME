(function (root) {
  "use strict";

  var KEY = "outcome-welcome";
  var CARDS = [
    {
      id: "welcome",
      sketch: "promise",
      kicker: "Welcome",
      title: "Welcome to OUTCOME",
      body: "Know what was agreed. Verify what happened. Control what happens next. OUTCOME keeps the promise, the evidence, and the next decision in one record.",
      primary: "Continue"
    },
    {
      id: "commitments",
      sketch: "agreement",
      kicker: "Your commitments",
      title: "Record what was agreed",
      body: "A commitment is the original promise: who, what, how much, and on what condition. Describe it in ordinary language, review the extraction, and confirm it before anything is saved.",
      primary: "Continue"
    },
    {
      id: "evidence",
      sketch: "evidence",
      kicker: "Evidence and verification",
      title: "Establish what actually happened",
      body: "Evidence is appended and reviewed by a person. An unverified note does not erase a verified fact, and a review is not a payment.",
      primary: "Continue"
    },
    {
      id: "exposure",
      sketch: "exposure",
      kicker: "Discrepancies and exposure",
      title: "See the difference, and what it is worth",
      body: "When the outcome does not match the agreement, OUTCOME estimates the exposure. In the sample, 50 cartons were agreed, 42 were received, 8 are outstanding, and the estimate is $40. The estimate is not an invoice and not a transfer.",
      primary: "Continue"
    },
    {
      id: "decisions",
      sketch: "decision",
      kicker: "Intelligence and decisions",
      title: "Insight stays with a person",
      body: "OUTCOME can explain a record and recommend a next step. A person approves or refuses. An approval is recorded as APPROVED — NOT SUBMITTED unless a completed transfer exists. An explanation never authorises a payment.",
      primary: "Continue"
    },
    {
      id: "start",
      sketch: "start",
      kicker: "Get started",
      title: "Start with your own record, or a labelled sample",
      body: "Continue opens a form for your own commitment. Nothing is saved until you submit it. Or explore the Acme Supplies sample. Those records are synthetic and stay separate from yours. Signing in did not authorise a payment.",
      primary: "Continue"
    }
  ];

  function normalize(raw) {
    var status = raw && raw.status;
    if (status !== "new" && status !== "in_progress" && status !== "completed" && status !== "dismissed" && status !== "anonymous" && status !== "unavailable") {
      status = "new";
    }
    var step = Number(raw && raw.step);
    if (!Number.isInteger(step) || step < 0 || step >= CARDS.length) step = 0;
    return {
      status: status,
      step: step,
      accountId: raw && raw.accountId ? String(raw.accountId) : ""
    };
  }

  function readMap(storage) {
    try {
      var parsed = JSON.parse(storage.getItem(KEY) || "{}");
      return parsed && typeof parsed === "object" ? parsed : {};
    } catch (error) {
      return {};
    }
  }

  function recall(storage, accountId) {
    if (!accountId) return null;
    var entry = readMap(storage)[accountId];
    return entry ? normalize(entry) : null;
  }

  function remember(storage, accountId, state) {
    if (!accountId) return null;
    var all = readMap(storage);
    var next = normalize(Object.assign({}, state, { accountId: accountId }));
    all[accountId] = { status: next.status, step: next.step, accountId: accountId };
    storage.setItem(KEY, JSON.stringify(all));
    return recall(storage, accountId);
  }

  function resolve(server, local, accountId) {
    if (server && server.status && server.status !== "unavailable") {
      return normalize(Object.assign({}, server, { accountId: accountId || "" }));
    }
    if (local && local.accountId === accountId) return normalize(local);
    return normalize({ status: "new", step: 0, accountId: accountId || "" });
  }

  function shouldAutoPresent(state) {
    if (!state) return false;
    return state.status === "new" || state.status === "in_progress";
  }

  function sketch(name) {
    if (name === "promise") {
      return '<div class="mini-app" aria-hidden="true"><p class="mini-kicker">One record</p><p class="mini-title">Know what was agreed.</p><p class="mini-title">Verify what happened.</p><p class="mini-title">Control what happens&nbsp;next.</p></div>';
    }
    if (name === "agreement") {
      return '<div class="mini-app" aria-hidden="true"><p class="mini-kicker">Commitment</p><p class="mini-title">Northwind</p><p class="mini-line">10 boxes · USD 80 · due 20 Dec 2026</p><p class="mini-line">Release only after the obligation is verified.</p></div>';
    }
    if (name === "evidence") {
      return '<div class="mini-app" aria-hidden="true"><div class="mini-row"><span>Quantity received</span><b>42</b></div><div class="mini-row"><span>Review</span><b>Verified</b></div><p class="mini-line">A later unverified note does not clear this figure.</p></div>';
    }
    if (name === "exposure") {
      return '<div class="mini-app" aria-hidden="true"><p class="mini-kicker">Illustrative example</p><div class="mini-row"><span>Agreed</span><b>50 cartons</b></div><div class="mini-row"><span>Received</span><b>42</b></div><div class="mini-row"><span>Outstanding</span><b>8</b></div><div class="mini-row"><span>Estimated exposure</span><b>$40</b></div></div>';
    }
    if (name === "decision") {
      return '<div class="mini-app" aria-hidden="true"><p class="mini-kicker">Recommendation</p><p class="mini-title">Hold the release.</p><p class="mini-line">A person decides. APPROVED — NOT SUBMITTED is not a payment.</p></div>';
    }
    return '<div class="mini-app" aria-hidden="true"><div class="mini-choice"><b>Your commitment</b><span>Describe it, then confirm.</span></div><div class="mini-choice"><b>Labelled sample</b><span>Acme Supplies. Not your records.</span></div></div>';
  }

  root.OutcomeWelcome = {
    KEY: KEY,
    CARDS: CARDS,
    normalize: normalize,
    recall: recall,
    remember: remember,
    resolve: resolve,
    shouldAutoPresent: shouldAutoPresent,
    sketch: sketch
  };
})(typeof window !== "undefined" ? window : globalThis);
