use near_sdk::borsh::{BorshDeserialize, BorshSerialize};
use near_sdk::store::LookupMap;
use near_sdk::{env, near, AccountId, BorshStorageKey, PanicOnDefault};

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

    /// Append a hash-only commitment event. The caller must be the configured
    /// owner account. A restricted function-call key can be scoped by NEAR to
    /// this method alone; protocol-level key permissions are checked first.
    pub fn record_event(
        &mut self,
        commitment_id: String,
        event_type: String,
        payload_hash: String,
        previous_hash: String,
    ) -> u64 {
        assert_eq!(
            env::predecessor_account_id(),
            self.owner_id,
            "Only the configured owner account may append audit events"
        );
        assert!(!commitment_id.trim().is_empty(), "commitment_id is required");
        assert!(commitment_id.len() <= 128, "commitment_id is too long");
        assert!(!event_type.trim().is_empty(), "event_type is required");
        assert!(event_type.len() <= 48, "event_type is too long");
        assert!(is_sha256_hex(&payload_hash), "payload_hash must be 32-byte SHA-256 hex");
        assert_eq!(previous_hash, self.last_hash, "Audit chain predecessor does not match latest event");

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
        let event = AuditEvent {
            sequence,
            commitment_id,
            event_type,
            payload_hash: payload_hash.to_lowercase(),
            previous_hash,
            event_hash: event_hash.clone(),
            actor,
            block_height,
            block_timestamp_ns,
        };
        self.events.insert(sequence, event);
        self.next_sequence += 1;
        self.last_hash = event_hash;
        env::log_str(&format!("OUTCOME_AUDIT_EVENT:{}:{}", sequence, self.last_hash));
        sequence
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
