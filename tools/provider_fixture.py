"""Local HTTP fixture for integration checks; NOT a real language model.

python tools/provider_fixture.py --port 18081
Set LLM_BASE_URL=http://127.0.0.1:18081/v1 and a nonsecret fixture API key
only for a local test. Always binds loopback. No question or auth-header logging.
"""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        if length > 65536:
            self.send_error(413)
            return
        try:
            payload = json.loads(self.rfile.read(length))
            model = payload["model"]
            if not payload["messages"]:
                raise ValueError("empty messages")
        except (ValueError, KeyError, TypeError):
            self.send_error(400)
            return
        response = {
            "id": "fixture-response", "object": "chat.completion",
            "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "Fixture answer: this response proves HTTP integration, not live LLM inference."}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 17, "total_tokens": 29},
        }
        body = json.dumps(response).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18081)
    args = parser.parse_args()
    print(f"Local HTTP provider FIXTURE on 127.0.0.1:{args.port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
