(function (root) {
  "use strict";

  /* Pure helpers for the Get started journey: sign in with a NEAR testnet wallet,
     open a personal workspace, record a first commitment. Nothing here talks to a
     network, and nothing here reports success. Success is only ever what the server
     reported back (an authenticated /api/session, a created workspace). */

  var STEPS = [
    { id: "signin", label: "Sign in" },
    { id: "workspace", label: "Open your workspace" },
    { id: "record", label: "Record your first commitment" }
  ];

  var IMPLICIT = /^[0-9a-f]{64}$/;
  var NAMED = /^[a-z0-9]([a-z0-9_-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9_-]{0,61}[a-z0-9])?)*$/;

  function fail(message) { return { ok: false, message: message }; }
  function pass() { return { ok: true, message: "" }; }

  /* Mirrors the server's own testnet rule, so a wrong account is explained before a round trip. */
  function validateAccount(accountId) {
    if (typeof accountId !== "string" || !accountId) return fail("The wallet did not return an account. Connect again.");
    if (accountId.length < 2 || accountId.length > 64) return fail("The wallet returned an account name that OUTCOME cannot use.");
    if (IMPLICIT.test(accountId)) return pass();
    if (accountId.slice(-5) === ".near" || accountId.slice(-8) !== ".testnet" || !NAMED.test(accountId)) {
      return fail("This preview only accepts a NEAR testnet account. A mainnet account was not connected, and nothing was paid.");
    }
    return pass();
  }

  function nonceLength(nonce) {
    try {
      var raw = typeof atob === "function" ? atob(nonce) : Buffer.from(nonce, "base64").toString("binary");
      return raw.length;
    } catch (error) { return -1; }
  }

  function validateChallenge(challenge) {
    var bad = "Sign-in could not start because the server's request was incomplete. Try again.";
    if (!challenge || typeof challenge !== "object") return fail(bad);
    if (typeof challenge.challenge_id !== "string" || challenge.challenge_id.length < 8 || challenge.challenge_id.length > 80) return fail(bad);
    if (typeof challenge.message !== "string" || !challenge.message) return fail(bad);
    if (typeof challenge.recipient !== "string" || !challenge.recipient) return fail(bad);
    if (typeof challenge.nonce !== "string" || nonceLength(challenge.nonce) !== 32) return fail(bad);
    return pass();
  }

  function validateSigned(signed, accountId) {
    if (!signed || typeof signed !== "object") return fail("The wallet did not return a signature. Nothing was changed.");
    if (signed.accountId && signed.accountId !== accountId) return fail("The wallet signed in with a different account. Connect again.");
    if (typeof signed.publicKey !== "string" || signed.publicKey.indexOf("ed25519:") !== 0) return fail("The wallet did not return a usable key. Nothing was changed.");
    if (typeof signed.signature !== "string" || !signed.signature) return fail("The wallet did not return a signature. Nothing was changed.");
    return pass();
  }

  /* The server must confirm the session before anything calls it signed in. */
  function validateSession(session, accountId) {
    if (!session || typeof session !== "object" || session.authenticated !== true) {
      return fail("Sign-in was not confirmed by the server. You are not signed in. Try again.");
    }
    if (accountId && session.account_id !== accountId) {
      return fail("The server confirmed a different account from the one you connected. Sign in again.");
    }
    return pass();
  }

  var CANCELLED = /reject|cancel|denied|declin|dismiss|closed|user abort|aborted by user/i;

  /* Turns any failure into a calm sentence. Raw server text is never shown for a 5xx. */
  function describeFailure(error, phase) {
    var status = error && error.status;
    var message = String((error && error.message) || "");
    var nothing = " Nothing was paid.";
    if (error && error.code === "validation") return { code: "validation", message: message, retryable: true };
    if (/connector did not load/i.test(message)) {
      return { code: "wallet_unavailable", message: "The wallet connector did not load. Check your connection or any content blocker, then try again." + nothing, retryable: true };
    }
    if (/testnet account|mainnet/i.test(message)) return { code: "wrong_network", message: message, retryable: true };
    if (!status && CANCELLED.test(message + " " + String((error && error.name) || ""))) {
      return { code: "cancelled", message: "Sign-in was cancelled in your wallet. Nothing was changed." + nothing, retryable: true };
    }
    if (error && error.network) return { code: "network", message: "OUTCOME could not be reached. Check your connection, then try again." + nothing, retryable: true };
    if (status === 429) return { code: "rate_limited", message: "Too many attempts from this network. Wait a few minutes, then try again.", retryable: true };
    if (status >= 500) {
      var what = phase === "workspace" ? "Your workspace could not be opened right now. Nothing was lost." : "Sign-in is unavailable right now.";
      return { code: "server", message: what + " Try again shortly." + nothing, retryable: true };
    }
    if (phase === "workspace" && (status === 401 || status === 403)) {
      return { code: "session_ended", message: "Your sign-in has ended. Sign in again to open your workspace.", retryable: true, signInAgain: true };
    }
    if (phase === "workspace" && status === 404) return { code: "not_found", message: "Your workspace could not be found. Try again.", retryable: true };
    if (status === 400 || status === 401 || status === 403 || status === 410 || status === 422) {
      return { code: "rejected", message: "OUTCOME could not verify the wallet's signature. Try signing in again." + nothing, retryable: true };
    }
    if (message && !/internal|traceback|exception|\/var\/|sql/i.test(message) && message.length <= 160) {
      return { code: "other", message: message, retryable: true };
    }
    return { code: "other", message: "Something went wrong. Try again." + nothing, retryable: true };
  }

  /* Which step of the journey a visitor is on, given what the server last reported. */
  function stageFor(session) {
    if (!session || session.authenticated !== true) return "signin";
    if (!(session.personal && session.personal.active)) return "workspace";
    return "record";
  }

  root.OutcomeStart = {
    STEPS: STEPS,
    validateAccount: validateAccount,
    validateChallenge: validateChallenge,
    validateSigned: validateSigned,
    validateSession: validateSession,
    describeFailure: describeFailure,
    stageFor: stageFor
  };
})(typeof window !== "undefined" ? window : globalThis);
