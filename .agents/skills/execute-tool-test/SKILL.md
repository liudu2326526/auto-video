---
name: execute-tool-test
description: Execute an approved tool-testing script with the auto-video execution_agent CLI, record actual outcomes, timing, limits and hashed evidence, and produce a screenwriter handoff. Use for repeatable tool tests and supervised operator evidence collection; fixtures never prove third-party capabilities.
---

# Execute tool test

Read the [runtime contract](../../../docs/test-execution-agent.md). Work at the repository root.
This project Skill is a supervised operator workflow, not an unattended UI driver or an authorization grant.

## Prepare and freeze

1. Read the user's current scope, script, source/input rights, expected tool version and independent success criteria. Classify the actual effect of every action. A manifest's scope label cannot authorize an account, purchase, upload, publication or other external mutation. The MVP blocks such scopes because no independent approval/budget gateway is implemented. Do not relabel them as local UI.
2. Confirm a private runtime root outside Git. Set `test_kind=controlled_fixture` for safe GUI fixtures as well as generated examples; a `product_test` label does not establish origin or independent review. Freeze input and scenario with `plan`; take the reported hashes into the work record. No arbitrary shell or argv adapters exist. The bundled `file_transform` is a controlled text fixture, not Gamma, CapCut or generic software support.
3. Shared Mac: obtain the coordinator's desktop slot before any UI, screen inspection or recording. The executor's file lease is not a desktop lease. Use the platform's supported computer-use tools after reading their current documentation; the CLI never clicks or captures the screen. Stop if another task/person is using the desktop or if source identity/privacy/authority becomes uncertain.

## Execute and verify

Use the documented `plan` and `run` commands. A `waiting_observation` response binds one operation ID/token to a supervised UI instruction, but it does not prove an action ran or grant permission. Check the actual current app/window/document identity and safe visible content before operating. Execute the script once, observe the result and preserve actual failures/limits; do not secretly regenerate or replace a failed take.

Record monotonic action timestamps in the operator evidence log when supported. The MVP's `observation_wait_ms` measures time awaiting callback and includes human/coordination delay; `active_ui_elapsed_ms=null` must stay unknown. Never present that wait as product generation time. Declared versions and operator product-limit statements remain attributed observations unless independently checked.

Copy only explicitly approved, relevant evidence to an observation directory. Supply the operation ID/token from `run` in the [observation schema](../../../docs/test-execution-agent.md#supervised-observation). Use `observe` once with a request ID. A success toast or screenshot alone becomes unknown. One result file that explicitly mismatches the frozen criterion is failed, even if the operator reports the UI action completed. A matching result file passes the frozen byte/text criterion; it still cannot independently establish which tool produced it or its semantic usefulness. An outcome marked unknown must be reconciled, never retried under a new key. UI reconciliation is not implemented in the CLI; report the blocker rather than forging a completion receipt.

For optional recording, separately use the existing [record-tool-demo Skill](../record-tool-demo/SKILL.md) only in an authorized desktop slot. Recording success and tool-result success are independent. Do not flip a take's `tool_test_status` merely because this executor has a result. Take-to-test binding/import is unimplemented, so report references as unverified. No automatic media upload or account access.

## Report to the screenwriter

Run `status`, then `handoff`. Revalidate all evidence hashes. Give the screenwriter the local `screenwriter_test_handoff` JSON: observed successes, failures, blocked/unexecuted/unknown steps, real timing and its scope, exact evidence references, product observations versus executor limitations, unknown costs and forbidden inferences. Do not send a message or upload artifacts unless the user separately asks.

Keep `ready_for_script_freeze=false` until independent semantic review and the overall workflow's real acceptance gates are satisfied; this helper never freezes, publishes or writes production SQL. A fixture handoff remains synthetic. If interrupted, inspect status/lease and reconcile a bound completion receipt; absence of a PID or result file is not proof that an external action never ran. Preserve originals and report the exact blocked action, target, reason and whether execution is known, denied or uncertain.
