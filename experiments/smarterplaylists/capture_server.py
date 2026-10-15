"""Local transport for the isolated browser experiment, without credentials."""

import json
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PORT = 8766


def validate_report(report):
    targets = json.loads((ROOT / "targets.json").read_text(encoding="utf-8"))
    if set(report["entities"]) != {"track", "artist", "album"}:
        raise ValueError("Expected three entities")
    for entity, result in report["entities"].items():
        raw = result["raw"]
        expected = targets[entity]["uri"]
        if raw["status"] != "complete" or raw.get("playlist_uri"):
            raise ValueError("Expected completed runs without playlist output")
        if not raw["tracks"] or any(row["uri"] != expected for row in raw["tracks"]):
            raise ValueError("Unexpected or empty entity results")
        if result["values"]["uri"] != expected or result["conflicts"]:
            raise ValueError("Unexpected merged entity or conflicting values")


class CaptureHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def do_POST(self):
        if self.path != "/capture":
            self.send_error(404)
            return
        if self.headers.get("Origin") != f"http://127.0.0.1:{PORT}":
            self.send_error(403)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 2_000_000:
                raise ValueError("Invalid body size")
            report = json.loads(self.rfile.read(length))
            validate_report(report)
        except (ValueError, KeyError, TypeError):
            self.send_error(400, "Invalid experiment report")
            return
        summary = {
            "captured_at": report["captured_at"],
            "entities": {
                entity: {
                    "values": result["values"],
                    "missing_requested": result["missing_requested"],
                    "conflicts": result["conflicts"],
                }
                for entity, result in report["entities"].items()
            },
        }
        for filename, content in [("report.json", report), ("summary.json", summary)]:
            (ROOT / filename).write_text(
                json.dumps(content, ensure_ascii=True, indent=2) + "\n",
                encoding="utf-8",
            )
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"saved":["report.json","summary.json"]}')


if __name__ == "__main__":
    print(f"Experiment transport: http://127.0.0.1:{PORT}", flush=True)
    HTTPServer(("127.0.0.1", PORT), CaptureHandler).serve_forever()