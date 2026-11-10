"use strict";

const assert = require("assert");
const { isMethodNameMismatch, allowanceWithinCap, rejectionHash } = require("./access_key_policy.cjs");

const realMessage = "Transaction method name admin_probe isn't allowed by the access key";
assert.equal(isMethodNameMismatch({ type: "MethodNameMismatch", message: realMessage }), true);
assert.equal(isMethodNameMismatch({ message: realMessage }), true);
assert.equal(isMethodNameMismatch({ message: "method name admin_probe not allowed" }), false);
assert.equal(isMethodNameMismatch({ type: "NotEnoughAllowance", message: "not enough allowance" }), false);

const cap = "250000000000000000000000";
assert.equal(allowanceWithinCap(cap, cap), true);
assert.equal(allowanceWithinCap("249812090593100600000000", cap), true);
assert.equal(allowanceWithinCap("250000000000000000000001", cap), false);
assert.equal(allowanceWithinCap("0", cap), false);
assert.equal(allowanceWithinCap(null, cap), false);

assert.equal(rejectionHash({ context: { transactionHash: "abc" } }), "abc");
assert.equal(rejectionHash({ message: realMessage }), null);

console.log("access key policy checks passed");
