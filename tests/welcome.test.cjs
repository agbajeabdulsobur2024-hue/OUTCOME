"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function memoryStorage() {
  const data = new Map();
  return {
    getItem(key) { return data.has(key) ? data.get(key) : null; },
    setItem(key, value) { data.set(key, String(value)); },
  };
}

function loadWelcome() {
  const source = fs.readFileSync(path.resolve(__dirname, "../static/welcome.js"), "utf8");
  const context = { window: {}, globalThis: {} };
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(source, context);
  return context.window.OutcomeWelcome || context.OutcomeWelcome;
}

function main() {
  const welcome = loadWelcome();
  assert(welcome.CARDS.length === 6, "the introduction is not six cards");
  assert(welcome.CARDS[0].title === "Welcome to OUTCOME", "the first card is not the welcome");
  assert(welcome.CARDS[welcome.CARDS.length - 1].id === "start", "the last card does not offer a start");
  assert(welcome.CARDS.every((card) => card.title && card.body && card.primary === "Continue" && card.sketch), "a card is missing its message or continue");
  assert(welcome.CARDS.some((card) => card.body.includes("APPROVED — NOT SUBMITTED")), "approval was described as a payment");
  assert(welcome.CARDS.some((card) => card.body.includes("$40") && card.body.includes("50 cartons")), "the exposure card lost the sample figures");
  assert(welcome.sketch("exposure").includes("$40"), "the exposure illustration lost the estimate");
  assert(welcome.CARDS.every((card) => welcome.sketch(card.sketch).includes("mini-app")), "a card has no product illustration");

  const storage = memoryStorage();
  assert(welcome.recall(storage, "alice.testnet") === null, "an empty browser invented progress");
  welcome.remember(storage, "alice.testnet", { status: "completed", step: 5 });
  welcome.remember(storage, "bob.testnet", { status: "in_progress", step: 2 });
  assert(welcome.recall(storage, "alice.testnet").status === "completed", "Alice's completion was lost");
  assert(welcome.recall(storage, "bob.testnet").step === 2, "Bob's progress was overwritten by Alice");
  assert(welcome.recall(storage, "alice.testnet").accountId === "alice.testnet", "a cached record lost its account");

  const fresh = welcome.resolve({ status: "new", step: 0 }, welcome.recall(storage, "alice.testnet"), "alice.testnet");
  assert(fresh.status === "new" && fresh.step === 0, "browser cache overrode a server record of a new account");
  const done = welcome.resolve({ status: "completed", step: 5 }, { status: "in_progress", step: 1, accountId: "alice.testnet" }, "alice.testnet");
  assert(done.status === "completed", "a completed server record would show the introduction again");
  const resumed = welcome.resolve({ status: "in_progress", step: 3 }, null, "carol.testnet");
  assert(resumed.status === "in_progress" && resumed.step === 3, "midway progress was not resumed");
  const offline = welcome.resolve({ status: "unavailable" }, welcome.recall(storage, "bob.testnet"), "bob.testnet");
  assert(offline.status === "in_progress" && offline.step === 2, "a failed read discarded the same account's cache");
  const other = welcome.resolve({ status: "unavailable" }, welcome.recall(storage, "alice.testnet"), "bob.testnet");
  assert(other.status === "new", "another account's cache was reused");

  assert(welcome.shouldAutoPresent({ status: "new", step: 0 }), "a first authenticated visit would not open");
  assert(welcome.shouldAutoPresent({ status: "in_progress", step: 4 }), "a paused introduction would not resume");
  assert(!welcome.shouldAutoPresent({ status: "completed", step: 5 }), "a completed introduction would open again");
  assert(!welcome.shouldAutoPresent({ status: "dismissed", step: 1 }), "a skipped introduction would open again");
  assert(!welcome.shouldAutoPresent({ status: "anonymous", step: 0 }), "a visitor was treated as a signed-in first visit");
  assert(!welcome.shouldAutoPresent(null), "a missing session opened the signed-in introduction");

  const bounded = welcome.normalize({ status: "completed", step: 99 });
  assert(bounded.step === 0 && bounded.status === "completed", "an invalid step was kept");
  assert(welcome.normalize({ status: "nope", step: 1 }).status === "new", "an unknown status was trusted");

  console.log("welcome state is per account and the server wins");
}

main();
