"""Evidence-backed single-host executor. GUI actions belong to its supervised Skill."""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import threading
import time
import uuid

from .storage import Error, digest, encoded, private_dir, read_regular, store, write_new

ID = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
SHA = re.compile(r"[a-f0-9]{64}\Z")
TERMINAL = {"succeeded", "failed", "blocked"}
WORKER = Path(__file__).with_name("worker.py")
LOG_LIMIT = 16384


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def identifier(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise Error("identifier must contain 1–64 ASCII letters, digits, hyphen or underscore")
    return value


def fields(value, required, optional=()):
    if not isinstance(value, dict) or set(value) - set(required) - set(optional) or set(required) - set(value):
        raise Error("missing or unknown fields")


def number(value, minimum, maximum):
    if type(value) not in (int, float) or not minimum <= value <= maximum:
        raise Error("number outside permitted range")


def text(value, maximum=4096):
    if not isinstance(value, str) or not value or len(value.encode()) > maximum:
        raise Error("invalid or oversized text")


def json_file(path):
    try:
        return json.loads(read_regular(path))
    except (ValueError, UnicodeError) as exc:
        raise Error("invalid UTF-8 JSON") from exc


def validate_spec(spec):
    fields(spec, ("schema_version", "job_id", "tool_id", "tool_version", "script_id", "input_file", "input_rights", "steps"), ("test_kind",))
    if type(spec["schema_version"]) is not int or spec["schema_version"] != 1:
        raise Error("schema_version must be integer 1; no implicit legacy migration")
    for key in ("job_id", "tool_id", "script_id"):
        identifier(spec[key])
    text(spec["tool_version"], 128)
    if spec["input_rights"] not in ("generated_fixture", "user_supplied"):
        raise Error("declare input rights; this declaration grants no authority")
    if spec.get("test_kind", "controlled_fixture") not in ("controlled_fixture", "product_test"):
        raise Error("test_kind must be controlled_fixture or product_test")
    name = spec["input_file"]
    if not isinstance(name, str) or Path(name).name != name or name in (".", "..", "") or "\\" in name:
        raise Error("input_file must be a single adjacent filename")
    if not isinstance(spec["steps"], list) or not 1 <= len(spec["steps"]) <= 50:
        raise Error("a script needs 1–50 steps")
    ids, keys = set(), set()
    for step in spec["steps"]:
        fields(step, ("step_id", "operation_key", "adapter", "scope", "required", "params", "expect"), ("instruction",))
        for key in ("step_id", "operation_key", "adapter"):
            identifier(step[key])
        if step["step_id"] in ids or step["operation_key"] in keys:
            raise Error("step IDs and operation keys must be unique")
        ids.add(step["step_id"])
        keys.add(step["operation_key"])
        if type(step["required"]) is not bool:
            raise Error("required must be boolean")
        if step["scope"] not in ("local_file", "local_ui", "external_submit", "account", "payment"):
            raise Error("unknown action scope")
        fields(step["expect"], ("kind", "value"))
        kind, value = step["expect"]["kind"], step["expect"]["value"]
        if kind == "sha256":
            if not isinstance(value, str) or not SHA.fullmatch(value):
                raise Error("invalid expected SHA256")
        elif kind == "text_equals":
            text(value, 1048576)
        else:
            raise Error("only independent file hash or exact text validation is implemented")
        if step["adapter"] == "file_transform":
            fields(step["params"], ("transform",), ("max_input_bytes", "timeout_seconds", "delay_ms", "noise_bytes", "fixture_fault"))
            p = step["params"]
            if p["transform"] not in ("uppercase", "normalize_newlines"):
                raise Error("unknown fixed transformation")
            for key, lo, hi, default in (("max_input_bytes", 1, 1048576, 1048576), ("timeout_seconds", .05, 30, 5), ("delay_ms", 0, 35000, 0), ("noise_bytes", 0, 262144, 0)):
                number(p.get(key, default), lo, hi)
                if key != "timeout_seconds" and type(p.get(key, default)) is not int:
                    raise Error("byte counts and delay_ms must be integers")
            if p.get("fixture_fault") not in (None, "wrong_result", "exit_nonzero"):
                raise Error("unknown controlled fixture fault")
        elif step["params"] != {}:
            raise Error("unimplemented adapters cannot receive executable parameters")
        if "instruction" in step:
            text(step["instruction"])
        if step["adapter"] == "supervised_cua" and not step.get("instruction"):
            raise Error("a supervised UI step needs operator instructions")
    if not any(s["required"] for s in spec["steps"]):
        raise Error("at least one step must be required")
    return spec


def load(db, run_id):
    identifier(run_id)
    row = db.execute("SELECT payload FROM runs WHERE id=?", (run_id,)).fetchone()
    if not row:
        raise Error("test_run does not exist")
    return json.loads(row[0])


def persist(db, run):
    db.execute("INSERT INTO runs VALUES(?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload", (run["test_run_id"], encoded(run).decode()))
    for step in run["steps"]:
        if step.get("operation_id"):
            db.execute("UPDATE operations SET state=? WHERE id=?", (step["status"], step["operation_id"]))
    db.commit()


def summary(run):
    steps = run["steps"]
    statuses = {s["status"] for s in steps}
    required = [s for s in steps if s["spec"]["required"]]
    if "unknown" in statuses:
        state, result = "needs_reconciliation", None
    elif statuses & {"running", "waiting_observation", "pending"}:
        state, result = ("waiting_observation" if "waiting_observation" in statuses else "planned"), None
    else:
        state = "completed"
        # Optional failures still produce partial so the screenwriter sees the gap.
        if all(s["status"] == "succeeded" for s in steps) and required:
            result = "success"
        elif "succeeded" in statuses:
            result = "partial"
        elif "failed" in statuses:
            result = "failure"
        else:
            result = "blocked"
    return {"schema_version": 1, "artifact_kind": "tool_test_run", "test_run_id": run["test_run_id"],
            "job_id": run["spec"]["job_id"], "tool_id": run["spec"]["tool_id"],
            "tool_version": run["spec"]["tool_version"], "script_sha256": run["script_sha256"],
            "input_sha256": run["input_sha256"], "worker_sha256": run["worker_sha256"],
            "test_kind": run.get("test_kind", "legacy_unspecified"),
            "state": state, "result": result, "succeeded": result == "success",
            "sql_candidate": None if result is None else {"result": result, "succeeded": result == "success"},
            "sql_imported": False, "started_utc": run["created_utc"], "steps": steps}


def check_frozen(root, run):
    directory = root / run["test_run_id"]
    if digest(read_regular(directory / "input.bin", private=True)) != run["input_sha256"] or digest(read_regular(directory / "script.json", private=True)) != run["script_sha256"]:
        raise Error("frozen input or script changed")


def recover(db, run):
    changed = False
    for step in run["steps"]:
        if step["status"] == "running":
            step.update(status="unknown", reason="controller_interrupted_after_intent",
                        result_verified=False, process_ok=None)
            changed = True
    if changed:
        persist(db, run)


def request(db, request_id, payload):
    identifier(request_id)
    fingerprint = digest(encoded(payload))
    row = db.execute("SELECT fingerprint,response FROM requests WHERE id=?", (request_id,)).fetchone()
    if row:
        if row[0] != fingerprint:
            raise Error("request_id already used with different parameters")
        return True, json.loads(row[1]) if row[1] else None
    db.execute("INSERT INTO requests VALUES(?,?,NULL)", (request_id, fingerprint))
    db.commit()
    return False, None


def respond(db, request_id, value):
    db.execute("UPDATE requests SET response=? WHERE id=?", (encoded(value).decode(), request_id))
    db.commit()
    return value


def bind_evidence(root, operation_dir, path, kind):
    if not Path(path).is_relative_to(operation_dir):
        raise Error("evidence outside its operation")
    content = read_regular(path, private=True)
    relative = str(Path(path).relative_to(root))
    return {"evidence_id": digest((relative + digest(content)).encode())[:24],
            "kind": kind, "path": relative, "sha256": digest(content), "bytes": len(content)}


def verified(output, expect):
    if expect["kind"] == "sha256":
        return digest(output) == expect["value"]
    try:
        return output.decode("utf-8") == expect["value"]
    except UnicodeDecodeError:
        return False


class BoundedLog:
    def __init__(self, stream):
        self.stream = stream
        self.first = bytearray()
        self.last = bytearray()
        self.total = 0
        self.hash = hashlib.sha256()
        self.thread = threading.Thread(target=self.read, daemon=True)

    def read(self):
        while True:
            chunk = self.stream.read(4096)
            if not chunk:
                break
            self.total += len(chunk)
            self.hash.update(chunk)
            missing = max(0, LOG_LIMIT // 2 - len(self.first))
            self.first.extend(chunk[:missing])
            self.last.extend(chunk[missing:])
            if len(self.last) > LOG_LIMIT // 2:
                del self.last[:-LOG_LIMIT // 2]
        self.stream.close()

    def finish(self, path):
        self.thread.join()
        write_new(path, bytes(self.first + self.last))
        return {"stream_bytes": self.total, "stream_sha256": self.hash.hexdigest(), "truncated": self.total > LOG_LIMIT}


def settle_receipt(root, run, step, operation_dir, exit_code=None, reconciled=False):
    receipt_bytes = read_regular(operation_dir / "receipt.json", private=True)
    receipt = json.loads(receipt_bytes)
    for key, expected in (("schema_version", 1), ("operation_id", step["operation_id"]), ("script_sha256", run["script_sha256"]), ("input_sha256", run["input_sha256"]), ("worker_sha256", run["worker_sha256"])):
        if receipt.get(key) != expected or (key == "schema_version" and type(receipt[key]) is not int):
            raise Error("receipt identity mismatch")
    if exit_code is not None and exit_code != receipt["exit_code"]:
        raise Error("receipt and observed process exit disagree")
    step["evidence"].append(bind_evidence(root, operation_dir, operation_dir / "receipt.json", "worker_receipt"))
    step.update(exit_code=exit_code, process_ok=(exit_code == 0) if exit_code is not None else None,
                process_receipt_exit_code=receipt["exit_code"], lifecycle_reconciled=reconciled)
    step["observed_tool_descriptor"] = {"kind": "bundled_file_transform", "worker_sha256": receipt["worker_sha256"],
                                        "python_version": receipt.get("python_version"), "third_party_app_version": None}
    started, ended = receipt["process_started_mono_ns"], receipt["process_finished_mono_ns"]
    if type(started) is not int or type(ended) is not int or ended < started:
        raise Error("invalid receipt timing")
    step["worker_elapsed_ms"] = round((ended - started) / 1000000, 3)
    if receipt["status"] == "blocked" and receipt["exit_code"] == 12:
        step.update(status="blocked", reason=receipt["reason"], result_verified=False)
        step["limits"].append(receipt["limit"])
    elif receipt["status"] == "failed" and receipt["exit_code"] != 0:
        step.update(status="failed", reason=receipt["reason"], result_verified=False)
    elif receipt["status"] == "completed" and receipt["exit_code"] == 0:
        content = read_regular(operation_dir / "result.txt", private=True)
        if digest(content) != receipt["output_sha256"]:
            raise Error("result does not match worker receipt")
        evidence = bind_evidence(root, operation_dir, operation_dir / "result.txt", "result_file")
        step["evidence"].append(evidence)
        correct = verified(content, step["spec"]["expect"])
        step.update(status="succeeded" if correct else "failed", result_verified=correct,
                    reason=None if correct else "independent_result_mismatch")
    else:
        raise Error("invalid receipt outcome")


def execute_file(root, db, lock, run, step, operation_dir):
    config = {"schema_version": 1, "operation_id": step["operation_id"],
              "input_path": str(root / run["test_run_id"] / "input.bin"),
              "input_sha256": run["input_sha256"], "script_sha256": run["script_sha256"],
              "worker_sha256": run["worker_sha256"], "params": step["spec"]["params"]}
    config_path = operation_dir / "config.json"
    write_new(config_path, encoded(config))
    step["config_sha256"] = digest(encoded(config))
    command = [sys.executable, "-I", "-S", str(WORKER), "--config", str(config_path)]
    step["argv_sha256"] = digest(encoded(command))
    persist(db, run)  # intent and exact configuration durable before Popen.
    if digest(read_regular(WORKER)) != run["worker_sha256"]:
        step.update(status="blocked", reason="frozen_worker_changed", process_ok=None)
        return
    start = time.monotonic_ns()
    try:
        process = subprocess.Popen(command,
                                   cwd=operation_dir, env={"PATH": os.defpath, "LANG": "en_US.UTF-8"},
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   pass_fds=(lock,))
    except OSError:
        step.update(status="failed", reason="confirmed_process_not_started", process_ok=False, result_verified=False)
        return
    logs = [BoundedLog(process.stdout), BoundedLog(process.stderr)]
    for log in logs:
        log.thread.start()
    timed_out = False
    try:
        code = process.wait(timeout=config["params"].get("timeout_seconds", 5))
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()  # only the child object created in this invocation, never a stored PID.
        code = process.wait()
    step.update(executor_elapsed_ms=round((time.monotonic_ns() - start) / 1000000, 3), finished_utc=now())
    for name, log in zip(("stdout", "stderr"), logs):
        path = operation_dir / (name + ".log")
        metadata = log.finish(path)
        evidence = bind_evidence(root, operation_dir, path, "log")
        evidence.update(metadata, channel=name)
        step["evidence"].append(evidence)
    persist(db, run)  # Preserve logs even if receipt validation is interrupted.
    if timed_out:
        step.update(status="failed", reason="local_worker_timeout_confirmed_terminated", process_ok=False,
                    result_verified=False, exit_code=code, quarantine=True)
    else:
        try:
            settle_receipt(root, run, step, operation_dir, code)
        except (Error, OSError, ValueError, KeyError, TypeError):
            step.update(status="unknown", reason="missing_or_invalid_completion_receipt", process_ok=code == 0,
                        result_verified=False, quarantine=True)


def begin(root, db, run, step):
    spec = step["spec"]
    existing = db.execute("SELECT id,run FROM operations WHERE job=? AND opkey=? AND state IN ('running','waiting_observation','unknown')", (run["spec"]["job_id"], spec["operation_key"])).fetchone()
    if existing:
        step.update(status="blocked", reason="unresolved_operation_in_other_run", process_ok=None,
                    unresolved_operation_id=existing[0], limits=[{"name": "unresolved_operation", "source": "executor_scope"}])
        return None
    operation_id = uuid.uuid4().hex
    directory = private_dir(root / run["test_run_id"] / operation_id)
    step.update(operation_id=operation_id, attempt_no=1, status="running", started_utc=now(),
                started_mono_ns=time.monotonic_ns(), started_wall_ns=time.time_ns(), evidence=[], limits=[])
    db.execute("INSERT INTO operations VALUES(?,?,?,?,?,?)", (operation_id, run["spec"]["job_id"], spec["operation_key"], run["test_run_id"], spec["step_id"], "running"))
    persist(db, run)
    write_new(directory / "intent.json", encoded({"operation_id": operation_id, "script_sha256": run["script_sha256"], "input_sha256": run["input_sha256"], "worker_sha256": run["worker_sha256"], "step": spec}))
    step["evidence"].append(bind_evidence(root, directory, directory / "intent.json", "intent"))
    return directory


def run_steps(root, db, lock, run):
    for step in run["steps"]:
        if step["status"] in ("unknown", "waiting_observation"):
            break
        if step["status"] != "pending":
            continue
        directory = begin(root, db, run, step)
        if directory is None:
            persist(db, run)
            continue
        spec = step["spec"]
        dispatch_started = time.monotonic_ns()
        if spec["scope"] not in ("local_file", "local_ui"):
            step.update(status="blocked", reason="trusted_action_approval_and_budget_gateway_not_implemented")
            step["limits"].append({"name": "high_impact_action", "source": "executor_scope"})
        elif spec["adapter"] == "file_transform" and spec["scope"] == "local_file":
            step["synthetic"] = True
            execute_file(root, db, lock, run, step, directory)
        elif spec["adapter"] == "supervised_cua" and spec["scope"] == "local_ui":
            step.update(status="waiting_observation", reason="needs_coordinated_operator_and_real_action_authority",
                        process_ok=None, observation_token=uuid.uuid4().hex)
        else:
            step.update(status="blocked", reason="adapter_or_scope_not_implemented")
            step["limits"].append({"name": "adapter_scope", "source": "executor_scope"})
        if step["status"] != "waiting_observation":
            step.setdefault("executor_elapsed_ms", round((time.monotonic_ns() - dispatch_started) / 1000000, 3))
            step.setdefault("finished_utc", now())
        persist(db, run)
        if step["status"] in ("unknown", "waiting_observation"):
            break


def observe(root, db, run, step, observation, observation_base):
    fields(observation, ("schema_version", "operation_id", "observation_token", "outcome", "note", "evidence_files", "product_limits"))
    if type(observation["schema_version"]) is not int or observation["schema_version"] != 1:
        raise Error("observation schema_version must be integer 1")
    if step["status"] != "waiting_observation" or step["spec"]["adapter"] != "supervised_cua":
        raise Error("no matching pending UI observation; use reconcile for unknown")
    if observation["operation_id"] != step["operation_id"] or observation["observation_token"] != step["observation_token"]:
        raise Error("observation identity mismatch")
    if observation["outcome"] not in ("succeeded", "failed", "blocked", "unknown"):
        raise Error("unknown observation outcome")
    text(observation["note"])
    files = observation["evidence_files"]
    if not isinstance(files, list) or len(files) > 8:
        raise Error("at most eight explicit evidence files")
    limits = observation["product_limits"]
    if not isinstance(limits, list) or len(limits) > 16:
        raise Error("at most sixteen product observations")
    for limit in limits:
        fields(limit, ("statement", "evidence_index"))
        text(limit["statement"])
        if type(limit["evidence_index"]) is not int or not 0 <= limit["evidence_index"] < len(files):
            raise Error("product limits must cite supplied evidence")
    staged = []
    for file in files:
        fields(file, ("filename", "kind"))
        if file["kind"] not in ("result_file", "screen", "log"):
            raise Error("unsupported observation evidence kind")
        name = file["filename"]
        if not isinstance(name, str) or Path(name).name != name or name in (".", "..", "") or "\\" in name:
            raise Error("evidence must be a single adjacent filename")
        staged.append((file["kind"], read_regular(observation_base / name)))
    directory = root / run["test_run_id"] / step["operation_id"]
    imported = []
    # Unique callback directory: a partial import is never overwritten/reused.
    callback = private_dir(directory / ("observation-" + uuid.uuid4().hex))
    for index, (kind, content) in enumerate(staged):
        path = callback / (str(index) + ".bin")
        write_new(path, content)
        imported.append(bind_evidence(root, directory, path, kind))
    write_new(callback / "operator-attestation.json", encoded(observation))
    step["evidence"].extend(imported)
    step["evidence"].append(bind_evidence(root, directory, callback / "operator-attestation.json", "operator_attestation"))
    outcome = observation["outcome"]
    result_files = [content for kind, content in staged if kind == "result_file"]
    correct = len(result_files) == 1 and verified(result_files[0], step["spec"]["expect"])
    # Operator assertions and screenshots alone do not prove tool output correctness.
    if outcome == "succeeded":
        if len(result_files) == 1:
            status = "succeeded" if correct else "failed"
            reason = None if correct else "independent_result_mismatch"
        else:
            status, reason = "unknown", "operator_success_without_one_result_artifact"
    else:
        status, reason = outcome, "operator_attestation_" + outcome
    mono_wait = time.monotonic_ns() - step["started_mono_ns"]
    wall_wait = time.time_ns() - step["started_wall_ns"]
    clock_consistent = mono_wait >= 0 and abs(mono_wait - wall_wait) <= 5000000000
    step.update(status=status, reason=reason, process_ok=None, result_verified=bool(correct and status == "succeeded"),
                operator_note=observation["note"], provenance="operator_attested_independently_file_checked",
                observation_wait_ms=round(mono_wait / 1000000, 3) if clock_consistent else None,
                observation_timing="monotonic_control_wait_clock_consistent" if clock_consistent else "unknown_clock_discontinuity",
                active_ui_elapsed_ms=None, finished_utc=now())
    # Monotonic wait includes human delay; it is explicitly not active operation time.
    for limit in limits:
        step["limits"].append({"statement": limit["statement"], "source": "operator_attestation",
                               "evidence_id": imported[limit["evidence_index"]]["evidence_id"],
                               "independently_verified": False})
    persist(db, run)


def handoff(root, run):
    result = summary(run)
    facts, gaps, limits = [], [], []
    for step in run["steps"]:
        for evidence in step["evidence"]:
            relative = Path(evidence["path"])
            if relative.is_absolute() or ".." in relative.parts or len(relative.parts) < 3 or relative.parts[:2] != (run["test_run_id"], step["operation_id"]):
                raise Error("evidence path outside its test_run/operation")
            content = read_regular(root / relative, private=True)
            if digest(content) != evidence["sha256"] or len(content) != evidence["bytes"]:
                raise Error("evidence modified or missing; handoff refused")
        if step["status"] == "succeeded" and step["result_verified"]:
            refs = [e["evidence_id"] for e in step["evidence"] if e["kind"] == "result_file"]
            if not refs:
                raise Error("a supported fact needs result evidence")
            facts.append({"step_id": step["spec"]["step_id"], "statement": "This test's output matched its frozen file success criterion.",
                          "evidence_refs": refs, "synthetic": step["synthetic"],
                          "provenance": "controlled_fixture" if step["synthetic"] else "operator_attestation_plus_file_check",
                          "third_party_capability_verified": False})
        else:
            gaps.append({"step_id": step["spec"]["step_id"], "status": step["status"], "reason": step.get("reason")})
        limits.extend({"step_id": step["spec"]["step_id"], **item} for item in step["limits"])
    return {"schema_version": 1, "artifact_kind": "screenwriter_test_handoff", "test_run": result,
            "facts": facts, "gaps": gaps, "limits": limits, "pricing": {"value": None, "source": "not_observed"},
            "recording_link": {"verified": False, "reason": "separate_take_binding_not_imported"},
            "ready_for_script_freeze": False, "independent_semantic_review": "pending",
            "environment": run["environment"],
            "do_not_say": ["A successful process proves a correct result.", "Fixture results prove Gamma or other third-party capabilities.",
                           "Blocked by executor scope means the product cannot do it.", "Unknown or unexecuted actions succeeded or can safely be resubmitted.",
                           "An operator-supplied file proves its tool of origin, privacy or price."]}


def dispatch(action, root, run_id, request_id=None, manifest=None, step_id=None, observation_path=None):
    identifier(run_id)
    with store(root) as (root, db, lock):
        if action == "plan":
            manifest_path = Path(os.path.abspath(manifest))
            spec = validate_spec(json_file(manifest_path))
            content = read_regular(manifest_path.parent / spec["input_file"], 1048576)
            content.decode("utf-8")  # the implemented local adapter is text-only.
            payload = {"action": action, "run_id": run_id, "spec": spec, "input_sha256": digest(content)}
            replay, response = request(db, request_id, payload)
            if replay:
                return response if response else {"state": "unknown", "reason": "interrupted_plan_inspect_store"}
            if db.execute("SELECT 1 FROM runs WHERE id=?", (run_id,)).fetchone():
                raise Error("test_run already exists; no overwriting")
            directory = private_dir(root / run_id)
            write_new(directory / "input.bin", content, 0o400)
            write_new(directory / "script.json", encoded(spec), 0o400)
            run = {"schema_version": 1, "test_run_id": run_id, "spec": spec,
                   "test_kind": spec.get("test_kind", "controlled_fixture"),
                   "script_sha256": digest(encoded(spec)), "input_sha256": digest(content),
                   "worker_sha256": digest(read_regular(WORKER)), "created_utc": now(),
                   "environment": {"system": platform.system(), "release": platform.release(), "machine": platform.machine(),
                                   "python": platform.python_version(), "executor_version": "0.1.0",
                                   "tool_version_source": "scenario_declared_not_independently_queried"},
                   "steps": [{"spec": item, "status": "pending", "process_ok": None, "result_verified": False,
                              "synthetic": item["adapter"] == "file_transform" or spec.get("test_kind", "controlled_fixture") == "controlled_fixture",
                              "evidence": [], "limits": []} for item in spec["steps"]]}
            persist(db, run)
            return respond(db, request_id, summary(run))
        run = load(db, run_id)
        recover(db, run)  # the inherited lease must be free before an interrupted run is reconciled.
        if action == "status":
            return summary(run)
        check_frozen(root, run)
        observation = json_file(observation_path) if action == "observe" else None
        payload = {"action": action, "run_id": run_id, "step_id": step_id, "observation": observation}
        replay, response = request(db, request_id, payload)
        if replay:
            return response if response else {**summary(run), "request_outcome": "interrupted_no_resubmit"}
        if action == "run":
            run_steps(root, db, lock, run)
        elif action in ("observe", "reconcile"):
            identifier(step_id)
            step = next((s for s in run["steps"] if s["spec"]["step_id"] == step_id), None)
            if step is None:
                raise Error("step does not exist")
            if action == "observe":
                observe(root, db, run, step, observation, Path(os.path.abspath(observation_path)).parent)
            elif step["status"] == "unknown" and step["spec"]["adapter"] == "file_transform":
                try:
                    settle_receipt(root, run, step, root / run_id / step["operation_id"], reconciled=True)
                except (Error, OSError, ValueError, KeyError, TypeError):
                    step.update(reason="reconciliation_inconclusive_preserve_unknown")
                persist(db, run)
            elif step["status"] not in TERMINAL:
                raise Error("no supported reconciliation evidence; never retry the action")
        elif action == "handoff":
            value = handoff(root, run)
            path = root / run_id / ("handoff-" + identifier(request_id) + ".json")
            write_new(path, encoded(value))
            return respond(db, request_id, {"handoff_path": str(path), "sha256": digest(encoded(value)), "handoff": value})
        else:
            raise Error("unsupported command")
        return respond(db, request_id, summary(run))
