# Contract upgrade review

This is a review of the source in `near/contract`. It is not a deployment. No transaction was sent to `thewordweaver.testnet` or to mainnet for this change.

The contract already on `thewordweaver.testnet` is the earlier owner-gated deploy. `get_anchor_policy` returns method-not-found there. The owner record that is already final is `record_event` [3N8VVzFr3QLm6iJKvS2twM4VxCWZyrec9FfqP2zNb2kj](https://testnet.nearblocks.io/txns/3N8VVzFr3QLm6iJKvS2twM4VxCWZyrec9FfqP2zNb2kj). Its stored id is `OUTCOME-DEMO-PO-104`. That is an owner event, not a tester anchor and not an `outcome-` plus SHA-256 digest from this app. Deploy and init [8qK4xfgicVX61qERb9cyTZhbjMoynDvJAMTmiXpQT5cy](https://testnet.nearblocks.io/txns/8qK4xfgicVX61qERb9cyTZhbjMoynDvJAMTmiXpQT5cy) is FINAL. Code hash `7KHQPNxsWM1vKCYR4b743c7ZAj9fVhcZcXSjoLKsRQaS`.

## What changed in the source

`record_event` is payable.

- The owner can still append. A zero deposit is allowed. Any deposit the owner attaches is refunded, because the contract account keeps paying for the owner’s storage.
- Any other predecessor can append only when the attached deposit is at least the storage this write adds.
- If a non-owner attaches more than that amount, the contract keeps the storage cost and refunds the rest.
- If a non-owner attaches less, the call panics and state does not change.
- `payload_hash` must still be 64 hex characters, and `previous_hash` must still equal the current chain tip.
- `admin_probe` is still owner-only.
- `get_anchor_policy` returns `any_account`.
- The `Contract` struct is unchanged, so an upgrade does not re-initialise state and does not wipe existing events. Do not call `new` again.

The app records a tester receipt only when the attached deposit equals the same byte formula. A zero deposit is accepted only from `thewordweaver.testnet`. Any other positive deposit is rejected by the app even if the contract would have refunded the surplus. The wallet should attach the exact string from `get_required_storage_deposit`. The product does not offer that signature until the live view returns `any_account`.

## Storage calculation

NEAR charges `10^19` yoctoNEAR per byte (`env::storage_byte_cost()`), which is 0.00001 NEAR per byte.

One new event stores:

- a LookupMap key: 1-byte Borsh enum prefix for `StorageKey::Events`, plus 8 bytes for the `u64` sequence
- a Borsh value: `u64` sequence, four strings (`commitment_id`, `event_type`, `payload_hash`, `previous_hash`), a 64-character `event_hash`, the actor account id, and two `u64` timestamps
- if `last_hash` is shorter than 64 characters, the root state grows by `64 - len(last_hash)` bytes

Each string is a 4-byte length plus its UTF-8 bytes. The contract measures this with `borsh::to_vec` on a same-sized event and the same key. The Python helper `app.anchors.storage_bytes_for_event` uses that layout.

Sandbox measurements, local chain only:

| Write | Bytes | Deposit |
|---|---:|---:|
| First owner event (`owner-event-1`, empty previous hash, actor `sandbox`, `last_hash` empty) | 281 | Owner attached 0. The contract balance covers it. |
| Second event (`tester-event-2`, 64-character previous hash, actor `tester.sandbox`, `last_hash` already 64) | 290 | `2900000000000000000000` yoctoNEAR, which is 0.0029 NEAR |

Less than 290 bytes of deposit on that second write panicked with `Insufficient storage deposit: need 2900000000000000000000 yoctoNEAR`. The exact quoted amount succeeded. A larger attachment would be accepted by the contract and the surplus refunded; the app would not file that transaction as this order’s receipt.

## How the ten checks were tested

Command, from `near/` after `cargo +1.93.0 build --target wasm32-unknown-unknown --release`:

```bash
NEAR_RPC_TIMEOUT_SECS=40 node scripts/storage_anchor_test.cjs
```

The script starts a local neard 2.14.0 sandbox. It does not talk to testnet or mainnet.

| Check | Result in that sandbox run |
|---|---|
| 1. Owner can append | Owner `record_event` with 0 deposit returned success. |
| 2. A second account can append once the policy is `any_account` | `tester.sandbox` appended event 2 with the quoted deposit. `get_anchor_policy` returned `any_account`. |
| 3. Non-owner writes must attach storage | A 0 deposit from the tester panicked. |
| 4. The contract enforces the requirement | The panic named the exact yoctoNEAR amount. One byte less (`10^19` yoctoNEAR under the quote) also panicked. |
| 5. Hash and previous-hash checks remain | A non-hex payload panicked. A wrong previous hash panicked and did not move the tip. |
| 6. `admin_probe` stays owner-only | The tester’s full-access key panicked with the owner assertion. `get_admin_probe_count` stayed 0. |
| 7. Existing records survive an upgrade | After the first event, the same wasm was deployed again without `new`. `get_event(1)` still returned `owner-event-1`, including after the tester append. |
| 8. Second account append | Covered by check 2. The actor on event 2 was `tester.sandbox`. |
| 9. Unauthorised `admin_probe` | Covered by check 6. |
| 10. Insufficient deposits and invalid hashes | Covered by checks 3, 4, and 5. |

Python also checks the 281-byte and 290-byte examples in `tests/test_public_hackathon.py`.

## What this did not test

- A deploy onto `thewordweaver.testnet`. The live code hash is still the owner-gated contract.
- Whether the byte formula matches storage growth on that live account after a real upgrade. The sandbox account names are shorter than a typical `*.testnet` name, so the deposit changes with the actor length. The view method is the figure to attach.
- Gas refunds, a full-access key’s ability to upgrade, and the restricted-key proof. Those already exist as separate testnet transactions and were not repeated.
- A tester wallet in the browser. The UI hides the sign button until `get_anchor_policy` returns `any_account`.

## Deploy plan for later

Use the full-access key of `thewordweaver.testnet` only on a machine you control. Do not put it in this repository, the browser, or Vercel.

1. Build `near/contract/target/wasm32-unknown-unknown/release/outcome_audit_anchor.wasm` with Rust 1.93 or newer.
2. Deploy that wasm to `thewordweaver.testnet` as a code upgrade. Do not call `new`.
3. Call `get_event` for the existing sequence and confirm `OUTCOME-DEMO-PO-104` is still there. Confirm `get_anchor_policy` returns `any_account`.
4. Only then will the app offer a tester signature. The wallet must attach the exact `get_required_storage_deposit` for that account and those arguments.
