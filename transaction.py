"""
Assignment 2 - Part 1: UTXO transactions, txid, ECDSA signatures and output locking.

Transaction shape (JSON):
{
  "txid": "<64 hex>",
  "inputs": [
    {
      "prev_txid": "<64 hex>",          # tx that created the output being spent
      "output_index": 0,                # position of that output (0-based)
      "scriptSig": {                    # unlocking key
        "signature": "<DER hex>",
        "public_key": "<33-byte compressed pubkey hex>"
      }
    }
  ],
  "outputs": [
    {
      "amount": 20,                     # positive integer, atomic units
      "scriptPubKey": {"recipient_address": "1..."}   # locking script
    }
  ]
}

There is no balance anywhere: a wallet's "balance" is just the sum of the
unspent outputs locked to its address. There is no fee field either:
fee = sum(inputs) - sum(outputs).

UTXO set format used by the helpers below (Part 2 maintains it):
    { "<txid>:<output_index>": {"amount": int, "address": str}, ... }
"""

import hashlib
import json

import base58
import ecdsa
from ecdsa.util import sigdecode_der, sigencode_der

from vanity import pubkey_to_address, ripemd160, sha256

ZERO_TXID = "0" * 64
BLOCK_SUBSIDY = 50  # used by the coinbase in Part 2


# ---------------------------------------------------------------------------
# Addresses / locking
# ---------------------------------------------------------------------------
def hash160(data: bytes) -> bytes:
    """RIPEMD160(SHA256(data)), 20 bytes."""
    return ripemd160(sha256(data))


def address_to_hash160(address: str) -> bytes:
    """Decode a Base58Check P2PKH address back to its 20-byte public key hash.

    Verifies the length, the 4-byte checksum and the 0x00 version byte.
    """
    raw = base58.b58decode(address)
    if len(raw) != 25:
        raise ValueError("address must decode to 25 bytes")
    versioned, checksum = raw[:21], raw[21:]
    if sha256(sha256(versioned))[:4] != checksum:
        raise ValueError("bad address checksum")
    if versioned[0] != 0x00:
        raise ValueError("address version byte is not 0x00")
    return versioned[1:]


def is_valid_address(address) -> bool:
    if not isinstance(address, str):
        return False
    try:
        address_to_hash160(address)
        return True
    except Exception:
        return False


def outpoint_key(txid: str, output_index: int) -> str:
    return f"{txid}:{output_index}"


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------
def make_output(amount: int, recipient_address: str) -> dict:
    return {"amount": amount, "scriptPubKey": {"recipient_address": recipient_address}}


def make_input(prev_txid: str, output_index: int) -> dict:
    # scriptSig is filled in after the txid is known (the txid excludes it)
    return {"prev_txid": prev_txid, "output_index": output_index, "scriptSig": None}


# ---------------------------------------------------------------------------
# txid serialization
# ---------------------------------------------------------------------------
def txid_preimage(tx: dict) -> str:
    """Exact string hashed to get the txid. scriptSig is NOT included.

    Compact JSON (separators "," and ":"), keys in exactly this order:
      {"inputs":[{"prev_txid":..,"output_index":..}, ...],
       "outputs":[{"amount":..,"recipient_address":..}, ...]}
    A coinbase input additionally carries "height" (after output_index) so
    coinbases in different blocks never share a txid.
    """
    inputs = []
    for i in tx["inputs"]:
        item = {"prev_txid": i["prev_txid"], "output_index": i["output_index"]}
        if "height" in i:
            item["height"] = i["height"]
        inputs.append(item)
    outputs = [
        {
            "amount": o["amount"],
            "recipient_address": o["scriptPubKey"]["recipient_address"],
        }
        for o in tx["outputs"]
    ]
    return json.dumps({"inputs": inputs, "outputs": outputs}, separators=(",", ":"))


def compute_txid(tx: dict) -> str:
    return hashlib.sha256(txid_preimage(tx).encode()).hexdigest()


# ---------------------------------------------------------------------------
# Signatures
# ---------------------------------------------------------------------------
def sign_txid(txid: str, private_key_hex: str) -> str:
    """ECDSA secp256k1 signature over the 32-byte txid (DER, hex).

    The txid is already a SHA-256 digest, so it is signed directly as the
    digest. RFC 6979 deterministic nonces are used (no RNG needed).
    """
    sk = ecdsa.SigningKey.from_string(
        bytes.fromhex(private_key_hex), curve=ecdsa.SECP256k1
    )
    sig = sk.sign_digest_deterministic(
        bytes.fromhex(txid), hashfunc=hashlib.sha256, sigencode=sigencode_der
    )
    return sig.hex()


def verify_signature(txid: str, signature_hex: str, public_key_hex: str) -> bool:
    try:
        vk = ecdsa.VerifyingKey.from_string(
            bytes.fromhex(public_key_hex), curve=ecdsa.SECP256k1
        )
        return vk.verify_digest(
            bytes.fromhex(signature_hex), bytes.fromhex(txid), sigdecode=sigdecode_der
        )
    except Exception:  # bad hex, bad point, bad DER, BadSignatureError...
        return False


def unlocks(inp: dict, txid: str, locked_address: str):
    """Does this input open the lock on the output it spends?

    1. Hash160(public_key) must equal the hash decoded from the output's
       scriptPubKey address.
    2. The signature must be valid for that public key over the txid.
    Returns (ok, reason).
    """
    script = inp.get("scriptSig")
    if not isinstance(script, dict):
        return False, "missing scriptSig"
    sig, pub = script.get("signature"), script.get("public_key")
    if not isinstance(sig, str) or not isinstance(pub, str):
        return False, "scriptSig needs signature and public_key"
    try:
        pub_bytes = bytes.fromhex(pub)
        expected = address_to_hash160(locked_address)
    except ValueError as e:
        return False, f"bad key/address: {e}"
    if hash160(pub_bytes) != expected:
        return False, "public key does not hash to the output's address"
    if not verify_signature(txid, sig, pub):
        return False, "invalid ECDSA signature"
    return True, "ok"


# ---------------------------------------------------------------------------
# Value / fees
# ---------------------------------------------------------------------------
def output_total(tx: dict) -> int:
    return sum(o["amount"] for o in tx["outputs"])


def input_total(tx: dict, utxo_set: dict) -> int:
    total = 0
    for i in tx["inputs"]:
        key = outpoint_key(i["prev_txid"], i["output_index"])
        if key not in utxo_set:
            raise ValueError(f"unknown outpoint {key}")
        total += utxo_set[key]["amount"]
    return total


def compute_fee(tx: dict, utxo_set: dict) -> int:
    """Fee = sum(input amounts) - sum(output amounts). Must be >= 0."""
    return input_total(tx, utxo_set) - output_total(tx)


# ---------------------------------------------------------------------------
# Wallet side: create + sign a payment with a change output
# ---------------------------------------------------------------------------
def create_transaction(
    keypair: dict, utxo_set: dict, recipient: str, amount: int, fee: int, exclude=()
) -> dict:
    """Spend the sender's UTXOs to pay `amount` to `recipient` leaving `fee`.

    UTXOs are spent whole, so whatever is left after amount + fee goes back
    to the sender as a change output (output index 1). `exclude` is a set of
    "txid:index" keys not to use (e.g. already spent by a mempool tx).
    """
    if not (isinstance(amount, int) and not isinstance(amount, bool) and amount > 0):
        raise ValueError("amount must be a positive integer")
    if not (isinstance(fee, int) and not isinstance(fee, bool) and fee >= 0):
        raise ValueError("fee must be a non-negative integer")
    if not is_valid_address(recipient):
        raise ValueError(f"invalid recipient address {recipient}")
    sender = keypair["address"]
    if pubkey_to_address(bytes.fromhex(keypair["public_key"])) != sender:
        raise ValueError("keypair public_key does not match its address")

    mine = [
        (key, u)
        for key, u in utxo_set.items()
        if u["address"] == sender and key not in exclude
    ]
    mine.sort(key=lambda kv: kv[1]["amount"], reverse=True)  # fewest inputs

    need, selected, total = amount + fee, [], 0
    for key, u in mine:
        if total >= need:
            break
        selected.append(key)
        total += u["amount"]
    if total < need:
        raise ValueError(f"insufficient funds: have {total}, need {need}")

    inputs = []
    for key in selected:
        prev_txid, idx = key.rsplit(":", 1)
        inputs.append(make_input(prev_txid, int(idx)))
    outputs = [make_output(amount, recipient)]
    change = total - need
    if change > 0:
        outputs.append(make_output(change, sender))

    tx = {"txid": None, "inputs": inputs, "outputs": outputs}
    tx["txid"] = compute_txid(tx)
    sig = sign_txid(tx["txid"], keypair["private_key"])
    for inp in tx["inputs"]:  # every input is ours, so the same sig over txid works
        inp["scriptSig"] = {"signature": sig, "public_key": keypair["public_key"]}
    return tx


# ---------------------------------------------------------------------------
# Coinbase (structure only; rules are enforced in Part 2)
# ---------------------------------------------------------------------------
def make_coinbase(height: int, miner_address: str, amount: int) -> dict:
    tx = {
        "txid": None,
        "inputs": [
            {
                "prev_txid": ZERO_TXID,
                "output_index": 0,
                "height": height,
                "scriptSig": None,
            }
        ],
        "outputs": [make_output(amount, miner_address)],
    }
    tx["txid"] = compute_txid(tx)
    return tx


def is_coinbase(tx: dict) -> bool:
    ins = tx.get("inputs") or []
    return (
        len(ins) == 1
        and ins[0].get("prev_txid") == ZERO_TXID
        and ins[0].get("output_index") == 0
    )


# ---------------------------------------------------------------------------
# Structural check (shape, types, amounts, txid) - no UTXO lookups here
# ---------------------------------------------------------------------------
def _is_int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _is_hex64(x) -> bool:
    if not isinstance(x, str) or len(x) != 64:
        return False
    try:
        int(x, 16)
        return True
    except ValueError:
        return False


def validate_structure(tx):
    """Returns (ok, reason). Catches malformed data so a bad peer can't crash us."""
    try:
        if not isinstance(tx, dict):
            return False, "transaction is not an object"
        for k in ("txid", "inputs", "outputs"):
            if k not in tx:
                return False, f"missing field '{k}'"
        if not isinstance(tx["inputs"], list) or not tx["inputs"]:
            return False, "inputs must be a non-empty list"
        if not isinstance(tx["outputs"], list) or not tx["outputs"]:
            return False, "outputs must be a non-empty list"
        for n, i in enumerate(tx["inputs"]):
            if not isinstance(i, dict) or not _is_hex64(i.get("prev_txid")):
                return False, f"input {n}: bad prev_txid"
            if not _is_int(i.get("output_index")) or i["output_index"] < 0:
                return False, f"input {n}: bad output_index"
            if "height" in i and (not _is_int(i["height"]) or i["height"] < 0):
                return False, f"input {n}: bad height"
        for n, o in enumerate(tx["outputs"]):
            if (
                not isinstance(o, dict)
                or not _is_int(o.get("amount"))
                or o["amount"] <= 0
            ):
                return False, f"output {n}: amount must be a positive integer"
            spk = o.get("scriptPubKey")
            if not isinstance(spk, dict) or not is_valid_address(
                spk.get("recipient_address")
            ):
                return False, f"output {n}: bad recipient_address"
        if compute_txid(tx) != tx["txid"]:
            return False, "txid does not match transaction contents"
        return True, "ok"
    except Exception as e:
        return False, f"malformed transaction: {e}"
