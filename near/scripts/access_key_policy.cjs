"use strict";

// Pure checks for the restricted function-call key demo.
// near-api-js 2.1.4 formats MethodNameMismatch as
// "Transaction method name <name> isn't allowed by the access key"
// and puts the signed hash on error.context.transactionHash.

function isMethodNameMismatch(error) {
  const message = String(error && (error.message || error));
  return Boolean(error && error.type === "MethodNameMismatch")
    || /MethodNameMismatch|isn't allowed by the access key/i.test(message);
}

function allowanceWithinCap(value, cap) {
  try {
    const remaining = BigInt(value);
    const limit = BigInt(cap);
    return remaining > 0n && remaining <= limit;
  } catch (_) {
    return false;
  }
}

function rejectionHash(error) {
  return (error && error.context && error.context.transactionHash) || null;
}

module.exports = { isMethodNameMismatch, allowanceWithinCap, rejectionHash };
