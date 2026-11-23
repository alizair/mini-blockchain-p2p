import hashlib
import json
import threading
from datetime import datetime, timezone

from transaction import validate_structure

GENESIS = {
    "index": 0,
    "prev_hash": "0" * 64,
    "timestamp": "2026-01-01T00:00:00Z",
    "transactions": [],  # chain starts with no coins
    "nonce": 0,
    "miner_address": "GENESIS",
}


def hash_input(block: dict) -> str:
    """Order: index|timestamp|prev_hash|txids|nonce|miner_address  ('hash' NOT included).

    txids = compact JSON list of the block's txids in block order, e.g.
    ["9f2c...","4be1..."]; empty block -> []. Each txid commits to its
    transaction's inputs and outputs, so the block hash covers them too.
    """
    txids = json.dumps(
        [tx["txid"] for tx in block["transactions"]], separators=(",", ":")
    )
    return (
        f'{block["index"]}|{block["timestamp"]}|{block["prev_hash"]}|'
        f'{txids}|{block["nonce"]}|{block["miner_address"]}'
    )


def compute_hash(block: dict) -> str:
    return hashlib.sha256(hash_input(block).encode()).hexdigest()


def make_genesis() -> dict:
    g = dict(GENESIS)
    g["hash"] = compute_hash(g)
    return g


class Blockchain:
    def __init__(self, difficulty: int):
        self.chain = [make_genesis()]
        self.pending = (
            []
        )  # unconfirmed transactions (full mempool rules come in Part 2)
        self.difficulty = difficulty
        self.lock = threading.Lock()  # one thread at a time may change the chain

    def tip(self) -> dict:
        return self.chain[-1]

    def add_transaction(self, tx: dict):
        """Part 1: structural check only (shape, amounts, txid).
        Part 2 adds the 4 checks against the UTXO set."""
        ok, reason = validate_structure(tx)
        if ok:
            self.pending.append(tx)
        return ok, reason

    # ---------- Mining ----------
    def mine(self, miner_address: str) -> dict:
        with self.lock:  # held for the whole mining run -> incoming blocks wait
            tip = self.tip()
            txs, self.pending = self.pending, []  # take pending txs
            block = {
                "index": tip["index"] + 1,
                "prev_hash": tip["hash"],
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "transactions": txs,
                "nonce": 0,
                "miner_address": miner_address,
            }
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

        print(f'[+] Nonce: {block["nonce"]} -> Hash: {h}')
        print(f'[+] Block #{block["index"]} Mined! Nonce: {block["nonce"]}')
        print(f"[+] Block Hash: {h}")
        print(f"[+] Miner: {miner_address}")
        return block

    # ---------- Receiving ----------
    def receive_block(self, block: dict):
        with self.lock:
            ok, reason = self._validate(block)
            if ok:
                self.chain.append(block)
                print(
                    f'[+] Block #{block["index"]} appended. Chain height: {len(self.chain) - 1}'
                )
            else:
                print(f"[x] Block rejected: {reason}")
            return ok, reason

    def _validate(self, block: dict):
        required = (
            "index",
            "prev_hash",
            "timestamp",
            "transactions",
            "nonce",
            "miner_address",
            "hash",
        )
        if not all(k in block for k in required):
            return False, "missing fields"
        if not isinstance(block["transactions"], list):
            return False, "transactions must be a list"
        tip = self.tip()

        # 1. Hash verification
        recomputed = compute_hash(block)
        if recomputed != block["hash"]:
            return False, "hash verification failed"
        print("    [✓] 1. Hash verification")

        # 2. Difficulty verification
        target = "0" * self.difficulty
        if not recomputed.startswith(target):
            return False, f"difficulty not met (needs {target}...)"
        print(f"    [✓] 2. Difficulty ({target}...)")

        # 3. Chain continuity
        if block["prev_hash"] != tip["hash"]:
            return False, f'prev_hash does not match our tip (block #{tip["index"]})'
        print("    [✓] 3. Chain continuity")

        # 4. Index sequence
        if block["index"] != tip["index"] + 1:
            return False, f'index {block["index"]} != tip+1 ({tip["index"] + 1})'
        print("    [✓] 4. Index sequence")

        # 5. Every transaction is well-formed and its txid matches its contents
        for n, tx in enumerate(block["transactions"]):
            ok, reason = validate_structure(tx)
            if not ok:
                return False, f"transaction {n}: {reason}"
        print(f'    [✓] 5. Transaction structure ({len(block["transactions"])} txs)')

        return True, "ok"
