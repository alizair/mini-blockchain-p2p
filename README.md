# Mini Blockchain P2P - ECE 496C / 519C Assignment 2 (UTXO Currency)

Assignment 2 turns the Assignment 1 text-message ledger into a UTXO currency: block payloads now hold signed **transactions** instead of text messages (see [Assignment 2 - Part 1](#assignment-2---part-1-utxo-transactions-signatures--output-locking)).

Assignment 1 base:

1. **Part 1** - Legacy Bitcoin (P2PKH) vanity address generator
2. **Part 2** - Hardcoded genesis block, deterministic block hashing and on-demand Proof-of-Work mining
3. **Part 3** - HTTP-based P2P network of 3 local nodes with block validation (consensus upgraded to most-work in A2)

Language: **Python 3.9+**

---

## Project Structure

```
mini-blockchain-p2p/
├── main.py            # Entry point: CLI args, node startup, terminal commands
├── vanity.py          # Part 1: keypair generation, P2PKH address, vanity search
├── blockchain.py      # Part 2: genesis block, hashing, mining, validation
├── network.py         # Part 3: Flask server, endpoints, broadcasting
├── transaction.py     # A2 Part 1: tx structure, txid, ECDSA signing, locking, fees, change
├── test_part1.py      # A2 Part 1 self-test (python test_part1.py)
├── node1.json         # Node 1 config (port 8001)
├── node2.json         # Node 2 config (port 8002)
├── node3.json         # Node 3 config (port 8003)
├── keypair.json       # Assignment 1 vanity keypair, used by Node1 (assignment use only)
├── keypair2.json      # Node2 keypair (no vanity prefix)
├── keypair3.json      # Node3 keypair (no vanity prefix)
├── requirements.txt
├── README.md
└── AI_Usage_Log.md
```

---

## Setup

```bash
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Dependencies: `ecdsa`, `pycryptodome` (RIPEMD-160), `base58`, `flask`, `requests`.

---

## Running the 3 Nodes

Open **three terminals**, activate the virtual environment in each, then run one command per terminal:

```bash
# Terminal 1 (Node1, Assignment 1 vanity keypair)
python main.py --config node1.json --vanity-prefix ALI --difficulty 4

# Terminal 2
python main.py --config node2.json --difficulty 4

# Terminal 3
python main.py --config node3.json --difficulty 4
```

Each node loads the keypair file named in its config. An existing keypair file is never overwritten (coins are locked to its address); `--vanity-prefix` only triggers a vanity search when `keypair.json` does not exist yet.

Start all three nodes before mining. The chain is kept in memory only.

### CLI Arguments

| Argument          | Description                                                        |
| ----------------- | ------------------------------------------------------------------ |
| `--config`        | Path to the node's JSON config file                                |
| `--vanity-prefix` | Node1 only: characters 2–4 of the address (case-insensitive)       |
| `--difficulty`    | Number of leading hex zeros required in a block hash (default `4`) |

### Config Format (`node1.json`)

```json
{
  "node_id": "Node1",
  "port": 8001,
  "peers": ["http://127.0.0.1:8002", "http://127.0.0.1:8003"],
  "keypair": "keypair.json"
}
```

Each node lists the other two nodes as peers. `keypair` is `keypair.json` (Node1), `keypair2.json` (Node2) or `keypair3.json` (Node3).

---

## Commands

### Terminal

| Command      | Action                                                                              |
| ------------ | ----------------------------------------------------------------------------------- |
| `send <address> <amount> [--fee N]` | Create + sign a tx (with change), add to mempool, broadcast to peers |
| `mine`       | Mine a block: coinbase (50 + fees) + valid mempool txs, then broadcast |
| `balance`    | This node's balance and the UTXOs that make it up                    |
| `utxos`      | The node's whole confirmed UTXO set                                  |
| `mempool`    | Unconfirmed transactions waiting to be mined                         |
| `chain`      | Print the chain with each block's transactions                       |
| `sync`       | Ask peers for their chains, switch to one with more work (bonus)     |
| `address`    | Print this node's address and public key                             |
| `doublespend <address> <amount>` | Demo: re-spend a UTXO already spent in the mempool (rejected) |
| `badsig <address> <amount>` | Demo: tx with a signature over the wrong data (rejected)      |
| `quit`       | Stop the node                                                                       |

### HTTP Endpoints

| Method & Path   | Purpose                                                    |
| --------------- | ---------------------------------------------------------- |
| `POST /block`   | Receive a block from a peer (runs the block validation checks) |
| `POST /tx`      | Receive a transaction from a peer (runs Checks 1-4, adds to mempool) |
| `GET /mempool`  | Return this node's mempool as JSON                         |
| `POST /mine`    | Trigger mining on this node                                |
| `GET /chain`    | Return this node's chain as JSON                           |

---

## Part 1 - Vanity Address Generation

Standard Legacy P2PKH generation (`vanity.py`):

1. Generate an ECDSA **secp256k1** keypair; take the **compressed** public key (33 bytes).
2. `pubkey_hash = RIPEMD160(SHA256(pubkey))` (20 bytes).
3. Prepend mainnet version byte `0x00` (21 bytes).
4. `checksum = SHA256(SHA256(versioned))[:4]`.
5. `address = Base58(versioned + checksum)`.

**Vanity rule:** `address[1:4].upper() == prefix.upper()`. Keys are generated randomly until this holds, then saved to `keypair.json` and reused on every run (no searching during the demo).

To regenerate the keypair:

```bash
rm keypair.json
python vanity.py ALI
```

Sanity check against a known test vector (private key = 1):

```bash
python vanity.py test     # prints 1BgGZ9tcN4rm9KBzDn7KprQz87SZ26SAMH
```

---

## Part 2 - Block Structure & Hashing

### Block Fields

| Field           | Type         | Description                               |
| --------------- | ------------ | ----------------------------------------- |
| `index`         | int          | Block height                              |
| `prev_hash`     | str (64 hex) | Hash of the previous block                |
| `timestamp`     | str          | UTC ISO-8601, e.g. `2026-10-24T12:00:00Z` |
| `transactions`  | list[tx]     | Transactions in the block (A2, replaces `data`) |
| `merkle_root`   | str (64 hex) | Merkle root of the block's txids (A2 bonus) |
| `nonce`         | int          | Proof-of-Work counter                     |
| `miner_address` | str          | Miner's vanity address                    |
| `hash`          | str (64 hex) | SHA-256 of the fields above               |

### Hashing Format (exact)

The `hash` field is **not** part of the hash input. Fields are joined with `|` in this order:

```
index|timestamp|prev_hash|merkle_root|nonce|miner_address
```

- `merkle_root` replaces the txid list (bonus). It is computed over the block's txids **in block order**:
  - each level pairs neighbours: `sha256(left_hex + right_hex)` on the 64-char hex strings, output as hex
  - an odd count duplicates the last hash
  - one tx → the root is its txid; no transactions → `"0" * 64`
- Each txid commits to its transaction's inputs and outputs, and the root commits to every txid and their order, so the block hash covers all transactions. Receivers recompute the root from `transactions` and reject the block if it doesn't match.
- `index` and `nonce` are written as plain decimal integers
- The resulting string is UTF-8 encoded and hashed with **SHA-256**, output as lowercase hex

Python:

```python
root = merkle_root([tx["txid"] for tx in block["transactions"]])
s = f'{index}|{timestamp}|{prev_hash}|{root}|{nonce}|{miner_address}'
hash = hashlib.sha256(s.encode()).hexdigest()
```

Example input string for genesis:

```
0|2026-01-01T00:00:00Z|0000000000000000000000000000000000000000000000000000000000000000|0000000000000000000000000000000000000000000000000000000000000000|0|GENESIS
```

### Genesis Block (hardcoded, identical on all nodes)

| Field         | Value                  |
| ------------- | ---------------------- |
| index         | `0`                    |
| prev_hash     | `"0" * 64`             |
| timestamp     | `2026-01-01T00:00:00Z` |
| nonce         | `0`                    |
| miner_address | `GENESIS`              |
| transactions  | `[]` (no coins at start) |

The genesis hash is computed with the same formula but is trusted and **not** checked for Proof-of-Work.

### Mining

On `mine`, the node takes all pending transactions (or `[]` if none), builds a block on top of its tip with its vanity address as `miner_address`, and increments `nonce` from 0 until the hash starts with `difficulty` zeros. The block is appended to the local chain **first**, then broadcast to peers. Mining runs once per command (no background mining).

---

## Part 3 - P2P Network, Validation & Consensus

### Communication

Each node runs a Flask HTTP server in a background thread. After mining, the block is `POST`ed to `/block` on every peer (one thread per peer). Nodes are fully connected, so received blocks are **not** re-forwarded. If a peer is offline, the node logs a warning and keeps running.

### Validation (on every received block)

1. **Hash verification:** recompute the hash from the payload (excluding `hash`) and compare to `block["hash"]`; recompute the Merkle root from `transactions` and compare to `block["merkle_root"]`.
2. **Difficulty verification:** the recomputed hash starts with `difficulty` zeros.
3. **Chain continuity:** `block["prev_hash"] == tip["hash"]`.
4. **Index sequence:** `block["index"] == tip["index"] + 1`.
5. **Coinbase (A2):** the first transaction is a coinbase (zeroed outpoint, `height` = block index) and it is the only one.
6. **Transactions (A2):** every other transaction passes Checks 1-4 below, with no outpoint spent twice within the block.
7. **Reward (A2):** coinbase output total ≤ 50 + total fees of the block's other transactions.

After a block is accepted (mined locally or received), the node updates its UTXO set (removes spent outputs, adds new outputs) and removes the block's transactions, and any mempool tx whose inputs are now gone, from its mempool.

The block is appended only if all checks pass. Each check is printed with ✓ in the node's terminal.

### Consensus: Most-Work Chain Rule (A2 bonus, replaces First-Valid-Block-Wins)

- **Normal case:** a node accepts the first valid block that extends its tip.
- **Ties:** a competing block at the same height has equal work, so the node keeps its own chain.
- **Sync trigger:** if a received block does **not** fit the tip (`prev_hash` or `index` mismatch), the node assumes a peer may be ahead. It requests full chains with `GET /chain` from every peer (`sync_with_peers` in `network.py`). The `sync` command does the same by hand.
- **Chain check (`try_replace_chain` in `blockchain.py`):**
  1. The genesis block must match ours.
  2. Total work = number of blocks × `16^difficulty`. Each leading hex zero has a 1/16 chance, so that's the expected number of hashes per block. The peer's chain must have **more** total work than ours. Difficulty is fixed, so the most-work chain is the longest valid chain.
  3. The chain is rebuilt from genesis in a fresh `Blockchain`: every block runs all validation checks and `apply_block`. This rebuilds the UTXO set from scratch, and one invalid block rejects the whole chain.
- **Reorganization:** if the peer's chain passes, the node replaces its chain and UTXO set. Transactions from its own dropped blocks, plus its old mempool, are re-checked against the new UTXO set, and the valid ones go back into the mempool. The coinbases of dropped blocks disappear.

**Concurrency:** a single lock protects the chain. Mining holds the lock until its block is found and appended, so a block that arrives mid-mining is validated afterwards against the new tip and rejected. Neither miner yields its own block.

### Forks

If two nodes mine at the same height at the same time, each keeps its own block for now, so the network temporarily splits. As soon as either side mines the next block, the other side's nodes receive a block that doesn't fit their tip. They fetch the longer chain and reorganize onto it, so the network converges again.

---

## Assignment 2 - Part 1: UTXO Transactions, Signatures & Output Locking

Code: `transaction.py`. There are no account balances: value lives in **unspent transaction outputs (UTXOs)**. A balance is the sum of the UTXOs locked to an address.

### Transaction Structure

```json
{
  "txid": "5f54115a...f29214",
  "inputs": [
    {
      "prev_txid": "cafa8691...99abcc",
      "output_index": 0,
      "scriptSig": { "signature": "<DER hex>", "public_key": "<33-byte compressed pubkey hex>" }
    }
  ],
  "outputs": [
    { "amount": 20, "scriptPubKey": { "recipient_address": "1Fhsqa...qrZ5N" } },
    { "amount": 29, "scriptPubKey": { "recipient_address": "1ALiVn...Xe1tP" } }
  ]
}
```

| Field                                  | Meaning                                                                 |
| -------------------------------------- | ----------------------------------------------------------------------- |
| `inputs[].prev_txid` + `output_index`  | **Outpoint**: which earlier output is being spent (`txid:index`)       |
| `inputs[].scriptSig`                   | Unlocking key: spender's ECDSA signature + full (compressed) public key |
| `outputs[].amount`                     | Positive integer, atomic units (no fractions, no zero/negative)         |
| `outputs[].scriptPubKey.recipient_address` | Lock: `Base58Check(0x00 ‖ RIPEMD160(SHA256(PublicKey)))`            |

### txid Serialization (exact)

`txid = SHA-256(preimage)` as lowercase hex, where the preimage is compact JSON (`separators=(",", ":")`, UTF-8) with keys in exactly this order. **`scriptSig` is excluded**, so signing does not change the txid.

```
{"inputs":[{"prev_txid":<hex>,"output_index":<int>}, ...],"outputs":[{"amount":<int>,"recipient_address":<str>}, ...]}
```

Real example (Node1 pays Node2 20, fee 1, change 29):

```
{"inputs":[{"prev_txid":"cafa8691cd4cfd195091db2b97777a52c99cfcc8a5258c10f5e7ee355199abcc","output_index":0}],"outputs":[{"amount":20,"recipient_address":"1FhsqaSQfW2zUi9MshycFudAzxdnKqrZ5N"},{"amount":29,"recipient_address":"1ALiVng8exiZxpwRJujRX9aMbocNAXe1tP"}]}
```

A coinbase input also carries `"height":<int>` right after `output_index` (`{"prev_txid":"000…000","output_index":0,"height":1}`), so coinbases in different blocks never share a txid.

### Signing & Lock Verification

- **Sign:** the spender signs the 32-byte txid directly as the digest with ECDSA **secp256k1** (`sign_digest_deterministic`, RFC 6979 nonces, DER encoding). The txid is already a SHA-256 hash, so it is not hashed again.
- **scriptSig:** `{"signature": <DER hex>, "public_key": <compressed pubkey hex>}`.
- **Unlock check (`unlocks()`):**
  1. Decode the output's `recipient_address` with Base58Check (verify the 4-byte checksum and version `0x00`) to get the 20-byte hash.
  2. Require `RIPEMD160(SHA256(public_key)) ==` that hash: the key must belong to the address.
  3. Require the signature to verify over the txid with that public key.

### Fees & Change

- There is **no fee field**. `fee = sum(input amounts) − sum(output amounts)` (`compute_fee()`), and it must be `≥ 0`. Anything not assigned to an output goes to the miner.
- UTXOs are spent **whole**, like banknotes. `create_transaction(keypair, utxo_set, recipient, amount, fee)` picks the sender's UTXOs (largest first) until it covers `amount + fee`, then creates:
  - output 0: `amount` → recipient
  - output 1: `total − amount − fee` → sender (**change**), left out if it is 0
- It computes the txid and then fills every input's `scriptSig`.

UTXO set format used by these helpers: `{"<txid>:<index>": {"amount": int, "address": str}}`.

### Self-Test

```bash
python test_part1.py
```

This test builds a 50-unit output locked to Node1 and spends 20 to Node2 with fee 1 (29 change). It checks:

- the txid ignores `scriptSig`
- tampering breaks the txid or the signature
- Node3's key cannot unlock Node1's output
- non-positive or fractional amounts are rejected
- the block hash depends on txid order

---

## Assignment 2 - Part 2: Mempool, Validation & Coinbase Rewards

Code: `blockchain.py` (`check_transaction`, `add_transaction`, `apply_block`, `mine`, `_validate`).

- **UTXO set:** `bc.utxos = {"txid:index": {"amount", "address"}}`, built only from confirmed blocks.
- **Mempool:** `bc.mempool`, a list of unconfirmed transactions.

### The 4 Transaction Checks (`check_transaction`)

Every transaction runs these before it enters the mempool or a block:

1. **Existence:** every input outpoint is in the confirmed UTXO set. Outputs of mempool transactions cannot be spent.
2. **Unspent / double spend:** the outpoint is not already spent by a mempool transaction (or by an earlier tx in the same block), and it doesn't appear twice in the same tx.
3. **Authorization:** `pubkey_to_address(public_key)` equals the output's address, and the ECDSA signature is valid over the txid.
4. **Conservation:** `sum(inputs) >= sum(outputs)`. The difference is the fee.

Coinbase transactions skip these checks; the block checks 5-7 cover them instead.

### Coinbase

The first transaction in every mined block has:
- one input: `prev_txid = "000…000"`, `output_index = 0`, `height = <block index>` (the height is in the txid, so coinbase txids are unique)
- one output: `50 + fees` locked to the miner's address

### Broadcasting

- **Transactions:** `send` posts the new tx to every peer's `/tx`. Peers run Checks 1-4 and add it to their mempool, or log the reason and reject it.
- **Blocks:** after `mine`, the block is posted to every peer's `/block`. Invalid data is rejected with a logged reason and never crashes the node.

## Happy Path Demo

```
Node1> mine                                   # Block #1, coinbase 50 -> Node1
Node1> balance                                # 50
Node1> send <Node2 address> 20 --fee 1        # change 29 back to Node1
Node2> mempool                                # the tx shows up on Node2 / Node3
Node1> doublespend <Node2 address> 30         # rejected: Check 2 (locally and by peers)
Node1> mine                                   # Block #2, coinbase 51 (50 + 1 fee)
Node1> utxos                                  # 51 (coinbase), 20 (Node2), 29 (change)
Node1> balance                                # 80
Node2> balance                                # 20
Node1> badsig <Node2 address> 5               # rejected: Check 3 invalid signature
```

Run `badsig` when Node1 has a UTXO that isn't already pending in the mempool (for example right after a `mine`).

---

## Demo Walkthrough

**1. Launch:** start the three nodes as shown above.

**2. On-demand mining:** on Node 1:

```
Node1> mine
```

Nodes 2 and 3 print the four validation checks and append Block #1. Run `chain` on each node to confirm identical chains.

**3. Fork:** restart all three nodes with `--difficulty 6`, then trigger Nodes 2 and 3 simultaneously from a fourth terminal:

```bash
curl -s -X POST http://127.0.0.1:8002/mine > /dev/null & curl -s -X POST http://127.0.0.1:8003/mine > /dev/null & wait
```

(or type `mine` in both terminals quickly). Then run `chain` on each node:

- Node 2 and Node 3 each hold their own Block #1 (different hashes) and rejected the other's with a `prev_hash` mismatch.
- Node 1 holds whichever block arrived first.
- Run `mine` again on Node 2. Node 3 logs `Block does not extend our tip, requesting full chains from peers...` then `REORG: switched from height 1 to 2 (more work). UTXO set rebuilt...`.
- Run `chain` / `utxos` on all three nodes: they are identical again. Node 3's orphaned coinbase is gone.

**Offline peer test:** stop one node and run `mine` on another; a warning is logged and the node continues normally.

---

## Notes

- `keypair.json` is for this assignment only and must never be used for real funds.
- The `NotOpenSSLWarning` from `urllib3` on macOS system Python is harmless (only local plain HTTP is used).
