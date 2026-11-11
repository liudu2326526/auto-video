"""Fixed-origin AIGV client with private sessions and no inference replay."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import platform
import re
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable

ORIGIN = "https://dev-aigv.yingsaidata.com"
PROTOCOL = "desktop-auth-entry.v1"
WHISPER_ROUTE = "/api/desktop-agent/v1/whisper/audio/transcriptions"
MODEL_ROUTE = "/api/desktop-agent/v1/model-services/gpt/responses"
MAX_AUDIO_BYTES = 25_000_000
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_EVENT_BYTES = 4 * 1024 * 1024
MAX_STREAM_BYTES = 64 * 1024 * 1024
KNOWN_REMOTE_CODES = frozenset({
    "credentials_invalid", "password_login_disabled", "environment_mismatch",
    "session_invalid", "binding_mismatch", "session_audience_mismatch",
    "usage_pending", "RATE_LIMIT", "rate_limit_exceeded", "insufficient_quota",
    "model_not_found", "PROVIDER_AUTH_FAILED", "PROVIDER_REJECTED",
    "PROVIDER_UNAVAILABLE", "SERVER", "UNAUTHORIZED", "FORBIDDEN",
})


class ServiceError(RuntimeError):
    """Only safe, closed diagnostics are exposed to the caller."""
    def __init__(self, code: str, *, status: int | None = None,
                 outcome_known: bool = False) -> None:
        self.code = code
        self.status = status
        self.outcome_known = outcome_known
        super().__init__(code + (f" (HTTP {status})" if status is not None else ""))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _verified_context() -> ssl.SSLContext:
    """Keep platform trust, reusing installed certifi only for an empty store."""
    try:
        context = ssl.create_default_context()
        if not context.cert_store_stats().get("x509_ca", 0):
            try:
                import certifi
            except ImportError:
                raise ServiceError("local_ca_bundle_required", outcome_known=True) from None
            context.load_verify_locations(cafile=certifi.where())
        if not context.cert_store_stats().get("x509_ca", 0):
            raise ServiceError("local_ca_bundle_required", outcome_known=True)
        return context
    except (OSError, ssl.SSLError, ValueError, AttributeError):
        raise ServiceError("local_ca_bundle_unavailable", outcome_known=True) from None


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _write_json(path: Path, value: Any, *, private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700 if private else 0o755)
    if path.is_symlink():
        raise ServiceError("unsafe_state_path")
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                         0o600 if private else 0o644)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(_json_bytes(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _epoch(value: Any) -> float:
    if not isinstance(value, str):
        raise ServiceError("invalid_session_response")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("Timezone required")
        return parsed.timestamp()
    except (ValueError, OverflowError):
        raise ServiceError("invalid_session_response") from None


def _identifier(value: Any) -> str:
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise ServiceError("invalid_session_response") from None


def _validate_agent(raw: Any) -> dict:
    if not isinstance(raw, dict) or raw.get("audience") != "desktop-agent":
        raise ServiceError("invalid_session_response")
    scopes = raw.get("scope")
    if (not isinstance(scopes, list) or not 1 <= len(scopes) <= 11 or
            any(not isinstance(scope, str) or not 1 <= len(scope) <= 100 for scope in scopes) or
            "desktop-agent:session" not in scopes):
        raise ServiceError("invalid_session_response")
    result = {"audience": "desktop-agent", "scope": scopes,
              "session_jti": _identifier(raw.get("session_jti"))}
    for key in ("access_token", "refresh_handle"):
        value = raw.get(key)
        if not isinstance(value, str) or not 48 <= len(value) <= 200:
            raise ServiceError("invalid_session_response")
        result[key] = value
    for key in ("expires_at", "refresh_expires_at"):
        _epoch(raw.get(key))
        result[key] = raw[key]
    if _epoch(result["refresh_expires_at"]) <= _epoch(result["expires_at"]):
        raise ServiceError("invalid_session_response")
    return result


class ServiceClient:
    """A task-owned public API client; no internal Runtime identity is forged.

    ``transport`` is an offline-test hook taking (urllib Request, timeout_seconds)
    and returning an HTTP-response-like object with status/read/readline/close.
    Production callers should omit it. The default opener rejects redirects.
    """
    def __init__(self, account_file: Path, private_dir: Path,
                 username_labels: list[str], password_labels: list[str],
                 transport: Callable | None = None) -> None:
        self.account_file = Path(account_file).expanduser()
        self.private_dir = Path(private_dir).expanduser()
        if self.private_dir.is_symlink():
            raise ServiceError("unsafe_private_directory")
        self.private_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt":
            if self.private_dir.stat().st_uid != os.getuid():
                raise ServiceError("unsafe_private_directory")
            self.private_dir.chmod(0o700)
        self.session_path = self.private_dir / "session.json"
        if self.session_path.is_symlink():
            raise ServiceError("unsafe_state_path")
        self.username_labels = [str(label).strip().casefold() for label in username_labels]
        self.password_labels = [str(label).strip().casefold() for label in password_labels]
        if not self.username_labels or not self.password_labels:
            raise ServiceError("credential_labels_required")
        self._lock = threading.RLock()
        if transport is None:
            opener = urllib.request.build_opener(
                _NoRedirect(), urllib.request.HTTPSHandler(context=_verified_context()))
            self._transport = lambda request, timeout: opener.open(request, timeout=timeout)
        else:
            self._transport = transport
        self._state: dict | None = None
        self.last_logout_result: dict | None = None
        self._platform, self._arch = self._host_identity()
        if self.session_path.exists():
            self._load()

    @staticmethod
    def _host_identity() -> tuple[str, str]:
        machine = platform.machine().casefold()
        if sys.platform == "darwin" and machine in {"arm64", "aarch64"}:
            return "darwin", "arm64"
        if sys.platform == "win32" and machine in {"amd64", "x86_64"}:
            return "win32", "x64"
        raise ServiceError("unsupported_platform")

    def _save(self) -> None:
        _write_json(self.session_path, self._state, private=True)

    def _load(self) -> None:
        try:
            if self.session_path.stat().st_size > 128 * 1024:
                raise ServiceError("invalid_saved_session")
            saved = json.loads(self.session_path.read_text(encoding="utf-8"))
            base = saved["base"]
            if (saved.get("origin") != ORIGIN or base.get("protocol_version") != PROTOCOL or
                    base.get("environment") != "development" or
                    base["client"]["client_id"] != "cn.insai.aigv.desktop" or
                    base["device"]["platform"] != self._platform or
                    base["device"]["arch"] != self._arch):
                raise ServiceError("invalid_saved_session")
            _identifier(base["client"]["instance_id"])
            if saved.get("session"):
                session = saved["session"]
                _identifier(session["desktop_session_id"])
                session["agent_session"] = _validate_agent(session["agent_session"])
            self._state = saved
            if os.name != "nt":
                self.session_path.chmod(0o600)
        except (OSError, ValueError, KeyError, TypeError):
            raise ServiceError("invalid_saved_session") from None

    def _credentials(self) -> tuple[str, str]:
        try:
            if self.account_file.stat().st_size > 64 * 1024:
                raise ServiceError("credential_file_too_large")
            source = self.account_file.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError):
            raise ServiceError("credential_file_unreadable") from None
        pairs: dict[str, list[str]] = {}
        if source.lstrip().startswith("{"):
            try:
                raw = json.loads(source)
            except ValueError:
                raise ServiceError("invalid_credential_file") from None
            if not isinstance(raw, dict):
                raise ServiceError("invalid_credential_file")
            for key, value in raw.items():
                if isinstance(key, str) and isinstance(value, str):
                    pairs.setdefault(key.strip().casefold(), []).append(value)
        else:
            # Labels are supplied by configuration; document prose is never executed.
            for line in source.splitlines():
                match = re.match(r"^\s*([^:=\uFF1A]+?)\s*[:=\uFF1A]\s*(.*?)\s*$", line)
                if match:
                    pairs.setdefault(match[1].strip().casefold(), []).append(match[2])
        def selected(labels: list[str], limit: int, trim: bool) -> str:
            values = [value.strip() if trim else value for label in labels
                      for value in pairs.get(label, [])]
            if len(values) != 1 or not 1 <= len(values[0]) <= limit:
                raise ServiceError("credential_fields_invalid")
            return values[0]
        return selected(self.username_labels, 128, True), selected(self.password_labels, 256, False)

    @staticmethod
    def _remote_error(response, status: int) -> ServiceError:
        code = "remote_request_failed"
        try:
            raw = json.loads(response.read(128 * 1024 + 1))
            supplied = raw.get("error", {}).get("code")
            if supplied in KNOWN_REMOTE_CODES:
                code = supplied
        except (ValueError, AttributeError, TypeError, OSError):
            pass
        if 300 <= status < 400:
            code = "redirect_rejected"
        return ServiceError(code, status=status, outcome_known=400 <= status < 500)

    def _open(self, method: str, route: str, *, body: bytes | None = None,
              headers: dict | None = None, timeout: int = 45):
        if route not in {"/api/desktop-auth/password/login", "/api/desktop-agent/session/refresh",
                         "/api/desktop-auth/session", WHISPER_ROUTE, MODEL_ROUTE}:
            raise ServiceError("unsupported_service_route")
        request = urllib.request.Request(ORIGIN + route, data=body, method=method,
                                         headers={"Accept": "application/json", **(headers or {})})
        try:
            response = self._transport(request, timeout)
        except urllib.error.HTTPError as error:
            try:
                safe = self._remote_error(error, error.code)
            finally:
                error.close()
            raise safe from None
        except ssl.SSLCertVerificationError:
            raise ServiceError("tls_certificate_verification_failed", outcome_known=True) from None
        except urllib.error.URLError as error:
            if isinstance(error.reason, ssl.SSLCertVerificationError):
                raise ServiceError("tls_certificate_verification_failed", outcome_known=True) from None
            raise ServiceError("network_outcome_unknown") from None
        except (OSError, TimeoutError):
            raise ServiceError("network_outcome_unknown") from None
        status_code = getattr(response, "status", None)
        if status_code is None:
            status_code = response.getcode()
        if not isinstance(status_code, int) or not 200 <= status_code < 300:
            try:
                safe = self._remote_error(response, status_code if isinstance(status_code, int) else 0)
            finally:
                response.close()
            raise safe
        return response

    def _json_request(self, method: str, route: str, *, value: Any = None,
                      headers: dict | None = None, timeout: int = 45,
                      max_bytes: int = 128 * 1024) -> dict:
        merged = dict(headers or {})
        body = None
        if value is not None:
            body = _json_bytes(value)
            merged["Content-Type"] = "application/json"
        response = self._open(method, route, body=body, headers=merged, timeout=timeout)
        try:
            content = response.read(max_bytes + 1)
            if len(content) > max_bytes:
                raise ServiceError("service_response_too_large")
            result = json.loads(content)
            if not isinstance(result, dict):
                raise ServiceError("invalid_service_response")
            return result
        except (ValueError, UnicodeError):
            raise ServiceError("invalid_service_response") from None
        except (OSError, TimeoutError):
            raise ServiceError("network_outcome_unknown") from None
        finally:
            response.close()

    def login(self) -> dict:
        with self._lock:
            if self._state:
                if self._state.get("session") and not self._state.get("logout_pending"):
                    self._token(required_scope=None)
                    return self._login_view(reused=True)
                raise ServiceError("existing_auth_outcome_requires_review")
            username, password = self._credentials()
            instance = str(uuid.uuid4())
            base = {"protocol_version": PROTOCOL, "environment": "development",
                    "client": {"client_id": "cn.insai.aigv.desktop", "instance_id": instance,
                               "version": "3.12.0"},
                    "device": {"device_id": "zhicut_" + instance.replace("-", ""),
                               "platform": self._platform, "arch": self._arch}}
            self._state = {"origin": ORIGIN, "base": base, "session": None,
                           "login_state": "in_flight", "refresh_uncertain": False,
                           "logout_pending": False}
            self._save()
            try:
                raw = self._json_request("POST", "/api/desktop-auth/password/login",
                                         value={**base, "username": username, "password": password}, timeout=30)
                exchange = raw.get("data")
                if not isinstance(exchange, dict) or exchange.get("protocol_version") != PROTOCOL:
                    raise ServiceError("invalid_session_response")
                session = {"desktop_session_id": _identifier(exchange.get("desktop_session_id")),
                           "agent_session": _validate_agent(exchange.get("agent_session"))}
                if _epoch(session["agent_session"]["expires_at"]) <= time.time():
                    raise ServiceError("invalid_session_response")
                self._state["session"] = session
                self._state["login_state"] = "completed"
                self._save()
                return self._login_view(reused=False)
            except BaseException as error:
                self._state["login_state"] = ("failed" if isinstance(error, ServiceError)
                                               and error.outcome_known else "outcome_unknown")
                self._save()
                raise
            finally:
                username = password = ""

    def _login_view(self, *, reused: bool) -> dict:
        agent = self._state["session"]["agent_session"]
        return {"authenticated": True, "audience": "desktop-agent",
                "expiresAt": agent["expires_at"], "reused": reused}

    def _token(self, required_scope: str | None = "desktop-agent:inference:invoke") -> str:
        with self._lock:
            if not self._state or not self._state.get("session") or self._state.get("logout_pending"):
                raise ServiceError("not_authenticated")
            if self._state.get("refresh_uncertain"):
                raise ServiceError("session_refresh_outcome_requires_review")
            agent = self._state["session"]["agent_session"]
            if _epoch(agent["expires_at"]) <= time.time() + 60:
                if _epoch(agent["refresh_expires_at"]) <= time.time():
                    raise ServiceError("session_expired")
                self._state["refresh_uncertain"] = True
                self._save()
                raw = self._json_request("POST", "/api/desktop-agent/session/refresh", value={
                    **self._state["base"],
                    "desktop_session_id": self._state["session"]["desktop_session_id"],
                    "refresh_handle": agent["refresh_handle"],
                }, timeout=30)
                replacement = _validate_agent(raw.get("data"))
                if _epoch(replacement["expires_at"]) <= time.time():
                    raise ServiceError("invalid_session_response")
                self._state["session"]["agent_session"] = replacement
                self._state["refresh_uncertain"] = False
                try:
                    self._save()
                except Exception:
                    self._state["refresh_uncertain"] = True
                    raise ServiceError("private_state_unavailable") from None
                agent = replacement
            if required_scope is not None and required_scope not in agent["scope"]:
                raise ServiceError("inference_scope_missing")
            return agent["access_token"]

    def transcribe(self, audio_file: Path, language: str | None = None) -> dict:
        audio_file = Path(audio_file)
        if not audio_file.is_file() or not 0 < audio_file.stat().st_size <= MAX_AUDIO_BYTES:
            raise ServiceError("audio_chunk_size_invalid")
        if language is not None and not re.fullmatch(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*", language):
            raise ServiceError("transcription_language_invalid")
        token = self._token()
        boundary = "zhicut-" + uuid.uuid4().hex
        fields = [("model", "large-v3"), ("response_format", "verbose_json"),
                  ("timestamp_granularities[]", "word"), ("timestamp_granularities[]", "segment")]
        if language:
            fields.append(("language", language))
        parts = []
        for key, value in fields:
            parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n'
                          f'{value}\r\n').encode("utf-8"))
        parts.extend([(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
                       'filename="audio.wav"\r\nContent-Type: audio/wav\r\n\r\n').encode("ascii"),
                      audio_file.read_bytes(), f"\r\n--{boundary}--\r\n".encode("ascii")])
        response = self._open("POST", WHISPER_ROUTE, body=b"".join(parts), timeout=720, headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "multipart/form-data; boundary=" + boundary,
        })
        try:
            raw = response.read(MAX_JSON_BYTES + 1)
            if len(raw) > MAX_JSON_BYTES:
                raise ServiceError("service_response_too_large")
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ServiceError("invalid_transcription_response")
            return result
        except (ValueError, UnicodeError):
            raise ServiceError("invalid_transcription_response") from None
        except (OSError, TimeoutError):
            raise ServiceError("network_outcome_unknown") from None
        finally:
            response.close()

    def analyze(self, body: dict, evidence_dir: Path) -> dict:
        evidence_dir = Path(evidence_dir)
        evidence_dir.mkdir(parents=True, exist_ok=True)
        state_file = evidence_dir / "model-request-state.json"
        response_file = evidence_dir / "model-response.json"
        encoded = _json_bytes(body)
        if len(encoded) > 32 * 1024 * 1024:
            raise ServiceError("model_request_too_large")
        digest = hashlib.sha256(encoded).hexdigest()
        if state_file.exists():
            try:
                previous = json.loads(state_file.read_text(encoding="utf-8"))
                if previous.get("requestSha256") != digest:
                    raise ServiceError("model_request_identity_mismatch")
                if previous.get("state") == "completed" and response_file.exists():
                    cached = json.loads(response_file.read_text(encoding="utf-8"))
                    if cached.get("status") == "completed":
                        return cached
            except (OSError, ValueError, TypeError):
                raise ServiceError("invalid_model_request_state") from None
            raise ServiceError("existing_model_request_requires_review")
        if body.get("stream") is not True:
            raise ServiceError("model_stream_required")
        token = self._token()
        journal = {"state": "in_flight", "requestSha256": digest,
                   "startedAt": dt.datetime.now(dt.timezone.utc).isoformat()}
        _write_json(state_file, journal)
        stream = None
        completed = None
        terminal = None
        stream_bytes = 0
        try:
            stream = self._open("POST", MODEL_ROUTE, body=encoded, timeout=720, headers={
                "Authorization": "Bearer " + token, "Content-Type": "application/json",
            })
            with (evidence_dir / "model-events.jsonl").open("w", encoding="utf-8") as log:
                event_lines: list[bytes] = []
                event_bytes = 0
                while True:
                    line = stream.readline(MAX_EVENT_BYTES + 1)
                    if not line:
                        if event_lines:
                            completed, terminal = self._consume_event(event_lines, log, completed, terminal)
                        break
                    stream_bytes += len(line)
                    event_bytes += len(line)
                    if event_bytes > MAX_EVENT_BYTES or stream_bytes > MAX_STREAM_BYTES:
                        raise ServiceError("model_stream_too_large")
                    if line.strip() == b"":
                        completed, terminal = self._consume_event(event_lines, log, completed, terminal)
                        event_lines = []
                        event_bytes = 0
                        if terminal:
                            break
                    elif line.startswith(b"data:"):
                        event_lines.append(line[5:].lstrip().rstrip(b"\r\n"))
                if terminal == "incomplete":
                    _write_json(evidence_dir / "model-incomplete.json", completed)
                    journal["state"] = "incomplete"
                    _write_json(state_file, journal)
                    raise ServiceError("model_response_incomplete", outcome_known=True)
                if terminal == "failed":
                    _write_json(evidence_dir / "model-failed.json", completed)
                    journal["state"] = "failed"
                    _write_json(state_file, journal)
                    raise ServiceError("model_response_failed", outcome_known=True)
                if terminal != "completed" or not isinstance(completed, dict) or completed.get("status") != "completed":
                    raise ServiceError("model_completion_not_observed")
            _write_json(response_file, completed)
            journal["state"] = "completed"
            journal["completedAt"] = dt.datetime.now(dt.timezone.utc).isoformat()
            _write_json(state_file, journal)
            return completed
        except BaseException as error:
            if journal["state"] == "in_flight":
                journal["state"] = ("failed" if isinstance(error, ServiceError)
                                    and error.outcome_known else "outcome_unknown")
                journal["errorCode"] = error.code if isinstance(error, ServiceError) else "interrupted"
                _write_json(state_file, journal)
            raise
        finally:
            if stream is not None:
                stream.close()

    @staticmethod
    def _consume_event(lines: list[bytes], log, completed, terminal):
        if not lines:
            return completed, terminal
        raw = b"\n".join(lines)
        if raw == b"[DONE]":
            return completed, terminal
        try:
            event = json.loads(raw)
        except (ValueError, UnicodeError):
            raise ServiceError("invalid_model_event") from None
        if not isinstance(event, dict):
            raise ServiceError("invalid_model_event")
        log.write(json.dumps(event, ensure_ascii=False) + "\n")
        log.flush()
        event_type = event.get("type")
        if event_type in {"response.completed", "response.incomplete", "response.failed"}:
            return event.get("response"), event_type.split(".", 1)[1]
        if event_type == "error":
            return {"status": "failed", "error": {"code": "model_stream_error"}}, "failed"
        return completed, terminal

    def logout(self) -> dict:
        with self._lock:
            if not self._state or not self._state.get("session"):
                if self._state and self._state.get("login_state") in {"in_flight", "outcome_unknown"}:
                    return {"status": "unknown", "agentRevoked": False, "webRevoked": False}
                self.session_path.unlink(missing_ok=True)
                self._state = None
                return {"status": "already_revoked", "agentRevoked": True, "webRevoked": True}
            self._state["logout_pending"] = True
            self._save()
            session = self._state["session"]
            try:
                response = self._json_request("DELETE", "/api/desktop-auth/session", value={
                    **self._state["base"], "desktop_session_id": session["desktop_session_id"],
                    "refresh_handle": session["agent_session"]["refresh_handle"], "reason": "user_logout",
                }, timeout=30)
                result = response.get("data")
                if not isinstance(result, dict) or result.get("desktop_session_id") != session["desktop_session_id"]:
                    raise ServiceError("invalid_logout_response")
                status_value = result.get("status")
                if status_value not in {"revoked", "already_revoked", "partial"}:
                    raise ServiceError("invalid_logout_response")
                safe = {"status": status_value, "agentRevoked": result.get("agent_revoked") is True,
                        "webRevoked": result.get("web_revoked") is True}
                if status_value != "partial" and safe["agentRevoked"] and safe["webRevoked"]:
                    self.session_path.unlink(missing_ok=True)
                    self._state = None
                else:
                    safe["status"] = "partial"
                self.last_logout_result = safe
                return safe
            except ServiceError as error:
                safe = {"status": "unknown", "agentRevoked": False,
                        "webRevoked": False, "errorCode": error.code}
                self.last_logout_result = safe
                return safe

    def __enter__(self):
        self.login()
        return self

    def __exit__(self, exception_type, exception, traceback):
        try:
            result = self.logout()
        except Exception:
            if exception is None:
                raise ServiceError("session_revocation_unconfirmed") from None
            return False
        if exception is None and result["status"] not in {"revoked", "already_revoked"}:
            raise ServiceError("session_revocation_unconfirmed")
        return False
