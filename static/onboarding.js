(function (root) {
  "use strict";

  var KEY = "outcome-guide";

  var STEPS = [
    {
      id: "introduce",
      place: "landing",
      focus: "",
      wallet: false,
      action: "enter-sample",
      kicker: "Get started",
      title: "See an agreement held to its evidence",
      body: "A labelled sample. No wallet. Nothing is paid.",
      primary: "Get started"
    },
    {
      id: "sample",
      place: "overview",
      focus: "order",
      wallet: false,
      action: "open-order",
      kicker: "Sample",
      title: "These records are not yours",
      body: "Acme Supplies is a labelled sample: two printer-paper orders. It stays out of a personal workspace.",
      primary: "Open the 50-carton order"
    },
    {
      id: "agreement",
      place: "commitment",
      focus: "facts",
      wallet: false,
      action: "next",
      kicker: "Agreement",
      title: "50 cartons. USD 250.",
      body: "PO-104 is due after verified full delivery. The terms stay with the evidence.",
      primary: "See the evidence"
    },
    {
      id: "evidence",
      place: "commitment",
      focus: "evidence",
      wallet: false,
      action: "next",
      kicker: "Evidence",
      title: "42 arrived, and a person verified it",
      body: "A later unverified note would not clear that shortfall. Verification is a review, not a payment.",
      primary: "See the exposure"
    },
    {
      id: "exposure",
      place: "commitment",
      focus: "exposure",
      wallet: false,
      action: "show-signals",
      kicker: "Exposure",
      title: "Eight cartons. USD 40.",
      body: "The estimate is not an invoice and not a transfer. The evidence does not match the commitment.",
      primary: "See the repeated signal"
    },
    {
      id: "signal",
      place: "signals",
      focus: "signal",
      wallet: false,
      action: "show-recommendation",
      kicker: "Signal",
      title: "The same gap, twice",
      body: "PO-109 is also short. A repeated signal is not a finding of intent, misconduct, or fraud.",
      primary: "See the recommendation"
    },
    {
      id: "recommendation",
      place: "commitment",
      focus: "next",
      wallet: false,
      action: "show-payments",
      kicker: "Recommendation",
      title: "A person decides what happens next",
      body: "Ask Acme for the missing 8 cartons or a credit note. OUTCOME does not send that request, and it does not move money.",
      primary: "See why USD 250 stays blocked"
    },
    {
      id: "approval",
      place: "payments",
      focus: "proposal",
      wallet: false,
      action: "next",
      kicker: "Blocked",
      title: "USD 250 cannot be approved",
      body: "Full delivery was not verified, so the proposal stays blocked. When a proposal is allowed, a human approval is recorded as APPROVED — NOT SUBMITTED. That record is not a payment.",
      primary: "Record my own commitment"
    },
    {
      id: "real",
      place: "workspace",
      focus: "sample",
      wallet: true,
      action: "connect-for-real",
      kicker: "Your records",
      title: "Keep yours out of the sample",
      body: "A personal workspace is separate from these Acme records. Connecting a NEAR testnet wallet names that workspace. It does not authorise a payment.",
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

  function presentation(state) {
    if (!state || state.status === "new") return "arrive";
    if (state.status === "dismissed" || state.status === "completed") return "quiet";
    if (state.status === "in_progress" && state.step > 0) return "resume";
    return "arrive";
  }

  function progressFor(stepIndex) {
    var visible = STEPS.filter(function (step) { return step.place !== "landing"; });
    var step = STEPS[stepIndex];
    var index = -1;
    for (var i = 0; i < visible.length; i += 1) {
      if (step && visible[i].id === step.id) index = i;
    }
    return { index: index, total: visible.length };
  }

  function shareInflight(slot, start) {
    if (slot && slot.current) return slot.current;
    var tracked;
    tracked = Promise.resolve().then(start).then(
      function (value) {
        if (slot.current === tracked) slot.current = null;
        return value;
      },
      function (error) {
        if (slot.current === tracked) slot.current = null;
        throw error;
      }
    );
    slot.current = tracked;
    return tracked;
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
    presentation: presentation,
    progressFor: progressFor,
    shareInflight: shareInflight,
    begin: begin,
    move: move,
    dismiss: dismiss,
    complete: complete,
    revisit: revisit,
    bannerFor: bannerFor
  };
})(typeof window !== "undefined" ? window : globalThis);
