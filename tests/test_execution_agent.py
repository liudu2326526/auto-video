import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from execution_agent import core
from execution_agent.storage import Error, encoded, read_regular, root_path, store, write_new


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="execution-tests-")
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "runtime"
        self.source = self.base / "input.txt"
        self.source.write_text("hello 星屿\r\n", encoding="utf-8", newline="")
        self.manifest = self.base / "scenario.json"
        self.spec = {"schema_version": 1, "job_id": "job", "tool_id": "file-transform-fixture", "tool_version": "1",
                     "script_id": "script", "input_file": "input.txt", "input_rights": "generated_fixture",
                     "steps": [self.step()]}
        self.write_spec()

    def tearDown(self):
        self.temp.cleanup()

    def step(self, name="one", **params):
        return {"step_id": name, "operation_key": "op-" + name, "adapter": "file_transform", "scope": "local_file",
                "required": True, "params": {"transform": "uppercase", **params},
                "expect": {"kind": "text_equals", "value": "HELLO 星屿\r\n"}}

    def write_spec(self):
        self.manifest.write_bytes(encoded(self.spec))

    def call(self, action, request=None, run="run", **kwargs):
        if action == "plan":
            kwargs["manifest"] = str(self.manifest)
        return core.dispatch(action, str(self.root), run, request, **kwargs)

    def plan(self, run="run", request="plan"):
        self.write_spec()
        return self.call("plan", request, run)

    def execute(self):
        self.plan()
        return self.call("run", "execute")

    def ui(self):
        self.spec["steps"][0].update(adapter="supervised_cua", scope="local_ui", params={}, instruction="Use the approved safe tool and verify its output.")
        result = self.execute()
        return result["steps"][0]

    def observation(self, step, **kwargs):
        value = {"schema_version": 1, "operation_id": step["operation_id"], "observation_token": step["observation_token"],
                 "outcome": "succeeded", "note": "Controlled bridge test, no UI action performed", "evidence_files": [], "product_limits": []}
        value.update(kwargs)
        path = self.base / "observation.json"
        path.write_bytes(encoded(value))
        return str(path)

    def test_success_runs_real_worker_and_verifies_output(self):
        result = self.execute()
        step = result["steps"][0]
        self.assertEqual(result["result"], "success")
        self.assertTrue(step["process_ok"])
        self.assertTrue(step["result_verified"])
        self.assertTrue(step["synthetic"])
        self.assertGreater(step["executor_elapsed_ms"], 0)
        self.assertGreaterEqual(step["worker_elapsed_ms"], 0)
        output = next(e for e in step["evidence"] if e["kind"] == "result_file")
        self.assertEqual((self.root / output["path"]).read_bytes(), b"HELLO \xe6\x98\x9f\xe5\xb1\xbf\r\n")

    def test_zero_exit_with_wrong_result_is_failure(self):
        self.spec["steps"][0]["params"]["fixture_fault"] = "wrong_result"
        result = self.execute()
        self.assertEqual(result["result"], "failure")
        self.assertTrue(result["steps"][0]["process_ok"])
        self.assertFalse(result["steps"][0]["result_verified"])

    def test_nonzero_exit_recorded_separately(self):
        self.spec["steps"][0]["params"]["fixture_fault"] = "exit_nonzero"
        result = self.execute()
        self.assertFalse(result["steps"][0]["process_ok"])
        self.assertEqual(result["steps"][0]["exit_code"], 13)

    def test_observed_limit_is_adapter_not_product(self):
        self.spec["steps"][0]["params"]["max_input_bytes"] = 1
        result = self.execute()
        self.assertEqual(result["result"], "blocked")
        limit = result["steps"][0]["limits"][0]
        self.assertEqual(limit["source"], "adapter_observed")
        self.assertEqual(limit["value"], 1)

    def test_mixed_results_partial_and_optional_gap_visible(self):
        self.spec["steps"] += [self.step("bad", fixture_fault="wrong_result"), self.step("limited", max_input_bytes=1)]
        self.spec["steps"][1]["required"] = False
        result = self.execute()
        self.assertEqual(result["result"], "partial")
        self.assertFalse(result["succeeded"])
        report = self.call("handoff", "handoff")["handoff"]
        self.assertEqual(len(report["facts"]), 1)
        self.assertEqual(len(report["gaps"]), 2)
        self.assertEqual(len(report["limits"]), 1)

    def test_timeout_terminates_and_waits_owned_local_child(self):
        self.spec["steps"][0]["params"].update(delay_ms=2000, timeout_seconds=.1)
        start = time.monotonic()
        result = self.execute()
        self.assertLess(time.monotonic() - start, 2)
        step = result["steps"][0]
        self.assertEqual(step["status"], "failed")
        self.assertEqual(step["reason"], "local_worker_timeout_confirmed_terminated")
        self.assertTrue(step["quarantine"])
        self.assertFalse(step["result_verified"])

    def test_same_request_replay_and_new_key_do_not_repeat_step(self):
        result = self.execute()
        with patch.object(core.subprocess, "Popen", side_effect=AssertionError("duplicate child")):
            self.assertEqual(self.call("run", "execute"), result)
            self.assertEqual(self.call("run", "new-key"), result)

    def test_same_request_with_changed_parameters_refused(self):
        self.plan()
        with self.assertRaisesRegex(Error, "different parameters"):
            self.call("run", "plan")

    def test_plan_replay_hashes_input_bytes(self):
        self.plan()
        self.source.write_text("changed")
        with self.assertRaisesRegex(Error, "different parameters"):
            self.call("plan", "plan")

    def test_same_take_new_request_refuses_overwrite(self):
        self.plan()
        with self.assertRaisesRegex(Error, "already exists"):
            self.call("plan", "second-plan")

    def test_interrupted_intent_is_unknown_without_output(self):
        self.plan()
        with store(str(self.root)) as (root, db, _):
            run = core.load(db, "run")
            core.begin(root, db, run, run["steps"][0])
        result = self.call("status")
        self.assertEqual(result["state"], "needs_reconciliation")
        self.assertIsNone(result["result"])
        self.assertIsNone(result["sql_candidate"])
        with patch.object(core.subprocess, "Popen", side_effect=AssertionError("blind retry")):
            self.assertEqual(self.call("run", "new-key")["steps"][0]["status"], "unknown")

    def test_unknown_blocks_other_run_same_job_operation(self):
        self.plan()
        with store(str(self.root)) as (root, db, _):
            run = core.load(db, "run")
            core.begin(root, db, run, run["steps"][0])
        self.plan("other", "other-plan")
        result = self.call("run", "other-execute", "other")
        self.assertEqual(result["steps"][0]["reason"], "unresolved_operation_in_other_run")
        self.assertEqual(result["result"], "blocked")

    def test_reconcile_absent_receipt_stays_unknown(self):
        self.test_interrupted_intent_is_unknown_without_output()
        result = self.call("reconcile", "reconcile", step_id="one")
        self.assertEqual(result["steps"][0]["status"], "unknown")
        self.assertIsNone(result["sql_candidate"])

    def test_completed_worker_receipt_can_reconcile_without_reexecution(self):
        self.plan()
        with patch.object(core, "settle_receipt", side_effect=RuntimeError("controller interrupted")):
            with self.assertRaises(RuntimeError):
                self.call("run", "execute")
        self.assertEqual(self.call("status")["steps"][0]["status"], "unknown")
        with patch.object(core.subprocess, "Popen", side_effect=AssertionError("no replay")):
            result = self.call("reconcile", "reconcile", step_id="one")
        self.assertEqual(result["result"], "success")
        self.assertIsNone(result["steps"][0]["process_ok"])
        self.assertTrue(result["steps"][0]["lifecycle_reconciled"])

    def test_mismatched_receipt_does_not_settle_unknown(self):
        self.plan()
        with patch.object(core, "settle_receipt", side_effect=RuntimeError("controller interrupted")):
            with self.assertRaises(RuntimeError):
                self.call("run", "execute")
        receipt = next((self.root / "run").glob("*/receipt.json"))
        value = json.loads(receipt.read_bytes())
        value["operation_id"] = "other"
        receipt.write_bytes(encoded(value))
        result = self.call("reconcile", "reconcile", step_id="one")
        self.assertEqual(result["steps"][0]["status"], "unknown")

    def test_child_inherits_lease_after_controller_closes_its_fd(self):
        with store(str(self.root)) as (_, _, lock):
            child = subprocess.Popen([sys.executable, "-I", "-S", "-c", "import time; time.sleep(.35)"], pass_fds=(lock,))
        try:
            with self.assertRaisesRegex(Error, "busy"):
                with store(str(self.root)):
                    pass
        finally:
            child.wait(timeout=3)
        with store(str(self.root)):
            pass

    def test_evidence_tampering_refuses_handoff(self):
        result = self.execute()
        evidence = next(e for e in result["steps"][0]["evidence"] if e["kind"] == "result_file")
        (self.root / evidence["path"]).write_bytes(b"tampered")
        with self.assertRaisesRegex(Error, "modified"):
            self.call("handoff", "handoff")

    def test_logs_are_bounded_with_full_stream_hash(self):
        self.spec["steps"][0]["params"]["noise_bytes"] = 100000
        result = self.execute()
        evidence = next(e for e in result["steps"][0]["evidence"] if e.get("channel") == "stdout")
        self.assertTrue(evidence["truncated"])
        self.assertEqual(evidence["stream_bytes"], 100000)
        self.assertEqual(evidence["bytes"], 16384)
        self.assertEqual(evidence["stream_sha256"], hashlib.sha256(b"L" * 100000).hexdigest())
        self.assertEqual(evidence["sha256"], hashlib.sha256(b"L" * 16384).hexdigest())

    def test_frozen_input_tampering_refused(self):
        self.plan()
        snapshot = self.root / "run" / "input.bin"
        snapshot.chmod(0o600)
        snapshot.write_bytes(b"modified")
        with self.assertRaisesRegex(Error, "frozen"):
            self.call("run", "execute")

    def test_frozen_script_tampering_refused(self):
        self.plan()
        snapshot = self.root / "run" / "script.json"
        snapshot.chmod(0o600)
        snapshot.write_bytes(b"{}")
        with self.assertRaisesRegex(Error, "frozen"):
            self.call("handoff", "handoff")

    def test_symlink_source_refused(self):
        self.source.rename(self.base / "real.txt")
        self.source.symlink_to("real.txt")
        with self.assertRaisesRegex(Error, "symlink"):
            self.plan()

    def test_hardlink_source_refused(self):
        os.link(self.source, self.base / "hard.txt")
        with self.assertRaisesRegex(Error, "single-link"):
            self.plan()

    def test_public_root_refused_not_chmodded(self):
        for mode in (0o755, 0o777):
            with self.subTest(mode=mode):
                self.root.mkdir(exist_ok=True)
                self.root.chmod(mode)
                with self.assertRaisesRegex(Error, "0700"):
                    self.plan()
                self.assertEqual(self.root.stat().st_mode & 0o777, mode)

    def test_private_root_and_all_runtime_files_are_private(self):
        self.execute()
        for path in (self.root, *self.root.rglob("*")):
            self.assertEqual(path.stat().st_mode & 0o077, 0)

    def test_symlink_and_public_internal_files_refused(self):
        self.plan()
        bad = self.root / "wrong.txt"
        bad.symlink_to(self.source)
        with self.assertRaisesRegex(Error, "unsafe runtime"):
            self.call("status")
        bad.unlink()
        bad.write_text("not private")
        bad.chmod(0o644)
        with self.assertRaisesRegex(Error, "unsafe runtime"):
            self.call("status")

    def test_output_publication_never_replaces_existing_or_symlink(self):
        target = self.base / "output.txt"
        target.write_text("keep")
        with self.assertRaises(FileExistsError):
            write_new(target, b"replace")
        self.assertEqual(target.read_text(), "keep")
        target.unlink()
        target.symlink_to(self.source)
        with self.assertRaisesRegex(Error, "symlink"):
            write_new(target, b"replace")

    def test_unknown_schema_and_fields_and_path_escape_refused(self):
        changes = [("schema_version", True), ("schema_version", 2), ("input_file", "../input.txt"), ("other", "field")]
        for key, value in changes:
            with self.subTest(key=key, value=value):
                spec = copy.deepcopy(self.spec)
                spec[key] = value
                with self.assertRaises(Error):
                    core.validate_spec(spec)
        spec = copy.deepcopy(self.spec)
        del spec["schema_version"]
        with self.assertRaises(Error):
            core.validate_spec(spec)

    def test_arbitrary_argv_and_unbounded_params_refused(self):
        for changes in ({"argv": ["sh"]}, {"delay_ms": 40000}, {"noise_bytes": 999999}, {"timeout_seconds": True}, {"max_input_bytes": .5}):
            with self.subTest(changes=changes):
                spec = copy.deepcopy(self.spec)
                spec["steps"][0]["params"].update(changes)
                with self.assertRaises(Error):
                    core.validate_spec(spec)

    def test_runtime_inside_git_refused(self):
        (self.base / ".git").mkdir()
        with self.assertRaisesRegex(Error, "outside every Git"):
            self.plan()

    def test_relative_root_refused(self):
        with self.assertRaisesRegex(Error, "absolute"):
            root_path("relative-execution-root")

    def test_unsupported_adapter_is_executor_limit(self):
        self.spec["steps"][0].update(adapter="arbitrary_software", params={})
        result = self.execute()
        self.assertEqual(result["result"], "blocked")
        self.assertEqual(result["steps"][0]["limits"][0]["source"], "executor_scope")

    def test_high_impact_scope_not_authorized_by_manifest(self):
        self.spec["steps"][0]["scope"] = "payment"
        result = self.execute()
        self.assertEqual(result["steps"][0]["reason"], "trusted_action_approval_and_budget_gateway_not_implemented")
        self.assertIsNone(result["steps"][0]["process_ok"])

    def test_ui_stops_without_touching_desktop(self):
        with patch.object(core.subprocess, "Popen", side_effect=AssertionError("desktop launch")):
            step = self.ui()
        self.assertEqual(step["status"], "waiting_observation")
        self.assertFalse(step["result_verified"])

    def test_ui_toast_only_is_unknown_not_success(self):
        step = self.ui()
        path = self.observation(step)
        result = self.call("observe", "observe", step_id="one", observation_path=path)
        self.assertEqual(result["steps"][0]["status"], "unknown")
        self.assertIsNone(result["sql_candidate"])

    def test_ui_artifact_is_checked_but_origin_and_freeze_remain_unverified(self):
        step = self.ui()
        (self.base / "result.txt").write_bytes("HELLO 星屿\r\n".encode())
        path = self.observation(step, evidence_files=[{"filename": "result.txt", "kind": "result_file"}])
        result = self.call("observe", "observe", step_id="one", observation_path=path)
        self.assertTrue(result["steps"][0]["result_verified"])
        self.assertIsNone(result["steps"][0]["process_ok"])
        self.assertIsNone(result["steps"][0]["active_ui_elapsed_ms"])
        report = self.call("handoff", "handoff")["handoff"]
        self.assertFalse(report["ready_for_script_freeze"])
        self.assertFalse(report["facts"][0]["third_party_capability_verified"])

    def test_ui_wrong_identity_refused(self):
        step = self.ui()
        path = self.observation(step, operation_id="wrong")
        with self.assertRaisesRegex(Error, "identity mismatch"):
            self.call("observe", "observe", step_id="one", observation_path=path)

    def test_ui_evidence_escape_and_uncited_product_limits_refused(self):
        step = self.ui()
        path = self.observation(step, evidence_files=[{"filename": "../secret.txt", "kind": "log"}])
        with self.assertRaisesRegex(Error, "adjacent filename"):
            self.call("observe", "observe", step_id="one", observation_path=path)
        path = self.observation(step, product_limits=[{"statement": "claimed limit", "evidence_index": 0}])
        with self.assertRaisesRegex(Error, "cite supplied evidence"):
            self.call("observe", "observe2", step_id="one", observation_path=path)

    def test_handoff_is_synthetic_and_no_auto_publish(self):
        self.execute()
        first = self.call("handoff", "handoff")
        self.assertTrue(first["handoff"]["facts"][0]["synthetic"])
        self.assertFalse(first["handoff"]["ready_for_script_freeze"])
        self.assertIsNone(first["handoff"]["pricing"]["value"])
        self.assertFalse(first["handoff"]["recording_link"]["verified"])
        self.assertEqual(self.call("handoff", "handoff"), first)
        self.assertEqual(hashlib.sha256(Path(first["handoff_path"]).read_bytes()).hexdigest(), first["sha256"])

    def test_known_spawn_failure_is_not_unknown(self):
        self.plan()
        with patch.object(core.subprocess, "Popen", side_effect=OSError("fixture spawn unavailable")):
            result = self.call("run", "execute")
        self.assertEqual(result["result"], "failure")
        self.assertEqual(result["steps"][0]["reason"], "confirmed_process_not_started")
        self.assertGreaterEqual(result["steps"][0]["executor_elapsed_ms"], 0)

    def test_existing_result_is_preserved_and_completion_unknown(self):
        self.plan()
        original = core.execute_file
        def occupied(root, db, lock, run, step, directory):
            write_new(directory / "result.txt", b"keep existing")
            return original(root, db, lock, run, step, directory)
        with patch.object(core, "execute_file", side_effect=occupied):
            result = self.call("run", "execute")
        self.assertEqual(result["steps"][0]["status"], "unknown")
        self.assertEqual(next((self.root / "run").glob("*/result.txt")).read_bytes(), b"keep existing")

    def test_ui_cancel_attestation_does_not_execute_or_claim_success(self):
        step = self.ui()
        path = self.observation(step, outcome="blocked", note="Cancelled before action; controlled test attestation")
        with patch.object(core.subprocess, "Popen", side_effect=AssertionError("must not execute")):
            result = self.call("observe", "observe", step_id="one", observation_path=path)
        self.assertEqual(result["result"], "blocked")
        self.assertFalse(result["steps"][0]["result_verified"])
        self.assertIsNone(result["steps"][0]["active_ui_elapsed_ms"])

    def test_ui_limit_keeps_operator_provenance(self):
        step = self.ui()
        (self.base / "limit.txt").write_text("Controlled test limit statement")
        path = self.observation(step, outcome="blocked", evidence_files=[{"filename": "limit.txt", "kind": "log"}],
                                product_limits=[{"statement": "Observed quota text", "evidence_index": 0}])
        result = self.call("observe", "observe", step_id="one", observation_path=path)
        limit = result["steps"][0]["limits"][0]
        self.assertEqual(limit["source"], "operator_attestation")
        self.assertFalse(limit["independently_verified"])

    def test_ui_same_observation_replay_and_new_key_reject_duplicate(self):
        step = self.ui()
        path = self.observation(step, outcome="blocked")
        first = self.call("observe", "observe", step_id="one", observation_path=path)
        self.assertEqual(self.call("observe", "observe", step_id="one", observation_path=path), first)
        with self.assertRaisesRegex(Error, "no matching pending"):
            self.call("observe", "second-observe", step_id="one", observation_path=path)

    def test_ui_clock_discontinuity_does_not_invent_elapsed(self):
        step = self.ui()
        path = self.observation(step, outcome="blocked")
        with patch.object(core.time, "time_ns", return_value=time.time_ns() + 60000000000):
            result = self.call("observe", "observe", step_id="one", observation_path=path)
        self.assertIsNone(result["steps"][0]["observation_wait_ms"])
        self.assertEqual(result["steps"][0]["observation_timing"], "unknown_clock_discontinuity")

    def test_unknown_handoff_preserves_uncertainty(self):
        self.test_interrupted_intent_is_unknown_without_output()
        report = self.call("handoff", "handoff")["handoff"]
        self.assertEqual(report["facts"], [])
        self.assertEqual(report["gaps"][0]["status"], "unknown")
        self.assertIsNone(report["test_run"]["sql_candidate"])

    def test_deleted_evidence_refuses_handoff(self):
        result = self.execute()
        evidence = next(e for e in result["steps"][0]["evidence"] if e["kind"] == "result_file")
        (self.root / evidence["path"]).unlink()
        with self.assertRaises(FileNotFoundError):
            self.call("handoff", "handoff")

    def test_future_store_schema_is_refused(self):
        self.plan()
        with sqlite3.connect(self.root / "state.sqlite3") as db:
            db.execute("PRAGMA user_version=2")
        with self.assertRaisesRegex(Error, "unsupported store"):
            self.call("status")

    def test_evidence_cannot_be_bound_to_another_operation(self):
        self.spec["steps"].append(self.step("two"))
        self.execute()
        with store(str(self.root)) as (_, db, _):
            run = core.load(db, "run")
            evidence = next(e for e in run["steps"][1]["evidence"] if e["kind"] == "result_file")
            run["steps"][0]["evidence"].append(evidence)
            core.persist(db, run)
        with self.assertRaisesRegex(Error, "test_run/operation"):
            self.call("handoff", "handoff")

    def test_ui_fixture_defaults_synthetic_and_product_is_only_a_declaration(self):
        step = self.ui()
        self.assertTrue(step["synthetic"])
        self.assertEqual(self.call("status")["test_kind"], "controlled_fixture")
        self.spec["test_kind"] = "product_test"
        self.spec["job_id"] = "declared-product-job"
        self.plan("product", "product-plan")
        with store(str(self.root)) as (_, db, _):
            run = core.load(db, "product")
            self.assertFalse(run["steps"][0]["synthetic"])
        self.assertFalse(self.call("handoff", "product-handoff", "product")["handoff"]["ready_for_script_freeze"])

    def test_ui_one_wrong_artifact_is_failure_not_unknown(self):
        step = self.ui()
        (self.base / "result.txt").write_text("controlled wrong result")
        path = self.observation(step, evidence_files=[{"filename": "result.txt", "kind": "result_file"}])
        result = self.call("observe", "observe", step_id="one", observation_path=path)
        self.assertEqual(result["result"], "failure")
        self.assertEqual(result["steps"][0]["reason"], "independent_result_mismatch")
        self.assertFalse(result["steps"][0]["result_verified"])

    def test_unknown_test_kind_refused(self):
        self.spec["test_kind"] = "pretend_verified"
        with self.assertRaisesRegex(Error, "test_kind"):
            core.validate_spec(self.spec)

    def test_real_cli_stdout_contract_and_exit_status(self):
        command = [sys.executable, "-m", "execution_agent", "plan", "--root", str(self.root), "--run-id", "run", "--request-id", "plan", "--manifest", str(self.manifest)]
        result = subprocess.run(command, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["command_ok"])
        result = subprocess.run(command[:3] + ["status", "--root", str(self.root), "--run-id", "missing"], capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(json.loads(result.stdout)["command_ok"])


if __name__ == "__main__":
    unittest.main()
