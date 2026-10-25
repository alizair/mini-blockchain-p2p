# Mini Blockchain P2P - ECE 496C / 519C Assignment 1

A lightweight Layer 1 blockchain with:

1. **Part 1** - Legacy Bitcoin (P2PKH) vanity address generator
2. **Part 2** - Hardcoded genesis block, deterministic block hashing and on-demand Proof-of-Work mining
3. **Part 3** - HTTP-based P2P network of 3 local nodes with block validation and First-Valid-Block-Wins consensus

Language: **Python 3.9+**

---

## Project Structure

```
mini-blockchain-p2p/
├── main.py            # Entry point: CLI args, node startup, terminal commands
├── vanity.py          # Part 1: keypair generation, P2PKH address, vanity search
├── blockchain.py      # Part 2: genesis block, hashing, mining, validation
├── network.py         # Part 3: Flask server, endpoints, broadcasting
├── node1.json         # Node 1 config (port 8001)
├── node2.json         # Node 2 config (port 8002)
├── node3.json         # Node 3 config (port 8003)
├── keypair.json       # Pre-computed vanity keypair (assignment use only, never for real funds)
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
# Terminal 1
python main.py --config node1.json --vanity-prefix ALI --difficulty 4

# Terminal 2
python main.py --config node2.json --vanity-prefix ALI --difficulty 4

# Terminal 3
python main.py --config node3.json --vanity-prefix ALI --difficulty 4
```

Start all three nodes before mining. The chain is kept in memory only.

### CLI Arguments

| Argument          | Description                                                        |
| ----------------- | ------------------------------------------------------------------ |
| `--config`        | Path to the node's JSON config file                                |
| `--vanity-prefix` | Required characters 2–4 of the address (case-insensitive)          |
| `--difficulty`    | Number of leading hex zeros required in a block hash (default `4`) |

### Config Format (`node1.json`)

```json
{
  "node_id": "Node1",
  "port": 8001,
  "peers": ["http://127.0.0.1:8002", "http://127.0.0.1:8003"]
}
```

Each node lists the other two nodes as peers.

---

## Commands

### Terminal

| Command      | Action                                                                              |
| ------------ | ----------------------------------------------------------------------------------- |
| `msg <text>` | Add a pending message (local to this node)                                          |
| `mine`       | Mine one block with pending messages, add it to the local chain, broadcast it, stop |
| `chain`      | Print this node's chain                                                             |
| `pending`    | Show pending messages                                                               |
| `quit`       | Stop the node                                                                       |

### HTTP Endpoints

| Method & Path   | Purpose                                                    |
| --------------- | ---------------------------------------------------------- |
| `POST /block`   | Receive a block from a peer (runs the 4 validation checks) |
| `POST /message` | Add a pending message, body: `{"text": "..."}`             |
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
| `data`          | list[str]    | Messages included in the block            |
| `nonce`         | int          | Proof-of-Work counter                     |
| `miner_address` | str          | Miner's vanity address                    |
| `hash`          | str (64 hex) | SHA-256 of the fields above               |

### Hashing Format (exact)

The `hash` field is **not** part of the hash input. Fields are joined with `|` in this order:

```
index|timestamp|prev_hash|data|nonce|miner_address
```

- `data` is serialized with `json.dumps(data, separators=(",", ":"))` → e.g. `["hello","world"]`, empty list → `[]`
- `index` and `nonce` are written as plain decimal integers
- The resulting string is UTF-8 encoded and hashed with **SHA-256**, output as lowercase hex

Python:

```python
data_str = json.dumps(block["data"], separators=(",", ":"))
s = f'{index}|{timestamp}|{prev_hash}|{data_str}|{nonce}|{miner_address}'
hash = hashlib.sha256(s.encode()).hexdigest()
```

Example input string for genesis:

```
0|2026-01-01T00:00:00Z|0000000000000000000000000000000000000000000000000000000000000000|["Genesis Block"]|0|GENESIS
```

### Genesis Block (hardcoded, identical on all nodes)

| Field         | Value                  |
| ------------- | ---------------------- |
| index         | `0`                    |
| prev_hash     | `"0" * 64`             |
| timestamp     | `2026-01-01T00:00:00Z` |
| nonce         | `0`                    |
| miner_address | `GENESIS`              |
| data          | `["Genesis Block"]`    |

The genesis hash is computed with the same formula but is trusted and **not** checked for Proof-of-Work.

### Mining

On `mine`, the node takes all pending messages (or `[]` if none), builds a block on top of its tip with its vanity address as `miner_address`, and increments `nonce` from 0 until the hash starts with `difficulty` zeros. The block is appended to the local chain **first**, then broadcast to peers. Mining runs once per command (no background mining).

---

## Part 3 - P2P Network, Validation & Consensus

### Communication

Each node runs a Flask HTTP server in a background thread. After mining, the block is `POST`ed to `/block` on every peer (one thread per peer). Nodes are fully connected, so received blocks are **not** re-forwarded. If a peer is offline, the node logs a warning and keeps running.

### Validation (on every received block)

1. **Hash verification:** recompute the hash from the payload (excluding `hash`) and compare to `block["hash"]`.
2. **Difficulty verification:** the recomputed hash starts with `difficulty` zeros.
3. **Chain continuity:** `block["prev_hash"] == tip["hash"]`.
4. **Index sequence:** `block["index"] == tip["index"] + 1`.

The block is appended only if all four checks pass. Each check is printed with ✓ in the node's terminal.

### Consensus: First-Valid-Block-Wins

A node accepts the first valid block that extends its current tip and rejects any later block at the same height (it fails the continuity check).

**Concurrency:** a single lock protects the chain. Mining holds the lock until its block is found and appended, so a block that arrives mid-mining is validated afterwards against the new tip and rejected. Neither miner yields its own block.

### Known Limitation - Forks

If two nodes mine at the same height simultaneously, each keeps its own block and rejects the other's, so the network splits permanently. This is expected for Assignment 1; most-work chain selection is planned for Assignment 2.

---

## Demo Walkthrough

**1. Launch:** start the three nodes as shown above.

**2. On-demand mining:** on Node 1:

```
Node1> msg hello from node 1
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
- Mining again on Node 2 is rejected by Node 3, so the split persists.

**Offline peer test:** stop one node and run `mine` on another; a warning is logged and the node continues normally.

---

## Notes

- `keypair.json` is for this assignment only and must never be used for real funds.
- The `NotOpenSSLWarning` from `urllib3` on macOS system Python is harmless (only local plain HTTP is used).
