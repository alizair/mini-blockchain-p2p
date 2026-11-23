"""
Part 1 self-test: txid, signatures, output locking, fees, change, block hash.

    python test_part1.py

Uses the three real keypair files. The "funding" output is a coinbase-shaped
transaction built locally just for the test (real coinbases come in Part 2).
"""
import copy
import json

from blockchain import block_merkle_root, compute_hash, hash_input, make_genesis, merkle_root
from transaction import (
    compute_fee, compute_txid, create_transaction, make_coinbase, outpoint_key,
    txid_preimage, unlocks, validate_structure,
)

PASS = 0


def check(cond, msg):
    global PASS
    if not cond:
        raise SystemExit(f"[FAIL] {msg}")
    PASS += 1
    print(f"[ok] {msg}")


def load(p):
    with open(p) as f:
        return json.load(f)


n1, n2, n3 = load("keypair.json"), load("keypair2.json"), load("keypair3.json")

# --- a 50-unit output locked to Node1 -----------------------------------
fund = make_coinbase(1, n1["address"], 50)
utxo = {outpoint_key(fund["txid"], 0): {"amount": 50, "address": n1["address"]}}
check(validate_structure(fund)[0], "funding tx is well-formed")
check(make_coinbase(2, n1["address"], 50)["txid"] != fund["txid"],
      "coinbase txids differ by block height")

# --- Node1 pays Node2 20 with fee 1 -> change 29 -----------------------
tx = create_transaction(n1, utxo, n2["address"], 20, 1)
print("    txid preimage:", txid_preimage(tx))
print("    txid         :", tx["txid"])
check(validate_structure(tx) == (True, "ok"), "payment tx is well-formed")
check(tx["outputs"][0]["amount"] == 20 and
      tx["outputs"][0]["scriptPubKey"]["recipient_address"] == n2["address"],
      "output 0 pays 20 to Node2")
check(tx["outputs"][1]["amount"] == 29 and
      tx["outputs"][1]["scriptPubKey"]["recipient_address"] == n1["address"],
      "output 1 is 29 change back to Node1")
check(compute_fee(tx, utxo) == 1, "fee = inputs - outputs = 1")
check(unlocks(tx["inputs"][0], tx["txid"], n1["address"])[0],
      "Node1's signature + pubkey unlock the output")

# --- txid excludes scriptSig -------------------------------------------
t = copy.deepcopy(tx)
t["inputs"][0]["scriptSig"] = None
check(compute_txid(t) == tx["txid"], "txid does not depend on scriptSig")

# --- tampering ----------------------------------------------------------
t = copy.deepcopy(tx)
t["outputs"][0]["amount"] = 49
check(validate_structure(t) == (False, "txid does not match transaction contents"),
      "changing an amount breaks the txid")
t["txid"] = compute_txid(t)  # attacker recomputes txid -> old signature no longer valid
check(unlocks(t["inputs"][0], t["txid"], n1["address"]) == (False, "invalid ECDSA signature"),
      "re-hashed tampered tx fails signature check")

forged = create_transaction(n3, {outpoint_key(fund["txid"], 0):
                                 {"amount": 50, "address": n3["address"]}},
                            n3["address"], 49, 1)
check(unlocks(forged["inputs"][0], forged["txid"], n1["address"])[1]
      == "public key does not hash to the output's address",
      "Node3 cannot spend an output locked to Node1 (wrong key)")

t = copy.deepcopy(tx)
t["inputs"][0]["scriptSig"]["public_key"] = n1["public_key"]
t["inputs"][0]["scriptSig"]["signature"] = forged["inputs"][0]["scriptSig"]["signature"]
check(not unlocks(t["inputs"][0], t["txid"], n1["address"])[0],
      "right pubkey + someone else's signature is rejected")

# --- amount rules -------------------------------------------------------
for bad in (0, -5, 2.5, True):
    t = copy.deepcopy(tx)
    t["outputs"][0]["amount"] = bad
    check(not validate_structure(t)[0], f"output amount {bad!r} rejected")
for args, why in (((n2["address"], 50, 1), "insufficient funds"),
                  ((n2["address"], 0, 1), "zero amount"),
                  (("1notAnAddress", 5, 1), "bad recipient")):
    try:
        create_transaction(n1, utxo, *args)
        check(False, why)
    except ValueError:
        check(True, f"create_transaction refuses: {why}")

exact = create_transaction(n1, utxo, n2["address"], 49, 1)
check(len(exact["outputs"]) == 1, "no change output when nothing is left over")

# --- block hash commits to txids ---------------------------------------
g = make_genesis()
block = {"index": 1, "prev_hash": g["hash"], "timestamp": "2026-11-22T00:00:00Z",
         "transactions": [fund, tx], "nonce": 0, "miner_address": n1["address"]}
block["merkle_root"] = block_merkle_root(block)
print("    block hash input:", hash_input(block))
h = compute_hash(block)
block2 = dict(block, transactions=[tx, fund])
block2["merkle_root"] = block_merkle_root(block2)
check(compute_hash(block2) != h, "block hash depends on tx order (via Merkle root)")
check(make_genesis()["transactions"] == [] and hash_input(make_genesis()).split("|")[3] == "0" * 64,
      "genesis has no transactions (Merkle root = 64 zeros)")
check(merkle_root(["a" * 64]) == "a" * 64, "Merkle root of one tx is its txid")

print(f"\nAll {PASS} Part 1 checks passed.")
