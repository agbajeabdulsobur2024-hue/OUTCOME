"""Human payment decisions. This does not call NEAR Intents and does not move funds.

The official Agent API publishes network \"mainnet\" at GET /v1/network and the
hackathon rules say no Intents testnet exists (Q-007). A passing proposal can
be approved by a person. That approval is recorded. It is not a sandbox payment.
"""

import json
from urllib import error as urlerror
from urllib import request as urlrequest

OFFICIAL_NETWORK_URL = 'https://api.agentsonintents.com/v1/network'
NOT_SUBMITTED_REASON = (
    'A person approved this proposal. NEAR Intents has no testnet, and the official '
    'Agent API is mainnet-only, so OUTCOME did not submit a transfer. No funds moved.'
)


def observe_official_network() -> str:
    """Read the public network document. This sends no payment and uses no key."""
    req = urlrequest.Request(OFFICIAL_NETWORK_URL, headers={'Accept': 'application/json'})
    try:
        with urlrequest.urlopen(req, timeout=8) as response:
            payload = json.loads(response.read().decode('utf-8'))
    except (urlerror.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError):
        return 'unconfirmed'
    if not isinstance(payload, dict):
        return 'unconfirmed'
    network = str(payload.get('network') or '').strip().lower()
    return network or 'unconfirmed'


def human_payment_outcome(decision: str, proposal_status: str, official_network: str) -> dict:
    if decision == 'APPROVED' and proposal_status != 'AWAITING_HUMAN_APPROVAL':
        raise ValueError('Only a proposal that is waiting for a person can be approved.')
    if decision == 'REJECTED':
        return {
            'financial_action': 'NOT_SUBMITTED',
            'human_decision': 'REJECTED',
            'transfer_executed': False,
            'transaction_hash': None,
            'sandbox_executed': False,
            'official_network': official_network,
            'reason': 'A person rejected this proposal. No transfer was submitted.',
        }
    if official_network == 'testnet':
        # The published API does not return this. If it ever does, still do not
        # submit from this build: there is no partner key and no grant.
        reason = NOT_SUBMITTED_REASON
    else:
        reason = NOT_SUBMITTED_REASON
    return {
        'financial_action': 'NOT_SUBMITTED',
        'human_decision': 'APPROVED',
        'transfer_executed': False,
        'transaction_hash': None,
        'sandbox_executed': False,
        'official_network': official_network,
        'reason': reason,
    }
