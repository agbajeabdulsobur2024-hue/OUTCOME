# NEAR testnet audit anchor and restricted function-call key

This is an isolated testnet demonstration. It does not move NEAR or supplier funds.

## What the testnet proof is designed to demonstrate

1. OUTCOME's Rust contract stores a SHA-256 digest, commitment reference, event type, predecessor hash, signer account, block height and block timestamp.
2. The contract enforces an append-only hash chain: every new event must reference the previous event hash.
3. A dedicated NEAR function-call access key is created with:
   - receiver account: the audit contract only;
   - allowed method: \`record_event\` only;
   - allowance: 0.25 NEAR for testnet gas.
4. That restricted key successfully submits a real testnet transaction to \`record_event\`.
5. The same key then attempts to call the state-changing \`admin_probe\` method. The NEAR protocol must reject the transaction because the method is not in the key's allowlist. The script only reports success if the rejection is specifically a method-name permission failure.

The contract contains \`admin_probe\` as a harmless state-changing method so the method-scope boundary is observable. It does not transfer tokens.

## Build the contract

Requirements: Rust stable, Cargo, and the \`wasm32-unknown-unknown\` target.

\`\`\`bash
rustup target add wasm32-unknown-unknown
cd near/contract
cargo build --target wasm32-unknown-unknown --release
\`\`\`

The WASM output is:

\`\`\`
near/contract/target/wasm32-unknown-unknown/release/outcome_audit_anchor.wasm
\`\`\`

Install the NEAR CLI and create/fund a testnet account. The contract can be deployed to that same account (so the account ID is both contract ID and owner ID), or to a separate contract account. Use a testnet account only.

## Deploy and initialize

From the repository root, with your testnet account available in the NEAR CLI keychain:

\`\`\`bash
near contract deploy YOUR_CONTRACT.testnet \\
  use-file near/contract/target/wasm32-unknown-unknown/release/outcome_audit_anchor.wasm \\
  with-init-call new '{"owner_id":"YOUR_OWNER.testnet"}' \\
  --prepaid-gas '100 Tgas' \\
  --attached-deposit '0 NEAR' \\
  network-config testnet \\
  sign-as YOUR_OWNER.testnet \\
  sign-with-keychain \\
  send
\`\`\`

Do not initialize an already initialized contract. For the simplest demo, set \`YOUR_CONTRACT.testnet\` and \`YOUR_OWNER.testnet\` to the same account.

## Run the restricted-key proof

The Node script uses the owner's full-access private key only to add a restricted access key. It then signs the audit append and the forbidden method attempt with the restricted key.

\`\`\`bash
cd near
npm install
export NEAR_TESTNET_OWNER_ID='YOUR_OWNER.testnet'
export NEAR_TESTNET_CONTRACT_ID='YOUR_CONTRACT.testnet'
read -s -p 'Owner testnet private key: ' NEAR_TESTNET_OWNER_PRIVATE_KEY
export NEAR_TESTNET_OWNER_PRIVATE_KEY
printf '\\n'
npm run testnet:security-demo
unset NEAR_TESTNET_OWNER_PRIVATE_KEY
\`\`\`

To anchor an actual local OUTCOME record digest instead of the built-in synthetic fixture, also set `OUTCOME_API_URL` (for example `http://127.0.0.1:8000`) and `OUTCOME_COMMITMENT_ID` to the record ID. The script calls the privacy-minimized `/api/commitments/{id}/audit-anchor` endpoint; only the returned SHA-256 digest and synthetic commitment identifier are written on-chain. The endpoint deliberately omits supplier names, free-text notes and payment destinations.

The owner private key is read from the environment and is never written by the script. The script creates \`near/.function-call-key.json\` with mode 0600 and adds that public key to the account with the exact function-call permission described above. That file is ignored by Git. Keep it private; do not upload it or paste it into ChatGPT.

Expected successful output includes:
- a testnet transaction hash for \`record_event\`;
- a block explorer link;
- a protocol error for \`admin_probe\` that identifies the method-name restriction.

If the forbidden call fails for any other reason (insufficient allowance, missing account, network failure, invalid contract, etc.), the script exits with an error and does not claim the key restriction was proven.

## Verify on-chain state

Use the block explorer link printed by the script to inspect the accepted \`record_event\` transaction. The contract's \`get_latest\`, \`get_event\`, \`get_latest_hash\` and \`get_next_sequence\` view methods expose the audit state. You can also query them with the NEAR CLI.

Only hashes and synthetic identifiers are anchored. Do not place supplier names, invoices, bank details, personal data or confidential contract text on a public blockchain.

## Important scope boundary

After the script completes successfully, it will demonstrate NEAR protocol-level receiver/method restrictions on actual testnet transactions. Until then, this remains an unexecuted testnet proof. It does not yet make OUTCOME a payment executor and does not authorize a real supplier transfer. The function-call key can only call the audit contract's \`record_event\` method; it cannot call other contracts or arbitrary methods. The current OUTCOME web backend does not hold this key. The demo script is the isolated signer for this proof.

After this proof is run successfully, the next integration step is to export a canonical commitment-state digest from OUTCOME and have a dedicated, authenticated server-side anchoring worker submit only that digest using the same restricted key. Do not expose the restricted private key to the browser.
