"""
SpeechStudio Engine - Audio Provenance (P3.28)
===============================================

Pure functions implementing the approved Scene Audio Provenance design
(docs/design/SCENE_AUDIO_PROVENANCE_DESIGN_RECORD.md) as mandated by the
P3.28 directive.

THE PRODUCT RULE (design record §0 — governs everything below):
    The system NEVER attempts to determine whether the user changed text,
    prompt, tokens, or semantics. There is no text diffing, no token
    tracking, no prompt hashing, no automatic invalidation. The system
    only ever records three facts:

      1. Has this generation slot ever been generated successfully?
      2. What audio versions exist for it? (append-only list)
      3. Which output is explicitly selected, where explicit selection
         is required?

    The user alone decides when to regenerate.

THE ONE AUTHORITATIVE RECORD:
    ``Scene.audio_assets`` — the existing append-only stream. An asset
    exists in it if and only if that generation succeeded. Every question
    below is DERIVED from that single stream with pure functions:

      - generation slot identity + expected slot structure
      - block generated state            (never stored — Rec 5)
      - Scene completeness               (never eagerly mutated — Rec 7)
      - generation run identity          (metadata, no registry — Rec 4)
      - per-slot audio versions          (monotonic max+1 — Rec 6)
      - Scene combined output lineage + staleness (Rec 13–15)
      - Scene output resolution          (Rec 16 + P3.28 §15 correction)

THE ONE WRITER:
    :func:`register_generation_result` is the ONLY function that appends
    a new asset to a Scene. It resolves the Scene by ``result.scene_id``
    (never by "whichever scene is active") — closing defect D-3 — and
    recomputes that Scene's derived coverage state — closing defect D-2.

Scene completeness states (P3.28 §5):
    NOT_GENERATED  no slot has audio (and nothing failed)
    GENERATING     a generation run is currently in flight for the Scene
    PARTIAL        some expected slots are covered, others are not
    COMPLETE       every expected slot has at least one successful asset
    ERROR          zero slots covered AND the last run had failures

    Starting a generation is NEVER completion. A failed regeneration of
    an already covered slot does NOT degrade a COMPLETE Scene, because
    the previous successful version remains valid.

Scene output resolution (P3.28 §15 correction + the accepted follow-up
safety rule):
    1. explicit user selection (a stale selection is honoured but flagged)
    2. latest NON-STALE COMPLETE Scene Combined output (completeness is
       guaranteed by the structural staleness check R2)
    3. a PROVABLY complete full-scene asset — LEGACY scope only: Scenes
       that have no combined outputs at all. A single Part AudioAsset
       is NEVER treated as the complete Scene output (no exceptions,
       not even for a single-slot Scene).
    If all candidates are stale, missing, or only Parts exist the Scene
    REQUIRES USER REVIEW — the application never silently assembles
    stale or partial audio.

This module is pure Python (os/re/datetime only — no Qt, no numpy, no
engine state) and fully unit-testable.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from engine.logger import get_logger

logger = get_logger("audio_provenance")


# ---------------------------------------------------------------------------
# Scene completeness state vocabulary (P3.28 §5)
# ---------------------------------------------------------------------------
SCENE_NOT_GENERATED = "not_generated"
SCENE_GENERATING = "generating"
SCENE_PARTIAL = "partial"
SCENE_COMPLETE = "complete"
SCENE_ERROR = "error"

SCENE_STATES = (
    SCENE_NOT_GENERATED, SCENE_GENERATING, SCENE_PARTIAL,
    SCENE_COMPLETE, SCENE_ERROR,
)

# Human-readable labels for UI display (Batch window coverage header,
# sidebar badges, assembly rows).
SCENE_STATE_LABELS = {
    SCENE_NOT_GENERATED: "NOT GENERATED",
    SCENE_GENERATING: "GENERATING",
    SCENE_PARTIAL: "PARTIAL",
    SCENE_COMPLETE: "COMPLETE",
    SCENE_ERROR: "ERROR",
}

# Legacy status values that map onto the new vocabulary on load (Rec 24:
# legacy values recompute — "complete" may honestly downgrade to "partial").
_LEGACY_STATUS_MAP = {
    "draft": SCENE_NOT_GENERATED,
    "ready": SCENE_NOT_GENERATED,
}

# Selected-output kinds (Scene.selected_output)
KIND_SCENE_COMBINED = "scene_combined"
KIND_ASSET = "asset"


# ---------------------------------------------------------------------------
# Slot identity (P3.28 §3 / design record §3.1)
# ---------------------------------------------------------------------------
def block_slot_id(block_id: str, part_of_block: int) -> str:
    """slot_id for a block-derived part: ``{block_id}:{part_of_block}``."""
    return "{0}:{1}".format(block_id, max(1, int(part_of_block)))


def plain_slot_id(part_index: int) -> str:
    """slot_id for a plain / speaker-split part: ``plain:{part_index}``."""
    return "plain:{0}".format(max(1, int(part_index)))


def materialize_expected_slots(parts: List[Any]) -> List[Dict[str, Any]]:
    """Build the expected slot list from a splitter run's parts.

    Slots are materialised ONLY by explicit generation structure events
    (Generate Long preview / Re-detect) — never on a keystroke (§0).

    Each slot dict: {slot_id, block_id, block_label, part_index,
    part_of_block, speaker, character_id}. ``block_id`` is None for
    plain/speaker-split parts (slot_id = ``plain:{part_index}``).
    """
    slots: List[Dict[str, Any]] = []
    for i, part in enumerate(parts):
        part_index = i + 1
        block_id = getattr(part, "source_block_id", None)
        part_of_block = getattr(part, "part_of_block", 1) or 1
        if block_id:
            slot_id = block_slot_id(block_id, part_of_block)
        else:
            slot_id = plain_slot_id(part_index)
        slots.append({
            "slot_id": slot_id,
            "block_id": block_id,
            "block_label": getattr(part, "block_label", "") or "",
            "part_index": part_index,
            "part_of_block": int(part_of_block),
            "speaker": getattr(part, "speaker", "") or "",
            "character_id": getattr(part, "character_id", None),
        })
    return slots


def _slot_by_part_index(scene: Any) -> Dict[int, Dict[str, Any]]:
    """Index the Scene's expected slots by global part_index."""
    out: Dict[int, Dict[str, Any]] = {}
    for slot in (getattr(scene, "expected_audio_slots", None) or []):
        if not isinstance(slot, dict):
            continue
        try:
            out[int(slot.get("part_index", 0))] = slot
        except (TypeError, ValueError):
            continue
    return out


def slot_of_asset(asset: Dict[str, Any],
                  scene: Optional[Any] = None) -> Optional[str]:
    """The slot_id an AudioAsset belongs to, or None (legacy scope).

    Join rule (P3.44.5 §8 — block-aware): when the Scene has an
    expected slot structure, the asset's ``part_index`` selects the
    CANDIDATE slot, and then part identity must AGREE:

    * a block-derived slot (``slot.block_id`` set) qualifies ONLY an
      asset carrying the SAME ``block_id`` — ``part_index`` alone is
      NOT sufficient. A positional line-up across structurally
      different generation plans (blocks edited / re-split / rebuilt
      so the block identities differ) previously let OLD audio be
      matched to NEW slots: derived coverage reported the new
      structure as covered and Combine could assemble old audio under
      the new structure (the audited data-integrity reproduction);
    * a plain slot (``block_id`` None — plain / speaker-split part)
      qualifies only a plain asset (``block_id`` None): positional
      identity is the whole part identity in the plain model, and a
      block-aware asset on the other side is evidence of a different
      structure;
    * a mismatch on either side returns None — a modern block-aware
      asset never silently downgrades to a weaker positional
      comparison when the stronger one failed.

    A candidate position that no longer exists (the structure shrank)
    also yields None for slotted scenes: the asset is from a different
    structure and must not be re-anchored by the legacy synthesis.

    LEGACY scope (scene without an expected slot structure, or
    ``scene=None`` — pre-P3.28 scenes, external tools): a block asset
    maps to ``{block_id}:{part_index}`` and a plain asset to
    ``plain:{part_index}`` — degraded but unambiguous; such scenes use
    legacy coverage anyway. This is the documented compatibility path
    for block-less historical records: it is preserved verbatim.
    """
    if not isinstance(asset, dict):
        return None
    part_index = asset.get("part_index")
    block_id = asset.get("block_id")
    if part_index is None and not block_id:
        return None  # full-scene asset (single Generate) — not a slot
    if scene is not None:
        slots_by_index = _slot_by_part_index(scene)
        if slots_by_index:
            try:
                slot = slots_by_index.get(int(part_index))
            except (TypeError, ValueError):
                slot = None
            if slot is None:
                # The position does not exist in the current structure —
                # the asset is from a structurally different plan.
                return None
            slot_block = slot.get("block_id")
            if slot_block is not None:
                # Block-derived slot: the SAME block identity is
                # mandatory (P3.44.5 §8).
                if block_id is not None and block_id == slot_block:
                    return slot.get("slot_id")
                return None
            # Plain slot: positional identity, plain asset only.
            if block_id is None:
                return slot.get("slot_id")
            return None
    if block_id:
        try:
            return block_slot_id(block_id, int(part_index or 1))
        except (TypeError, ValueError):
            return None
    try:
        return plain_slot_id(int(part_index))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Slot → assets / coverage
# ---------------------------------------------------------------------------
def _valid_assets(scene: Any) -> List[Dict[str, Any]]:
    assets = getattr(scene, "audio_assets", None) or []
    return [a for a in assets if isinstance(a, dict)]


def assets_for_slot(scene: Any, slot_id: str) -> List[Dict[str, Any]]:
    """All successful assets of a slot, ordered by (part_version, list pos)."""
    matched = []
    for pos, asset in enumerate(_valid_assets(scene)):
        if slot_of_asset(asset, scene) == slot_id:
            version = asset.get("part_version")
            try:
                version = int(version) if version is not None else 0
            except (TypeError, ValueError):
                version = 0
            matched.append((version, pos, asset))
    matched.sort(key=lambda t: (t[0], t[1]))
    return [a for _v, _p, a in matched]


def is_slot_covered(scene: Any, slot_id: str) -> bool:
    """Has this slot ever been generated successfully? (§0 question 1)"""
    return bool(assets_for_slot(scene, slot_id))


def latest_asset_for_slot(scene: Any, slot_id: str) -> Optional[Dict[str, Any]]:
    """The highest-version asset of a slot (deterministic default)."""
    assets = assets_for_slot(scene, slot_id)
    return assets[-1] if assets else None


def resolved_asset_for_slot(scene: Any, slot_id: str) -> Optional[Dict[str, Any]]:
    """The asset a slot's audio resolves to: explicit selection, else latest.

    Selection map: ``Scene.selected_block_audio`` ({slot_id → asset_id}).
    A selection pointing at a missing asset falls back to latest (nothing
    is ever destroyed by a dangling selection).
    """
    selection = getattr(scene, "selected_block_audio", None) or {}
    wanted = selection.get(slot_id) if isinstance(selection, dict) else None
    if wanted:
        for asset in assets_for_slot(scene, slot_id):
            if asset.get("id") == wanted:
                return asset
    return latest_asset_for_slot(scene, slot_id)


def slot_versions(scene: Any, slot_id: str) -> List[Dict[str, Any]]:
    """Ordered version summary of a slot: [{part_version, id, duration,
    output_path, generated_at, generation_run}]."""
    out = []
    for asset in assets_for_slot(scene, slot_id):
        out.append({
            "part_version": asset.get("part_version"),
            "id": asset.get("id", ""),
            "duration": asset.get("duration", 0.0),
            "output_path": asset.get("output_path", ""),
            "generated_at": asset.get("generated_at", ""),
            "generation_run": asset.get("generation_run"),
            "speaker": asset.get("speaker"),
        })
    return out


# ---------------------------------------------------------------------------
# Block generated state — DERIVED, never stored (P3.28 §4 / Rec 5)
# ---------------------------------------------------------------------------
def block_slot_states(scene: Any) -> List[Dict[str, Any]]:
    """Per-block derived state for the Batch window.

    Returns one entry per block (in expected-slot order), each:
    {block_id, block_label, slots: [slot_id...], covered, total,
     generated, version_count}. A block is ``generated`` iff EVERY
    currently-expected slot of that block has ≥ 1 successful AudioAsset.
    Plain parts are grouped under block_id=None (label "Plain narration").
    """
    blocks: "Dict[Any, Dict[str, Any]]" = {}
    order: List[Any] = []
    for slot in (getattr(scene, "expected_audio_slots", None) or []):
        if not isinstance(slot, dict):
            continue
        key = slot.get("block_id")
        if key not in blocks:
            blocks[key] = {
                "block_id": key,
                "block_label": (slot.get("block_label")
                                or ("Plain narration" if not key else "Block")),
                "slots": [],
                "covered": 0,
                "total": 0,
                "version_count": 0,
            }
            order.append(key)
        entry = blocks[key]
        slot_id = slot.get("slot_id", "")
        entry["slots"].append(slot_id)
        entry["total"] += 1
        if is_slot_covered(scene, slot_id):
            entry["covered"] += 1
        entry["version_count"] += len(assets_for_slot(scene, slot_id))
    out = []
    for key in order:
        entry = blocks[key]
        entry["generated"] = (entry["covered"] == entry["total"]
                              and entry["total"] > 0)
        out.append(entry)
    return out


# ---------------------------------------------------------------------------
# Scene completeness — pure function (P3.28 §5 / Rec 7)
# ---------------------------------------------------------------------------
def coverage_counts(scene: Any) -> Tuple[int, int]:
    """(covered_slots, total_expected_slots).

    Legacy scenes (no expected_audio_slots ever materialised): covered
    ⟺ ≥ 1 asset exists — preserving today's practical semantics until
    the first new-model batch preview materialises slots (Rec 24).
    """
    slots = [s for s in (getattr(scene, "expected_audio_slots", None) or [])
             if isinstance(s, dict)]
    if not slots:
        return (1 if _valid_assets(scene) else 0), 1
    covered = sum(1 for s in slots if is_slot_covered(scene, s["slot_id"]))
    return covered, len(slots)


def scene_coverage_state(scene: Any, generating: bool = False,
                         last_run_failed: bool = False) -> str:
    """Derive the Scene completeness state (never eagerly mutated).

    Args:
        scene: the Scene model object.
        generating: True while a generation run is in flight for this
            Scene (set at batch start, cleared at batch end).
        last_run_failed: True when the most recent completed run for this
            Scene had ≥ 1 failure (used only for the ERROR state).
    """
    if generating:
        return SCENE_GENERATING
    covered, total = coverage_counts(scene)
    if total > 0 and covered == total:
        return SCENE_COMPLETE
    if covered > 0:
        return SCENE_PARTIAL
    # Zero coverage.
    if last_run_failed:
        return SCENE_ERROR
    return SCENE_NOT_GENERATED


def recompute_scene_status(scene: Any, generating: bool = False,
                           last_run_failed: bool = False) -> str:
    """Recompute + store the Scene's derived status. Returns the new value.

    The persisted ``Scene.status`` field keeps the same key with the new
    derived value set (Rec 7), so existing sidebar badges keep working.
    """
    state = scene_coverage_state(scene, generating=generating,
                                 last_run_failed=last_run_failed)
    try:
        scene.status = state
    except Exception:  # pragma: no cover - defensive (plain dict scenes)
        pass
    return state


def normalize_legacy_status(status: str) -> str:
    """Map legacy status strings onto the P3.28 vocabulary (tolerant)."""
    if status in SCENE_STATES:
        return status
    return _LEGACY_STATUS_MAP.get(status or "", SCENE_NOT_GENERATED)


def recompute_all_scene_status(project: Any) -> None:
    """Recompute every Scene's derived status (scene load / project save)."""
    scenes = getattr(project, "scenes", None) or []
    for scene in scenes:
        # A persisted "error" with zero coverage stays ERROR (last run
        # had failures); any other legacy value recomputes honestly.
        failed_hint = (normalize_legacy_status(getattr(scene, "status", ""))
                       == SCENE_ERROR)
        recompute_scene_status(scene, last_run_failed=failed_hint)


# ---------------------------------------------------------------------------
# Generation run identity (P3.28 §6 / Rec 4) — metadata, no registry
# ---------------------------------------------------------------------------
_RUN_RE = re.compile(r"-r(\d+)$")


def allocate_generation_run(scene: Any) -> str:
    """Allocate the next scene-scoped run id: ``{scene8}-r{NNN}``.

    Monotonic max+1 over run ids recorded on the Scene's assets and
    combined outputs. Runs are reconstructible by grouping on
    ``generation_run`` — no run registry exists.
    """
    scene_id = getattr(scene, "id", "") or "scene"
    max_seq = 0
    for asset in _valid_assets(scene):
        run = asset.get("generation_run") or ""
        m = _RUN_RE.search(str(run))
        if m:
            max_seq = max(max_seq, int(m.group(1)))
    for entry in (getattr(scene, "combined_outputs", None) or []):
        if not isinstance(entry, dict):
            continue
        run = entry.get("generation_run") or ""
        m = _RUN_RE.search(str(run))
        if m:
            max_seq = max(max_seq, int(m.group(1)))
    return "{0}-r{1:03d}".format(str(scene_id)[:8], max_seq + 1)


# ---------------------------------------------------------------------------
# Per-slot version allocation (P3.28 §7 / Rec 6) — monotonic max+1,
# disk-guarded; never overwrites, never reuses a live filename
# ---------------------------------------------------------------------------
def next_slot_version(scene: Any, slot_id: str, outputs_dir: str,
                      project: str, scene_name: str, part_index: int,
                      speaker: Optional[str] = None,
                      scene_id: Optional[str] = None
                      ) -> Tuple[str, int]:
    """Allocate (filename, version) for the next generation of a slot.

    Start version = 1 + max(part_version among the slot's assets)
    (asset-aware: deleted files never cause version reuse), then the
    output_guard disk-guard bumps upward while the filename exists.
    """
    from engine.output_guard import next_free_part_filename
    start = 1
    for asset in assets_for_slot(scene, slot_id):
        try:
            v = int(asset.get("part_version") or 0)
        except (TypeError, ValueError):
            v = 0
        start = max(start, v + 1)
    return next_free_part_filename(
        outputs_dir, project=project, scene=scene_name,
        part_index=part_index, speaker=speaker, scene_id=scene_id,
        start_version=start)


# ---------------------------------------------------------------------------
# Scene combined outputs (P3.28 §13 / Rec 13–14)
# ---------------------------------------------------------------------------
def build_scene_combined_filename(project: str, scene_name: str,
                                  version: int,
                                  scene_id: Optional[str] = None) -> str:
    """``{Proj}_{Scene}_Combined_v{NN}_{scene8}.wav`` (Rec 2 convention)."""
    from engine.output_guard import normalize_name_component
    base = "{0}_{1}_Combined_v{2:02d}".format(
        normalize_name_component(project or "Project"),
        normalize_name_component(scene_name or "Scene"),
        max(1, int(version)))
    if scene_id:
        base += "_{0}".format(str(scene_id)[:8])
    return base + ".wav"


def next_scene_combined_version(scene: Any, outputs_dir: str,
                                project: str, scene_name: str,
                                scene_id: Optional[str] = None
                                ) -> Tuple[str, int]:
    """Allocate the next Scene Combined version (max+1, disk-guarded)."""
    version = 1
    for entry in (getattr(scene, "combined_outputs", None) or []):
        if not isinstance(entry, dict):
            continue
        try:
            v = int(entry.get("version") or 0)
        except (TypeError, ValueError):
            v = 0
        version = max(version, v + 1)
    while True:
        filename = build_scene_combined_filename(
            project, scene_name, version, scene_id=scene_id)
        if not os.path.exists(os.path.join(outputs_dir, filename)):
            return filename, version
        version += 1


def is_scene_combined_stale(scene: Any, entry: Dict[str, Any],
                            app_root: str = "") -> Tuple[bool, List[str]]:
    """Staleness of a Scene combined output = "no longer represents the
    CURRENT Scene composition" (accepted follow-up R1 + R2).

    stale ⟺
      R2  the recorded slot set ≠ the Scene's current expected slot set
          (the structure changed since the snapshot), OR
      R1  any recorded source's asset id ≠ that slot's currently RESOLVED
          asset id (``resolved_asset_for_slot`` — explicit per-slot
          selection, else latest). A deliberately curated OLDER version
          IS the composition and is therefore NOT stale; a newer version
          merely existing while an older one is selected is NOT staleness, OR
      a referenced source file is missing from disk.

    Stale never means "bad" or "unusable": the snapshot remains
    explicitly selectable (Use Anyway). No timestamps involved (immune
    to defect D-4). Returns (stale, reasons).
    """
    reasons: List[str] = []
    if not isinstance(entry, dict):
        return True, ["malformed combined output entry"]
    expected = [s for s in (getattr(scene, "expected_audio_slots", None) or [])
                if isinstance(s, dict)]
    expected_ids = {s.get("slot_id", "") for s in expected}
    recorded = [s for s in (entry.get("sources") or [])
                if isinstance(s, dict)]
    recorded_ids = {s.get("slot_id", "") for s in recorded}

    # R2 — structural check: the snapshot's slot set must match the
    # Scene's current expected slot set EXACTLY (added or removed slots
    # both count: a snapshot missing a new block's audio must never
    # silently pass as the complete Scene).
    if recorded_ids != expected_ids:
        reasons.append(
            "Scene structure has changed since this Combined output "
            "({0} parts expected, {1} in snapshot)".format(
                len(expected_ids), len(recorded_ids)))

    # R1 — resolved-identity check for every slot that is STILL expected
    # (removed slots are covered by the structural reason above).
    recorded_by_slot: Dict[str, Dict[str, Any]] = {
        s.get("slot_id", ""): s for s in recorded}
    for slot_id in sorted(expected_ids):
        source = recorded_by_slot.get(slot_id)
        if source is None:
            continue  # newly added slot — covered by R2
        label = (source.get("block_label")
                 or source.get("speaker") or slot_id)
        resolved = resolved_asset_for_slot(scene, slot_id)
        if resolved is None:
            # Expected slot with no successful asset at all (hand-edited
            # model or files removed from a copy) — the snapshot cannot
            # represent the current composition.
            reasons.append(
                "'{0}' has no generated audio".format(label))
            continue
        resolved_id = str(resolved.get("id") or "")
        recorded_asset_id = source.get("asset_id")
        current_v = _as_int(resolved.get("part_version"))
        recorded_v = _as_int(source.get("part_version"))
        if recorded_asset_id:
            # Strict identity (real lineage always records asset_id).
            matches = (resolved_id == str(recorded_asset_id))
        else:
            # Tolerant fallback for hand-written / legacy lineage entries
            # that recorded no asset identity: version equality.
            matches = (current_v == recorded_v)
        if matches:
            # Identity matches: only a missing file can still stale it.
            if app_root and resolved.get("output_path"):
                if not _file_exists(app_root, resolved.get("output_path")):
                    reasons.append(
                        "source file missing: {0}".format(os.path.basename(
                            str(resolved.get("output_path")))))
            continue
        # Identity differs — describe the difference by version.
        if current_v > recorded_v:
            # P3.30(f): reason wording per the user's approved copy
            # ("B1 has a newer version: v02") — colon, no parentheses.
            reasons.append(
                "{0} has a newer version: v{1:02d}".format(
                    label, current_v))
        elif current_v < recorded_v:
            # The user deliberately selected an OLDER version than the
            # snapshot contains — the composition moved below it.
            reasons.append(
                "{0}'s selected version (v{1:02d}) differs from this "
                "Combined output (v{2:02d})".format(
                    label, current_v, recorded_v))
        else:
            reasons.append(
                "{0}'s selected audio differs from this Combined "
                "output".format(label))
    return bool(reasons), reasons


def _file_exists(app_root: str, rel_path: Any) -> bool:
    if not rel_path or not isinstance(rel_path, str):
        return False
    full = rel_path if os.path.isabs(rel_path) else os.path.join(
        app_root, rel_path)
    return os.path.isfile(full)


def build_scene_combined_entry(
    scene: Any, version: int, output_path: str, duration: float,
    sources: List[Dict[str, Any]], silence_ms: int,
    generation_run: Optional[str], project_name: str = "",
) -> Dict[str, Any]:
    """Build a Scene.combined_outputs entry with per-slot lineage (§13).

    ``sources`` items carry the resolved per-slot lineage:
    {slot_id, block_id, block_label, part_index, asset_id, part_version,
     speaker, character_id, output_path, duration}.
    """
    import uuid
    return {
        "id": str(uuid.uuid4())[:12],
        "version": int(version),
        "output_path": output_path,
        "duration": float(duration or 0.0),
        "slot_count": len(sources),
        "silence_ms": int(silence_ms),
        "sources": [dict(s) for s in sources],
        "created_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "generation_run": generation_run,
        "project_name": project_name,
    }


def resolved_slot_sources(scene: Any, app_root: str) -> Tuple[
        List[Dict[str, Any]], List[str]]:
    """Resolve the per-slot audio sources for a Scene Combine operation.

    Uses ``resolved_asset_for_slot`` (explicit per-slot selection, else
    latest version) for EVERY expected slot in slot order. Combined
    outputs are structurally NEVER sources (double-inclusion protection,
    P3.28 §14): the combine input set is exactly the expected slot set.

    Returns (sources, blockers). ``blockers`` names uncovered slots and
    missing files — the caller must NOT silently skip them (§52).
    """
    slots = [s for s in (getattr(scene, "expected_audio_slots", None) or [])
             if isinstance(s, dict)]
    sources: List[Dict[str, Any]] = []
    blockers: List[str] = []
    for slot in slots:
        slot_id = slot.get("slot_id", "")
        label = (slot.get("block_label")
                 or (slot.get("speaker") if slot.get("speaker") else "")
                 or "Part {0}".format(slot.get("part_index", "?")))
        asset = resolved_asset_for_slot(scene, slot_id)
        if asset is None:
            blockers.append("'{0}' has no generated audio yet".format(label))
            continue
        rel = asset.get("output_path", "")
        if not rel or not _file_exists(app_root, rel):
            blockers.append(
                "'{0}': audio file missing ({1})".format(label, rel or "?"))
            continue
        sources.append({
            "slot_id": slot_id,
            "block_id": slot.get("block_id"),
            "block_label": slot.get("block_label", ""),
            "part_index": slot.get("part_index"),
            "asset_id": asset.get("id", ""),
            "part_version": asset.get("part_version"),
            "speaker": asset.get("speaker") or slot.get("speaker") or None,
            "character_id": asset.get("character_id")
                            or slot.get("character_id") or None,
            "output_path": rel,
            "duration": asset.get("duration", 0.0),
        })
    return sources, blockers


# ---------------------------------------------------------------------------
# Complete-scene asset proof + Scene output resolution
# (P3.28 §15 correction + accepted follow-up safety rule)
# ---------------------------------------------------------------------------
# Legacy part-file naming conventions (pre-P3.28 Generate Long parts:
# "{Proj}_{Scene}_Part_NN..." / P3.28+ "..._Long_vNN_Part_NNN...").
# Used ONLY as the NEGATIVE proof below — identity always lives in the
# model ids, never parsed from filenames (design record §20).
_LEGACY_PART_NAME_RE = re.compile(r"_part_\d+|_long_v\d+", re.IGNORECASE)


def is_complete_scene_asset(asset: Any) -> bool:
    """True only when the asset is PROVABLY a full-Scene output, never a Part.

    Proof: the asset carries NO slot provenance (no block_id, no
    part_index, no part_version — i.e. it was produced by a full-scene
    Single Generate or a pre-P3.28 generation) AND its filename does
    not carry a legacy part marker ("_Part_NN" / "_Long_vNN").

    A Part AudioAsset NEVER passes this proof — no exceptions, not even
    for a Scene with a single slot. Automatic resolution must never
    present one Part as the whole Scene's audio (accepted follow-up
    safety rule).
    """
    if not isinstance(asset, dict):
        return False
    if (asset.get("block_id")
            or asset.get("part_index") is not None
            or asset.get("part_version") is not None):
        return False  # explicit slot provenance — it is a Part
    name = os.path.basename(str(asset.get("output_path") or ""))
    if _LEGACY_PART_NAME_RE.search(name):
        return False  # legacy part-file naming — it is a Part
    return True


def resolve_scene_output(scene: Any, app_root: str = "") -> Dict[str, Any]:
    """Resolve which audio represents this Scene (the ONLY thing Project
    Assembly consumes).

    Resolution chain (P3.28 §15 + accepted follow-up safety rule):
      1. explicit ``Scene.selected_output`` (kind+id) when it resolves to
         an existing file → label "Selected · CUSTOM". An explicitly
         selected STALE output is honoured but flagged (the user chose it
         deliberately and receives a clear warning).
      2. latest NON-STALE COMPLETE Scene Combined output → label
         "Combined vNN" (completeness is guaranteed by the structural
         staleness check R2: a snapshot whose slot set differs from the
         current expected slots is stale).
      3. a PROVABLY complete full-scene asset (``is_complete_scene_asset``)
         — LEGACY scope ONLY: Scenes that have NO combined outputs at
         all. A single Part AudioAsset is NEVER treated as the complete
         Scene output.
      If candidates exist but ALL are stale, missing, or only Parts →
      ``requires_review=True`` — the Scene requires user review; the
      application must NEVER silently assemble stale or partial audio.

    Returns a dict: {kind, id, path (absolute or ""), rel_path, label,
    stale, missing, custom, requires_review, reason, entry?}.
    """
    result: Dict[str, Any] = {
        "kind": None, "id": "", "path": "", "rel_path": "", "label": "",
        "stale": False, "missing": False, "custom": False,
        "requires_review": False, "reason": "",
    }
    combined = [e for e in (getattr(scene, "combined_outputs", None) or [])
                if isinstance(e, dict)]

    # --- Rule 1: explicit selection -----------------------------------
    selected = getattr(scene, "selected_output", None)
    if isinstance(selected, dict) and selected.get("kind") and selected.get("id"):
        kind = selected.get("kind")
        want_id = selected.get("id")
        if kind == KIND_SCENE_COMBINED:
            for entry in combined:
                if entry.get("id") == want_id:
                    rel = entry.get("output_path", "")
                    if _file_exists(app_root, rel):
                        stale, reasons = is_scene_combined_stale(
                            scene, entry, app_root)
                        result.update({
                            "kind": KIND_SCENE_COMBINED, "id": want_id,
                            "rel_path": rel, "path": _abs(app_root, rel),
                            "label": "Selected \u00b7 Combined v{0:02d}".format(
                                _as_int(entry.get("version"))),
                            "custom": True, "stale": stale,
                            "reason": ("; ".join(reasons) if stale else ""),
                            "entry": entry,
                        })
                        return result
                    # selected but file gone → fall through to review
                    result["requires_review"] = True
                    result["reason"] = (
                        "The selected combined output's file is missing.")
                    break
        elif kind == KIND_ASSET:
            for asset in reversed(_valid_assets(scene)):
                if asset.get("id") == want_id:
                    rel = asset.get("output_path", "")
                    if _file_exists(app_root, rel):
                        result.update({
                            "kind": KIND_ASSET, "id": want_id,
                            "rel_path": rel, "path": _abs(app_root, rel),
                            "label": "Selected \u00b7 CUSTOM",
                            "custom": True, "stale": False,
                            "entry": asset,
                        })
                        return result
                    result["requires_review"] = True
                    result["reason"] = (
                        "The selected audio asset's file is missing.")
                    break

    # --- Rule 2: latest NON-STALE Scene Combined -----------------------
    # Scan versions newest-first; skip stale or missing entries.
    ordered = sorted(
        combined, key=lambda e: _as_int(e.get("version")), reverse=True)
    stale_candidates: List[str] = []
    for entry in ordered:
        rel = entry.get("output_path", "")
        if not rel or not _file_exists(app_root, rel):
            continue
        stale, reasons = is_scene_combined_stale(scene, entry, app_root)
        if stale:
            stale_candidates.append("; ".join(reasons) or "stale")
            continue
        result.update({
            "kind": KIND_SCENE_COMBINED, "id": entry.get("id", ""),
            "rel_path": rel, "path": _abs(app_root, rel),
            "label": "Combined v{0:02d}".format(
                _as_int(entry.get("version"))),
            "entry": entry,
        })
        return result

    # P3.30(g) AUDIT CORRECTION: a Scene that HAS combined outputs but
    # whose combined outputs are ALL stale/missing must NEVER silently
    # fall through to Rule 3. For block-based Scenes the latest
    # audio_asset is ONE PART's WAV — the old fall-through made Project
    # Assembly consume a single part as the whole Scene's audio without
    # a word (the "behaves strangely" report). Such a Scene requires
    # review: the user either re-combines (one click) or explicitly
    # chooses the stale combined output (Use Anyway). Rule 3 remains
    # EXACTLY as designed for its purpose: LEGACY scenes that have no
    # combined outputs at all.
    if combined:
        result["requires_review"] = True
        if not result["reason"]:
            if stale_candidates:
                result["reason"] = (
                    "All combined outputs are stale: "
                    + " | ".join(stale_candidates[:3]))
            else:
                result["reason"] = (
                    "All combined output files are missing from disk.")
        return result

    # --- Rule 3: PROVABLE full-scene asset (LEGACY scope ONLY: Scenes
    # that have no combined outputs at all — never a fallback from a
    # failed Combined resolution). Accepted safety rule: an asset
    # resolves here only when it is provably a COMPLETE-Scene output;
    # a single Part AudioAsset is NEVER the Scene's audio.
    if not combined:
        for asset in reversed(_valid_assets(scene)):
            if not is_complete_scene_asset(asset):
                continue
            rel = asset.get("output_path", "")
            if not rel or not _file_exists(app_root, rel):
                continue
            result.update({
                "kind": KIND_ASSET, "id": asset.get("id", ""),
                "rel_path": rel, "path": _abs(app_root, rel),
                "label": "Latest audio", "entry": asset,
            })
            return result

    # --- All candidates stale, missing, or only Parts → user review ----
    result["requires_review"] = True
    if not result["reason"]:
        if stale_candidates:
            result["reason"] = (
                "All combined outputs are stale: "
                + " | ".join(stale_candidates[:3]))
        elif combined:
            result["reason"] = (
                "All combined output files are missing from disk.")
        elif _valid_assets(scene):
            if any(is_complete_scene_asset(a) for a in _valid_assets(scene)):
                result["reason"] = (
                    "All Scene audio files are missing from disk.")
            else:
                result["reason"] = (
                    "This Scene has only part audio — a Part is never used "
                    "as the Scene output automatically. Combine the Scene, "
                    "or choose an output explicitly.")
        else:
            result["reason"] = "This Scene has no generated audio yet."
    return result


def _abs(app_root: str, rel: str) -> str:
    return rel if os.path.isabs(rel) else os.path.join(app_root, rel)


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# THE ONE registration writer (P3.28 §23 / design record §6)
# ---------------------------------------------------------------------------
def register_generation_result(project: Any, result: Any,
                               scene_fallback: Any = None,
                               ) -> Optional[Dict[str, Any]]:
    """Register a successful generation result — the ONLY asset writer.

    - Resolves the Scene by ``result.scene_id`` (never by whichever Scene
      happens to be active) — closes defect D-3. Falls back to
      ``scene_fallback`` only for legacy results without a scene_id.
    - Appends the asset dict (with block_id / part_index / part_version /
      generation_run provenance) to that Scene's append-only stream.
    - Recomputes that Scene's derived coverage state (D-2: the Scene
      never flips to COMPLETE merely because a part finished — with a run
      in flight the caller passes generating=True).

    Returns the appended asset dict, or None when the result has no Scene
    context (single generations outside any Scene).
    """
    scene = None
    scene_id = getattr(result, "scene_id", None)
    if scene_id and project is not None:
        scene = project.get_scene(scene_id)
    if scene is None and scene_fallback is not None:
        scene = scene_fallback
    if scene is None:
        return None

    import uuid
    asset = {
        "id": str(uuid.uuid4())[:12],
        "scene_id": getattr(scene, "id", ""),
        "output_path": getattr(result, "output_path", "") or "",
        "duration": float(getattr(result, "output_duration", 0.0) or 0.0),
        "speaker": getattr(result, "speaker", None),
        "character_id": getattr(result, "character_id", None),
        "voice_profile_id": (
            result.voice_profile.id if getattr(result, "voice_profile", None)
            else None),
        "generated_at": getattr(result, "timestamp", None),
        # P3.28 provenance (additive; None for legacy full-scene assets):
        "block_id": getattr(result, "block_id", None),
        "part_index": getattr(result, "part_index", None),
        "part_version": getattr(result, "part_version", None),
        "generation_run": getattr(result, "generation_run", None),
    }
    assets = getattr(scene, "audio_assets", None)
    if assets is None:
        scene.audio_assets = assets = []
    assets.append(asset)

    # Recompute the derived coverage state. While a batch run is in
    # flight for this Scene the state must stay GENERATING (D-2); the
    # caller signals this via the scene's status already being
    # "generating" — preserve it, the final recompute happens at batch
    # end.
    generating = (normalize_legacy_status(
        getattr(scene, "status", "")) == SCENE_GENERATING)
    failed_hint = (normalize_legacy_status(
        getattr(scene, "status", "")) == SCENE_ERROR)
    recompute_scene_status(scene, generating=generating,
                           last_run_failed=failed_hint)
    logger.info(
        "P3.28: asset registered on scene '%s' (path=%s, part=%s v%s, "
        "run=%s) -> status=%s",
        getattr(scene, "name", "?"), asset["output_path"],
        asset["part_index"], asset["part_version"],
        asset["generation_run"], getattr(scene, "status", "?"))
    return asset


# ---------------------------------------------------------------------------
# Timestamp parsing (D-4 fix — used by combined_audio.is_combined_output_stale)
# ---------------------------------------------------------------------------
_TS_FORMATS = ("%Y-%m-%dT%H:%M:%S", "%Y%m%d_%H%M%S")


def parse_any_timestamp(value: Any) -> Optional[datetime]:
    """Parse an ISO or compact timestamp; None when unparseable.

    Fixes defect D-4: ``is_combined_output_stale`` previously compared an
    ISO ``created_at`` against a compact ``generated_at`` LEXICOGRAPHICALLY
    (always True for any non-empty value).
    """
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    for fmt in _TS_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None
