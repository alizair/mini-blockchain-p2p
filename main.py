import argparse
import json

from vanity import load_or_create, matches
from blockchain import Blockchain


def print_chain(bc: Blockchain):
    for b in bc.chain:
        print(
            f'  #{b["index"]}  hash={b["hash"][:16]}...  prev={b["prev_hash"][:16]}...  '
            f'nonce={b["nonce"]}  miner={b["miner_address"]}  data={b["data"]}'
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--vanity-prefix", required=True)
    parser.add_argument("--difficulty", type=int, default=4)
    args = parser.parse_args()

    with open(args.config) as f:
        config = json.load(f)
    node_id = config["node_id"]

    # ----- Part 1: identity -----
    kp = load_or_create(args.vanity_prefix)
    address = kp["address"]
    print(f"[+] Public Address : {address}")
    if matches(address, args.vanity_prefix):
        print(
            f'[+] Prefix Status : MATCH FOUND ("{address[1:4]}" matches "{args.vanity_prefix.upper()}" case-insensitive)'
        )
    print("[+] Node Identity initialized successfully.")

    # ----- Part 2: chain -----
    bc = Blockchain(args.difficulty)
    print(f"[+] {node_id} ready. Commands: msg <text> | mine | chain | pending | quit")

    while True:
        try:
            line = input(f"{node_id}> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if line.startswith("msg "):
            bc.add_message(line[4:])
            print(f"[+] Added pending message ({len(bc.pending)} pending)")
        elif line == "mine":
            print(f"[+] Triggering Mining Cycle on {node_id}...")
            bc.mine(address)
        elif line == "chain":
            print_chain(bc)
        elif line == "pending":
            print(bc.pending)
        elif line == "quit":
            break
        elif line:
            print("Unknown command.")


if __name__ == "__main__":
    main()
