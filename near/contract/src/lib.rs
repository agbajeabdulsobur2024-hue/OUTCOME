use near_sdk::borsh::BorshSerialize;
use near_sdk::store::LookupMap;
use near_sdk::{env, near, AccountId, BorshStorageKey, NearToken, PanicOnDefault, Promise};

#[near(serializers = [json, borsh])]
#[derive(Clone)]
pub struct AuditEvent {
    pub sequence: u64,
    pub commitment_id: String,
    pub event_type: String,
    pub payload_hash: String,
    pub previous_hash: String,
    pub event_hash: String,
    pub actor: AccountId,
    pub block_height: u64,
    pub block_timestamp_ns: u64,
}

#[derive(BorshSerialize, BorshStorageKey)]
enum StorageKey {
    Events,
}

#[near(contract_state)]
#[derive(PanicOnDefault)]
pub struct Contract {
    owner_id: AccountId,
    events: LookupMap<u64, AuditEvent>,
    next_sequence: u64,
    last_hash: String,
    admin_probe_count: u64,
}

#[near]
impl Contract {
    #[init]
    pub fn new(owner_id: AccountId) -> Self {
        assert!(!env::state_exists(), "Contract is already initialized");
        Self {
            owner_id,
            events: LookupMap::new(StorageKey::Events),
            next_sequence: 1,
            last_hash: String::new(),
            admin_probe_count: 0,
        }
    }

    /// Append one hash-only audit event for any financial commitment.
    ///
    /// `commitment_id` and `event_type` are opaque caller strings. This
    /// contract has no supplier, carton, milestone, or other workflow, and it
    /// does not allow or reject names from a fixed list. Length limits and the
    /// SHA-256 check are the only content rules. The predecessor account is
    /// the actor.
    ///
    /// The owner may attach zero deposit; that storage stays on the contract
    /// account. Every other account must attach at least the storage this
    /// call measures with Borsh for these exact arguments. That amount is not
    /// a fixed constant. Surplus is refunded. A short deposit panics and does
    /// not write. This payable method is not deployed at
    /// `thewordweaver.testnet`. `admin_probe` stays owner-only.
    #[payable]
    pub fn record_event(
        &mut self,
        commitment_id: String,
        event_type: String,
        payload_hash: String,
        previous_hash: String,
    ) -> u64 {
        assert!(!commitment_id.trim().is_empty(), "commitment_id is required");
        assert!(commitment_id.len() <= 128, "commitment_id is too long");
        assert!(!event_type.trim().is_empty(), "event_type is required");
        assert!(event_type.len() <= 48, "event_type is too long");
        assert!(is_sha256_hex(&payload_hash), "payload_hash must be 32-byte SHA-256 hex");
        assert_eq!(
            previous_hash, self.last_hash,
            "Audit chain predecessor does not match latest event"
        );

        let sequence = self.next_sequence;
        let actor = env::predecessor_account_id();
        let block_height = env::block_height();
        let block_timestamp_ns = env::block_timestamp();
        let canonical = format!(
            "{}|{}|{}|{}|{}|{}|{}|{}",
            sequence,
            commitment_id,
            event_type,
            payload_hash.to_lowercase(),
            previous_hash,
            actor,
            block_height,
            block_timestamp_ns
        );
        let event_hash = to_hex(&env::sha256(canonical.as_bytes()));
        let used = estimate_new_event_bytes(
            self.last_hash.as_str(),
            commitment_id.as_str(),
            event_type.as_str(),
            &payload_hash.to_lowercase(),
            previous_hash.as_str(),
            actor.as_str(),
        );
        let event = AuditEvent {
            sequence,
            commitment_id,
            event_type,
            payload_hash: payload_hash.to_lowercase(),
            previous_hash,
            event_hash: event_hash.clone(),
            actor: actor.clone(),
            block_height,
            block_timestamp_ns,
        };
        self.events.insert(sequence, event);
        self.events.flush();
        self.next_sequence += 1;
        self.last_hash = event_hash;
        let required = env::storage_byte_cost().saturating_mul(u128::from(used));
        let attached = env::attached_deposit();
        let is_owner = actor == self.owner_id;
        if !is_owner {
            assert!(
                attached >= required,
                "Insufficient storage deposit: need {} yoctoNEAR",
                required.as_yoctonear()
            );
        }
        let refund = if is_owner {
            attached
        } else {
            attached.saturating_sub(required)
        };
        if refund > NearToken::from_yoctonear(0) {
            Promise::new(env::predecessor_account_id()).transfer(refund);
        }
        env::log_str(&format!(
            "OUTCOME_AUDIT_EVENT:{}:{}:{}",
            sequence,
            self.last_hash,
            used
        ));
        sequence
    }

    /// YoctoNEAR a non-owner must attach for these arguments.
    /// The owner may attach zero. This does not write state.
    pub fn get_required_storage_deposit(
        &self,
        commitment_id: String,
        event_type: String,
        payload_hash: String,
        previous_hash: String,
        actor_account_id: String,
    ) -> String {
        let bytes = estimate_new_event_bytes(
            &self.last_hash,
            &commitment_id,
            &event_type,
            &payload_hash,
            &previous_hash,
            &actor_account_id,
        );
        env::storage_byte_cost()
            .saturating_mul(u128::from(bytes))
            .as_yoctonear()
            .to_string()
    }

    /// This state-changing method is intentionally excluded from the agent
    /// key's allowlist. An attempt to call it with that key must be rejected
    /// by NEAR protocol access-key permissions before contract execution.
    pub fn admin_probe(&mut self) -> u64 {
        assert_eq!(
            env::predecessor_account_id(),
            self.owner_id,
            "Only the configured owner account may run admin_probe"
        );
        self.admin_probe_count += 1;
        self.admin_probe_count
    }

    pub fn get_event(&self, sequence: u64) -> Option<AuditEvent> {
        self.events.get(&sequence).cloned()
    }

    pub fn get_latest(&self) -> Option<AuditEvent> {
        if self.next_sequence <= 1 {
            None
        } else {
            self.get_event(self.next_sequence - 1)
        }
    }

    pub fn get_latest_hash(&self) -> String {
        self.last_hash.clone()
    }

    pub fn get_next_sequence(&self) -> u64 {
        self.next_sequence
    }

    pub fn get_owner(&self) -> AccountId {
        self.owner_id.clone()
    }

    pub fn get_admin_probe_count(&self) -> u64 {
        self.admin_probe_count
    }

    /// Policy implemented by this wasm.
    ///
    /// `any_account` means any predecessor may call `record_event`. A
    /// non-owner must still attach the deposit from
    /// `get_required_storage_deposit` for that call. This string is the rule
    /// in this code, not a reading of `thewordweaver.testnet`. That account
    /// does not expose this method until this wasm is deployed there.
    pub fn get_anchor_policy(&self) -> String {
        "any_account".to_string()
    }
}

fn estimate_new_event_bytes(
    last_hash: &str,
    commitment_id: &str,
    event_type: &str,
    payload_hash: &str,
    previous_hash: &str,
    actor_account_id: &str,
) -> u64 {
    let actor = AccountId::try_from(actor_account_id.to_string())
        .unwrap_or_else(|_| env::panic_str("actor account id is invalid"));
    let event = AuditEvent {
        sequence: 0,
        commitment_id: commitment_id.to_string(),
        event_type: event_type.to_string(),
        payload_hash: payload_hash.to_string(),
        previous_hash: previous_hash.to_string(),
        event_hash: "0".repeat(64),
        actor,
        block_height: 0,
        block_timestamp_ns: 0,
    };
    let value_len = borsh::to_vec(&event)
        .unwrap_or_else(|_| env::panic_str("could not measure event storage"))
        .len() as u64;
    let mut key = borsh::to_vec(&StorageKey::Events)
        .unwrap_or_else(|_| env::panic_str("could not measure storage key"));
    key.extend(
        borsh::to_vec(&0u64).unwrap_or_else(|_| env::panic_str("could not measure sequence key")),
    );
    let mut total = value_len + key.len() as u64;
    // Root contract state grows when last_hash becomes the 64-character event hash.
    if last_hash.len() < 64 {
        total += (64 - last_hash.len()) as u64;
    }
    total
}

fn is_sha256_hex(value: &str) -> bool {
    value.len() == 64 && value.bytes().all(|byte| byte.is_ascii_hexdigit())
}

fn to_hex(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut output = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        output.push(HEX[(byte >> 4) as usize] as char);
        output.push(HEX[(byte & 0x0f) as usize] as char);
    }
    output
}
