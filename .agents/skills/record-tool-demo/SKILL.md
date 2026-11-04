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

Use the live scope authorized by the current user; this skill itself grants no desktop or account approval. A generated safe fixture is suitable before authorized third-party testing. Build is separate and does not start capture:

```sh
python3.12 -m recording_agent build --root <private-runtime-directory-outside-git>
python3.12 -m recording_agent record --root <private-runtime-directory-outside-git> \
  --request-id <unique-request> --take-id <unique-take> --job-id <job-id> \
  --mode fixture --duration 7
```

Third-party capture requires authorized scope and expected bundle/PID. Use either the system single-window picker, or the reviewed `explicit` path with its separately approved one-query metadata scope: [explicit selection](../../../docs/recording-explicit-window.md). In explicit mode first run `--validate-only`, inspect the source receipt, then use its CGWindowID/launch identity with approved bounds/display for a new recording take. Source validation never constructs a stream and is not media success. Do not expand metadata access, choose another window after mismatch, or fall back to display capture. No audio/microphone/camera flags exist. Permission failure exits without requesting access; report it without changing TCC/SIP.

Use confirmed input navigation and synchronize it with `recording_started`; do not assume a click or scroll changed the visible page. Stop timing is best effort even with a strict monotonic timer. Keep the original duration acceptance, preserve VFR PTS, and report actual media versus callback timing: [timing validation](../../../docs/recording-stop-timing.md). All-frame automated privacy checks do not replace a full human visual review; preserve pending review accurately and keep raw media/frames local.

For browser capture, exclude browser tabs, address bars and debugging notices at the source; do not hide security UI or keep an uncropped original. The current Chrome path is intentionally blocked before capture because atomic viewport evidence is unavailable. Read [current crop status](../../../docs/browser-content-capture.md), report the capability blocker, and do not retry with the old full-window path, fixed pixels or export-only cropping. Component/fixture tests are not real-browser recording success.

## Reconcile and hand off

Use `status --root ... --take-id ...`. While a synchronous record runs, another local command may request `stop --root ... --take-id ... --request-id ...`. Reusing the same request is safe; changed parameters under the same key are refused. Do not launch duplicates after timeouts. Use `recover` only after the cooperative inherited desktop lock is released; incomplete terminal evidence remains unknown and original partial media stays preserved.

Inspect persisted manifest and actual footage locally. `validated` requires the duration plan as well as media checks; explicit early stop is `partial/stopped_early` with `plan_complete=false`, never complete-plan acceptance. Even validated has `tool_test_status=not_performed` and visual review pending. Preserve VFR PTS; final 30fps, audio, actual tool result correctness and privacy require their own checks. Return source identity, hashes, actual timing, usable intervals and gaps, real test evidence and limitations. Keep media/frames/logs outside Git and model services. The helper is supervised and standalone: no avcd SQL import, secure OS gateway, asynchronous start daemon, automatic tool operation or publisher integration is implemented.


## Experimental browser probe

Only within separately authorized scope, `browser-probe --kind fixture --root <fresh-private-root>`
serves the generated safe fixture, while `--kind gamma-identity` is metadata-only and never records
third-party pixels. `browser-probe-status --root <root>` reads its separate experimental receipt;
unknown is not success and a replay never starts another session. See [latest live results](../../../docs/browser-viewport-validation.md).
Gamma returned no Capture Handle exposed to this local origin; keep actual Gamma capture blocked.
Do not inject a handle or treat titles/origin alone as document identity. This skill does not grant
account, permission or new software access. Zoom/cancel/pointer and the newest preview remain unvalidated.


An explicit trusted user authorization for a particular local whole-window raw followed by crop
can override the default source-exclusion requirement for that task. Only then use
`--browser-ui-policy local_raw_then_crop` with explicit Chrome process/window/display/bounds
and exact `--expected-window-title`, validate first, retain raw privately, derive crop from
measured pre/post geometry, preserve VFR time base, decode both outputs, and disclose that
uncropped raw still contains browser UI. This option itself grants no authorization and
must not become the default. See [completed supervised example](../../../docs/gamma-local-window-crop.md).
