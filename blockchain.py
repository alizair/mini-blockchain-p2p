import hashlib
import json
import threading
from datetime import datetime, timezone

from transaction import (
    BLOCK_SUBSIDY, is_coinbase, make_coinbase, outpoint_key, unlocks, validate_structure,
)

GENESIS = {
    "index": 0,
    "prev_hash": "0" * 64,
    "timestamp": "2026-01-01T00:00:00Z",
    "transactions": [],  # chain starts with no coins
    "nonce": 0,
    "miner_address": "GENESIS",
}


def merkle_root(txids):
    """Pair up hashes level by level: sha256(left + right) on the hex strings.
    Odd count -> duplicate the last one. No transactions -> 64 zeros."""
    if not txids:
        return "0" * 64
    level = list(txids)
    while len(level) > 1:
        if len(level) % 2 == 1:
            level.append(level[-1])
        level = [
            hashlib.sha256((level[i] + level[i + 1]).encode()).hexdigest()
            for i in range(0, len(level), 2)
        ]
    return level[0]


def block_merkle_root(block):
    return merkle_root([tx["txid"] for tx in block["transactions"]])


def hash_input(block: dict) -> str:
    """Order: index|timestamp|prev_hash|merkle_root|nonce|miner_address  ('hash' NOT included)."""
    return (
        f'{block["index"]}|{block["timestamp"]}|{block["prev_hash"]}|'
        f'{block["merkle_root"]}|{block["nonce"]}|{block["miner_address"]}'
    )


def compute_hash(block: dict) -> str:
    return hashlib.sha256(hash_input(block).encode()).hexdigest()


def make_genesis() -> dict:
    g = dict(GENESIS)
    g["merkle_root"] = block_merkle_root(g)
    g["hash"] = compute_hash(g)
    return g


class Blockchain:
    def __init__(self, difficulty: int):
        self.chain = [make_genesis()]
        self.mempool = []  # unconfirmed transactions
        self.utxos = {}    # confirmed UTXO set: {"txid:index": {"amount", "address"}}
        self.difficulty = difficulty
        self.lock = threading.Lock()  # one thread at a time may change the chain

    def tip(self) -> dict:
        return self.chain[-1]

    # ---------- Transaction validation (the 4 checks) ----------
    def check_transaction(self, tx, already_spent):
        """already_spent = outpoints used by the mempool (or by earlier txs in the block).
        Returns (ok, reason, fee)."""
        ok, reason = validate_structure(tx)
        if not ok:
            return False, reason, 0
        if is_coinbase(tx):
            return False, "coinbase not allowed here", 0

        seen = set()
        total_in = 0
        for inp in tx["inputs"]:
            key = outpoint_key(inp["prev_txid"], inp["output_index"])
            # Check 1: existence in the confirmed UTXO set
            if key not in self.utxos:
                return False, f"Check 1 failed: {key[:12]}... not in UTXO set", 0
            # Check 2: not already spent (mempool / block) and not spent twice in this tx
            if key in already_spent:
                return False, f"Check 2 failed: {key[:12]}... already spent (double spend)", 0
            if key in seen:
                return False, f"Check 2 failed: {key[:12]}... spent twice in same tx", 0
            seen.add(key)
            # Check 3: signature + public key open the lock
            ok, reason = unlocks(inp, tx["txid"], self.utxos[key]["address"])
            if not ok:
                return False, f"Check 3 failed: {reason}", 0
            total_in += self.utxos[key]["amount"]

        # Check 4: conservation of value
        total_out = sum(o["amount"] for o in tx["outputs"])
        if total_in < total_out:
            return False, f"Check 4 failed: inputs {total_in} < outputs {total_out}", 0
        return True, "ok", total_in - total_out

    def mempool_spent(self):
        spent = set()
        for tx in self.mempool:
            for inp in tx["inputs"]:
                spent.add(outpoint_key(inp["prev_txid"], inp["output_index"]))
        return spent

    def add_transaction(self, tx):
        with self.lock:
            if any(t["txid"] == tx.get("txid") for t in self.mempool):
                return False, "already in mempool"
            ok, reason, fee = self.check_transaction(tx, self.mempool_spent())
            if ok:
                self.mempool.append(tx)
            return ok, reason

    # ---------- UTXO bookkeeping ----------
    def apply_block(self, block):
        """Update the UTXO set and clean the mempool after a block is accepted."""
        for tx in block["transactions"]:
            if not is_coinbase(tx):
                for inp in tx["inputs"]:
                    self.utxos.pop(outpoint_key(inp["prev_txid"], inp["output_index"]), None)
            for i, o in enumerate(tx["outputs"]):
                self.utxos[outpoint_key(tx["txid"], i)] = {
                    "amount": o["amount"],
                    "address": o["scriptPubKey"]["recipient_address"],
                }
        # drop mined txs, and any mempool tx whose inputs are now gone
        mined = {tx["txid"] for tx in block["transactions"]}
        self.mempool = [
            tx for tx in self.mempool
            if tx["txid"] not in mined and all(
                outpoint_key(i["prev_txid"], i["output_index"]) in self.utxos for i in tx["inputs"]
            )
        ]

    def balance(self, address):
        mine = {k: u["amount"] for k, u in self.utxos.items() if u["address"] == address}
        return sum(mine.values()), mine

    # ---------- Mining ----------
    def mine(self, miner_address: str) -> dict:
        with self.lock:  # held for the whole mining run -> incoming blocks wait
            tip = self.tip()

            # take valid mempool txs, no outpoint spent twice
            txs, spent, fees = [], set(), 0
            for tx in self.mempool:
                ok, reason, fee = self.check_transaction(tx, spent)
                if ok:
                    txs.append(tx)
                    fees += fee
                    for inp in tx["inputs"]:
                        spent.add(outpoint_key(inp["prev_txid"], inp["output_index"]))

            coinbase = make_coinbase(tip["index"] + 1, miner_address, BLOCK_SUBSIDY + fees)
            block = {
                "index": tip["index"] + 1,
                "prev_hash": tip["hash"],
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "transactions": [coinbase] + txs,
                "nonce": 0,
                "miner_address": miner_address,
            }
            block["merkle_root"] = block_merkle_root(block)
            target = "0" * self.difficulty
            print(
                f'[+] Block #{block["index"]} Target Difficulty: {self.difficulty} leading zeros ("{target}...")'
            )

            while True:
                h = compute_hash(block)
                if block["nonce"] == 0:
                    print(f"[+] Nonce: 0 -> Hash: {h}")
                if h.startswith(target):
                    break
                block["nonce"] += 1

            block["hash"] = h
            self.chain.append(block)  # local chain first, broadcast after
            self.apply_block(block)

        print(f'[+] Nonce: {block["nonce"]} -> Hash: {h}')
        print(f'[+] Block #{block["index"]} Mined! Nonce: {block["nonce"]}')
        print(f"[+] Block Hash: {h}")
        print(f"[+] Coinbase: {BLOCK_SUBSIDY + fees} (50 + {fees} fees) -> {miner_address}  |  {len(txs)} txs")
        return block

    # ---------- Most-work chain rule ----------
    def total_work(self, chain):
        # expected hashes per block = 16^difficulty (each leading hex zero is 1/16)
        return (len(chain) - 1) * 16 ** self.difficulty

    def try_replace_chain(self, new_chain):
        """Rebuild the peer's chain from genesis with full validation. If it is valid
        and has more total work than ours, switch to it. Returns (switched, reason)."""
        if not isinstance(new_chain, list) or not new_chain:
            return False, "empty chain"
        if new_chain[0].get("hash") != make_genesis()["hash"]:
            return False, "different genesis block"
        if self.total_work(new_chain) <= self.total_work(self.chain):
            return False, "not more work than our chain"

        temp = Blockchain(self.difficulty)
        for block in new_chain[1:]:
            ok, reason = temp._validate(block, verbose=False)
            if not ok:
                return False, f'block #{block.get("index")} invalid: {reason}'
            temp.chain.append(block)
            temp.apply_block(block)

        with self.lock:
            if self.total_work(new_chain) <= self.total_work(self.chain):
                return False, "not more work than our chain"
            # txs from our blocks that are not in the new chain go back to the mempool
            new_txids = {tx["txid"] for b in temp.chain for tx in b["transactions"]}
            old_txs = [tx for b in self.chain[1:] for tx in b["transactions"]
                       if not is_coinbase(tx) and tx["txid"] not in new_txids]
            pending = old_txs + self.mempool

            old_height = len(self.chain) - 1
            self.chain, self.utxos, self.mempool = temp.chain, temp.utxos, []
            for tx in pending:
                ok, reason, fee = self.check_transaction(tx, self.mempool_spent())
                if ok:
                    self.mempool.append(tx)
        print(f"[+] REORG: switched from height {old_height} to {len(self.chain) - 1} "
              f"(more work). UTXO set rebuilt, {len(self.mempool)} txs back in mempool.")
        return True, "switched"

    # ---------- Receiving ----------
    def receive_block(self, block: dict):
        with self.lock:
            ok, reason = self._validate(block)
            if ok:
                self.chain.append(block)
                self.apply_block(block)
                print(
                    f'[+] Block #{block["index"]} appended. Chain height: {len(self.chain) - 1}'
                )
            else:
                print(f"[x] Block rejected: {reason}")
            return ok, reason

    def _validate(self, block: dict, verbose=True):
        def log(msg):
            if verbose:
                print(msg)

        required = (
            "index",
            "prev_hash",
            "timestamp",
            "transactions",
            "merkle_root",
            "nonce",
            "miner_address",
            "hash",
        )
        if not all(k in block for k in required):
            return False, "missing fields"
        tip = self.tip()

        # 1. Hash verification
        recomputed = compute_hash(block)
        if recomputed != block["hash"]:
            return False, "hash verification failed"
        if block_merkle_root(block) != block["merkle_root"]:
            return False, "merkle root does not match transactions"
        log("    [✓] 1. Hash verification (incl. Merkle root)")

        # 2. Difficulty verification
        target = "0" * self.difficulty
        if not recomputed.startswith(target):
            return False, f"difficulty not met (needs {target}...)"
        log(f"    [✓] 2. Difficulty ({target}...)")

        # 3. Chain continuity
        if block["prev_hash"] != tip["hash"]:
            return False, f'prev_hash does not match our tip (block #{tip["index"]})'
        log("    [✓] 3. Chain continuity")

        # 4. Index sequence
        if block["index"] != tip["index"] + 1:
            return False, f'index {block["index"]} != tip+1 ({tip["index"] + 1})'
        log("    [✓] 4. Index sequence")

        txs = block["transactions"]

        # 5. First tx is the only coinbase
        if not txs or not is_coinbase(txs[0]) or not validate_structure(txs[0])[0]:
            return False, "first transaction is not a valid coinbase"
        if txs[0]["inputs"][0].get("height") != block["index"]:
            return False, "coinbase height does not match block index"
        if any(is_coinbase(tx) for tx in txs[1:]):
            return False, "more than one coinbase"
        log("    [✓] 5. Coinbase is first and unique")

        # 6. Every other tx passes Checks 1-4, no outpoint spent twice in the block
        spent, fees = set(), 0
        for tx in txs[1:]:
            ok, reason, fee = self.check_transaction(tx, spent)
            if not ok:
                return False, f'tx {tx.get("txid", "?")[:8]}: {reason}'
            fees += fee
            for inp in tx["inputs"]:
                spent.add(outpoint_key(inp["prev_txid"], inp["output_index"]))
        log(f"    [✓] 6. All {len(txs) - 1} transactions pass Checks 1-4")

        # 7. Coinbase reward <= 50 + fees
        reward = sum(o["amount"] for o in txs[0]["outputs"])
        if reward > BLOCK_SUBSIDY + fees:
            return False, f"coinbase {reward} > {BLOCK_SUBSIDY} + {fees} fees"
        log(f"    [✓] 7. Coinbase reward {reward} <= {BLOCK_SUBSIDY} + {fees}")

        return True, "ok"
