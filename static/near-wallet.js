import { NearConnector } from '@hot-labs/near-connect';

const NETWORK = 'testnet';
const listeners = new Set();
const state = { accountId: '', connected: false, error: '' };

const connector = new NearConnector({
  network: NETWORK,
  autoConnect: false,
  features: {
    signMessage: true,
    signAndSendTransaction: true,
    testnet: true,
  },
});

function emit() {
  const snapshot = { accountId: state.accountId, connected: state.connected, error: state.error, network: NETWORK };
  listeners.forEach((listener) => listener(snapshot));
}

connector.on('wallet:signOut', () => {
  state.accountId = '';
  state.connected = false;
  emit();
});

connector.on('wallet:signIn', (event) => {
  const account = event && event.accounts && event.accounts[0];
  state.accountId = account && account.accountId ? account.accountId : '';
  state.connected = Boolean(state.accountId);
  state.error = '';
  emit();
});

async function connect() {
  state.error = '';
  try {
    await connector.connect();
    const wallet = await connector.wallet();
    const accounts = await wallet.getAccounts();
    state.accountId = accounts[0] ? accounts[0].accountId : '';
    state.connected = Boolean(state.accountId);
    if (!state.connected) throw new Error('The wallet did not return a testnet account.');
    if (!state.accountId.endsWith('.testnet') && !/^[0-9a-f]{64}$/.test(state.accountId)) {
      throw new Error('Connect a NEAR testnet account. Mainnet accounts are not used here.');
    }
    emit();
    return state.accountId;
  } catch (error) {
    state.connected = false;
    state.error = error && error.message ? error.message : 'Wallet connection failed.';
    emit();
    throw error;
  }
}

async function disconnect() {
  try {
    await connector.disconnect();
  } catch (error) {
    state.error = error && error.message ? error.message : 'Could not disconnect the wallet.';
  }
  state.accountId = '';
  state.connected = false;
  emit();
}

async function signMessage(params) {
  const wallet = await connector.wallet();
  return wallet.signMessage({
    message: params.message,
    recipient: params.recipient,
    nonce: params.nonce,
    network: NETWORK,
  });
}

async function signAndSendRecordEvent(contractId, args, deposit) {
  if (!String(contractId).endsWith('.testnet')) {
    throw new Error('Refusing to sign anything except a testnet contract.');
  }
  if (!/^[0-9]+$/.test(String(deposit || ''))) {
    throw new Error('Refusing to sign without the exact storage deposit.');
  }
  const wallet = await connector.wallet();
  return wallet.signAndSendTransaction({
    receiverId: contractId,
    network: NETWORK,
    actions: [{
      type: 'FunctionCall',
      params: {
        methodName: 'record_event',
        args,
        gas: '100000000000000',
        deposit: String(deposit),
      },
    }],
  });
}

window.OutcomeWallet = {
  network: NETWORK,
  connect,
  disconnect,
  signMessage,
  signAndSendRecordEvent,
  getState: () => ({ accountId: state.accountId, connected: state.connected, error: state.error, network: NETWORK }),
  subscribe(listener) {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
};
