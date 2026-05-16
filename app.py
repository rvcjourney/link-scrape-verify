import os
import threading
import requests as _req
from flask import Flask, request, jsonify, send_from_directory
from scraper import run_search, is_chrome_debug_running

WEBHOOK_URL = "https://n8n.b2botix.ai/webhook-test/linkedin-filter"

app = Flask(__name__, static_folder=".")

_search_lock    = threading.Lock()
_running_search = False


@app.route("/")
def index():
    return send_from_directory(".", "index.html")


@app.route("/chrome-status")
def chrome_status():
    return jsonify({"connected": is_chrome_debug_running()})


@app.route("/search", methods=["POST"])
def search():
    global _running_search
    data  = request.get_json(force=True)
    query = (data.get("query") or "").strip()
    if not query:
        return jsonify({"error": "Query is empty"}), 400

    with _search_lock:
        if _running_search:
            return jsonify({"error": "A search is already running."}), 429
        _running_search = True

    try:
        urls = run_search(query)

        return jsonify({"urls": urls, "count": len(urls)})

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        with _search_lock:
            _running_search = False


@app.route("/send-webhook", methods=["POST"])
def send_webhook():
    data  = request.get_json(force=True) or {}
    query = (data.get("query") or "").strip()
    urls  = data.get("urls") or []
    if not urls:
        return jsonify({"error": "No URLs to send"}), 400
    try:
        wh = _req.post(
            WEBHOOK_URL,
            json={"query": query, "urls": urls},
            timeout=300,
        )
        wh.raise_for_status()
        return jsonify({"ok": True, "status": wh.status_code, "response": wh.json()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 4008))
    print(f"\n  LinkedIn Scraper UI → http://localhost:{port}\n")
    app.run(debug=False, host="0.0.0.0", port=port)
