import logging
import threading

import requests
from flask import Flask, jsonify, request


def broadcast(block: dict, peers: list):
    """Send the block to every peer, each in its own thread so we don't freeze."""
    for peer in peers:
        threading.Thread(target=_send, args=(peer, "/block", block, f'Block #{block["index"]}'),
                         daemon=True).start()


def broadcast_tx(tx: dict, peers: list):
    """Send a new transaction to every peer."""
    for peer in peers:
        threading.Thread(target=_send, args=(peer, "/tx", tx, f'TX {tx["txid"][:8]}'),
                         daemon=True).start()


def _send(peer: str, path: str, payload: dict, label: str):
    try:
        r = requests.post(f"{peer}{path}", json=payload, timeout=300)
        res = r.json()
        status = (
            "accepted" if res.get("accepted") else f'rejected ({res.get("reason")})'
        )
        print(f"[>] {peer}: {label} {status}")
    except (requests.exceptions.RequestException, ValueError):
        print(f"[!] Warning: peer {peer} is unreachable. Continuing without it.")


def sync_with_peers(bc, peers: list):
    """Most-work rule: ask every peer for its full chain and switch if one has more work."""
    for peer in peers:
        try:
            chain = requests.get(f"{peer}/chain", timeout=30).json()
        except (requests.exceptions.RequestException, ValueError):
            continue
        switched, reason = bc.try_replace_chain(chain)
        if not switched and reason != "not more work than our chain":
            print(f"[x] Chain from {peer} rejected: {reason}")


def create_app(bc, node_id: str, address: str, peers: list) -> Flask:
    app = Flask(node_id)

    @app.post("/block")
    def receive_block():
        block = request.get_json(silent=True)
        if not isinstance(block, dict):
            return jsonify(accepted=False, reason="invalid JSON"), 400
        print(
            f'\n[<] Received Block #{block.get("index")} from {block.get("miner_address")}'
        )
        try:
            ok, reason = bc.receive_block(block)
        except Exception as e:  # malformed field types etc.
            print(f"[x] Block rejected: bad block: {e}")
            return jsonify(accepted=False, reason=f"bad block: {e}"), 400
        if not ok and ("prev_hash" in reason or "index" in reason):
            # block doesn't fit our tip -> maybe a peer has a longer chain
            print("[?] Block does not extend our tip, requesting full chains from peers...")
            threading.Thread(target=sync_with_peers, args=(bc, peers), daemon=True).start()
        return jsonify(accepted=ok, reason=reason), (200 if ok else 409)

    @app.post("/tx")
    def receive_tx():
        tx = request.get_json(silent=True)
        if not isinstance(tx, dict):
            return jsonify(accepted=False, reason="invalid JSON"), 400
        try:
            ok, reason = bc.add_transaction(tx)
        except Exception as e:
            ok, reason = False, f"bad transaction: {e}"
        if ok:
            print(f'\n[<] TX {tx["txid"][:8]}... accepted to mempool ({len(bc.mempool)} pending)')
        else:
            print(f'\n[x] TX {str(tx.get("txid"))[:8]}... rejected: {reason}')
        return jsonify(accepted=ok, reason=reason), (200 if ok else 409)

    @app.post("/mine")
    def mine():
        print(f"\n[+] Triggering Mining Cycle on {node_id} (via HTTP)...")
        block = bc.mine(address)
        broadcast(block, peers)
        return jsonify(block)

    @app.get("/chain")
    def chain():
        return jsonify(bc.chain)

    @app.get("/mempool")
    def mempool():
        return jsonify(bc.mempool)

    return app


def start_server(app: Flask, port: int):
    logging.getLogger("werkzeug").setLevel(logging.ERROR)  # hide per-request log spam
    threading.Thread(
        target=lambda: app.run(
            host="127.0.0.1", port=port, threaded=True, use_reloader=False
        ),
        daemon=True,
    ).start()
