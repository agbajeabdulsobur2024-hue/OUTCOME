#!/usr/bin/env node
"use strict";

// Testnet-only demonstration of a NEAR function-call access key.
// Owner full-access key is read only from NEAR_TESTNET_OWNER_PRIVATE_KEY.
// A dedicated restricted key is generated locally and saved mode 0600.
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const nearAPI = require("near-api-js");

const NETWORK = "testnet";
const RPC = "https://rpc.testnet.near.org";
const ALLOWANCE = "250000000000000000000000"; // 0.25 NEAR in yoctoNEAR
const GAS = "100000000000000"; // 100 Tgas

function required(name) {
  const value = process.env[name];
  if (!value) throw new Error("Missing required environment variable: " + name);
  return value;
}

function parseRpcJson(value) {
  const bytes = Buffer.from(value.result);
  return JSON.parse(bytes.toString("utf8"));
}

async function main() {
  const ownerId = required("NEAR_TESTNET_OWNER_ID");
  const contractId = required("NEAR_TESTNET_CONTRACT_ID");
  const ownerPrivateKey = required("NEAR_TESTNET_OWNER_PRIVATE_KEY");
  const ownerKeyPair = nearAPI.KeyPair.fromString(ownerPrivateKey);

  const root = path.resolve(__dirname, "..");
  const keyFile = path.join(root, ".function-call-key.json");
  let functionKey;
  if (fs.existsSync(keyFile)) {
    const saved = JSON.parse(fs.readFileSync(keyFile, "utf8"));
    if (saved.ownerId !== ownerId || saved.contractId !== contractId) {
      throw new Error("Existing restricted key file belongs to a different owner or contract. Move it aside before continuing.");
    }
    functionKey = nearAPI.KeyPair.fromString(saved.privateKey);
  } else {
    functionKey = nearAPI.KeyPair.fromRandom("ed25519");
    const saved = {
      ownerId,
      contractId,
      publicKey: functionKey.getPublicKey().toString(),
      privateKey: functionKey.toString()
    };
    fs.writeFileSync(keyFile, JSON.stringify(saved, null, 2), { mode: 0o600, flag: "wx" });
    try { fs.chmodSync(keyFile, 0o600); } catch (_) {}
  }

  const ownerStore = new nearAPI.keyStores.InMemoryKeyStore();
  await ownerStore.setKey(NETWORK, ownerId, ownerKeyPair);
  const ownerNear = await nearAPI.connect({
    networkId: NETWORK,
    keyStore: ownerStore,
    nodeUrl: RPC,
    walletUrl: "https://testnet.mynearwallet.com",
    helperUrl: "https://helper.testnet.near.org",
    explorerUrl: "https://testnet.nearblocks.io"
  });
  const owner = await ownerNear.account(ownerId);

  // The NEAR protocol stores this as a FunctionCall permission:
  // receiver_id = contractId; method_names = ["record_event"]; allowance = 0.25 NEAR.
  await owner.addKey(functionKey.getPublicKey(), contractId, ["record_event"], ALLOWANCE);

  const agentStore = new nearAPI.keyStores.InMemoryKeyStore();
  await agentStore.setKey(NETWORK, ownerId, functionKey);
  const agentNear = await nearAPI.connect({
    networkId: NETWORK,
    keyStore: agentStore,
    nodeUrl: RPC,
    walletUrl: "https://testnet.mynearwallet.com",
    helperUrl: "https://helper.testnet.near.org",
    explorerUrl: "https://testnet.nearblocks.io"
  });
  const agent = await agentNear.account(ownerId);

  const latestRaw = await agentNear.connection.provider.query({
    request_type: "call_function",
    account_id: contractId,
    method_name: "get_latest_hash",
    args_base64: Buffer.from("{}").toString("base64"),
    finality: "final"
  });
  const previousHash = parseRpcJson(latestRaw);

  const demoPayload = {
    commitment_id: "OUTCOME-DEMO-PO-104",
    event_type: "SUPPLIER_EVIDENCE_REVIEWED",
    evidence_summary: "Synthetic demo evidence reviewed; no supplier PII or payment details."
  };
  const payloadHash = crypto.createHash("sha256")
    .update(JSON.stringify(demoPayload), "utf8").digest("hex");

  const accepted = await agent.functionCall({
    contractId,
    methodName: "record_event",
    args: {
      commitment_id: demoPayload.commitment_id,
      event_type: demoPayload.event_type,
      payload_hash: payloadHash,
      previous_hash: previousHash
    },
    gas: GAS,
    attachedDeposit: "0"
  });
  const acceptedHash = accepted.transaction && accepted.transaction.hash;
  if (!acceptedHash) throw new Error("record_event returned without a transaction hash.");

  console.log("PASS: restricted key appended a hash-only audit event.");
  console.log("Accepted transaction:", acceptedHash);
  console.log("Explorer:", "https://testnet.nearblocks.io/txns/" + acceptedHash);

  let blocked = false;
  try {
    await agent.functionCall({
      contractId,
      methodName: "admin_probe",
      args: {},
      gas: GAS,
      attachedDeposit: "0"
    });
  } catch (error) {
    const message = String(error && (error.message || error));
    if (/FunctionCallAccessKeyMethodNameMismatch|MethodNameMismatch|method name.*not allowed|method.*not.*permitted/i.test(message)) {
      blocked = true;
      console.log("PASS: NEAR protocol rejected admin_probe because the key only allows record_event.");
      console.log("Protocol rejection:", message.slice(0, 500));
    } else {
      throw new Error("admin_probe failed, but not with a recognized method-scope rejection. Cannot claim the key restriction was proven. " + message);
    }
  }
  if (!blocked) {
    throw new Error("SECURITY FAILURE: restricted function-call key unexpectedly invoked admin_probe.");
  }

  console.log("Restricted key public key:", functionKey.getPublicKey().toString());
  console.log("Local restricted key file:", keyFile);
  console.log("Keep this key file private. Do not commit it or paste it into chat.");
}

main().catch((error) => {
  console.error("NEAR testnet security demo failed:", error.message || error);
  process.exitCode = 1;
});
