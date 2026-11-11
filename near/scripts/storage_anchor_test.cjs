#!/usr/bin/env node
"use strict";

// Local sandbox proof for the storage-bounded record_event upgrade.
// This is not a testnet or mainnet transaction. It never talks to
// thewordweaver.testnet.

const { execFileSync } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { Sandbox, DEFAULT_ACCOUNT_ID, DEFAULT_PRIVATE_KEY } = require("near-sandbox");

const GAS = "100000000000000";
const BYTE = 10n ** 19n;
const REPO = path.resolve(__dirname, "../..");
const NEW_WASM = process.env.OUTCOME_AUDIT_WASM || path.resolve(
  __dirname,
  "../contract/target/wasm32-unknown-unknown/release/outcome_audit_anchor.wasm"
);
const OWNER_GATED_COMMIT = "299bed3";

function parseView(raw) {
  return JSON.parse(Buffer.from(raw.result).toString("utf8"));
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

function assert(condition, message) {
  if (!condition) throw new Error(message);
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

function buildOwnerGatedWasm() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "outcome-owner-gated-"));
  fs.mkdirSync(path.join(dir, "src"));
  const source = execFileSync("git", ["show", OWNER_GATED_COMMIT + ":near/contract/src/lib.rs"], {
    cwd: REPO,
    encoding: "utf8",
  });
  if (!source.includes("Only the configured owner account may append audit events")) {
    throw new Error("Refusing to build a previous wasm that is not the owner-gated contract");
  }
  fs.writeFileSync(path.join(dir, "src", "lib.rs"), source);
  fs.copyFileSync(path.join(REPO, "near", "contract", "Cargo.toml"), path.join(dir, "Cargo.toml"));
  execFileSync("cargo", ["+1.93.0", "build", "--target", "wasm32-unknown-unknown", "--release", "--quiet"], {
    cwd: dir,
    stdio: "inherit",
  });
  return path.join(dir, "target", "wasm32-unknown-unknown", "release", "outcome_audit_anchor.wasm");
}

async function main() {
  if (!fs.existsSync(NEW_WASM)) {
    throw new Error("Contract wasm not found at " + NEW_WASM);
  }
  const previousWasmPath = process.env.OUTCOME_PREVIOUS_WASM || buildOwnerGatedWasm();
  process.env.NEAR_RPC_TIMEOUT_SECS = process.env.NEAR_RPC_TIMEOUT_SECS || "300";
  const sandbox = await Sandbox.start({ version: "2.14.0" });
  const nearAPI = require("near-api-js");
  const ownerId = DEFAULT_ACCOUNT_ID;
  const summary = {
    label: "SANDBOX_NOT_TESTNET",
    ownerId,
    previousSource: OWNER_GATED_COMMIT,
    newWasm: path.basename(NEW_WASM),
  };
  try {
    const previousWasm = fs.readFileSync(previousWasmPath);
    const newWasm = fs.readFileSync(NEW_WASM);
    const keyStore = new nearAPI.keyStores.InMemoryKeyStore();
    const ownerKey = nearAPI.KeyPair.fromString(DEFAULT_PRIVATE_KEY);
    await keyStore.setKey("sandbox", ownerId, ownerKey);
    const near = await nearAPI.connect({ networkId: "sandbox", keyStore, nodeUrl: sandbox.rpcUrl });
    const owner = await near.account(ownerId);
    const provider = near.connection.provider;

    await owner.deployContract(previousWasm);
    await owner.functionCall({
      contractId: ownerId,
      methodName: "new",
      args: { owner_id: ownerId },
      gas: GAS,
      attachedDeposit: "0",
    });

    const firstArgs = {
      commitment_id: "ref-6f3a9c",
      event_type: "evidence-recorded",
      payload_hash: "ab".repeat(32),
      previous_hash: "",
    };
    const first = await owner.functionCall({
      contractId: ownerId,
      methodName: "record_event",
      args: firstArgs,
      gas: GAS,
      attachedDeposit: "0",
    });
    summary.ownerRecordTx = first.transaction.hash;
    const before = await view(provider, ownerId, "get_event", { sequence: 1 });
    assert(before.commitment_id === firstArgs.commitment_id, "Owner event was not stored on the owner-gated contract");
    assert(before.event_type === "evidence-recorded", "Owner-gated contract rejected an opaque event type");

    const auditorKey = nearAPI.KeyPair.fromRandom("ed25519");
    const auditorId = "auditor." + ownerId;
    await keyStore.setKey("sandbox", auditorId, auditorKey);
    const BN = require("bn.js");
    await owner.createAccount(auditorId, auditorKey.getPublicKey(), new BN("2000000000000000000000000"));
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
    assert(preserved.commitment_id === before.commitment_id, "Upgrade changed the stored commitment reference");
    assert(preserved.event_type === before.event_type, "Upgrade changed the stored event type");
    assert(preserved.payload_hash === before.payload_hash, "Upgrade changed the stored payload hash");
    assert(preserved.actor === ownerId, "Upgrade changed the stored actor");
    assert(preserved.event_hash === before.event_hash, "Upgrade changed the stored event hash");
    summary.statePreservedAfterUpgrade = true;
    summary.anchorPolicy = await view(provider, ownerId, "get_anchor_policy", {});
    assert(summary.anchorPolicy === "any_account", "Upgraded get_anchor_policy did not report any_account");

    const ownerAgain = await owner.functionCall({
      contractId: ownerId,
      methodName: "record_event",
      args: {
        commitment_id: "ref-decision-1",
        event_type: "decision-recorded",
        payload_hash: "ef".repeat(32),
        previous_hash: preserved.event_hash,
      },
      gas: GAS,
      attachedDeposit: "0",
    });
    summary.ownerStillAppendsWithZeroDeposit = Boolean(ownerAgain.transaction && ownerAgain.transaction.hash);
    const tip = await view(provider, ownerId, "get_latest_hash", {});

    const shortArgs = {
      commitment_id: "m1",
      event_type: "human-decision",
      payload_hash: "cd".repeat(32),
      previous_hash: tip,
    };
    const longArgs = {
      commitment_id: "milestone-acceptance-" + "a".repeat(24),
      event_type: "obligation-note",
      payload_hash: "12".repeat(32),
      previous_hash: tip,
    };
    const shortQuote = await view(provider, ownerId, "get_required_storage_deposit", {
      ...shortArgs,
      actor_account_id: auditorId,
    });
    const longQuote = await view(provider, ownerId, "get_required_storage_deposit", {
      ...longArgs,
      actor_account_id: auditorId,
    });
    summary.shortQuote = shortQuote;
    summary.longQuote = longQuote;
    assert(shortQuote !== longQuote, "Storage quote did not change when the anchored strings changed");
    assert(BigInt(shortQuote) > 0n && BigInt(longQuote) > 0n, "Storage quote was not a positive amount");

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

    const accepted = await auditor.functionCall({
      contractId: ownerId,
      methodName: "record_event",
      args: shortArgs,
      gas: GAS,
      attachedDeposit: shortQuote,
    });
    summary.secondAccountTx = accepted.transaction.hash;
    const auditorLog = (accepted.receipts_outcome || [])
      .flatMap((item) => (item.outcome && item.outcome.logs) || [])
      .find((line) => String(line).startsWith("OUTCOME_AUDIT_EVENT:"));
    summary.secondAccountStorageBytes = auditorLog ? auditorLog.split(":").pop() : null;
    assert(
      BigInt(summary.secondAccountStorageBytes) * BYTE === BigInt(shortQuote),
      "Charged deposit did not match the bytes this call measured"
    );
    const longAccepted = await auditor.functionCall({
      contractId: ownerId,
      methodName: "record_event",
      args: { ...longArgs, previous_hash: await view(provider, ownerId, "get_latest_hash", {}) },
      gas: GAS,
      attachedDeposit: longQuote,
    });
    summary.differentEventTypeAccepted = Boolean(longAccepted.transaction && longAccepted.transaction.hash);

    const stillFirst = await view(provider, ownerId, "get_event", { sequence: 1 });
    const second = await view(provider, ownerId, "get_event", { sequence: 2 });
    const third = await view(provider, ownerId, "get_event", { sequence: 3 });
    const fourth = await view(provider, ownerId, "get_event", { sequence: 4 });
    assert(stillFirst.commitment_id === "ref-6f3a9c", "The pre-upgrade event did not survive later appends");
    assert(second.event_type === "decision-recorded" && second.actor === ownerId, "Owner could not append after the upgrade");
    assert(third.actor === auditorId && third.event_type === "human-decision", "Second account event was not stored");
    assert(fourth.event_type === "obligation-note" && fourth.actor === auditorId, "A second opaque event type was rejected");
    summary.secondAccountAppended = true;

    await expectFailure(() => auditor.functionCall({
      contractId: ownerId,
      methodName: "admin_probe",
      args: {},
      gas: GAS,
      attachedDeposit: "0",
    }), "Only the configured owner account may run admin_probe");
    assert(await view(provider, ownerId, "get_admin_probe_count", {}) === 0, "admin_probe changed state for a non-owner");
    summary.adminProbeRejected = true;

    await owner.deployContract(previousWasm);
    const rolledFirst = await view(provider, ownerId, "get_event", { sequence: 1 });
    const rolledThird = await view(provider, ownerId, "get_event", { sequence: 3 });
    assert(rolledFirst.event_hash === stillFirst.event_hash, "Rollback wasm could not read the original event");
    assert(rolledThird.actor === auditorId, "Rollback wasm could not read an event written by the upgraded code");
    await expectFailure(() => view(provider, ownerId, "get_anchor_policy", {}), "MethodNotFound");
    const rolledTip = await view(provider, ownerId, "get_latest_hash", {});
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
    summary.rollbackRestoredOwnerGate = true;
    summary.recordsReadableAfterRollback = true;

    console.log(JSON.stringify(summary, null, 2));
  } finally {
    await sandbox.tearDown();
  }
}

main().catch((error) => {
  console.error("STORAGE_ANCHOR_TEST_FAILED");
  console.error(error && error.stack ? error.stack : error);
  process.exit(1);
});
