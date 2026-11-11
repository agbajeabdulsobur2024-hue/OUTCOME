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

function loadOnboarding() {
  const source = fs.readFileSync(path.resolve(__dirname, "../static/onboarding.js"), "utf8");
  const context = { window: {}, globalThis: {} };
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(source, context);
  return context.window.OutcomeOnboarding || context.OutcomeOnboarding;
}

function main() {
  const guide = loadOnboarding();
  const storage = memoryStorage();
  const fresh = guide.read(storage);
  assert(guide.shouldAutoStart(fresh), "a first visit did not start the guide");
  assert(fresh.status === "new" && fresh.step === 0, "a first visit was not at the start");

  const started = guide.begin(storage);
  assert(started.status === "in_progress" && started.step === 0, "begin did not record progress");
  assert(!guide.shouldAutoStart(guide.read(storage)), "an in-progress guide would start again");

  const moved = guide.move(storage, 3);
  assert(guide.read(storage).step === 3 && moved.status === "in_progress", "progress was not remembered");

  const done = guide.complete(storage);
  assert(done.status === "completed", "completion was not stored");
  assert(!guide.shouldAutoStart(guide.read(storage)), "a completed guide would interrupt a returning user");

  const again = guide.revisit(storage);
  assert(again.status === "in_progress" && again.step === 0, "revisit did not restart the guide");

  assert(guide.presentation(fresh) === "arrive", "a first visit did not arrive on the landing");
  assert(guide.presentation(started) === "arrive", "step zero still opened a tour");
  assert(guide.presentation(moved) === "resume", "a walk in progress would not resume");
  assert(guide.presentation(done) === "quiet", "a completed guide would interrupt a returning user");
  const progress = guide.progressFor(moved.step);
  assert(progress.total === guide.STEPS.filter((step) => step.place !== "landing").length, "progress still counted the landing");
  assert(progress.index === 2, "progress did not follow the visible moments");
  assert(guide.STEPS.filter((step) => step.place !== "landing").every((step) => step.focus), "a staged moment has nothing to point at");
  assert(guide.STEPS.every((step) => step.body.length <= 280), "guide copy is still a tutorial paragraph");

  guide.dismiss(storage, 2);
  assert(guide.read(storage).status === "dismissed", "dismiss was not stored");
  assert(!guide.shouldAutoStart(guide.read(storage)), "a dismissed guide would interrupt a returning user");
  assert(guide.presentation(guide.read(storage)) === "quiet", "a dismissed guide would open again");

  assert(guide.bannerFor("demo").includes("Sample"), "sample records were not labelled");
  assert(guide.bannerFor("personal") === "", "a personal workspace was labelled as a sample");
  assert(guide.bannerFor("demo") !== guide.bannerFor("personal"), "sample and real records used the same label");

  const walletSteps = guide.STEPS.filter((step) => step.wallet);
  assert(walletSteps.length === 1 && walletSteps[0].id === "real", "wallet was required before a personal record");
  assert(guide.STEPS[0].action === "enter-sample" && guide.STEPS[0].wallet === false, "the guide did not start without a wallet");
  assert(!guide.STEPS.slice(0, -1).some((step) => step.wallet), "an early step required a wallet");
  assert(guide.STEPS[guide.STEPS.length - 1].body.includes("does not authorise a payment"), "the wallet step did not say that connection is not a payment");
  assert(guide.STEPS.some((step) => step.body.includes("APPROVED — NOT SUBMITTED")), "approval copy dropped the not-submitted boundary");

  const html = fs.readFileSync(path.resolve(__dirname, "../static/index.html"), "utf8");
  const actions = html.slice(html.indexOf('class="landing-actions"'), html.indexOf('id="landingStatus"'));
  const getStarted = actions.indexOf('id="getStarted"');
  const revisit = actions.indexOf('id="exploreDemo"');
  assert(getStarted >= 0 && revisit > getStarted, "Get started was not the primary landing action");
  assert(actions.includes(">Get started<"), "the primary CTA was not Get started");
  assert(actions.includes(">Revisit the sample<"), "the sample was not a secondary action");
  assert(!actions.includes(">Explore Demo<"), "Explore Demo was still the landing CTA");
  assert(html.includes('id="guide"') && html.includes('id="sampleBanner"'), "the guide or sample banner is missing");
  assert(html.includes('src="/static/onboarding.js"'), "the page does not load the onboarding state");
  assert(html.includes('stage-progress'), "progress is not a slim indicator");
  assert(!html.includes("right:20px;bottom:20px"), "the guide is still a corner card");
  assert(!html.includes("Step ' +"), "progress is still announced as Step N of 9");
  assert(!html.includes("Loading commitments"), "the workspace still flashes Loading commitments");
  assert(html.indexOf('id="guide"') > html.indexOf('class="app-shell"'), "the guide sits on the landing page");
  assert(html.includes("OutcomeOnboarding.presentation"), "a first visit does not choose arrival, resume, or quiet");
  assert(!html.includes("shouldAutoStart"), "boot still starts the guide before Get started");
  assert(html.includes("function openTrial("), "a second sample request is not guarded");
  const enterSample = html.slice(html.indexOf("async function enterSample"), html.indexOf("function sampleOrder"));
  const seedDemo = html.slice(html.indexOf("async function seedDemo"), html.indexOf("async function createCommitment"));
  assert(enterSample.includes("openTrial("), "Get started can post a second trial");
  assert(seedDemo.includes("openTrial("), "Revisit the sample can post a second trial");

  let calls = 0;
  const slot = { current: null };
  const first = guide.shareInflight(slot, function () {
    calls += 1;
    return Promise.resolve("one");
  });
  const second = guide.shareInflight(slot, function () {
    calls += 1;
    return Promise.resolve("two");
  });
  assert(first === second, "a second trial started while the first was in flight");
  return first.then(function (value) {
    assert(value === "one" && calls === 1, "the shared trial did not keep the first result");
    console.log("onboarding starts once, remembers completion, and keeps the sample separate");
  });
}

main().catch(function (error) {
  console.error(error);
  process.exit(1);
});
