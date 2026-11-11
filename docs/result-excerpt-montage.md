# Source-bound result montage

`creative_agent montage` prepares a short silent asset for a result-first opening.
It holds one frame of an existing template and places two to four actual recorded
result excerpts inside a declared annotation rectangle. The original input and
other evidence rectangles can remain visible from the first output frame.

```sh
python3 -m creative_agent montage request.json --output new-private-attempt
```

The existing local Pillow, FFmpeg and Mac display font are required. The operation
installs nothing, drives no UI, synthesizes no product result, and gives no viewing,
listening, font or publication approval. Import the prepared asset into the native
editor, preserve old versions, and check the actual exported video separately.

The strict `result_excerpt_montage/v1` request has:

| Field | Meaning |
|---|---|
| `id` | Bounded local identifier |
| `base` | Template local path and actual SHA-256 |
| `base_frame` | Frame held for the entire preview |
| `annotation_area` | Even x/y/width/height, inside the template |
| `evidence_areas` | Protected input/caption/source rectangles; no intersection allowed |
| `claim` | Must be `editorial_preview_not_execution` |
| `source_note` | Observed history, including existing edits or manual changes |
| `panels` | Two to four source bindings, `in_frame`, exclusive `out_frame`, `crop`, `observed_content` |

Each excerpt is 15–90 normal-speed CFR30 frames. Their sum must equal the original
template duration, at most180 frames. Source media is silent, zero-based CFR30,
even sized, at most9000 frames. No fabricated UI text is drawn. Each excerpt has a
visible “页面节选 n/N” label; metadata maps every output range back to source frames.
The order may be editorial, so do not describe it as recorded navigation or actual
elapsed time. Labels/counts refer to selected excerpts, not the product's total pages.

Selection observations and protected rectangles are operator assertions, not machine
proof. Check complete crops for readability and privacy. The codec may change pixels
outside the overlay; declared non-overlap is not lossless pixel preservation. Source
hashes are rechecked, output is not overwritten, and partial files remain on failure.
The prepared asset has no audio; native captions and narration still need alignment.

This local feature was designed and implemented by Codex using the authorized fallback.
It is not presented as a Claude Code design or as a successful product test.

## Validation scope

Fourteen tests cover source hashes, strict fields, evidence overlap, crop/range/fps
limits, duration preservation, visible first-frame result ordering, silent output,
and refusing an existing output directory. The colour-control render checks actual
decoded pixels and source-range mapping. All 58 related creative tests passed on
the Mac, without skips; no new package installation was needed for this feature.

In the local Gamma v18 trial, three actual page excerpts replaced only the opening
90 frames of an existing 960-frame edit. The asset was imported through native
Jianying Replace Clip, saved, exported and reopened. The final file is 32 seconds,
1080×1920 at CFR30 with its existing narration; complete decoding and all 960
timestamps passed. Its SHA-256 is
`751048e7bf89bc9f71bc8875e7070ff116e0d3323eb916d711976d6130a6e5f0`.

Static exported anchors show the cover at frame 0, the progress page at frame 44,
and the data page at frame 89, with the original input fragments and excerpt labels.
This demonstrates that specific revision, not equivalence to a reference creator.
Continuous viewing, actual listening, physical-phone readability and complete
publication/privacy clearance remain unapproved. Local audio model blind tests did
not qualify it as a listener; objective acoustic predictions do not remove that gap.
No media, model weights, native project databases or private runtime logs belong in Git.
