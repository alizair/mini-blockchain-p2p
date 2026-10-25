import logging
import threading

import requests
from flask import Flask, jsonify, request


def broadcast(block: dict, peers: list):
    """Send the block to every peer, each in its own thread so we don't freeze."""
    for peer in peers:
        threading.Thread(target=_send, args=(peer, block), daemon=True).start()


def _send(peer: str, block: dict):
    try:
        r = requests.post(f"{peer}/block", json=block, timeout=300)
        res = r.json()
        status = (
            "accepted" if res.get("accepted") else f'rejected ({res.get("reason")})'
        )
        print(f'[>] {peer}: Block #{block["index"]} {status}')
    except (requests.exceptions.RequestException, ValueError):
        print(f"[!] Warning: peer {peer} is unreachable. Continuing without it.")


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
            return jsonify(accepted=False, reason=f"bad block: {e}"), 400
        return jsonify(accepted=ok, reason=reason), (200 if ok else 409)

    @app.post("/message")
    def add_message():
        text = (request.get_json(silent=True) or {}).get("text", "")
        n = bc.add_message(text)
        print(f"\n[+] Added pending message via HTTP ({n} pending)")
        return jsonify(pending=bc.pending)

    @app.post("/mine")
    def mine():
        print(f"\n[+] Triggering Mining Cycle on {node_id} (via HTTP)...")
        block = bc.mine(address)
        broadcast(block, peers)
        return jsonify(block)

    @app.get("/chain")
    def chain():
        return jsonify(bc.chain)

    return app


def start_server(app: Flask, port: int):
    logging.getLogger("werkzeug").setLevel(logging.ERROR)  # hide per-request log spam
    threading.Thread(
        target=lambda: app.run(
            host="127.0.0.1", port=port, threaded=True, use_reloader=False
        ),
        daemon=True,
    ).start()
