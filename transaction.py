import hashlib
import json

import base58
import ecdsa

from vanity import pubkey_to_address

ZERO_TXID = "0" * 64
BLOCK_SUBSIDY = 50

# UTXO set format: {"txid:index": {"amount": int, "address": str}}


def outpoint_key(txid, index):
    return f"{txid}:{index}"


def make_output(amount, address):
    return {"amount": amount, "scriptPubKey": {"recipient_address": address}}


def is_valid_address(address):
    try:
        base58.b58decode_check(address)  # checks the 4-byte checksum
        return True
    except Exception:
        return False


def txid_preimage(tx):
    # only outpoints and outputs, no scriptSig
    inputs = []
    for i in tx["inputs"]:
        item = {"prev_txid": i["prev_txid"], "output_index": i["output_index"]}
        if "height" in i:  # coinbase only
            item["height"] = i["height"]
        inputs.append(item)
    outputs = []
    for o in tx["outputs"]:
        outputs.append({"amount": o["amount"], "recipient_address": o["scriptPubKey"]["recipient_address"]})
    return json.dumps({"inputs": inputs, "outputs": outputs}, separators=(",", ":"))


def compute_txid(tx):
    return hashlib.sha256(txid_preimage(tx).encode()).hexdigest()


def sign_txid(txid, private_key_hex):
    sk = ecdsa.SigningKey.from_string(bytes.fromhex(private_key_hex), curve=ecdsa.SECP256k1)
    return sk.sign_digest(bytes.fromhex(txid), sigencode=ecdsa.util.sigencode_der).hex()


def verify_signature(txid, signature_hex, public_key_hex):
    try:
        vk = ecdsa.VerifyingKey.from_string(bytes.fromhex(public_key_hex), curve=ecdsa.SECP256k1)
        return vk.verify_digest(bytes.fromhex(signature_hex), bytes.fromhex(txid),
                                sigdecode=ecdsa.util.sigdecode_der)
    except Exception:
        return False


def unlocks(inp, txid, locked_address):
    # public key must hash to the address, and the signature must be valid over the txid
    script = inp.get("scriptSig") or {}
    pub = script.get("public_key", "")
    sig = script.get("signature", "")
    try:
        if pubkey_to_address(bytes.fromhex(pub)) != locked_address:
            return False, "public key does not hash to the output's address"
    except ValueError:
        return False, "bad public key"
    if not verify_signature(txid, sig, pub):
        return False, "invalid ECDSA signature"
    return True, "ok"


def compute_fee(tx, utxo_set):
    total_in = sum(utxo_set[outpoint_key(i["prev_txid"], i["output_index"])]["amount"] for i in tx["inputs"])
    total_out = sum(o["amount"] for o in tx["outputs"])
    return total_in - total_out


def create_transaction(keypair, utxo_set, recipient, amount, fee, exclude=()):
    if type(amount) is not int or amount <= 0:
        raise ValueError("amount must be a positive integer")
    if type(fee) is not int or fee < 0:
        raise ValueError("fee must be a non-negative integer")
    if not is_valid_address(recipient):
        raise ValueError("invalid recipient address")

    # pick our own UTXOs until amount + fee is covered
    selected, total = [], 0
    for key, u in utxo_set.items():
        if u["address"] == keypair["address"] and key not in exclude:
            selected.append(key)
            total += u["amount"]
            if total >= amount + fee:
                break
    if total < amount + fee:
        raise ValueError(f"insufficient funds: have {total}, need {amount + fee}")

    inputs = []
    for key in selected:
        prev_txid, index = key.split(":")
        inputs.append({"prev_txid": prev_txid, "output_index": int(index), "scriptSig": None})

    outputs = [make_output(amount, recipient)]
    change = total - amount - fee
    if change > 0:
        outputs.append(make_output(change, keypair["address"]))

    tx = {"txid": None, "inputs": inputs, "outputs": outputs}
    tx["txid"] = compute_txid(tx)
    sig = sign_txid(tx["txid"], keypair["private_key"])
    for inp in tx["inputs"]:
        inp["scriptSig"] = {"signature": sig, "public_key": keypair["public_key"]}
    return tx


def make_coinbase(height, miner_address, amount):
    tx = {
        "txid": None,
        "inputs": [{"prev_txid": ZERO_TXID, "output_index": 0, "height": height, "scriptSig": None}],
        "outputs": [make_output(amount, miner_address)],
    }
    tx["txid"] = compute_txid(tx)
    return tx


def is_coinbase(tx):
    return len(tx["inputs"]) == 1 and tx["inputs"][0]["prev_txid"] == ZERO_TXID


def validate_structure(tx):
    # basic shape check so a bad tx from a peer doesn't crash the node
    try:
        if not tx["inputs"] or not tx["outputs"]:
            return False, "transaction needs inputs and outputs"
        for o in tx["outputs"]:
            if type(o["amount"]) is not int or o["amount"] <= 0:
                return False, "amount must be a positive integer"
            if not is_valid_address(o["scriptPubKey"]["recipient_address"]):
                return False, "bad recipient_address"
        if compute_txid(tx) != tx["txid"]:
            return False, "txid does not match transaction contents"
        return True, "ok"
    except (KeyError, TypeError, AttributeError):
        return False, "malformed transaction"
