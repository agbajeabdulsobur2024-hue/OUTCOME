#!/usr/bin/env node
"use strict";

// Testnet-only demonstration of a NEAR function-call access key.
// Owner full-access key is read only from NEAR_TESTNET_OWNER_PRIVATE_KEY.
// A dedicated restricted key is generated locally and saved mode 0600.
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const nearAPI = require("near-api-js");
const { isMethodNameMismatch, allowanceWithinCap, rejectionHash } = require("./access_key_policy.cjs");

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

async function confirmMethodNameMismatch(provider, hash, accountId) {
  // nearcore 2.14 returns MethodNameMismatch from broadcast and often does not
  // index that rejected transaction. txStatus then times out with NOT_OBSERVED.
  // An indexed status that is some other failure is still a hard error.
  let lastError = null;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      const status = await provider.txStatus(hash, accountId);
      const failure = status && status.status && status.status.Failure;
      const encoded = JSON.stringify(failure || (status && status.status) || {});
      if (!encoded.includes("MethodNameMismatch")) {
        throw new Error("Rejected transaction status is not MethodNameMismatch. " + encoded.slice(0, 500));
      }
      return "indexed MethodNameMismatch: " + encoded;
    } catch (error) {
      const message = String(error && error.message || error);
      if (message.includes("not MethodNameMismatch")) throw error;
      lastError = error;
      if (/NOT_OBSERVED|TIMEOUT|Timeout/i.test(message)) break;
      await new Promise((resolve) => setTimeout(resolve, 400 * (attempt + 1)));
    }
  }
  return "broadcast MethodNameMismatch was returned to the signer; txStatus did not index "
    + hash + " (" + String(lastError && lastError.message || lastError).slice(0, 240) + ")";
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
  const publicKey = functionKey.getPublicKey().toString();
  const accessKeys = await ownerNear.connection.provider.query({
    request_type: "view_access_key_list",
    account_id: ownerId,
    finality: "final"
  });
  const existing = (accessKeys.keys || []).find((item) => item.public_key === publicKey);
  if (existing) {
    const permission = existing.access_key && existing.access_key.permission;
    const fc = permission && permission.FunctionCall;
    const methods = (fc && fc.method_names || []).slice().sort();
    if (!fc || fc.receiver_id !== contractId ||
        JSON.stringify(methods) !== JSON.stringify(["record_event"]) ||
        !allowanceWithinCap(fc.allowance, ALLOWANCE)) {
      throw new Error("Existing restricted key does not have the receiver, method, and remaining allowance expected.");
    }
    console.log("Reusing existing function-call key. Remaining allowance:", String(fc.allowance));
  } else {
    await owner.addKey(functionKey.getPublicKey(), contractId, ["record_event"], ALLOWANCE);
  }

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

  let anchorPayload;
  const appUrl = process.env.OUTCOME_API_URL;
  const commitmentId = process.env.OUTCOME_COMMITMENT_ID;
  if (appUrl || commitmentId) {
    if (!appUrl || !commitmentId) {
      throw new Error("Set both OUTCOME_API_URL and OUTCOME_COMMITMENT_ID, or neither.");
    }
    const digestResponse = await fetch(
      (appUrl.endsWith("/") ? appUrl.slice(0, -1) : appUrl) + "/api/commitments/" + encodeURIComponent(commitmentId) + "/audit-anchor"
    );
    if (!digestResponse.ok) {
      throw new Error("OUTCOME audit digest endpoint returned HTTP " + digestResponse.status);
    }
    const digest = await digestResponse.json();
    if (!/^[a-f0-9]{64}$/.test(digest.payload_hash || "")) {
      throw new Error("OUTCOME returned an invalid audit digest.");
    }
    anchorPayload = {
      commitment_id: digest.commitment_id,
      event_type: digest.event_type,
      payload_hash: digest.payload_hash
    };
    console.log("Anchoring privacy-minimized digest exported by OUTCOME.");
  } else {
    const demoPayload = {
      commitment_id: "OUTCOME-DEMO-PO-104",
      event_type: "SUPPLIER_EVIDENCE_REVIEWED",
      evidence_summary: "Synthetic demo evidence reviewed; no supplier PII or payment details."
    };
    anchorPayload = {
      commitment_id: demoPayload.commitment_id,
      event_type: demoPayload.event_type,
      payload_hash: crypto.createHash("sha256")
        .update(JSON.stringify(demoPayload), "utf8").digest("hex")
    };
    console.log("Anchoring the built-in synthetic fixture. Set OUTCOME_API_URL and OUTCOME_COMMITMENT_ID to anchor an actual local demo record digest.");
  }

  const accepted = await agent.functionCall({
    contractId,
    methodName: "record_event",
    args: {
      commitment_id: anchorPayload.commitment_id,
      event_type: anchorPayload.event_type,
      payload_hash: anchorPayload.payload_hash,
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
    if (!isMethodNameMismatch(error)) {
      throw new Error("admin_probe failed, but not with MethodNameMismatch. Cannot claim the key restriction was proven. " + message);
    }
    const rejectedHash = rejectionHash(error);
    if (!rejectedHash) {
      throw new Error("MethodNameMismatch was reported without error.context.transactionHash.");
    }
    const failureText = await confirmMethodNameMismatch(
      agentNear.connection.provider, rejectedHash, ownerId
    );
    blocked = true;
    console.log("PASS: NEAR protocol rejected admin_probe because the key only allows record_event.");
    console.log("Protocol rejection:", message.slice(0, 500));
    console.log("Rejected transaction:", rejectedHash);
    console.log("Explorer:", "https://testnet.nearblocks.io/txns/" + rejectedHash);
    console.log("RPC failure:", failureText.slice(0, 500));
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
