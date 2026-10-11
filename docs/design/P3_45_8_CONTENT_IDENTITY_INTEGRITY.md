# P3.45.8 — Narration Content-Identity Integrity (Design Record)

Owner mandate: generated audio must never again be silently associated
with text it was not generated for. Two association points could do
exactly that — **Re-add Missing** (queue repair) and **Scene Combine**
(slot resolution) — because `block_id` + `part_of_block` pin a POSITION,
not the text at that position, and an existing audio file was treated as
proof of coverage. This round closes both with a fail-closed content
identity, narrowly, without touching any frozen contract's substance.

## §1 The content identity (`content_sha`)

`engine/audio_provenance.part_content_sha(prompt, speaker, character_id)`
computes the deterministic identity of ONE generation part:

```
SHA-256( b"1\n" + speaker_utf8 + b"\n" + character_id_utf8 + b"\n" + prompt_utf8 )
```

* `prompt` is the **exact text sent to the generator** — on the
  production path `SplitPart.prompt` == `BatchJob.prompt` ==
  `GenerationRequest.text` == `GenerationResult.prompt` (the Engine sets
  the result prompt verbatim from the request; `to_request()` sends the
  job's prompt as the request text). Hashing the prompt captures the
  narration text AND every generation-relevant token the pipeline embeds
  in it.
* `speaker` / `character_id` participate because they are
  generation-relevant IDENTITY; `None` and `""` canonicalise to the empty
  string.
* **No normalisation, no trimming, no case folding** — byte-exact UTF-8.
  Meaningfully different generation inputs can never appear equivalent.
* The version prefix (`1\n`) inside the hashed bytes IS the versioning: a
  future v2 serialization can never collide with a v1 fingerprint.
* Deliberate boundary (documented decision): generation PARAMETERS that
  alter the audio without altering identity (voice profile, seed,
  temperature) are NOT hashed. They are the versioned-regeneration
  model's concern: same slot + new parameters = a new `part_version`,
  which is exactly the existing append-only contract. Changing them and
  regenerating creates a new version of the SAME identity.
* What the fingerprint is NOT used for: any automatic invalidation,
  deletion or regeneration. The §0 Product Rule of
  `engine/audio_provenance` (no prompt hashing, no automatic
  invalidation) is amended precisely and honestly: the fingerprint is a
  FAIL-CLOSED GATE at the two association points only — the system still
  never decides "the user must regenerate"; it refuses to combine audio
  it cannot prove belongs to the current narration and names the remedy.

### §1.1 Who stamps it (no second writer)

* `materialize_expected_slots(parts)` — the ONE slot writer (Generate
  Long's structure event) — stamps `content_sha` on every slot from the
  splitter part. Additive field: persisted scenes without it keep
  working and classify as legacy (unprovable) at the gated operations.
* `register_generation_result(result)` — the ONE asset writer — stamps
  `content_sha` from `result.prompt`/`speaker`/`character_id`: proof of
  the content the audio was ACTUALLY generated for.
* Both writers therefore agree by construction (pinned by
  `TestContentIdentityPrimitive`), and the fingerprints survive the
  project disk round-trip untouched (`Scene.to_dict` passes the slot and
  asset dicts through verbatim — pinned by the P3.45.6 closeout
  round-trip regression, now also asserting the fingerprints).

## §2 Re-add Missing — identity check before queue repair

`MainWindow._on_readd_missing_parts` keeps its whole existing pipeline
(the same `validate_block_ranges` gate, the same splitter pass, the same
`_build_long_part_job` builder, append-only queue repair) and adds ONE
check between the positional match and the job creation: the slot's
recorded `content_sha` must equal the current part's fingerprint.

Fail-closed outcomes, all REPORTED (never forced, never silent):

| state | outcome |
|---|---|
| slot has `content_sha`, matches current part | repair job created (E4) |
| slot has `content_sha`, differs (text/decomposition/speaker/character changed) | refused: "the current text at this part's position differs from the recorded generation content — run Generate Long" (E1–E3, E6) |
| slot has no `content_sha` (legacy project) | refused: "legacy structure without content identity — run Generate Long (review mode re-materialises the structure)…" (E5) |
| position gone / plain slot | refused exactly as before (P3.45.6-ADD behaviour) |

Duplicate identical texts are NOT ambiguous here: the slot's own
position + its own fingerprint prove its own content; each duplicate
block repairs its own row (E7). Ambiguity only exists where a position
is gone and matching is attempted BY FINGERPRINT ALONE — which is the
Combine reconciliation path below.

What Generate Long does when a repair is rejected (task §3): nothing
automatic, nothing destructive. It is the documented REMEDY — running it
re-materialises the structure (slots + fingerprints) from the current
text in review mode without starting any generation; the queue is
rebuilt as the workspace it is, assets/versions are untouched
(append-only), and regenerating the affected parts is the user's
decision.

## §3 Scene Combine — covered-slot integrity

`resolved_slot_sources(scene, app_root, current_block_ids=None,
current_parts=None)` gains the current decomposition's per-part
identities. `MainWindow._combine_current_identities(scene)` derives them
from ONE splitter pass over the live editor text with the CURRENT
settings — the same authoritative engine and parameter sources
Generate Long and Re-add use (read-only; the editor, block model,
recorded structure and queue are never touched).

Evidence tiers (all read-only; blocked audio is never deleted):

1. **Full tier** (`current_parts` provided — the ACTIVE scene): a
   covered slot is a valid source only when
   `slot.content_sha == current part's sha at the same slot position`
   AND `resolved asset.content_sha == that sha` — the recorded expected
   content AND the audio's generation content both provably match the
   current text. Otherwise the exact blocker: changed text at the
   position / audio generated for a previous version (includes
   explicitly selected older versions) / legacy slot or legacy asset
   (cannot be proven). A slot whose slot_id no longer exists in the
   current decomposition is reconciled ONLY on an UNAMBIGUOUS
   fingerprint match (exactly one current part) — the provable
   block-ID replacement, e.g. Re-detect over unchanged text (D4);
   zero matches → orphaned (D1); more than one → ambiguous (D5).
   `current_parts=[]` is POSITIVE evidence of an empty decomposition.
2. **Structure tier** (`current_parts=None`, `current_block_ids`
   provided — the by-id combine path for a NON-active scene): a covered
   slot whose Block provably no longer exists is blocked (orphaned
   audio, D1); a covered slot whose Block still exists is accepted
   WITHOUT content proof. **Precise constraint (documented per task
   §3):** the per-scene generation settings needed to re-run the
   splitter faithfully are not persisted, so no trustworthy
   decomposition can be derived for a non-active scene; full content
   verification happens on the active scene (make the scene active and
   combine from there for the full guarantee).
3. **Default** (both `None`): the exact legacy behaviour for every
   pre-existing caller — no classification at all.

**Contract change, superseding P3.45.6-ADD:** "covered slots are never
classified" no longer holds — an existing audio file is no longer proof.
The superseded behaviour is preserved honestly where EARNED: identical
proven state still combines (D3), and the re-detect-over-unchanged-text
case still combines once the content is PROVEN (D4) instead of assumed.
The historical tests pinning the old rule were updated to pin the new
one (each annotated in place); the P3.45.8 matrix lives in
`tests/test_p3_45_8_content_identity_integrity.py`.

## §4 Legacy projects (fail closed, honest remedy)

Pre-P3.45.8 persisted scenes have no `content_sha` on slots or assets,
and no other persisted metadata records the generation text (investigated:
slot and asset dicts carry ids/labels/versions/runs only) — so identity
cannot be proven from existing data. Per the mandate, the affected
operations REFUSE rather than fabricate provenance. The remedy chain is
non-destructive: run Generate Long (review mode suffices to re-materialise
the structure and stamp fingerprints), then regenerate the parts whose
audio must be proven. The append-only asset stream, the version model
and explicit selections are never touched by a refusal; historical audio
files stay on disk and rejoin their slot identities forever.

## §5 Regressions

`tests/test_p3_45_8_content_identity_integrity.py` (35 tests):
the identity primitive (determinism, byte-exactness, speaker/character
participation, writer agreement); the engine tier matrix for
`resolved_slot_sources` (proven chain / changed / stale-audio / legacy /
unique reconciliation / ambiguous / orphaned / positive-empty /
structure tier / byte-identical default); the REAL-MainWindow scenario
matrix E1–E8 and D1–D9 exactly as the task enumerated them, through the
real editor, real splitter, real batch generation and real Combine; and
the current-identity derivation itself. Updated historical pins:
`test_p3_45_6_batch_removal_combine_integrity.py` (the superseded
covered-slot rule now pins the new contract + companions),
`test_p3_28_audio_provenance.py` / `test_p3_31_scene_output_safety.py`
(fixtures now carry the full identity chain — the real-app state),
`test_p3_37_branding_documentation.py` (the current-phase doc pin).

## §6 Verification

Full battery (8 chunk invocations, the documented SSH06 class isolation):
**1919 distinct tests + 35 subtests, ZERO failures** (1924 executions =
1919 + 5 documented SSH12H13 re-runs; nothing skipped). Authoritative
collection count re-established this round: the historical "1889" claim
in commit messages overstated the distinct count by 6 (chunk-summation
double-count of the isolation re-run); the corrected baseline at
P3.45.7 was 1883 + 35, and P3.45.8 adds 36 (35 new + 1 net from the
addendum-file companion) → 1919 + 35. Validators: verify_compile
162/162, verify_architecture PASS (2 documented exceptions), 
verify_integration PASS, verify_functional_integrity 80/80; launch
smoke green on the real entry path.

## §7 Genuine UI screenshot

`docs/screenshots/guineo-main-window.png` (1440×860, ~150 KB) is a
genuine capture of the running application in this build: the real
entry sequence (real module import, entry-level QApplication,
settings-driven theme, real Engine + MainWindow), a clean generic
sample state (demo project "Eden" / scene "The Lighthouse" with sample
narration in Narration Blocks view + one sample character), the real
window rendered and captured via `window.grab()` (the window's own
pixels). Capture method honestly recorded: the sandbox provides no
usable display server (Xvfb attempted — blocked by the container's
X11 socket-directory ownership), so the capture ran on Qt's offscreen
platform; the pixels are produced by the real application's rendering
pipeline, not redrawn or fabricated. VLM-verified: main UI readable,
genuine application window, no private data. Referenced from README.md
and README_HU.md (relative path), shipped in the release ZIP and
attached to the GitHub Release.

## §8 Residuals

* The by-id combine path's structure-only tier (§3.2) — the precise
  constraint is documented above; full verification requires the scene
  to be active.
* Legacy projects must re-materialise + regenerate once to pass the
  gates (the safe price of provable integrity; §4).
* The tests/conftest.py logging/test-isolation topic is explicitly OUT
  OF SCOPE per the task's scope rule and remains recorded as an
  unresolved engineering issue in the closeout report (not silently
  fixed, not claimed fixed).
* Windows-side visual confirmation stays on the owner's manual
  checklist (unchanged house residual).
