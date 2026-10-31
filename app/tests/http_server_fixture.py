"""Live HTTP server helpers for the finance_hub route tests."""
from __future__ import annotations

from contextlib import contextmanager
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer


@contextmanager
def serve(handler):
    """Run `handler` on an ephemeral localhost port and yield its base URL."""
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    # shutdown() waits for serve_forever to notice its flag, which the default
    # 0.5s poll interval turned into ~0.5s of teardown per test.
    thread = threading.Thread(
        target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join()


def request_json(base_url, method, path, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(base_url + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))
