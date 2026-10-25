import hashlib
import json
import os
import sys

import ecdsa
import base58
from Crypto.Hash import RIPEMD160


def sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def ripemd160(data: bytes) -> bytes:
    h = RIPEMD160.new()
    h.update(data)
    return h.digest()


def generate_keypair():
    sk = ecdsa.SigningKey.generate(curve=ecdsa.SECP256k1)  # private key
    vk = sk.get_verifying_key()  # public key (curve point)
    pubkey = vk.to_string("compressed")  # 33 bytes: 02/03 + x
    return sk.to_string().hex(), pubkey


def pubkey_to_address(pubkey: bytes) -> str:
    # Step 2: hash the public key -> 20 bytes (the "public key hash")
    pubkey_hash = ripemd160(sha256(pubkey))

    # Step 3: prepend version byte 0x00 (mainnet P2PKH) -> 21 bytes
    versioned = b"\x00" + pubkey_hash

    # Step 4: checksum = first 4 bytes of double SHA-256
    checksum = sha256(sha256(versioned))[:4]

    # Step 5: append checksum (25 bytes total) and Base58-encode
    return base58.b58encode(versioned + checksum).decode()


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "test":
    sk = ecdsa.SigningKey.from_secret_exponent(1, curve=ecdsa.SECP256k1)
    pub = sk.get_verifying_key().to_string("compressed")
    print(pubkey_to_address(pub))  # must print 1BgGZ9tcN4rm9KBzDn7KprQz87SZ26SAMH


def matches(address: str, prefix: str) -> bool:
    # characters 2,3,4 of the address = indexes 1,2,3 in Python
    return address[1:4].upper() == prefix.upper()


def search_vanity(prefix: str) -> dict:
    attempts = 0
    while True:
        priv_hex, pub = generate_keypair()
        address = pubkey_to_address(pub)
        attempts += 1
        if attempts % 5000 == 0:
            print(f"[.] {attempts} attempts...")
        if matches(address, prefix):
            print(f"[+] Found after {attempts} attempts: {address}")
            return {
                "private_key": priv_hex,
                "public_key": pub.hex(),
                "address": address,
            }


def save_keypair(kp: dict, path="keypair.json"):
    with open(path, "w") as f:
        json.dump(kp, f, indent=2)


def load_or_create(prefix: str, path="keypair.json") -> dict:
    if os.path.exists(path):
        with open(path) as f:
            kp = json.load(f)
        if matches(kp["address"], prefix):
            print(f"[+] Loading keypair from {path}... (Found existing match)")
            return kp
        print("[!] Saved keypair doesn't match prefix, searching again...")
    kp = search_vanity(prefix)
    save_keypair(kp, path)
    return kp


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] != "test":
    kp = load_or_create(sys.argv[1])
    print(f"[+] Public Address : {kp['address']}")
