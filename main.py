import argparse
import json
import os

from vanity import load_or_create, load_or_create_plain, matches
from blockchain import Blockchain
from network import create_app, start_server, broadcast


def short(h: str) -> str:
    return f"{h[:4]}...{h[-2:]}"


def print_tx(tx, indent="      "):
    ins = ", ".join(
        (
            "coinbase(height=%s)" % i.get("height")
            if i["prev_txid"] == "0" * 64
            else f'{short(i["prev_txid"])}:{i["output_index"]}'
        )
        for i in tx["inputs"]
    )
    outs = ", ".join(
        f'[{n}] {o["amount"]} -> {o["scriptPubKey"]["recipient_address"]}'
        for n, o in enumerate(tx["outputs"])
    )
    print(f"{indent}tx {short(tx['txid'])}  in: {ins}  out: {outs}")


def print_chain(bc):
    for b in bc.chain:
        print(
            f'  #{b["index"]}  hash={b["hash"][:16]}...  prev={b["prev_hash"][:16]}...  '
            f'nonce={b["nonce"]}  miner={b["miner_address"]}  txs={len(b["transactions"])}'
        )
        for tx in b["transactions"]:
            print_tx(tx)


def load_identity(config: dict, prefix):
    """Node1 uses the Assignment 1 vanity keypair (keypair.json);
    Node2/Node3 use keypair2.json / keypair3.json (no vanity prefix)."""
    path = config.get("keypair", "keypair.json")
    if prefix and not os.path.exists(path):
        kp = load_or_create(prefix, path)  # vanity search only if no keypair yet
    else:
        # Never overwrite an existing keypair: coins are locked to its address.
        kp = load_or_create_plain(path)
    if prefix:
        if matches(kp["address"], prefix):
            print(
                f'[+] Prefix Status : MATCH FOUND ("{kp["address"][1:4]}" matches '
                f'"{prefix.upper()}" case-insensitive)'
            )
        else:
            print(
                f'[!] Prefix Status : "{kp["address"][1:4]}" does not match "{prefix.upper()}" '
                f"(keeping existing {path})"
            )
    return kp


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--vanity-prefix", help="only for Node1 (keypair.json)")
    parser.add_argument("--difficulty", type=int, default=4)
    args = parser.parse_args()

    with open(args.config) as f:
        config = json.load(f)
    node_id, port, peers = config["node_id"], config["port"], config["peers"]

    # Identity: secp256k1 keypair -> P2PKH address (outputs get locked to it)
    kp = load_identity(config, args.vanity_prefix)
    address = kp["address"]
    print(f"[+] Public Address : {address}")
    print("[+] Node Identity initialized successfully.")

    bc = Blockchain(args.difficulty)

    start_server(create_app(bc, node_id, address, peers), port)
    print(
        f"[+] {node_id} listening on http://127.0.0.1:{port}  |  Peers: {', '.join(peers)}"
    )
    print("[+] Commands: mine | chain | pending | address | quit")

    while True:
        try:
            line = input(f"{node_id}> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if line == "mine":
            print(f"[+] Triggering Mining Cycle on {node_id}...")
            block = bc.mine(address)
            broadcast(block, peers)
        elif line == "chain":
            print_chain(bc)
        elif line == "pending":
            if not bc.pending:
                print("[+] No pending transactions.")
            for tx in bc.pending:
                print_tx(tx, indent="  ")
        elif line == "address":
            print(f"[+] {address}  (pubkey {kp['public_key']})")
        elif line == "quit":
            break
        elif line:
            print("Unknown command.")


if __name__ == "__main__":
    main()
