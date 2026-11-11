(function (root) {
  "use strict";

  var KEY = "outcome-guide";

  var STEPS = [
    {
      id: "introduce",
      place: "landing",
      wallet: false,
      action: "enter-sample",
      kicker: "Get started",
      title: "See an agreement held to its evidence",
      body: "OUTCOME keeps what was agreed, what was verified, and what a person may do next on one record. A supplier order, a milestone, a repayment, or an expense uses that same record. This introduction uses a sample. It does not need a wallet, and it does not pay anyone.",
      primary: "Get started"
    },
    {
      id: "sample",
      place: "overview",
      wallet: false,
      action: "open-order",
      kicker: "Sample scenario",
      title: "These records are not your business",
      body: "Acme Supplies is one configuration: two printer-paper orders. The sample is labelled, and it stays out of a personal workspace. Open the 50-carton order to see the agreement itself.",
      primary: "Open the 50-carton order"
    },
    {
      id: "agreement",
      place: "commitment",
      wallet: false,
      action: "next",
      kicker: "Commitment",
      title: "The record is what was agreed",
      body: "PO-104 is 50 cartons for USD 250, due after verified full delivery. The terms stay with the evidence that follows. The type of obligation is a label. The checks do not change because this example is a supplier order.",
      primary: "See the evidence"
    },
    {
      id: "evidence",
      place: "commitment",
      wallet: false,
      action: "next",
      kicker: "Evidence and verification",
      title: "Evidence is appended, then a person verifies it",
      body: "The sample receiving record says 42 cartons arrived, and that fixture is marked verified. A later unverified note would not clear a verified shortfall. Verification is a review of the record. It is not a payment.",
      primary: "See the discrepancy"
    },
    {
      id: "exposure",
      place: "commitment",
      wallet: false,
      action: "show-signals",
      kicker: "Assessment",
      title: "The shortfall has an estimated exposure",
      body: "Eight cartons are missing. The assessment estimates USD 40 of exposure from the agreed price. That figure is not an invoice and not a transfer. The summary says the evidence does not match the commitment.",
      primary: "See the repeated signal"
    },
    {
      id: "signal",
      place: "signals",
      wallet: false,
      action: "show-recommendation",
      kicker: "Repeated signal",
      title: "A second short delivery is a signal, not a verdict",
      body: "PO-109 is also short. The signal is there because verified evidence repeated. It does not establish intent, misconduct, or fraud.",
      primary: "See the recommendation"
    },
    {
      id: "recommendation",
      place: "commitment",
      wallet: false,
      action: "show-payments",
      kicker: "Recommendation",
      title: "The next step is for a person",
      body: "The sample recommends asking the supplier for the missing 8 cartons or a credit note. OUTCOME does not send that request, and it does not move money.",
      primary: "See why USD 250 stays blocked"
    },
    {
      id: "approval",
      place: "payments",
      wallet: false,
      action: "next",
      kicker: "Approval",
      title: "A blocked proposal cannot be approved",
      body: "The sample still asks for the original USD 250. Full delivery was not verified, so the proposal stays blocked. When a proposal is allowed, a human approval is recorded as APPROVED — NOT SUBMITTED unless a real transfer exists. That record is not a payment.",
      primary: "Create my own commitment"
    },
    {
      id: "real",
      place: "workspace",
      wallet: true,
      action: "connect-for-real",
      kicker: "Your records",
      title: "Keep your commitment out of the sample",
      body: "A personal workspace is separate from these Acme records. Connecting a NEAR testnet wallet names that workspace. It does not authorise a payment, a transfer, or an on-chain signature. Tester signatures stay off while the live contract does not accept them.",
      primary: "Connect a wallet to record mine"
    }
  ];

  function blank() {
    return { status: "new", step: 0 };
  }

  function read(storage) {
    try {
      var raw = storage.getItem(KEY);
      if (!raw) return blank();
      var parsed = JSON.parse(raw);
      var status = parsed && parsed.status;
      if (status !== "in_progress" && status !== "completed" && status !== "dismissed") return blank();
      var step = Number(parsed.step);
      if (!Number.isInteger(step) || step < 0 || step >= STEPS.length) step = 0;
      return { status: status, step: step };
    } catch (error) {
      return blank();
    }
  }

  function write(storage, state) {
    storage.setItem(KEY, JSON.stringify({ status: state.status, step: state.step }));
    return read(storage);
  }

  function shouldAutoStart(state) {
    return !state || state.status === "new";
  }

  function begin(storage) {
    return write(storage, { status: "in_progress", step: 0 });
  }

  function move(storage, step) {
    var bounded = Math.max(0, Math.min(STEPS.length - 1, step));
    return write(storage, { status: "in_progress", step: bounded });
  }

  function dismiss(storage, step) {
    var current = Number(step);
    if (!Number.isInteger(current) || current < 0) current = 0;
    return write(storage, { status: "dismissed", step: current });
  }

  function complete(storage) {
    return write(storage, { status: "completed", step: STEPS.length - 1 });
  }

  function revisit(storage) {
    return begin(storage);
  }

  function bannerFor(scope) {
    if (scope === "demo") {
      return "Sample scenario. These records are synthetic. They are not your business, and they stay separate from a personal workspace.";
    }
    return "";
  }

  root.OutcomeOnboarding = {
    KEY: KEY,
    STEPS: STEPS,
    read: read,
    shouldAutoStart: shouldAutoStart,
    begin: begin,
    move: move,
    dismiss: dismiss,
    complete: complete,
    revisit: revisit,
    bannerFor: bannerFor
  };
})(typeof window !== "undefined" ? window : globalThis);
