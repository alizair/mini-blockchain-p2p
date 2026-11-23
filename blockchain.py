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


def hash_input(block: dict) -> str:
    """Order: index|timestamp|prev_hash|txids|nonce|miner_address  ('hash' NOT included)."""
    txids = json.dumps([tx["txid"] for tx in block["transactions"]], separators=(",", ":"))
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

        txs = block["transactions"]

        # 5. First tx is the only coinbase
        if not txs or not is_coinbase(txs[0]) or not validate_structure(txs[0])[0]:
            return False, "first transaction is not a valid coinbase"
        if txs[0]["inputs"][0].get("height") != block["index"]:
            return False, "coinbase height does not match block index"
        if any(is_coinbase(tx) for tx in txs[1:]):
            return False, "more than one coinbase"
        print("    [✓] 5. Coinbase is first and unique")

        # 6. Every other tx passes Checks 1-4, no outpoint spent twice in the block
        spent, fees = set(), 0
        for tx in txs[1:]:
            ok, reason, fee = self.check_transaction(tx, spent)
            if not ok:
                return False, f'tx {tx.get("txid", "?")[:8]}: {reason}'
            fees += fee
            for inp in tx["inputs"]:
                spent.add(outpoint_key(inp["prev_txid"], inp["output_index"]))
        print(f"    [✓] 6. All {len(txs) - 1} transactions pass Checks 1-4")

        # 7. Coinbase reward <= 50 + fees
        reward = sum(o["amount"] for o in txs[0]["outputs"])
        if reward > BLOCK_SUBSIDY + fees:
            return False, f"coinbase {reward} > {BLOCK_SUBSIDY} + {fees} fees"
        print(f"    [✓] 7. Coinbase reward {reward} <= {BLOCK_SUBSIDY} + {fees}")

        return True, "ok"
