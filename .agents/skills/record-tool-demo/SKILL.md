---
name: record-tool-demo
description: Perform the supervised operator workflow from a verified test scenario through a recording plan, bounded silent single-window capture, local inspection and producer handoff. Uses the auto-video recording_agent CLI; does not authorize accounts, real-tool testing or publication by itself.
---

# Record tool demo

Read [operator instructions](../../../agents/record-tool-demo.md) and [MVP behavior](../../../docs/recording-mvp.md). Work from the repository root. Python 3.12+, macOS 15.2 arm64, the existing Swift SDK and ffmpeg/ffprobe are required; do not install anything automatically.

## Explore and verify first

Read the researcher handoff and operator-approved scenario. Record fixed input hashes/rights, tool/environment version, success criteria and actual result evidence. Open and inspect the real result or query it within the authorized account; no public result page is required. Do not infer success from a toast or recording. Respect blocked/partial outcomes. New account access, login, payment or external submission requires applicable trusted user approval and budget handling from the overall design; this skill grants none.

## Plan and capture

Write a pre-recording shooting plan, including a reproducible start, explicit target app/window, input/action/result coverage and duration. Producer creates the final edit storyboard only after usable real footage exists.

The only currently authorized live validation is a generated safe fixture window, after the parent reviews the implementation. Build is separate and does not start capture:

```sh
python3.12 -m recording_agent build --root <private-runtime-directory-outside-git>
python3.12 -m recording_agent record --root <private-runtime-directory-outside-git> \
  --request-id <unique-request> --take-id <unique-take> --job-id <job-id> \
  --mode fixture --duration 7
```

Future third-party capture requires separately authorized scope, the expected target bundle/PID, and a system single-window selection. Do not substitute picker mode for current fixture authorization, use unrelated window enumeration, choose another window after mismatch, or fall back to display capture. No audio/microphone/camera flags exist. Permission failure exits without requesting access; report the missing permission rather than changing TCC or SIP.

## Reconcile and hand off

Use `status --root ... --take-id ...`. While a synchronous record runs, another local command may request `stop --root ... --take-id ... --request-id ...`. Reusing the same request is safe; changed parameters under the same key are refused. Do not launch duplicates after timeouts. Use `recover` only after the cooperative inherited desktop lock is released; incomplete terminal evidence remains unknown and original partial media stays preserved.

Inspect persisted manifest and actual footage locally. `validated` requires the duration plan as well as media checks; explicit early stop is `partial/stopped_early` with `plan_complete=false`, never complete-plan acceptance. Even validated has `tool_test_status=not_performed` and visual review pending. Preserve VFR PTS; final 30fps, audio, actual tool result correctness and privacy require their own checks. Return source identity, hashes, actual timing, usable intervals and gaps, real test evidence and limitations. Keep media/frames/logs outside Git and model services. The helper is supervised and standalone: no avcd SQL import, secure OS gateway, asynchronous start daemon, automatic tool operation or publisher integration is implemented.
