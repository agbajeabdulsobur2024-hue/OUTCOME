"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function loadAnchor() {
  const source = fs.readFileSync(path.resolve(__dirname, "../static/anchor-copy.js"), "utf8");
  const context = { window: {}, globalThis: {} };
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(source, context);
  return context.window.OutcomeAnchor || context.OutcomeAnchor;
}

function main() {
  const anchor = loadAnchor();
  const offered = anchor.claim("any_account");
  assert(offered.offer === true, "a live any_account policy did not offer a signature");
  assert(offered.policyLine.includes("does not authorise a payment"), "signing was described as a payment");
  assert(offered.policyLine.includes("No tester signature has been written"), "a tester signature was claimed on chain");
  assert(!/not offered/i.test(offered.policyLine), "the offered signature also said it was not offered");
  assert(!/owner-only/i.test(offered.policyLine), "the live policy was still described as owner-only");
  assert(offered.digestLine.includes("not on chain"), "an unwritten digest was described as on chain");
  assert(offered.codeHash === "5UNnZ4KvwoCxHt5ADhJnkCchP2h1xR6fnCXPP8c1rgpr", "the live code hash was stale");
  assert(offered.upgradeTx === "FNzw1EPWRwepL2Bni1XH9Wz2nWJyqxeV4tPCpS5joXrc", "the upgrade transaction was missing");
  assert(offered.ownerEventId === "OUTCOME-DEMO-PO-104", "the surviving owner event was dropped");
  assert(offered.ownerLine.includes("owner event"), "the surviving record was not labelled as an owner event");

  const unread = anchor.claim("");
  assert(unread.offer === false, "a missing policy still offered a signature");
  assert(unread.policyLine.includes("could not read"), "a failed policy read was not fail-closed");
  assert(!/owner-only/i.test(unread.policyLine), "a failed read was described as owner-only");

  const closed = anchor.claim("closed");
  assert(closed.offer === false, "a non-matching policy still offered a signature");
  assert(closed.policyLine.includes("not offered"), "a closed policy still invited a signature");
  assert(anchor.claim("pending").offer === false, "a policy check in progress offered a signature");

  const html = fs.readFileSync(path.resolve(__dirname, "../static/index.html"), "utf8");
  assert(!html.includes("7KHQPNxsWM1vKCYR4b743c7ZAj9fVhcZcXSjoLKsRQaS"), "the page still cites the owner-gated code hash");
  assert(!html.includes("owner-only"), "the page still says the live contract is owner-only");
  assert(!html.includes("Tester signatures are not offered"), "the landing still says tester signatures are not offered");
  assert(!html.includes("a tester signature is not offered"), "the audit panel still says a tester signature is not offered");
  assert(html.includes("5UNnZ4KvwoCxHt5ADhJnkCchP2h1xR6fnCXPP8c1rgpr"), "the landing does not show the live code hash");
  assert(html.includes("FNzw1EPWRwepL2Bni1XH9Wz2nWJyqxeV4tPCpS5joXrc"), "the landing does not show the upgrade transaction");
  assert(html.includes("OUTCOME-DEMO-PO-104"), "the surviving owner event is missing");
  assert(html.includes("No tester signature has been written on chain yet"), "the page claims a tester signature was written");
  assert(html.includes("not written on chain"), "a digest that is not on chain is no longer described that way");
  assert(html.includes("claim.offer ?"), "the sign button is not gated on the live policy");
  assert(html.includes('src="/static/anchor-copy.js"'), "the page does not load the audit copy");

  console.log("audit copy matches the live upgrade and stays fail-closed");
}

main();
