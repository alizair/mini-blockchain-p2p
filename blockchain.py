import hashlib
import json
from datetime import datetime, timezone

# ---------- Genesis block: identical on every node ----------
GENESIS = {
    "index": 0,
    "prev_hash": "0" * 64,
    "timestamp": "2026-01-01T00:00:00Z",
    "data": ["Genesis Block"],
    "nonce": 0,
    "miner_address": "GENESIS",
}


# ---------- Hashing ----------
def hash_input(block: dict) -> str:
    "Build the exact string we hash. Order: index|timestamp|prev_hash|data|nonce|miner_address."
    data_str = json.dumps(block["data"], separators=(",", ":"))
    return (
        f'{block["index"]}|{block["timestamp"]}|{block["prev_hash"]}|'
        f'{data_str}|{block["nonce"]}|{block["miner_address"]}'
    )


def compute_hash(block: dict) -> str:
    return hashlib.sha256(hash_input(block).encode()).hexdigest()


def make_genesis() -> dict:
    g = dict(GENESIS)
    g["hash"] = compute_hash(g)  # no PoW check for genesis
    return g


# ---------- The chain ----------
class Blockchain:
    def __init__(self, difficulty: int):
        self.chain = [make_genesis()]
        self.pending = []  # messages waiting to go into the next block
        self.difficulty = difficulty

    def tip(self) -> dict:
        return self.chain[-1]  # the latest block

    def add_message(self, text: str):
        self.pending.append(text)

    def mine(self, miner_address: str) -> dict:
        tip = self.tip()
        block = {
            "index": tip["index"] + 1,
            "prev_hash": tip["hash"],
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "data": list(self.pending),  # empty list [] if no messages
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
        print(f'[+] Nonce: {block["nonce"]} -> Hash: {h}')
        print(f'[+] Block #{block["index"]} Mined! Nonce: {block["nonce"]}')
        print(f"[+] Block Hash: {h}")
        print(f"[+] Miner: {miner_address}")

        self.chain.append(block)  # add to OUR chain first (Part 3 will broadcast after)
        self.pending.clear()
        return block
