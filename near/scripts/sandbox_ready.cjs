"use strict";

// near-sandbox hides the underlying ready-check error on error.cause.
// Print it so a timeout is not just "failed to become ready".

function logSandboxFailure(error) {
  const cause = error && error.cause;
  if (!cause) return;
  const text = cause.stack || cause.message || String(cause);
  console.error("sandbox ready cause:", text.split("\n").slice(0, 8).join("\n"));
}

module.exports = { logSandboxFailure };
