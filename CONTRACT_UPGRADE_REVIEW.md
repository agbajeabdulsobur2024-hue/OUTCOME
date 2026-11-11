# Contract upgrade review

This is a review package for a later decision. It is not a deployment. No transaction was sent to `thewordweaver.testnet`, to any public testnet account, or to mainnet. The app was not deployed. No payment rail was added.

A later deploy of this wasm would establish that a second account can append a hash to this contract on testnet. It would not, by itself, establish that OUTCOME is ready for independent public use. Workspaces, evidence review, payment policy, the hosted app, and a person’s authorisation are separate from this contract.

## What is live today

The contract at `thewordweaver.testnet` is still the owner-gated deploy.

- `record_event` [3N8VVzFr3QLm6iJKvS2twM4VxCWZyrec9FfqP2zNb2kj](https://testnet.nearblocks.io/txns/3N8VVzFr3QLm6iJKvS2twM4VxCWZyrec9FfqP2zNb2kj) is FINAL. The stored id is `OUTCOME-DEMO-PO-104`. That is an owner event, not a tester anchor and not an `outcome-` digest from this app.
- Deploy and init [8qK4xfgicVX61qERb9cyTZhbjMoynDvJAMTmiXpQT5cy](https://testnet.nearblocks.io/txns/8qK4xfgicVX61qERb9cyTZhbjMoynDvJAMTmiXpQT5cy) is FINAL. Code hash `7KHQPNxsWM1vKCYR4b743c7ZAj9fVhcZcXSjoLKsRQaS`.
- `get_anchor_policy` is not on that code. A view returns method-not-found. That absence is the live policy: only the owner can append.

The source of that owner gate is commit `299bed3` (`Only the configured owner account may append audit events`). Rebuilding that source with today’s compiler does not reproduce code hash `7KHQPNxsWM1vKCYR4b743c7ZAj9fVhcZcXSjoLKsRQaS`. The sandbox below starts from that source. It does not start from the wasm bytes that are on chain.

`main` already contains an undeployed intermediate (`get_anchor_policy` returns `any_account`, and any account can append with no deposit). Do not deploy that intermediate. It would let other accounts write storage at the contract’s expense. This branch is the storage-bounded proposal.

## 1. Complete contract diff

The patch below is the full `git diff` of `near/contract/src/lib.rs` from `299bed3` (the owner-gated source) to this branch. `Cargo.toml` is unchanged (`near-sdk` 5.17.2). `Contract` and `AuditEvent` gain no fields. The same patch file is [near/contract/UPGRADE_FROM_DEPLOYED.diff](near/contract/UPGRADE_FROM_DEPLOYED.diff) (198 lines, SHA-256 `9c646ae3519f221be3ea615acfeeb11abb67b475a41822fb6efb2c795456c0ec`).

`commitment_id` and `event_type` stay opaque strings. The contract does not name a supplier order, a carton, or a fixed list of demo event types. A call is rejected only for an empty or oversized string, a payload that is not 64 hex characters, a previous hash that is not the current tip, or a non-owner deposit below the measured storage.

```diff
diff --git a/near/contract/src/lib.rs b/near/contract/src/lib.rs
index 79fad62..ba70ac4 100644
--- a/near/contract/src/lib.rs
+++ b/near/contract/src/lib.rs
@@ -1,6 +1,6 @@
-use near_sdk::borsh::{BorshDeserialize, BorshSerialize};
+use near_sdk::borsh::BorshSerialize;
 use near_sdk::store::LookupMap;
-use near_sdk::{env, near, AccountId, BorshStorageKey, PanicOnDefault};
+use near_sdk::{env, near, AccountId, BorshStorageKey, NearToken, PanicOnDefault, Promise};
 
 #[near(serializers = [json, borsh])]
 #[derive(Clone)]
@@ -45,9 +45,21 @@ impl Contract {
         }
     }
 
-    /// Append a hash-only commitment event. The caller must be the configured
-    /// owner account. A restricted function-call key can be scoped by NEAR to
-    /// this method alone; protocol-level key permissions are checked first.
+    /// Append one hash-only audit event for any financial commitment.
+    ///
+    /// `commitment_id` and `event_type` are opaque caller strings. This
+    /// contract has no supplier, carton, milestone, or other workflow, and it
+    /// does not allow or reject names from a fixed list. Length limits and the
+    /// SHA-256 check are the only content rules. The predecessor account is
+    /// the actor.
+    ///
+    /// The owner may attach zero deposit; that storage stays on the contract
+    /// account. Every other account must attach at least the storage this
+    /// call measures with Borsh for these exact arguments. That amount is not
+    /// a fixed constant. Surplus is refunded. A short deposit panics and does
+    /// not write. This payable method is not deployed at
+    /// `thewordweaver.testnet`. `admin_probe` stays owner-only.
+    #[payable]
     pub fn record_event(
         &mut self,
         commitment_id: String,
@@ -55,17 +67,15 @@ impl Contract {
         payload_hash: String,
         previous_hash: String,
     ) -> u64 {
-        assert_eq!(
-            env::predecessor_account_id(),
-            self.owner_id,
-            "Only the configured owner account may append audit events"
-        );
         assert!(!commitment_id.trim().is_empty(), "commitment_id is required");
         assert!(commitment_id.len() <= 128, "commitment_id is too long");
         assert!(!event_type.trim().is_empty(), "event_type is required");
         assert!(event_type.len() <= 48, "event_type is too long");
         assert!(is_sha256_hex(&payload_hash), "payload_hash must be 32-byte SHA-256 hex");
-        assert_eq!(previous_hash, self.last_hash, "Audit chain predecessor does not match latest event");
+        assert_eq!(
+            previous_hash, self.last_hash,
+            "Audit chain predecessor does not match latest event"
+        );
 
         let sequence = self.next_sequence;
         let actor = env::predecessor_account_id();
@@ -83,6 +93,14 @@ impl Contract {
             block_timestamp_ns
         );
         let event_hash = to_hex(&env::sha256(canonical.as_bytes()));
+        let used = estimate_new_event_bytes(
+            self.last_hash.as_str(),
+            commitment_id.as_str(),
+            event_type.as_str(),
+            &payload_hash.to_lowercase(),
+            previous_hash.as_str(),
+            actor.as_str(),
+        );
         let event = AuditEvent {
             sequence,
             commitment_id,
@@ -90,17 +108,65 @@ impl Contract {
             payload_hash: payload_hash.to_lowercase(),
             previous_hash,
             event_hash: event_hash.clone(),
-            actor,
+            actor: actor.clone(),
             block_height,
             block_timestamp_ns,
         };
         self.events.insert(sequence, event);
+        self.events.flush();
         self.next_sequence += 1;
         self.last_hash = event_hash;
-        env::log_str(&format!("OUTCOME_AUDIT_EVENT:{}:{}", sequence, self.last_hash));
+        let required = env::storage_byte_cost().saturating_mul(u128::from(used));
+        let attached = env::attached_deposit();
+        let is_owner = actor == self.owner_id;
+        if !is_owner {
+            assert!(
+                attached >= required,
+                "Insufficient storage deposit: need {} yoctoNEAR",
+                required.as_yoctonear()
+            );
+        }
+        let refund = if is_owner {
+            attached
+        } else {
+            attached.saturating_sub(required)
+        };
+        if refund > NearToken::from_yoctonear(0) {
+            Promise::new(env::predecessor_account_id()).transfer(refund);
+        }
+        env::log_str(&format!(
+            "OUTCOME_AUDIT_EVENT:{}:{}:{}",
+            sequence,
+            self.last_hash,
+            used
+        ));
         sequence
     }
 
+    /// YoctoNEAR a non-owner must attach for these arguments.
+    /// The owner may attach zero. This does not write state.
+    pub fn get_required_storage_deposit(
+        &self,
+        commitment_id: String,
+        event_type: String,
+        payload_hash: String,
+        previous_hash: String,
+        actor_account_id: String,
+    ) -> String {
+        let bytes = estimate_new_event_bytes(
+            &self.last_hash,
+            &commitment_id,
+            &event_type,
+            &payload_hash,
+            &previous_hash,
+            &actor_account_id,
+        );
+        env::storage_byte_cost()
+            .saturating_mul(u128::from(bytes))
+            .as_yoctonear()
+            .to_string()
+    }
+
     /// This state-changing method is intentionally excluded from the agent
     /// key's allowlist. An attempt to call it with that key must be rejected
     /// by NEAR protocol access-key permissions before contract execution.
@@ -141,6 +207,54 @@ impl Contract {
     pub fn get_admin_probe_count(&self) -> u64 {
         self.admin_probe_count
     }
+
+    /// Policy implemented by this wasm.
+    ///
+    /// `any_account` means any predecessor may call `record_event`. A
+    /// non-owner must still attach the deposit from
+    /// `get_required_storage_deposit` for that call. This string is the rule
+    /// in this code, not a reading of `thewordweaver.testnet`. That account
+    /// does not expose this method until this wasm is deployed there.
+    pub fn get_anchor_policy(&self) -> String {
+        "any_account".to_string()
+    }
+}
+
+fn estimate_new_event_bytes(
+    last_hash: &str,
+    commitment_id: &str,
+    event_type: &str,
+    payload_hash: &str,
+    previous_hash: &str,
+    actor_account_id: &str,
+) -> u64 {
+    let actor = AccountId::try_from(actor_account_id.to_string())
+        .unwrap_or_else(|_| env::panic_str("actor account id is invalid"));
+    let event = AuditEvent {
+        sequence: 0,
+        commitment_id: commitment_id.to_string(),
+        event_type: event_type.to_string(),
+        payload_hash: payload_hash.to_string(),
+        previous_hash: previous_hash.to_string(),
+        event_hash: "0".repeat(64),
+        actor,
+        block_height: 0,
+        block_timestamp_ns: 0,
+    };
+    let value_len = borsh::to_vec(&event)
+        .unwrap_or_else(|_| env::panic_str("could not measure event storage"))
+        .len() as u64;
+    let mut key = borsh::to_vec(&StorageKey::Events)
+        .unwrap_or_else(|_| env::panic_str("could not measure storage key"));
+    key.extend(
+        borsh::to_vec(&0u64).unwrap_or_else(|_| env::panic_str("could not measure sequence key")),
+    );
+    let mut total = value_len + key.len() as u64;
+    // Root contract state grows when last_hash becomes the 64-character event hash.
+    if last_hash.len() < 64 {
+        total += (64 - last_hash.len()) as u64;
+    }
+    total
 }
 
 fn is_sha256_hex(value: &str) -> bool {
```

## 2. Storage cost and how the deposit is enforced

NEAR charges `env::storage_byte_cost()`, which is `10^19` yoctoNEAR per byte (0.00001 NEAR per byte).

`estimate_new_event_bytes` measures the write. It does not use a constant:

1. Build an `AuditEvent` with the caller’s strings and a 64-character stand-in for `event_hash`. Sequence and the two timestamps are zero. Their Borsh width is fixed at 8 bytes each, so the stand-in values do not change the size.
2. `borsh::to_vec` of that event is the value size. Each string is a 4-byte length plus its UTF-8 bytes.
3. Add the LookupMap key: Borsh of the `StorageKey::Events` enum prefix (1 byte) plus Borsh of a `u64` sequence (8 bytes).
4. If `last_hash` is shorter than 64 characters, add `64 - len(last_hash)` for the root state growing to the new tip. After the first event this term is zero.

`record_event` multiplies that byte count by `storage_byte_cost()`. The owner may attach zero; any attached tokens are refunded, and the contract account keeps paying for the owner’s storage. Any other predecessor must attach at least that product. The contract panics with `Insufficient storage deposit: need N yoctoNEAR` before it considers the write successful, and the sequence does not move. Surplus above `N` is refunded to the predecessor. `get_required_storage_deposit` runs the same function and returns `N` as a decimal string. It does not write.

The app accepts a non-owner receipt only when the attached deposit equals that same byte formula. A larger on-chain attachment can succeed and be partly refunded; this app will not file it. The wallet, once signing is offered, must attach the view’s exact string. Signing is not offered while the live view is missing or is not `any_account`.

Worked sizes, same formula, checked in `tests/test_public_hackathon.py`:

| Arguments | Bytes | YoctoNEAR |
|---|---:|---:|
| `owner-event-1` / `OWNER_RECORD` / empty previous hash / actor `sandbox` / `last_hash` empty | 281 | Owner attaches 0 |
| `tester-event-2` / `TESTER_RECORD` / 64-character previous hash / actor `tester.sandbox` / tip already 64 | 290 | `2900000000000000000000` |
| `m1` / `human-decision` / 64-character previous hash / actor `auditor.sandbox` / tip already 64 | 280 | `2800000000000000000000` |
| `milestone-acceptance-` plus 24 `a`s / `obligation-note` / same actor and a 64-character previous hash | 324 | `3240000000000000000000` |

280 and 324 are the two quotes from the sandbox run below. They differ because the strings differ. A single guessed constant cannot satisfy both.

## 3. Test results

Command, from `near/`, after `cargo +1.93.0 build --target wasm32-unknown-unknown --release` of this branch:

```bash
NEAR_RPC_TIMEOUT_SECS=40 node scripts/storage_anchor_test.cjs
```

The script compiles the owner-gated source at `299bed3` with the same toolchain, starts a local neard 2.14.0 sandbox, and stops there. It does not open a socket to public NEAR.

The second account is `auditor.sandbox`. It has its own key, created by the sandbox owner, and it is not the contract owner. The id ends in `.sandbox` because that local chain’s root account is `sandbox`. It is not an account on the public testnet, and the script does not create or use one.

Result of that run:

| Check | Result |
|---|---|
| Owner can append on the old code | `record_event` with deposit 0 stored event 1, type `evidence-recorded`, reference `ref-6f3a9c`. |
| A non-owner is rejected before the upgrade | `auditor.sandbox` panicked with the owner assertion. |
| Live policy before the upgrade | `get_anchor_policy` returned `MethodNotFound`. |
| Existing record survives the upgrade | After deploying this branch’s wasm and not calling `new`, event 1 kept the same reference, type, payload hash, actor, and event hash. |
| Policy the new code reports | `get_anchor_policy` returned `any_account`. |
| Owner can still append after the upgrade | A second owner event, type `decision-recorded`, succeeded with deposit 0. |
| Deposit follows the measurement | Quotes were `2800000000000000000000` and `3240000000000000000000`. They were not equal. |
| Insufficient deposit | Deposit 0 and one byte under the quote both panicked with `Insufficient storage deposit: need 2800000000000000000000 yoctoNEAR`. `get_next_sequence` did not change. |
| Invalid hash | A non-hex payload panicked. The sequence did not change. |
| Incorrect previous hash | A wrong tip panicked with the predecessor assertion. The sequence did not change. |
| Second account can anchor | `auditor.sandbox` stored `human-decision` at the exact 280-byte quote. The log’s byte count times `10^19` equalled the quote. The actor was `auditor.sandbox`. |
| Another event shape | The same account then stored `obligation-note` at the exact 324-byte quote. |
| Pre-upgrade event still present | Event 1 was still `ref-6f3a9c` after both tester writes. |
| `admin_probe` stays owner-only | The second account panicked. `get_admin_probe_count` stayed 0. |
| Rollback rehearsal | Redeploying the owner-gated wasm, without `new`, still read event 1 and the tester’s event. `get_anchor_policy` was `MethodNotFound` again. The second account’s next append was rejected by the owner gate. |

`PYTHONPATH=. python3 -m pytest -q` after those byte vectors: 89 passed, 2 skipped. The skipped tests are the live Turso checks. They need credentials this workspace does not have.

## 4. Upgrade compatibility

`Contract` is still `owner_id`, `events`, `next_sequence`, `last_hash`, `admin_probe_count`. `AuditEvent` is still the same eight fields in the same order. The upgrade adds methods and a deposit check. It does not add state.

The sandbox upgrade was a code deploy of this wasm onto an account initialised by the owner-gated wasm. `new` was not called. Event 1 remained readable, and later events from both accounts remained readable after the old wasm was deployed again.

That does not prove the bytes at code hash `7KHQPNxsWM1vKCYR4b743c7ZAj9fVhcZcXSjoLKsRQaS` will deserialise under this wasm. Only a deploy to `thewordweaver.testnet`, which this package does not do, can prove that the real `OUTCOME-DEMO-PO-104` event survives.

## 5. Later verification, and rollback

Do this only after a separate approval. Use the full-access key of `thewordweaver.testnet` on a machine you control. Do not put that key in this repository, the browser, or Vercel. Do not call `new`.

Before deploying, save the wasm that is on chain now. Its code hash is `7KHQPNxsWM1vKCYR4b743c7ZAj9fVhcZcXSjoLKsRQaS`. A wasm rebuilt from `299bed3` on another day is not a substitute unless its code hash matches.

Verification after the new code is deployed:

1. Call `get_event` for the sequence that holds `OUTCOME-DEMO-PO-104`. The reference, payload hash, and actor must match the transaction `3N8VVzFr3QLm6iJKvS2twM4VxCWZyrec9FfqP2zNb2kj`. If they do not, stop and roll back.
2. Call `get_owner`. It must still be `thewordweaver.testnet`.
3. Call `get_anchor_policy`. It must return `any_account`. Until this view succeeds, the app keeps the sign button hidden.
4. From the owner, one `record_event` with deposit 0 may be used as a smoke check. It spends the contract’s storage. It is optional and it is a real transaction.
5. From a second testnet account, call `get_required_storage_deposit` with that account’s id and the exact arguments, then attach that string. Do not reuse 280 or 324. Those were for `auditor.sandbox` and different strings.
6. Confirm a deposit one byte lower fails and `get_next_sequence` is unchanged.
7. Confirm `admin_probe` from the second account fails and `get_admin_probe_count` is unchanged.

Rollback, if step 1 fails or a later view cannot read state:

1. Deploy the saved wasm whose code hash is `7KHQPNxsWM1vKCYR4b743c7ZAj9fVhcZcXSjoLKsRQaS`.
2. Do not call `new`. Do not delete the account.
3. Read `OUTCOME-DEMO-PO-104` again.
4. `get_anchor_policy` should be method-not-found again, and a non-owner `record_event` should fail the owner check.

The sandbox rehearsal did those rollback reads against the owner-gated source, not against the live wasm. If the new code panics while loading state, the code is already replaced and the recovery is this redeploy. There is no automatic rollback.

Events written by the new code stayed readable under the old code in the sandbox, because the state layout did not change. That is the expected recovery shape. It is not yet shown for the live account.

## What this package does not decide

- The hosted app at `https://outcome-puce.vercel.app` is still version 0.4.0. This branch is not deployed there.
- No tester signature is offered until a live `get_anchor_policy` view returns `any_account`.
- Human approval remains `APPROVED — NOT SUBMITTED` unless a real transfer exists. This contract does not create one.
- Anchoring a hash is not payment, and a successful testnet deploy is not a public launch.
