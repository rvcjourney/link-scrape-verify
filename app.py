import os
import json
import time
import queue
import threading
import requests as _req
from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
from scraper import run_search, run_icp_search

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
    data         = request.get_json(force=True)
    sid          = (data.get("sid") or "").strip()
    combinations = data.get("combinations") or []
    # backwards-compat: flat queries list or single query string
    if not combinations:
        for q_str in (data.get("queries") or []):
            combinations.append({"query": q_str, "location": ""})
    if not combinations:
        single = (data.get("query") or "").strip()
        if single:
            combinations = [{"query": single, "location": ""}]
    if not combinations:
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
        all_urls, seen = [], set()
        for i, combo in enumerate(combinations):
            query    = (combo.get("query")    or "").strip()
            location = (combo.get("location") or "").strip()
            company  = (combo.get("company")  or "").strip()
            if not query:
                continue
            if len(combinations) > 1:
                log_fn(f"Search {i + 1}/{len(combinations)}: {query}")
            urls = run_search(query, log_fn=log_fn, location=location, company=company)
            for u in urls:
                if u not in seen:
                    seen.add(u)
                    all_urls.append(u)
        return jsonify({"urls": all_urls, "count": len(all_urls)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        log_fn(None)
        threading.Timer(15, _drop_queue, args=[sid]).start()


@app.route("/icp-search", methods=["POST"])
def icp_search():
    data         = request.get_json(force=True)
    sid          = (data.get("sid") or "").strip()
    combinations = data.get("combinations") or []
    if not combinations:
        single = (data.get("query") or "").strip()
        if single:
            combinations = [{"query": single, "location": ""}]
    if not combinations:
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
        all_results, seen_urls = [], set()
        for i, combo in enumerate(combinations):
            query    = (combo.get("query")    or "").strip()
            location = (combo.get("location") or "").strip()
            if not query:
                continue
            if len(combinations) > 1:
                log_fn(f"Search {i + 1}/{len(combinations)}: {query}")
            for r in run_icp_search(query, log_fn=log_fn, location=location):
                if r['url'] not in seen_urls:
                    seen_urls.add(r['url'])
                    all_results.append(r)
        return jsonify({"results": all_results, "count": len(all_results)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        log_fn(None)
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
            timeout=None,
        )
        wh.raise_for_status()
        return jsonify({"ok": True, "status": wh.status_code, "response": wh.json()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 4008))
    print(f"\n  LinkedIn Scraper UI → http://localhost:{port}\n")
    app.run(debug=False, host="0.0.0.0", port=port)
