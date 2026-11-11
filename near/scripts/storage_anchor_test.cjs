#!/usr/bin/env node
"use strict";

// Local sandbox proof for the storage-bounded record_event rules.
// This is not a testnet or mainnet transaction.

const fs = require("fs");
const path = require("path");
const { Sandbox, DEFAULT_ACCOUNT_ID, DEFAULT_PRIVATE_KEY } = require("near-sandbox");

const GAS = "100000000000000";
const BYTE = 10n ** 19n;
const WASM = process.env.OUTCOME_AUDIT_WASM || path.resolve(
  __dirname,
  "../contract/target/wasm32-unknown-unknown/release/outcome_audit_anchor.wasm"
);

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
      throw new Error("Expected failure containing " + needle + " but got " + text.slice(0, 500));
    }
    return;
  }
  throw new Error("Expected failure containing " + needle);
}

async function main() {
  if (!fs.existsSync(WASM)) {
    throw new Error("Contract wasm not found at " + WASM);
  }
  process.env.NEAR_RPC_TIMEOUT_SECS = process.env.NEAR_RPC_TIMEOUT_SECS || "300";
  const sandbox = await Sandbox.start({ version: "2.14.0" });
  const nearAPI = require("near-api-js");
  const ownerId = DEFAULT_ACCOUNT_ID;
  const summary = { label: "SANDBOX_NOT_TESTNET", ownerId };
  try {
    const wasm = fs.readFileSync(WASM);
    const keyStore = new nearAPI.keyStores.InMemoryKeyStore();
    const ownerKey = nearAPI.KeyPair.fromString(DEFAULT_PRIVATE_KEY);
    await keyStore.setKey("sandbox", ownerId, ownerKey);
    const near = await nearAPI.connect({ networkId: "sandbox", keyStore, nodeUrl: sandbox.rpcUrl });
    const owner = await near.account(ownerId);
    const provider = near.connection.provider;

    await owner.deployContract(wasm);
    await owner.functionCall({
      contractId: ownerId,
      methodName: "new",
      args: { owner_id: ownerId },
      gas: GAS,
      attachedDeposit: "0",
    });

    const first = await owner.functionCall({
      contractId: ownerId,
      methodName: "record_event",
      args: {
        commitment_id: "owner-event-1",
        event_type: "OWNER_RECORD",
        payload_hash: "ab".repeat(32),
        previous_hash: "",
      },
      gas: GAS,
      attachedDeposit: "0",
    });
    summary.ownerRecordTx = first.transaction.hash;
    const latestBeforeUpgrade = await view(provider, ownerId, "get_latest", {});
    const nextSequence = await view(provider, ownerId, "get_next_sequence", {});
    const latestHash = await view(provider, ownerId, "get_latest_hash", {});
    assert(
      latestBeforeUpgrade && latestBeforeUpgrade.commitment_id === "owner-event-1",
      "Owner event was not readable before upgrade. latest=" + JSON.stringify(latestBeforeUpgrade) + " next=" + JSON.stringify(nextSequence) + " hash=" + JSON.stringify(latestHash)
    );
    const ownerLog = (first.receipts_outcome || [])
      .flatMap((item) => (item.outcome && item.outcome.logs) || [])
      .find((line) => String(line).startsWith("OUTCOME_AUDIT_EVENT:"));
    summary.ownerStorageBytes = ownerLog ? ownerLog.split(":").pop() : null;

    await owner.deployContract(wasm);
    const preserved = await view(provider, ownerId, "get_event", { sequence: 1 });
    assert(preserved && preserved.commitment_id === "owner-event-1", "Upgrade wiped or changed the first event");
    summary.statePreservedAfterUpgrade = true;

    const testerKey = nearAPI.KeyPair.fromRandom("ed25519");
    const testerId = "tester." + ownerId;
    await keyStore.setKey("sandbox", testerId, testerKey);
    const BN = require("bn.js");
    await owner.createAccount(
      testerId,
      testerKey.getPublicKey(),
      new BN("2000000000000000000000000")
    );
    const tester = await near.account(testerId);
    const latest = await view(provider, ownerId, "get_latest_hash", {});
    const args = {
      commitment_id: "tester-event-2",
      event_type: "TESTER_RECORD",
      payload_hash: "cd".repeat(32),
      previous_hash: latest,
    };
    const quoted = await view(provider, ownerId, "get_required_storage_deposit", {
      ...args,
      actor_account_id: testerId,
    });
    summary.quotedDeposit = quoted;
    assert(/^[0-9]+$/.test(quoted) && BigInt(quoted) > 0n, "Storage quote was not a positive yocto amount");

    await expectFailure(() => tester.functionCall({
      contractId: ownerId,
      methodName: "record_event",
      args,
      gas: GAS,
      attachedDeposit: "0",
    }), "Insufficient storage deposit");

    const oneByteShort = (BigInt(quoted) - BYTE).toString();
    await expectFailure(() => tester.functionCall({
      contractId: ownerId,
      methodName: "record_event",
      args,
      gas: GAS,
      attachedDeposit: oneByteShort,
    }), "Insufficient storage deposit");

    await expectFailure(() => tester.functionCall({
      contractId: ownerId,
      methodName: "record_event",
      args: { ...args, payload_hash: "zz" },
      gas: GAS,
      attachedDeposit: quoted,
    }), "payload_hash must be 32-byte SHA-256 hex");

    await expectFailure(() => tester.functionCall({
      contractId: ownerId,
      methodName: "record_event",
      args: { ...args, previous_hash: "11".repeat(32) },
      gas: GAS,
      attachedDeposit: quoted,
    }), "Audit chain predecessor does not match");

    const accepted = await tester.functionCall({
      contractId: ownerId,
      methodName: "record_event",
      args,
      gas: GAS,
      attachedDeposit: quoted,
    });
    summary.testerRecordTx = accepted.transaction.hash;
    const testerLog = (accepted.receipts_outcome || [])
      .flatMap((item) => (item.outcome && item.outcome.logs) || [])
      .find((line) => String(line).startsWith("OUTCOME_AUDIT_EVENT:"));
    summary.testerStorageBytes = testerLog ? testerLog.split(":").pop() : null;
    if (summary.testerStorageBytes) {
      const measured = BigInt(summary.testerStorageBytes) * BYTE;
      assert(measured === BigInt(quoted), "Quoted deposit " + quoted + " did not match measured bytes " + summary.testerStorageBytes);
    }

    const stillFirst = await view(provider, ownerId, "get_event", { sequence: 1 });
    const second = await view(provider, ownerId, "get_event", { sequence: 2 });
    assert(stillFirst.commitment_id === "owner-event-1", "First event did not survive the tester append");
    assert(second.commitment_id === "tester-event-2", "Tester event was not stored");
    assert(second.actor === testerId, "Tester event actor was not the predecessor");
    summary.secondAccountAppended = true;

    await expectFailure(() => tester.functionCall({
      contractId: ownerId,
      methodName: "admin_probe",
      args: {},
      gas: GAS,
      attachedDeposit: "0",
    }), "Only the configured owner account may run admin_probe");
    const probeCount = await view(provider, ownerId, "get_admin_probe_count", {});
    assert(probeCount === 0, "admin_probe changed state for a non-owner");
    summary.adminProbeRejected = true;
    summary.anchorPolicy = await view(provider, ownerId, "get_anchor_policy", {});
    assert(summary.anchorPolicy === "any_account", "get_anchor_policy did not return any_account");

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
