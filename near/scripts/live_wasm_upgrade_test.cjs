#!/usr/bin/env node
"use strict";

// Sandbox replay of the wasm that is actually deployed at
// thewordweaver.testnet. Read-only RPC downloads that wasm and checks its
// code hash. No transaction is sent to testnet or mainnet.
//
// A sandbox account initialised by those bytes is not the live account.
// State created here can show that this branch's wasm loads state written
// by the live code and that rolling the same bytes back still reads it.
// It cannot show that OUTCOME-DEMO-PO-104 on thewordweaver.testnet survives
// a later deploy.

const crypto = require("crypto");
const fs = require("fs");
const os = require("os");
const path = require("path");
const nativeFetch = global.fetch;
const { Sandbox, DEFAULT_ACCOUNT_ID, DEFAULT_PRIVATE_KEY } = require("near-sandbox");
const nearAPI = require("near-api-js");
const { logSandboxFailure } = require("./sandbox_ready.cjs");
const nodeFetch = global.fetch;

const GAS = "100000000000000";
const BYTE = 10n ** 19n;
const SURPLUS = 10n ** 24n;
const LIVE_ACCOUNT = "thewordweaver.testnet";
const LIVE_CODE_HASH = "7KHQPNxsWM1vKCYR4b743c7ZAj9fVhcZcXSjoLKsRQaS";
const LIVE_RPC = process.env.NEAR_TESTNET_RPC || "https://rpc.testnet.near.org";
const NEW_WASM = process.env.OUTCOME_AUDIT_WASM || path.resolve(
  __dirname,
  "../contract/target/wasm32-unknown-unknown/release/outcome_audit_anchor.wasm"
);
const LIVE_EVENT = {
  commitment_id: "OUTCOME-DEMO-PO-104",
  event_type: "SUPPLIER_EVIDENCE_REVIEWED",
  payload_hash: "a94a6d3d62164f394847cf484cf32ba3caef8390ac0d88fba15e4c3efb60312f",
  previous_hash: "",
};

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function parseView(raw) {
  return JSON.parse(Buffer.from(raw.result).toString("utf8"));
}

async function rpc(method, params) {
  const response = await nativeFetch(LIVE_RPC, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", id: "outcome-readonly", method, params }),
  });
  const body = await response.json();
  if (!response.ok || body.error) {
    throw new Error("Read-only RPC " + method + " failed: " + JSON.stringify(body.error || response.status));
  }
  return body.result;
}

async function liveView(method, args) {
  return rpc("query", {
    request_type: "call_function",
    finality: "final",
    account_id: LIVE_ACCOUNT,
    method_name: method,
    args_base64: Buffer.from(JSON.stringify(args || {})).toString("base64"),
  });
}

async function downloadLiveWasm() {
  const account = await rpc("query", {
    request_type: "view_account",
    finality: "final",
    account_id: LIVE_ACCOUNT,
  });
  const code = await rpc("query", {
    request_type: "view_code",
    finality: "final",
    account_id: LIVE_ACCOUNT,
  });
  assert(account.code_hash === LIVE_CODE_HASH, "Live account code hash changed: " + account.code_hash);
  assert(code.hash === account.code_hash, "view_code hash does not match the account code hash");
  const wasm = Buffer.from(code.code_base64, "base64");
  const digest = nearAPI.utils.serialize.base_encode(crypto.createHash("sha256").update(wasm).digest());
  assert(digest === code.hash, "Downloaded wasm sha256 is " + digest + ", not the live code hash " + code.hash);
  const file = path.join(os.tmpdir(), "thewordweaver-live-" + digest + ".wasm");
  fs.writeFileSync(file, wasm);
  let policyError = "";
  const policy = await liveView("get_anchor_policy", {});
  if (!policy.error) {
    throw new Error("Live get_anchor_policy returned a value: " + JSON.stringify(policy).slice(0, 300));
  }
  policyError = String(policy.error);
  assert(policyError.includes("MethodNotFound"), "Live get_anchor_policy was not MethodNotFound: " + policyError);
  const latest = await liveView("get_latest", {});
  if (latest.error) throw new Error("Live get_latest failed: " + latest.error);
  const record = parseView(latest);
  assert(record.commitment_id === LIVE_EVENT.commitment_id, "Live record id changed");
  assert(record.event_type === LIVE_EVENT.event_type, "Live record type changed");
  assert(record.payload_hash === LIVE_EVENT.payload_hash, "Live payload hash changed");
  assert(record.actor === LIVE_ACCOUNT, "Live record actor changed");
  return { wasm, file, hash: digest, record };
}

async function view(provider, contractId, method, args) {
  const raw = await provider.query({
    request_type: "call_function",
    account_id: contractId,
    method_name: method,
    args_base64: Buffer.from(JSON.stringify(args || {})).toString("base64"),
    finality: "optimistic",
  });
  return parseView(raw);
}

async function expectFailure(work, needle) {
  try {
    await work();
  } catch (error) {
    const text = String(error && error.message || error);
    if (!text.includes(needle)) {
      throw new Error("Expected failure containing " + needle + " but got " + text.slice(0, 700));
    }
    return text;
  }
  throw new Error("Expected failure containing " + needle);
}

function transferDeposits(status) {
  const found = [];
  const visit = (node) => {
    if (!node || typeof node !== "object") return;
    if (node.Transfer && node.Transfer.deposit != null) {
      found.push(String(node.Transfer.deposit));
    }
    for (const value of Object.values(node)) visit(value);
  };
  visit(status);
  return found;
}

function sameRecord(event, fields, actor) {
  assert(event.commitment_id === fields.commitment_id, "commitment_id changed");
  assert(event.event_type === fields.event_type, "event_type changed");
  assert(event.payload_hash === fields.payload_hash, "payload_hash changed");
  assert(event.previous_hash === fields.previous_hash, "previous_hash changed");
  assert(event.actor === actor, "actor changed: " + event.actor);
  assert(typeof event.event_hash === "string" && event.event_hash.length === 64, "event hash was not readable");
}

async function main() {
  if (!fs.existsSync(NEW_WASM)) throw new Error("Build the branch wasm before this replay: " + NEW_WASM);
  const live = await downloadLiveWasm();
  process.env.NEAR_RPC_TIMEOUT_SECS = process.env.NEAR_RPC_TIMEOUT_SECS || "300";
  // near-api-js installs node-fetch on global.fetch at import. That body has
  // no cancel(), and near-sandbox's ready poll then times out even though the
  // process is up. Use the native fetch only for that poll.
  global.fetch = nativeFetch;
  const sandbox = await Sandbox.start({ version: "2.14.0" });
  global.fetch = nodeFetch;
  const ownerId = DEFAULT_ACCOUNT_ID;
  const summary = {
    label: "SANDBOX_REPLAY_OF_LIVE_WASM_NOT_A_TESTNET_TX",
    liveAccount: LIVE_ACCOUNT,
    liveCodeHash: live.hash,
    liveWasmBytes: live.wasm.length,
    liveRecord: live.record,
    sandboxOwner: ownerId,
    limit: "This replay uses state created under the live wasm in a local sandbox. It does not prove the on-chain OUTCOME-DEMO-PO-104 record survives until that account is upgraded.",
  };
  try {
    const newWasm = fs.readFileSync(NEW_WASM);
    assert(!newWasm.equals(live.wasm), "Branch wasm is byte-identical to the live wasm; the upgrade was not built");
    const keyStore = new nearAPI.keyStores.InMemoryKeyStore();
    await keyStore.setKey("sandbox", ownerId, nearAPI.KeyPair.fromString(DEFAULT_PRIVATE_KEY));
    const near = await nearAPI.connect({ networkId: "sandbox", keyStore, nodeUrl: sandbox.rpcUrl });
    const owner = await near.account(ownerId);
    const provider = near.connection.provider;

    await owner.deployContract(live.wasm);
    await owner.functionCall({
      contractId: ownerId,
      methodName: "new",
      args: { owner_id: ownerId },
      gas: GAS,
      attachedDeposit: "0",
    });
    await owner.functionCall({
      contractId: ownerId,
      methodName: "record_event",
      args: LIVE_EVENT,
      gas: GAS,
      attachedDeposit: "0",
    });
    const before = await view(provider, ownerId, "get_event", { sequence: 1 });
    sameRecord(before, LIVE_EVENT, ownerId);
    summary.sandboxEventHash = before.event_hash;

    const auditorKey = nearAPI.KeyPair.fromRandom("ed25519");
    const auditorId = "auditor." + ownerId;
    await keyStore.setKey("sandbox", auditorId, auditorKey);
    const BN = require("bn.js");
    await owner.createAccount(auditorId, auditorKey.getPublicKey(), new BN("50000000000000000000000000"));
    const auditor = await near.account(auditorId);
    summary.secondAccount = auditorId;
    await expectFailure(() => auditor.functionCall({
      contractId: ownerId,
      methodName: "record_event",
      args: {
        commitment_id: "ref-auditor-before",
        event_type: "human-decision",
        payload_hash: "cd".repeat(32),
        previous_hash: before.event_hash,
      },
      gas: GAS,
      attachedDeposit: "0",
    }), "Only the configured owner account may append");
    await expectFailure(() => view(provider, ownerId, "get_anchor_policy", {}), "MethodNotFound");
    summary.policyMissingBeforeUpgrade = true;

    await owner.deployContract(newWasm);
    const preserved = await view(provider, ownerId, "get_event", { sequence: 1 });
    sameRecord(preserved, LIVE_EVENT, ownerId);
    assert(preserved.event_hash === before.event_hash, "Upgrade changed the event hash");
    summary.statePreservedAfterUpgrade = true;
    summary.anchorPolicy = await view(provider, ownerId, "get_anchor_policy", {});
    assert(summary.anchorPolicy === "any_account", "Upgraded get_anchor_policy did not report any_account");
    assert(await view(provider, ownerId, "get_owner", {}) === ownerId, "Upgrade changed the owner");

    await owner.functionCall({
      contractId: ownerId,
      methodName: "record_event",
      args: {
        commitment_id: "ref-owner-after-upgrade",
        event_type: "decision-recorded",
        payload_hash: "ef".repeat(32),
        previous_hash: preserved.event_hash,
      },
      gas: GAS,
      attachedDeposit: "0",
    });
    const ownerAgain = await view(provider, ownerId, "get_event", { sequence: 2 });
    assert(ownerAgain.actor === ownerId && ownerAgain.event_type === "decision-recorded", "Owner could not append with a zero deposit after the upgrade");
    summary.ownerStillAppendsWithZeroDeposit = true;
    const tip = await view(provider, ownerId, "get_latest_hash", {});

    const shortArgs = {
      commitment_id: "m1",
      event_type: "human-decision",
      payload_hash: "cd".repeat(32),
      previous_hash: tip,
    };
    const shortQuote = await view(provider, ownerId, "get_required_storage_deposit", {
      ...shortArgs,
      actor_account_id: auditorId,
    });
    const longPreview = await view(provider, ownerId, "get_required_storage_deposit", {
      commitment_id: "milestone-acceptance-" + "a".repeat(24),
      event_type: "obligation-note",
      payload_hash: "12".repeat(32),
      previous_hash: tip,
      actor_account_id: auditorId,
    });
    summary.shortQuote = shortQuote;
    summary.longQuotePreview = longPreview;
    assert(shortQuote !== longPreview, "Storage quote did not change when the anchored strings changed");
    assert(BigInt(shortQuote) > 0n && BigInt(longPreview) > 0n, "Storage quote was not a positive amount");

    const sequenceBeforeRejects = await view(provider, ownerId, "get_next_sequence", {});
    await expectFailure(() => auditor.functionCall({
      contractId: ownerId,
      methodName: "record_event",
      args: shortArgs,
      gas: GAS,
      attachedDeposit: "0",
    }), "Insufficient storage deposit");
    await expectFailure(() => auditor.functionCall({
      contractId: ownerId,
      methodName: "record_event",
      args: shortArgs,
      gas: GAS,
      attachedDeposit: (BigInt(shortQuote) - BYTE).toString(),
    }), "Insufficient storage deposit");
    await expectFailure(() => auditor.functionCall({
      contractId: ownerId,
      methodName: "record_event",
      args: { ...shortArgs, payload_hash: "zz" },
      gas: GAS,
      attachedDeposit: shortQuote,
    }), "payload_hash must be 32-byte SHA-256 hex");
    await expectFailure(() => auditor.functionCall({
      contractId: ownerId,
      methodName: "record_event",
      args: { ...shortArgs, previous_hash: "11".repeat(32) },
      gas: GAS,
      attachedDeposit: shortQuote,
    }), "Audit chain predecessor does not match");
    const sequenceAfterRejects = await view(provider, ownerId, "get_next_sequence", {});
    assert(sequenceAfterRejects === sequenceBeforeRejects, "A rejected anchor advanced the sequence");
    summary.rejectsLeaveSequence = sequenceAfterRejects;

    const accepted = await auditor.functionCall({
      contractId: ownerId,
      methodName: "record_event",
      args: shortArgs,
      gas: GAS,
      attachedDeposit: shortQuote,
    });
    const auditorLog = (accepted.receipts_outcome || [])
      .flatMap((item) => (item.outcome && item.outcome.logs) || [])
      .find((line) => String(line).startsWith("OUTCOME_AUDIT_EVENT:"));
    summary.secondAccountStorageBytes = auditorLog ? auditorLog.split(":").pop() : null;
    assert(
      BigInt(summary.secondAccountStorageBytes) * BYTE === BigInt(shortQuote),
      "Charged deposit did not match the bytes this call measured"
    );
    const third = await view(provider, ownerId, "get_event", { sequence: 3 });
    assert(third.actor === auditorId && third.event_type === "human-decision", "Second account event was not stored");

    const surplusArgs = {
      commitment_id: "milestone-acceptance-" + "a".repeat(24),
      event_type: "obligation-note",
      payload_hash: "12".repeat(32),
      previous_hash: await view(provider, ownerId, "get_latest_hash", {}),
    };
    const surplusQuote = await view(provider, ownerId, "get_required_storage_deposit", {
      ...surplusArgs,
      actor_account_id: auditorId,
    });
    summary.surplusQuote = surplusQuote;
    assert(surplusQuote !== shortQuote, "Surplus event did not get its own storage quote");
    const attached = (BigInt(surplusQuote) + SURPLUS).toString();
    const surplusTx = await auditor.functionCall({
      contractId: ownerId,
      methodName: "record_event",
      args: surplusArgs,
      gas: GAS,
      attachedDeposit: attached,
    });
    const detailed = await provider.txStatusReceipts(surplusTx.transaction.hash, auditorId);
    const deposits = transferDeposits(detailed);
    summary.refundTransfers = deposits;
    assert(
      deposits.includes(SURPLUS.toString()),
      "Surplus deposit was not refunded. Transfers: " + deposits.join(",")
    );
    assert(
      !deposits.includes(attached),
      "The full attached deposit was returned, so the measured storage was not kept"
    );
    summary.surplusRefunded = true;
    const fourth = await view(provider, ownerId, "get_event", { sequence: 4 });
    assert(fourth.actor === auditorId && fourth.event_type === "obligation-note", "Surplus event was not stored");

    const stillFirst = await view(provider, ownerId, "get_event", { sequence: 1 });
    sameRecord(stillFirst, LIVE_EVENT, ownerId);
    assert(stillFirst.event_hash === before.event_hash, "The pre-upgrade event hash changed after later appends");

    await expectFailure(() => auditor.functionCall({
      contractId: ownerId,
      methodName: "admin_probe",
      args: {},
      gas: GAS,
      attachedDeposit: "0",
    }), "Only the configured owner account may run admin_probe");
    assert(await view(provider, ownerId, "get_admin_probe_count", {}) === 0, "admin_probe changed state for a non-owner");
    await owner.functionCall({
      contractId: ownerId,
      methodName: "admin_probe",
      args: {},
      gas: GAS,
      attachedDeposit: "0",
    });
    assert(await view(provider, ownerId, "get_admin_probe_count", {}) === 1, "Owner admin_probe did not run");
    await expectFailure(() => auditor.functionCall({
      contractId: ownerId,
      methodName: "admin_probe",
      args: {},
      gas: GAS,
      attachedDeposit: "0",
    }), "Only the configured owner account may run admin_probe");
    assert(await view(provider, ownerId, "get_admin_probe_count", {}) === 1, "A rejected admin_probe changed the count");
    summary.adminProbeOwnerOnly = true;

    await owner.deployContract(live.wasm);
    const rolledFirst = await view(provider, ownerId, "get_event", { sequence: 1 });
    const rolledThird = await view(provider, ownerId, "get_event", { sequence: 3 });
    const rolledFourth = await view(provider, ownerId, "get_event", { sequence: 4 });
    sameRecord(rolledFirst, LIVE_EVENT, ownerId);
    assert(rolledFirst.event_hash === before.event_hash, "Rollback wasm could not read the original event");
    assert(rolledThird.actor === auditorId, "Rollback wasm could not read the second account's event");
    assert(rolledFourth.event_type === "obligation-note", "Rollback wasm could not read the surplus event");
    await expectFailure(() => view(provider, ownerId, "get_anchor_policy", {}), "MethodNotFound");
    const rolledTip = await view(provider, ownerId, "get_latest_hash", {});
    const sequenceBeforeRollbackReject = await view(provider, ownerId, "get_next_sequence", {});
    await expectFailure(() => auditor.functionCall({
      contractId: ownerId,
      methodName: "record_event",
      args: {
        commitment_id: "ref-after-rollback",
        event_type: "note",
        payload_hash: "99".repeat(32),
        previous_hash: rolledTip,
      },
      gas: GAS,
      attachedDeposit: "0",
    }), "Only the configured owner account may append");
    assert(
      await view(provider, ownerId, "get_next_sequence", {}) === sequenceBeforeRollbackReject,
      "A rejected append after rollback advanced the sequence"
    );
    summary.rollbackRestoredOwnerGate = true;
    summary.recordsReadableAfterRollback = true;
    summary.getAnchorPolicyAfterRollback = "MethodNotFound";
    console.log(JSON.stringify(summary, null, 2));
  } finally {
    await sandbox.tearDown();
  }
}

main().catch((error) => {
  console.error("LIVE_WASM_UPGRADE_TEST_FAILED");
  console.error(error && error.stack ? error.stack : error);
  logSandboxFailure(error);
  process.exit(1);
});
