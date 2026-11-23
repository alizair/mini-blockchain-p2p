import argparse
import json
import os

from vanity import load_or_create, load_or_create_plain, matches
from blockchain import Blockchain
from network import create_app, start_server, broadcast, broadcast_tx
from transaction import create_transaction, sign_txid


def short(h: str) -> str:
    return f"{h[:4]}...{h[-2:]}"


def print_tx(tx, indent="      "):
    ins = ", ".join(
        "coinbase(height=%s)" % i.get("height") if i["prev_txid"] == "0" * 64
        else f'{short(i["prev_txid"])}:{i["output_index"]}'
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
            print(f'[!] Prefix Status : "{kp["address"][1:4]}" does not match "{prefix.upper()}" '
                  f"(keeping existing {path})")
    return kp


def send(bc, kp, peers, line):
    # send <address> <amount> [--fee N]
    parts = line.split()
    try:
        recipient, amount = parts[1], int(parts[2])
        fee = int(parts[parts.index("--fee") + 1]) if "--fee" in parts else 0
        # don't reuse UTXOs that our own mempool txs already spend
        tx = create_transaction(kp, bc.utxos, recipient, amount, fee, exclude=bc.mempool_spent())
    except (IndexError, ValueError) as e:
        print(f"[x] {e}  (usage: send <address> <amount> [--fee N])")
        return
    ok, reason = bc.add_transaction(tx)
    if not ok:
        print(f"[x] TX rejected: {reason}")
        return
    print(f"[+] TX {tx['txid'][:4]}...{tx['txid'][-2:]} accepted to mempool, broadcast to {len(peers)} peers")
    print_tx(tx, indent="  ")
    broadcast_tx(tx, peers)


def bad_tx(bc, kp, peers, line, kind):
    """Demo only: build an invalid tx and show that we and our peers reject it."""
    parts = line.split()
    try:
        recipient, amount = parts[1], int(parts[2])
        if kind == "doublespend":
            # re-spend a UTXO that a mempool tx is already spending
            spent = bc.mempool_spent()
            if not spent:
                print("[x] Nothing in the mempool to double spend. Run send first.")
                return
            utxos = {k: v for k, v in bc.utxos.items() if k in spent}
            tx = create_transaction(kp, utxos, recipient, amount, 0)
        else:
            tx = create_transaction(kp, bc.utxos, recipient, amount, 0, exclude=bc.mempool_spent())
            # replace the signature with one over a different message
            for inp in tx["inputs"]:
                inp["scriptSig"]["signature"] = sign_txid("00" * 32, kp["private_key"])
    except (IndexError, ValueError) as e:
        print(f"[x] {e}")
        return
    ok, reason = bc.add_transaction(tx)
    print(f"[x] Local node rejected {kind} TX {tx['txid'][:8]}...: {reason}" if not ok
          else f"[!] {kind} TX was accepted?!")
    print(f"[+] Sending it to peers anyway to show they reject it too...")
    broadcast_tx(tx, peers)


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
    print("[+] Commands: send <address> <amount> [--fee N] | mine | balance | utxos | mempool | chain | address | quit")
    print("[+] Demo:     doublespend <address> <amount> | badsig <address> <amount>")

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
        elif line.startswith("send "):
            send(bc, kp, peers, line)
        elif line == "balance":
            total, utxos = bc.balance(address)
            parts = ", ".join(f"{k[:4]}...{k[62:64]}:{k.split(':')[1]} = {a}" for k, a in utxos.items())
            print(f"[+] {total} (UTXOs: {parts})")
        elif line == "utxos":
            if not bc.utxos:
                print("[+] UTXO set is empty.")
            for k, u in bc.utxos.items():
                print(f"  {k[:8]}...:{k.split(':')[1]}  {u['amount']:>4} -> {u['address']}")
        elif line == "mempool":
            if not bc.mempool:
                print("[+] Mempool is empty.")
            for tx in bc.mempool:
                print_tx(tx, indent="  ")
        elif line.startswith("doublespend "):
            bad_tx(bc, kp, peers, line, "doublespend")
        elif line.startswith("badsig "):
            bad_tx(bc, kp, peers, line, "badsig")
        elif line == "address":
            print(f"[+] {address}  (pubkey {kp['public_key']})")
        elif line == "quit":
            break
        elif line:
            print("Unknown command.")


if __name__ == "__main__":
    main()
