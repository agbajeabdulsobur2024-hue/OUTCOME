"use strict";

// Regression for the landing-page boot crash:
// whenWallet(...).catch is not a function
// when OutcomeWallet is already loaded. subscribe() returns an unsubscribe
// function. boot() must still be able to attach .catch and continue.

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const htmlPath = path.resolve(__dirname, "../static/index.html");
const html = fs.readFileSync(htmlPath, "utf8");

function extractFunction(source, name) {
  const marker = "function " + name + "(";
  const start = source.indexOf(marker);
  if (start < 0) throw new Error(name + " was not found in static/index.html");
  const brace = source.indexOf("{", start);
  let depth = 0;
  for (let i = brace; i < source.length; i += 1) {
    if (source[i] === "{") depth += 1;
    else if (source[i] === "}") {
      depth -= 1;
      if (depth === 0) return source.slice(start, i + 1);
    }
  }
  throw new Error(name + " is unclosed in static/index.html");
}

function loadWhenWallet(windowObject, clock) {
  const source = extractFunction(html, "whenWallet");
  const context = {
    window: windowObject,
    Error,
    Promise,
    Date: clock ? { now: () => clock.now } : Date,
    setInterval: clock
      ? (fn) => {
          const id = clock.timers.length;
          clock.timers.push(setInterval(() => {
            clock.now += clock.step;
            fn();
          }, 5));
          return id;
        }
      : setInterval,
    clearInterval: clock
      ? (id) => clearInterval(clock.timers[id])
      : clearInterval,
  };
  vm.createContext(context);
  vm.runInContext(source + "\nthis.whenWallet = whenWallet;", context);
  return context.whenWallet;
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

async function alreadyLoaded() {
  let subscribed = 0;
  const whenWallet = loadWhenWallet({
    OutcomeWallet: {
      subscribe() {
        subscribed += 1;
        return function unsubscribe() {};
      },
    },
  });
  let threw = null;
  let pending;
  try {
    pending = whenWallet((wallet) => wallet.subscribe(() => {}));
    pending.catch(() => {});
  } catch (error) {
    threw = error;
  }
  assert(!threw, "boot-style .catch threw while the connector was already loaded: " + (threw && threw.message));
  assert(pending && typeof pending.then === "function" && typeof pending.catch === "function", "whenWallet did not return a Promise");
  const unsubscribe = await pending;
  assert(typeof unsubscribe === "function", "subscribe's unsubscribe function was not the promise result");
  assert(subscribed === 1, "subscribe was not called when the connector was already loaded");

  let rejected = null;
  try {
    await whenWallet(() => {
      throw new Error("connector failed");
    });
  } catch (error) {
    rejected = error;
  }
  assert(rejected && rejected.message === "connector failed", "a synchronous connector failure did not reject the promise");
}

async function appearsLater() {
  const walletWindow = {};
  const whenWallet = loadWhenWallet(walletWindow);
  let subscribed = 0;
  const pending = whenWallet((wallet) => {
    subscribed += 1;
    return wallet.subscribe(() => {});
  });
  assert(typeof pending.catch === "function", "whenWallet did not return a Promise while the connector was still loading");
  setTimeout(() => {
    walletWindow.OutcomeWallet = {
      subscribe() {
        return function unsubscribe() {};
      },
    };
  }, 30);
  const unsubscribe = await pending;
  assert(typeof unsubscribe === "function", "a late connector load did not resolve to the unsubscribe function");
  assert(subscribed === 1, "subscribe was not called after the connector loaded");
}

async function timesOut() {
  const clock = { now: 0, step: 1000, timers: [] };
  const whenWallet = loadWhenWallet({}, clock);
  let rejected = null;
  try {
    await whenWallet(() => {
      throw new Error("should not run");
    });
  } catch (error) {
    rejected = error;
  } finally {
    for (const timer of clock.timers) clearInterval(timer);
  }
  assert(
    rejected && rejected.message === "The wallet connector did not load. Check your connection and try again.",
    "a missing connector did not reject: " + (rejected && rejected.message)
  );
}

async function bootContinues() {
  const whenWallet = loadWhenWallet({
    OutcomeWallet: {
      subscribe() {
        return function unsubscribe() {};
      },
    },
  });
  async function boot() {
    whenWallet((wallet) => wallet.subscribe(() => {})).catch(() => {});
    return "showLanding";
  }
  assert(await boot() === "showLanding", "boot did not reach the landing decision");
}

async function main() {
  await alreadyLoaded();
  await appearsLater();
  await timesOut();
  await bootContinues();
  console.log("whenWallet returns a Promise when the connector is already loaded");
}

main().catch((error) => {
  console.error(error && error.stack ? error.stack : error);
  process.exit(1);
});
