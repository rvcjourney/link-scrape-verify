import os
import json
import time
import queue
import threading
import requests as _req
from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
from scraper import run_search, is_chrome_debug_running

WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "https://n8n.b2botix.ai/webhook/linkedin-filter")

app = Flask(__name__, static_folder=".")

# Per-session log queues — keyed by sid so concurrent users don't share state
_queues: dict[str, queue.Queue] = {}
_queues_lock = threading.Lock()


def _make_queue(sid: str) -> queue.Queue:
    q = queue.Queue(maxsize=300)
    with _queues_lock:
        _queues[sid] = q
    return q


def _get_queue(sid: str):
    with _queues_lock:
        return _queues.get(sid)


def _drop_queue(sid: str):
    with _queues_lock:
        _queues.pop(sid, None)


@app.route("/favicon.ico")
def favicon():
    return "", 204


@app.route("/")
def index():
    return send_from_directory(".", "index.html")


@app.route("/chrome-status")
def chrome_status():
    import config as _cfg
    if _cfg.BROWSER_HEADLESS:
        return jsonify({"connected": False, "headless": True})
    return jsonify({"connected": is_chrome_debug_running(), "headless": False})


@app.route("/search-log")
def search_log():
    sid = request.args.get("sid", "")

    def generate():
        # Wait up to 5 s for the queue to be registered
        # (SSE connects slightly before the POST arrives)
        q = None
        for _ in range(50):
            q = _get_queue(sid)
            if q is not None:
                break
            time.sleep(0.1)
        if q is None:
            yield f"data: {json.dumps({'done': True})}\n\n"
            return

        while True:
            try:
                msg = q.get(timeout=90)
                if msg is None:
                    yield f"data: {json.dumps({'done': True})}\n\n"
                    break
                yield f"data: {json.dumps({'msg': msg})}\n\n"
            except queue.Empty:
                yield f"data: {json.dumps({'ping': True})}\n\n"

    return Response(
        stream_with_context(generate()),
        content_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/search", methods=["POST"])
def search():
    data  = request.get_json(force=True)
    query = (data.get("query") or "").strip()
    sid   = (data.get("sid")   or "").strip()
    if not query:
        return jsonify({"error": "Query is empty"}), 400

    q = _make_queue(sid)

    def log_fn(msg):
        if msg is None:
            q.put(None)
            return
        try:
            q.put_nowait(str(msg))
        except queue.Full:
            pass

    try:
        urls = run_search(query, log_fn=log_fn)
        return jsonify({"urls": urls, "count": len(urls)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        log_fn(None)  # sentinel — tells the SSE stream this search is done
        threading.Timer(15, _drop_queue, args=[sid]).start()


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
            timeout=60,
        )
        wh.raise_for_status()
        return jsonify({"ok": True, "status": wh.status_code, "response": wh.json()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 4008))
    print(f"\n  LinkedIn Scraper UI → http://localhost:{port}\n")
    app.run(debug=False, host="0.0.0.0", port=port)
