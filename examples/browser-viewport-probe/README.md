# Browser viewport probe — incomplete experiment

This is a supervised, fixture-only experiment, **not an enabled recording_agent backend**.
A bounded Claude retry has now succeeded and Codex reviewed it before continuing.
The normal fixture produced an actual video with local checks; see the dated validation below.
Do not use this probe for Gamma or other third-party/private tabs.

The generated fixture opts in to a per-run Capture Handle, visible only to the loopback
recorder origin. The recorder requests a browser surface through the normal chooser,
then rejects other surfaces, unexpected tracks, missing/mismatched handles, mute/end,
size changes and oversized output. It neither hides browser notices nor installs extensions.
Six seconds and 16 MiB of application-retained encoded chunks are experimental limits;
they are not a hard bound on browser/encoder process memory. The sink is loopback-only,
not an independent attestation of the client's checks or a sandbox against malicious same-UID code.

To reproduce the supervised fixture, use a new private runtime directory outside Git:

```sh
python3.12 examples/browser-viewport-probe/server.py --root <existing-0700-runtime-directory>
```

Open the printed fixture and recorder URLs with the supported browser UI. Activate the
recorder before pressing its button. Select only `SAFE VIEWPORT FIXTURE`, with no audio.
The browser chooser grants only the current capture session; there is no persistent grant.
Window and monitor sources are never acceptable. No encoded output is submitted to the
loopback sink until all client checks pass. Source invalidation discards RAM chunks.

The first actual stream reported `displaySurface=browser`, 30 fps requested/settings,
VP9 availability, and no audio tracks; its dimensions changed after about 52 ms and the
probe discarded it without writing a video. Encoding-before-settlement was then adjusted
to require five stable size observations within two seconds. At that checkpoint it had only syntax checks. A subsequent chooser was explicitly
cancelled when the workflow clarification arrived. It returned `NotAllowedError`; this was
our cancellation, not evidence that macOS or the user denied recording permission.

Still unverified: actual video frame rate/duration/decode/corners, dynamic zoom and toolbar
changes, pointer mapping, encoded-byte budget rejection, late chooser grant, and shutdown
under browser throttling. No Gamma capture was attempted. Capture Handle opt-in on Gamma
has not been established; a missing handle must not be replaced with a title-only check.

See [research and exact state](../../docs/browser-content-capture.md).

## Follow-up actual video result

After a successful Claude design review, the stable-start revision produced 176 VP9 frames,
1750×958, zero audio, with full decode exit 0. PTS span is 6.471 seconds (target 6),
observed average about 27.04 fps; it is not a fixed-30fps claim. All 704 corner-patch checks
passed, and four local-only OCR samples retained content markers without the tested browser UI words.
The 6-second/16MiB bounds apply to this experimental fixture and retained application chunks,
not total browser memory. Follow-up native CUA tests verified identity invalidation, actual stream size changes,
encoded-byte budget rejection and late chooser grants without any media written. The original
extension-based window preparation failure remains recorded separately.

See [Claude draft, review, measured results and pending checks](../../docs/browser-viewport-validation.md).


## Fault injection cases

Use a fresh private runtime directory for each run. Normal browser selection is required every time.

- `--test-case invalidate`: fixture changes its exact handle 700ms after start; reject and discard RAM chunks.
- `--test-case overflow`: 64-byte client retention cap, instead of the normal16MiB, to exercise rejection.
- `--test-case late_grant`: 1500ms selection deadline, instead of the normal40000ms; a later grant stops immediately before encoding.

These test-only parameters do not weaken defaults. Source/identity/size checks are unchanged.
CaptureController keeps the recorder active during acquisition, using the documented Conditional Focus API.
Source rescaling after that still aborts; no claim that every normal session will retain stable dimensions.
Do not treat async Capture Handle events or settings polling as a per-frame proof for arbitrary sites.


## Experimental CLI

The canonical service is now `recording_agent.browser_probe`. The wrapper above remains supported.
Use `python3.12 -m recording_agent browser-probe --kind fixture --root <fresh-private-root>`.
A separate `--kind gamma-identity` mode only reads Capture Handle metadata, immediately stops
its track, never creates a pixel consumer or recorder, and rejects every video upload route.
The actual approved Gamma tab returned no exposed handle to this local origin, so automatic
Gamma recording remains unavailable. Do not inject a handle into third-party pages.

`browser-probe-status --root <root>` reads a persisted receipt. Repeating identical parameters
replays without starting; conflicting parameters are rejected. The service is bounded to
120 seconds by default, closes on explicit stopped/terminal client reports, and stores unknown
when completion is unproven. These receipts are separate from the main record take schema.

The fixture preview/frame observer revision still needs a new live positive capture and
zoom/cancel/pointer tests. 109 regression tests pass, but this is not end-to-end certification.
