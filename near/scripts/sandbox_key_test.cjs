#!/usr/bin/env node
"use strict";

// Local nearcore sandbox evidence. This is not a testnet transaction.
// Uses the published near-sandbox development account. Do not point it at testnet.

const fs = require("fs");
const path = require("path");
const { Sandbox, DEFAULT_ACCOUNT_ID, DEFAULT_PRIVATE_KEY } = require("near-sandbox");
const nearAPI = require("near-api-js");
const { isMethodNameMismatch, rejectionHash } = require("./access_key_policy.cjs");

const GAS = "100000000000000";
const ALLOWANCE = "250000000000000000000000";
const WASM = process.env.OUTCOME_AUDIT_WASM || path.resolve(
  __dirname,
  "../contract/target/wasm32-unknown-unknown/release/outcome_audit_anchor.wasm"
);

function parseView(raw) {
  return JSON.parse(Buffer.from(raw.result).toString("utf8"));
}

async function view(provider, contractId, method) {
  const raw = await provider.query({
    request_type: "call_function",
    account_id: contractId,
    method_name: method,
    args_base64: Buffer.from("{}").toString("base64"),
    finality: "final",
  });
  return parseView(raw);
}

async function main() {
  if (!fs.existsSync(WASM)) {
    throw new Error("Contract wasm not found at " + WASM + ". Build it before this sandbox test.");
  }
  process.env.NEAR_RPC_TIMEOUT_SECS = process.env.NEAR_RPC_TIMEOUT_SECS || "300";
  const externalRpc = process.env.SANDBOX_RPC || "";
  const sandbox = externalRpc ? null : await Sandbox.start({ version: "2.14.0" });
  const rpcUrl = externalRpc || sandbox.rpcUrl;
  const ownerId = DEFAULT_ACCOUNT_ID;
  const summary = {
    label: "SANDBOX_NOT_TESTNET",
    nearcore: "2.14.0",
    rpcUrl,
    ownerId,
  };
  try {
    const wasm = fs.readFileSync(WASM);
    const keyStore = new nearAPI.keyStores.InMemoryKeyStore();
    await keyStore.setKey("sandbox", ownerId, nearAPI.KeyPair.fromString(DEFAULT_PRIVATE_KEY));
    const near = await nearAPI.connect({
      networkId: "sandbox",
      keyStore,
      nodeUrl: rpcUrl,
    });
    const owner = await near.account(ownerId);
    summary.deployTx = (await owner.deployContract(wasm)).transaction.hash;
    summary.initTx = (await owner.functionCall({
      contractId: ownerId,
      methodName: "new",
      args: { owner_id: ownerId },
      gas: GAS,
      attachedDeposit: "0",
    })).transaction.hash;

    const functionKey = nearAPI.KeyPair.fromRandom("ed25519");
    summary.addKeyTx = (await owner.addKey(
      functionKey.getPublicKey(),
      ownerId,
      ["record_event"],
      ALLOWANCE
    )).transaction.hash;

    const agentStore = new nearAPI.keyStores.InMemoryKeyStore();
    await agentStore.setKey("sandbox", ownerId, functionKey);
    const agentNear = await nearAPI.connect({
      networkId: "sandbox",
      keyStore: agentStore,
      nodeUrl: rpcUrl,
    });
    const agent = await agentNear.account(ownerId);
    const provider = agentNear.connection.provider;
    const previousHash = await view(provider, ownerId, "get_latest_hash");
    const accepted = await agent.functionCall({
      contractId: ownerId,
      methodName: "record_event",
      args: {
        commitment_id: "outcome-sandbox",
        event_type: "SANDBOX_EVIDENCE",
        payload_hash: "ab".repeat(32),
        previous_hash: previousHash,
      },
      gas: GAS,
      attachedDeposit: "0",
    });
    summary.recordEventTx = accepted.transaction.hash;
    const recordStatus = await provider.txStatus(summary.recordEventTx, ownerId);
    if (!recordStatus.status || recordStatus.status.Failure) {
      throw new Error("record_event did not succeed in the sandbox.");
    }

    let rejected = null;
    try {
      await agent.functionCall({
        contractId: ownerId,
        methodName: "admin_probe",
        args: {},
        gas: GAS,
        attachedDeposit: "0",
      });
    } catch (error) {
      rejected = error;
    }
    if (!rejected || !isMethodNameMismatch(rejected)) {
      throw new Error("admin_probe was not rejected with MethodNameMismatch.");
    }
    summary.rejectedTx = rejectionHash(rejected);
    summary.adminProbeCount = await view(provider, ownerId, "get_admin_probe_count");
    if (summary.adminProbeCount !== 0) {
      throw new Error("admin_probe changed contract state despite the method restriction.");
    }
    if (summary.rejectedTx) {
      try {
        const status = await provider.txStatus(summary.rejectedTx, ownerId);
        summary.rejectedStatus = status.status && status.status.Failure
          ? status.status.Failure
          : status.status;
      } catch (error) {
        summary.rejectedStatusError = String(error && error.message || error).slice(0, 300);
      }
    }
    console.log(JSON.stringify(summary, null, 2));
    if (summary.rejectedStatus && !JSON.stringify(summary.rejectedStatus).includes("MethodNameMismatch")) {
      throw new Error("Sandbox rejection hash resolved, but the status was not MethodNameMismatch.");
    }
  } finally {
    if (sandbox) await sandbox.tearDown();
  }
}

main().catch((error) => {
  console.error("SANDBOX_KEY_TEST_FAILED");
  console.error(error && error.stack ? error.stack : error);
  process.exit(1);
});
