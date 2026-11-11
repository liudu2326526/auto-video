"""Loopback-only supervised fixture probe; not a production recording backend.

No extension, remote upload, camera or microphone. Runtime files stay outside Git.
The browser enforces tab selection; the fixture opts in to a per-run Capture Handle.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import time
import shutil
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlsplit


MAX_VIDEO = 16 * 1024 * 1024
ASSETS = Path(__file__).resolve().parents[1] / "examples/browser-viewport-probe"
from .core import DesktopLock, root_path, private_file, atomic_json
from .media import assess_duration, MediaError


def private_root(path):
    return root_path(path)


def write_new(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


class ProbeServer(HTTPServer):
    def __init__(self, root, test_case="normal", kind="fixture"):
        self.root = root
        self.nonce = secrets.token_urlsafe(20)
        self.handle = "safe-fixture-" + secrets.token_hex(12)
        self.saved = False
        self.test_case = test_case
        self.kind = kind
        self.final_report = None
        super().__init__(("127.0.0.1", 0), Handler)
        self.origin = f"http://127.0.0.1:{self.server_port}"


class Handler(BaseHTTPRequestHandler):
    server: ProbeServer

    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, *args):
        pass

    def reply(self, code, body=b"", content_type="text/plain; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; media-src blob:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.end_headers()
        self.wfile.write(body)

    def route(self):
        if self.headers.get("Host") != urlsplit(self.server.origin).netloc:
            return None
        path = urlsplit(self.path).path
        prefix = "/" + self.server.nonce + "/"
        return path[len(prefix):] if path.startswith(prefix) else None

    def do_GET(self):
        route = self.route()
        if route == "config.json":
            data = {"origin": self.server.origin, "handle": self.server.handle,
                    "duration_ms": 6000, "limit_bytes": 64 if self.server.test_case == "overflow" else MAX_VIDEO,
                    "selection_timeout_ms": 1500 if self.server.test_case == "late_grant" else 40000,
                    "test_case": self.server.test_case, "kind": self.server.kind,
                    "expected_origin": "https://gamma.app" if self.server.kind == "gamma-identity" else self.server.origin}
            return self.reply(200, json.dumps(data).encode(), "application/json")
        allowed = {"identity.html", "identity.js", "probe.css"} if self.server.kind == "gamma-identity" else {"fixture.html", "recorder.html", "probe.js", "probe.css"}
        if route not in allowed:
            return self.reply(404)
        content_type = {"html": "text/html", "js": "text/javascript", "css": "text/css"}[route.rsplit(".", 1)[1]]
        return self.reply(200, (ASSETS / route).read_bytes(), content_type + "; charset=utf-8")

    def do_POST(self):
        route = self.route()
        if self.headers.get("Origin") != self.server.origin or route not in ({"report"} if self.server.kind == "gamma-identity" else {"video", "report"}):
            return self.reply(403)
        try:
            if self.headers.get("Transfer-Encoding"):
                return self.reply(400)
            n = int(self.headers.get("Content-Length", "-1"))
            cap = MAX_VIDEO if route == "video" else 32768
            if not 0 < n <= cap:
                return self.reply(413)
            data = self.rfile.read(n)
            if len(data) != n:
                return self.reply(400)
            if route == "report":
                report = json.loads(data)
                if not isinstance(report, dict) or report.get("state") not in {"blocked", "ready", "recording", "discarded", "saved", "identity_checked"}:
                    return self.reply(400)
                name = "report-" + secrets.token_hex(4) + ".json"
                write_new(self.server.root / name, json.dumps(report, ensure_ascii=False, indent=2).encode())
                if report.get("terminal") is True and report.get("stream_stopped") is True:
                    self.server.final_report = report
            else:
                if self.server.saved or self.headers.get("Content-Type") != "video/webm":
                    return self.reply(409)
                # The client validates the surface before constructing MediaRecorder.
                # This local sink is not independent attestation of browser identity.
                if not data.startswith(bytes.fromhex("1a45dfa3")):
                    return self.reply(400)
                write_new(self.server.root / "fixture.webm", data)
                self.server.saved = True
                write_new(self.server.root / "video-receipt.json", json.dumps({"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}).encode())
            return self.reply(201, b"saved locally")
        except (ValueError, OSError):
            return self.reply(400)


def inspect_fixture_video(path):
    """Independent local WebM checks; corner marks are fixture evidence, not a privacy oracle."""
    path = Path(path)
    private_file(path)
    if not 0 < path.stat().st_size <= MAX_VIDEO:
        raise MediaError("Fixture video size is invalid")
    probe, ffmpeg = shutil.which("ffprobe"), shutil.which("ffmpeg")
    if not probe or not ffmpeg:
        raise MediaError("Existing ffprobe and ffmpeg are required")

    def command(args, timeout=20):
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        if r.returncode or r.stderr.strip():
            raise MediaError("Fixture media command failed")
        return json.loads(r.stdout) if r.stdout else None

    info = command([probe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)])
    streams = info.get("streams", [])
    if len(streams) != 1 or streams[0].get("codec_type") != "video":
        raise MediaError("Exactly one video stream and no audio/other tracks required")
    width, height = int(streams[0]["width"]), int(streams[0]["height"])
    if width % 2 or height % 2 or min(width, height) < 64 or width * height > 4194304:
        raise MediaError("Fixture dimensions must be even and within the pixel budget")
    frames = command([probe, "-v", "error", "-select_streams", "v:0", "-show_frames", "-show_entries",
                      "frame=best_effort_timestamp_time,width,height", "-of", "json", str(path)])["frames"]
    pts = [float(f["best_effort_timestamp_time"]) for f in frames]
    import math
    if not 2 <= len(pts) <= 600 or not all(math.isfinite(t) for t in pts) or any(b <= a for a, b in zip(pts, pts[1:])):
        raise MediaError("Invalid fixture frame timestamps")
    if any((int(f["width"]), int(f["height"])) != (width, height) for f in frames):
        raise MediaError("Fixture frame dimensions changed")
    span = pts[-1] - pts[0]
    assess_duration(span, 6)
    # Decode once to a bounded per-frame buffer; no sample images are saved.
    process = subprocess.Popen([ffmpeg, "-nostdin", "-v", "error", "-xerror", "-i", str(path),
                                "-fps_mode", "passthrough", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    import threading
    deadline = threading.Timer(30, process.kill)
    deadline.start()
    count = 0
    corners = [(5, 5, (22, 163, 74)), (width-15, 5, (124, 58, 237)),
               (5, height-15, (217, 119, 6)), (width-15, height-15, (190, 24, 93))]
    try:
        while True:
            data = bytearray()
            while len(data) < width*height*3:
                chunk = process.stdout.read(width*height*3-len(data))
                if not chunk:
                    break
                data.extend(chunk)
            if not data:
                break
            if len(data) != width*height*3 or count >= 600:
                raise MediaError("Incomplete or excessive decoded frames")
            for x, y, color in corners:
                matches = 0
                for yy in range(y, y+10):
                    for xx in range(x, x+10):
                        offset = (yy*width+xx)*3
                        matches += max(abs(data[offset+k]-color[k]) for k in range(3)) <= 25
                if matches < 90:
                    raise MediaError("Generated fixture corner marker missing")
            count += 1
        error = process.stderr.read()
        if process.wait(timeout=2) or error.strip() or count != len(pts):
            raise MediaError("Fixture full decode failed")
    finally:
        deadline.cancel()
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close(); process.stderr.close()
    return {"sha256":hashlib.sha256(path.read_bytes()).hexdigest(),"bytes":path.stat().st_size,
            "width":width,"height":height,"codec":streams[0].get("codec_name"),"frames":count,
            "audio_streams":0,"first_pts_seconds":pts[0],"last_pts_seconds":pts[-1],
            "pts_span_seconds":span,"observed_average_fps":(count-1)/span,
            "full_decode_exit":0,"fixture_corner_checks":count*4,"cfr_30fps":"not_certified",
            "visual_privacy_review":"pending"}


def read_status(value):
    root = Path(value).absolute()
    if not root.exists():
        raise ValueError("Probe root does not exist")
    root = private_root(root)
    for name in ("probe-result.json", "probe-request.json"):
        p = root / name
        if p.exists() or p.is_symlink():
            private_file(p)
            if p.stat().st_size > 65536:
                raise ValueError("Oversized probe status")
            result = json.loads(p.read_text())
            if type(result.get("schema_version")) is not int or result["schema_version"] != 1:
                raise ValueError("Unsupported probe status version")
            if name == "probe-result.json":
                return result
            return {"schema_version":1, "state":"unknown", "service_result":"pending_or_interrupted", "request":result, "capture_success":False}
    raise ValueError("Probe status absent")


def serve(value, test_case="normal", kind="fixture", timeout_seconds=120):
    if test_case not in {"normal", "invalidate", "overflow", "late_grant"} or kind not in {"fixture", "gamma-identity"}:
        raise ValueError("Unsupported probe kind or test case")
    if kind != "fixture" and test_case != "normal":
        raise ValueError("Identity-only diagnostics do not accept recording test cases")
    if not 30 <= timeout_seconds <= 180:
        raise ValueError("Service lifetime must be 30..180 seconds")
    root = private_root(value)
    spec = {"schema_version":1,"kind":kind,"test_case":test_case,"timeout_seconds":timeout_seconds,"audio":False,"third_party_recording":False}
    request = root / "probe-request.json"
    if request.exists() or request.is_symlink():
        private_file(request)
        if json.loads(request.read_text()) != spec:
            raise ValueError("Probe root reused with different parameters")
        return dict(read_status(root), replayed=True)
    if any(root.iterdir()):
        raise ValueError("Use a fresh empty private runtime root")
    with DesktopLock(), ProbeServer(root, test_case, kind) as server:
        atomic_json(request, spec)
        info = {"pid":os.getpid(),"origin":server.origin,"kind":kind,"capture_started":False,
                "fixture":f"{server.origin}/{server.nonce}/fixture.html" if kind == "fixture" else None,
                "recorder":f"{server.origin}/{server.nonce}/recorder.html" if kind == "fixture" else f"{server.origin}/{server.nonce}/identity.html"}
        atomic_json(root / "server.json", info)
        print(json.dumps(info), flush=True)
        deadline = time.monotonic() + timeout_seconds
        server.timeout = 0.25
        reason = "service_deadline"
        try:
            while time.monotonic() < deadline and server.final_report is None:
                server.handle_request()
            if server.final_report is not None:
                reason = "client_terminal_report"
        except KeyboardInterrupt:
            reason = "service_interrupted"
        report = server.final_report
        result = {"schema_version":1,"state":report.get("state") if report else "unknown",
                  "kind":kind,"service_end":reason,"capture_success":False,
                  "tool_test_status":"not_performed","visual_privacy_review":"pending",
                  "third_party_recording":False,"browser_report":report}
        if kind == "fixture" and report and report.get("state") == "saved":
            try:
                media = inspect_fixture_video(root / "fixture.webm")
                atomic_json(root / "media-validation.json", media)
                result.update(state="media_validated", fixture_media_validated=True, media=media)
            except (ValueError, KeyError, OSError, TimeoutError, subprocess.TimeoutExpired) as exc:
                result.update(state="quarantined", fixture_media_validated=False, media_error=type(exc).__name__)
        atomic_json(root / "probe-result.json", result)
        return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", required=True)
    p.add_argument("--test-case", choices=("normal", "invalidate", "overflow", "late_grant"), default="normal")
    p.add_argument("--kind", choices=("fixture", "gamma-identity"), default="fixture")
    p.add_argument("--timeout", type=int, default=120)
    a = p.parse_args()
    print(json.dumps(serve(a.root, a.test_case, a.kind, a.timeout), ensure_ascii=False))


if __name__ == "__main__":
    main()
