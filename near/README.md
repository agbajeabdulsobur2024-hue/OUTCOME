# NEAR testnet audit anchor and restricted function-call key

This is an isolated testnet demonstration. It does not move NEAR or supplier funds.

## Completed testnet run

Account `thewordweaver.testnet`:

- Deploy and init, FINAL: [`8qK4xfgicVX61qERb9cyTZhbjMoynDvJAMTmiXpQT5cy`](https://testnet.nearblocks.io/txns/8qK4xfgicVX61qERb9cyTZhbjMoynDvJAMTmiXpQT5cy). On-chain `code_hash` `7KHQPNxsWM1vKCYR4b743c7ZAj9fVhcZcXSjoLKsRQaS`.
- `record_event` accepted and stored, FINAL: [`3N8VVzFr3QLm6iJKvS2twM4VxCWZyrec9FfqP2zNb2kj`](https://testnet.nearblocks.io/txns/3N8VVzFr3QLm6iJKvS2twM4VxCWZyrec9FfqP2zNb2kj).
- `admin_probe` rejected with `MethodNameMismatch`: [`7KKsCtXwApgXPtQ79KWG6T9PVKTsYYRdoLvSeebgHERk`](https://testnet.nearblocks.io/txns/7KKsCtXwApgXPtQ79KWG6T9PVKTsYYRdoLvSeebgHERk). That rejection is not indexed, which is expected on this nearcore version.

The deployed contract accepts `record_event` only from its owner. The source in this repository now lets any account append a hash-only event and adds `get_anchor_policy`. That upgrade has not been submitted. The account owner must deploy it with their own key. Do not paste that key into the repo, the hosted app, or Vercel. `admin_probe` stays owner-only. The web app can ask a visitor to sign with their own testnet wallet. It does not hold a full-access key, and a full-access key was not put in Vercel. The steps below are how the restricted-key proof is repeated.

## What the testnet proof is designed to demonstrate

1. OUTCOME's Rust contract stores a SHA-256 digest, commitment reference, event type, predecessor hash, signer account, block height and block timestamp.
2. The contract enforces an append-only hash chain: every new event must reference the previous event hash.
3. A dedicated NEAR function-call access key is created with:
   - receiver account: the audit contract only;
   - allowed method: `record_event` only;
   - allowance: 0.25 NEAR for testnet gas. Later runs accept any remaining allowance greater than zero and at most that cap, because gas reduces it.
4. That restricted key successfully submits a real testnet transaction to `record_event`.
5. The same key then attempts to call the state-changing `admin_probe` method. The NEAR protocol must reject the transaction because the method is not in the key's allowlist. near-api-js 2.1.4 reports that as error type `MethodNameMismatch` and the message `Transaction method name admin_probe isn't allowed by the access key`. The script records `error.context.transactionHash` and re-queries that transaction when the node indexed it. On nearcore 2.14, `txStatus` for this rejection often times out with `NOT_OBSERVED` because the node returns the error from broadcast and does not index the transaction. The script still requires the broadcast error to be `MethodNameMismatch`, and it fails if an indexed status is some other failure. It does not treat an arbitrary error as proof.

The contract contains `admin_probe` as a harmless state-changing method so the method-scope boundary is observable. It does not transfer tokens.

A local sandbox exercise of the same boundary is `npm run test:sandbox-key` after the wasm build. Its transaction hashes belong to a local chain, not testnet.

## Build the contract

Requirements: Rust 1.93 or newer (the pinned `near-sys` 0.2.14 crate uses edition 2024), Cargo, and the `wasm32-unknown-unknown` target. `near-sdk` is pinned to `=5.17.2` in `Cargo.toml` and `Cargo.lock`. Do not delete the lockfile; an unlocked `5.17.2` requirement resolves to a later 5.x release.

```bash
rustup target add wasm32-unknown-unknown
cd near/contract
cargo build --target wasm32-unknown-unknown --release
```

The WASM output is:

```
near/contract/target/wasm32-unknown-unknown/release/outcome_audit_anchor.wasm
```

`cargo test` on the host target does not compile this crate: `near-sdk` refuses non-wasm builds unless a special cfg is set. The executable check is the sandbox script, which needs neard 2.14.x (protocol 88). Older near-sandbox 0.1.5 downloads neard 2.6.2, which rejects this wasm (`PrepareError: Deserialization`) because rustc emits `bulk-memory`.

Install the NEAR CLI and create/fund a testnet account. The contract can be deployed to that same account (so the account ID is both contract ID and owner ID), or to a separate contract account. Use a testnet account only.

## Deploy and initialize

Code can only be deployed by a full-access key of the contract account. Sign the deploy as `YOUR_CONTRACT.testnet`. The init argument `owner_id` is the account that will later hold the restricted key and call `record_event`. For the simplest demo, those two account IDs are the same.

`sign-with-keychain` uses the near-cli-rs OS keychain. Credentials created by the older JavaScript `near` CLI live in `~/.near-credentials` and need `sign-with-legacy-keychain` instead.

From the repository root:

```bash
near contract deploy YOUR_CONTRACT.testnet \
  use-file near/contract/target/wasm32-unknown-unknown/release/outcome_audit_anchor.wasm \
  with-init-call new json-args '{"owner_id":"YOUR_OWNER.testnet"}' \
  prepaid-gas '100 Tgas' \
  attached-deposit '0 NEAR' \
  network-config testnet \
  sign-as YOUR_CONTRACT.testnet \
  sign-with-keychain \
  send
```

Do not initialize an already initialized contract.

## Run the restricted-key proof

The Node script uses the owner's full-access private key only to add a restricted access key. It then signs the audit append and the forbidden method attempt with the restricted key.

`NEAR_TESTNET_OWNER_PRIVATE_KEY` must be the near-api-js string `ed25519:` followed by the base58 secret. A bare base58 string is rejected by `KeyPair.fromString`. `NEAR_TESTNET_OWNER_ID` must be the contract's `owner_id`, because the key's predecessor account is that owner.

```bash
cd near
npm install
export NEAR_TESTNET_OWNER_ID='YOUR_OWNER.testnet'
export NEAR_TESTNET_CONTRACT_ID='YOUR_CONTRACT.testnet'
read -s -p 'Owner testnet private key (ed25519:...): ' NEAR_TESTNET_OWNER_PRIVATE_KEY
export NEAR_TESTNET_OWNER_PRIVATE_KEY
printf '\n'
npm run testnet:security-demo
unset NEAR_TESTNET_OWNER_PRIVATE_KEY
```

To anchor an actual local OUTCOME record digest instead of the built-in synthetic fixture, also set `OUTCOME_API_URL` (for example `http://127.0.0.1:8000`) and `OUTCOME_COMMITMENT_ID` to the record ID. The script calls the privacy-minimized `/api/commitments/{id}/audit-anchor` endpoint; only the returned SHA-256 digest and synthetic commitment identifier are written on-chain. The endpoint deliberately omits supplier names, free-text notes and payment destinations.

The owner private key is read from the environment and is never written by the script. The script creates `near/.function-call-key.json` with mode 0600 and adds that public key to the account with the function-call permission described above. That file is ignored by Git. Keep it private; do not upload it or paste it into chat.

Expected successful output includes:
- a testnet transaction hash for `record_event`;
- a block explorer link;
- a `MethodNameMismatch` rejection for `admin_probe`, its transaction hash, and the RPC failure body.

If the forbidden call fails for any other reason (insufficient allowance, missing account, network failure, invalid contract, etc.), the script exits with an error and does not claim the key restriction was proven.

## Verify on-chain state

Use the block explorer links printed by the script to inspect the accepted `record_event` transaction and the rejected `admin_probe` transaction. The contract's `get_latest`, `get_event`, `get_latest_hash` and `get_next_sequence` view methods expose the audit state. You can also query them with the NEAR CLI.

Only hashes and synthetic identifiers are anchored. Do not place supplier names, invoices, bank details, personal data or confidential contract text on a public blockchain.

## Important scope boundary

The 11 November 2026 run on `thewordweaver.testnet` demonstrated NEAR protocol-level receiver and method restrictions on testnet transactions. A green sandbox run is not that demonstration. The integration does not make OUTCOME a payment executor and does not authorize a real supplier transfer. The function-call key can only call the audit contract's `record_event` method; it cannot call other contracts or arbitrary methods. The current OUTCOME web backend does not hold this key. The demo script is the isolated signer for this proof.

The next integration step is to export a canonical commitment-state digest from OUTCOME and have a dedicated, authenticated server-side anchoring worker submit only that digest using the same restricted key. Do not expose the restricted private key to the browser.
