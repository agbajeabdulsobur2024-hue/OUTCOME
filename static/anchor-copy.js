(function (root) {
  "use strict";

  var CODE_HASH = "5UNnZ4KvwoCxHt5ADhJnkCchP2h1xR6fnCXPP8c1rgpr";
  var UPGRADE_TX = "FNzw1EPWRwepL2Bni1XH9Wz2nWJyqxeV4tPCpS5joXrc";
  var OWNER_EVENT_TX = "3N8VVzFr3QLm6iJKvS2twM4VxCWZyrec9FfqP2zNb2kj";
  var OWNER_EVENT_ID = "OUTCOME-DEMO-PO-104";

  function claim(policy) {
    var offer = policy === "any_account";
    var policyLine;
    if (offer) {
      policyLine = "The live contract accepts a record from any account. No tester signature has been written on chain yet. Connecting a wallet to sign is for this audit record only and does not authorise a payment.";
    } else if (policy === "pending") {
      policyLine = "Checking the live audit policy. A signature is offered only when that view returns any account.";
    } else if (!policy) {
      policyLine = "OUTCOME could not read the live audit policy, so a signature is not offered.";
    } else {
      policyLine = "The live policy does not accept a signature from this wallet, so a signature is not offered.";
    }
    return {
      offer: offer,
      policyLine: policyLine,
      digestLine: "This order’s digest is not on chain.",
      ownerLine: "The existing on-chain record is still an owner event, stored id " + OWNER_EVENT_ID + ". It is not this order’s digest.",
      codeHash: CODE_HASH,
      upgradeTx: UPGRADE_TX,
      ownerEventTx: OWNER_EVENT_TX,
      ownerEventId: OWNER_EVENT_ID
    };
  }

  root.OutcomeAnchor = {
    claim: claim,
    CODE_HASH: CODE_HASH,
    UPGRADE_TX: UPGRADE_TX,
    OWNER_EVENT_TX: OWNER_EVENT_TX,
    OWNER_EVENT_ID: OWNER_EVENT_ID
  };
})(typeof window !== "undefined" ? window : globalThis);
