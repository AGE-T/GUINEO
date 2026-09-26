"""GUINEO P3.43 — VOICE PROFILE UNIFICATION runtime tests.

Covers the P3.43 implementation contract (§33):
  * Engine facade completeness + VOICE_CHANGED on every mutation
  * Speaker metadata set AND clear semantics
  * Avatar path convention (app-root-relative) + legacy migration + rendering
  * Transactional import (no partial state) + validation-before-replacement
  * Export / re-import round-trip (portable: stored paths never trusted)
  * Path traversal safety
  * The single editor (Create / Import / Edit — one architecture)
  * The unified Voice Profiles manager (list/detail, keyboard navigation,
    Play, dependency-aware delete, live VOICE_CHANGED refresh)
  * Entry point routing (Tools → Voice Profiles + Ctrl+L, File Import/Export,
    Advanced Import/Create, Friendly Change → lightweight picker,
    Character "Manage Voice Profiles…")
  * Character / Scene / RightPanel integration + refresh without restart
  * No ghost state from programmatic refreshes
  * The complete golden path (§26) on a real MainWindow

Run:
    LD_LIBRARY_PATH=/tmp QT_QPA_PLATFORM=offscreen python3 -m pytest \\
        tests/test_p3_43_voice_unification.py -v
"""
import os
import sys
import json
import shutil
import struct
import tempfile
import types
import unittest
import wave
from unittest.mock import patch, PropertyMock, MagicMock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# Fake torch/transformers machinery (not installed in the test env).
for _mod in ("torch", "transformers"):
    try:
        __import__(_mod)
    except ImportError:
        sys.modules.setdefault(_mod, types.ModuleType(_mod))

# P3.44.4 (test-env hygiene): a BARE torch stub satisfies THIS file, but
# poisons the process for any LATER-imported test module whose harness
# drives real generations — engine.generation_manager calls
# torch.no_grad() inside generate_speech(), and those modules'
# sys.modules.setdefault("torch", rich_fake) is then a NO-OP (the stub
# is already present). Runtime-verified cross-file interference:
# p3_43 + p3_44_1 in ONE process failed 11 generation tests with
# "module 'torch' has no attribute 'no_grad'". Enrich the stub with
# the standard house-fake no-ops so ANY import order works.
_torch_stub = sys.modules.get("torch")
if _torch_stub is not None and not hasattr(_torch_stub, "no_grad"):
    class _StubNoGrad:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    _torch_stub.no_grad = lambda: _StubNoGrad()
    _torch_stub.manual_seed = lambda _s: None
    _torch_stub.from_numpy = lambda arr: arr
    _torch_stub.cuda = types.SimpleNamespace(is_available=lambda: False)

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton, \
    QLabel, QComboBox, QWidget
from PySide6.QtTest import QTest
from PySide6.QtGui import QImage, QColor

_app = QApplication.instance() or QApplication([])

from engine.engine import Engine
from engine.events import EventType
from engine.models import Character, Project, Scene
from engine.errors import InvalidVoice
from engine.voice_manager import is_safe_profile_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_wav(path, seconds=1.5, rate=16000):
    import math
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        n = int(seconds * rate)
        frames = bytearray()
        for i in range(n):
            v = int(12000 * math.sin(2 * math.pi * 440 * i / rate))
            frames += struct.pack("<h", v)
        w.writeframes(bytes(frames))


def _make_png(path, color=(150, 80, 200)):
    img = QImage(32, 32, QImage.Format.Format_RGB32)
    img.fill(QColor(*color))
    img.save(path)


class _EventLog:
    """Collects VOICE_CHANGED events from an engine."""

    def __init__(self, engine):
        self.events = []
        self._engine = engine
        engine.subscribe(EventType.VOICE_CHANGED, self._cb)

    def _cb(self, event):
        self.events.append(getattr(event, "data", None))

    def count(self):
        return len(self.events)

    def detach(self):
        try:
            self._engine.unsubscribe(EventType.VOICE_CHANGED, self._cb)
        except Exception:
            pass


class _FakeMB:
    """Fake QMessageBox for modal flows (records every call)."""
    StandardButton = QMessageBox.StandardButton
    question_result = QMessageBox.StandardButton.Yes
    calls = []

    @staticmethod
    def question(*a, **k):
        _FakeMB.calls.append(("question", a[2] if len(a) > 2 else None))
        return _FakeMB.question_result

    @staticmethod
    def warning(*a, **k):
        _FakeMB.calls.append(("warning", a[2] if len(a) > 2 else None))
        return QMessageBox.StandardButton.Ok

    @staticmethod
    def information(*a, **k):
        _FakeMB.calls.append(("information", a[2] if len(a) > 2 else None))
        return QMessageBox.StandardButton.Ok

    @staticmethod
    def critical(*a, **k):
        _FakeMB.calls.append(("critical", a[2] if len(a) > 2 else None))
        return QMessageBox.StandardButton.Ok

    @staticmethod
    def reset():
        _FakeMB.calls = []
        _FakeMB.question_result = QMessageBox.StandardButton.Yes


class _FakeFD:
    """Fake QFileDialog for export/replace flows."""
    existing_dir = ""
    open_path = ""

    @staticmethod
    def getExistingDirectory(*a, **k):
        return _FakeFD.existing_dir

    @staticmethod
    def getOpenFileName(*a, **k):
        return (_FakeFD.open_path, "")

    @staticmethod
    def reset(dir_="", path=""):
        _FakeFD.existing_dir = dir_
        _FakeFD.open_path = path


# ===========================================================================
# 1. ENGINE FACADE — complete public voice API + VOICE_CHANGED
# ===========================================================================
class TestEngineFacade(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ss_p343_f_")
        self.engine = Engine(app_root=self.tmp)
        self.log = _EventLog(self.engine)
        self.work = tempfile.mkdtemp(prefix="ss_p343_w_")
        self.wav = os.path.join(self.work, "ref.wav")
        _make_wav(self.wav, 1.5)
        self.png = os.path.join(self.work, "avatar.png")
        _make_png(self.png)

    def tearDown(self):
        self.log.detach()
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(self.work, ignore_errors=True)

    # --- presence -----------------------------------------------------
    def test_facade_is_complete(self):
        """§5: every required voice operation exists on the public facade."""
        for name in ("list_voices", "get_voice", "create_voice", "delete_voice",
                     "import_voice_reference", "import_voice_profile",
                     "import_voice_profile_dir", "rename_voice",
                     "update_voice_transcript", "update_voice_details",
                     "import_voice_avatar", "remove_voice_avatar",
                     "set_voice_speaker_metadata", "export_voice_profile",
                     "validate_voice_profile", "voice_reference_path",
                     "resolve_voice_asset"):
            self.assertTrue(callable(getattr(self.engine, name, None)),
                            "Engine facade missing: {0}".format(name))
        self.assertTrue(os.path.isdir(getattr(self.engine, "app_root")))

    # --- create / import ----------------------------------------------
    def test_create_voice_emits_voice_changed(self):
        v = self.engine.create_voice("Solo")
        self.assertGreaterEqual(self.log.count(), 1)

    def test_transactional_import_persists_everything(self):
        v = self.engine.import_voice_profile(
            "Anna", wav_path=self.wav, transcript="hello",
            avatar_path=self.png, gender="Female", age_range="30s",
            mood="Warm", description="desc", tags=["a", "b"])
        got = self.engine.get_voice(v.id)
        self.assertEqual(got.name, "Anna")
        self.assertEqual(got.gender, "Female")
        self.assertEqual(got.age_range, "30s")
        self.assertEqual(got.mood, "Warm")
        self.assertEqual(got.description, "desc")
        self.assertEqual(got.tags, ["a", "b"])
        self.assertEqual(got.reference_transcript, "hello")
        self.assertEqual(got.sample_rate, 16000)
        self.assertEqual(got.channels, 1)
        self.assertTrue(got.has_reference)
        self.assertGreaterEqual(self.log.count(), 1)

    def test_transactional_import_rollback_on_bad_avatar(self):
        """§9: a failure at ANY stage leaves no partial profile."""
        before = {p.id for p in self.engine.list_voices()}
        bad_bmp = os.path.join(self.work, "x.bmp")
        QImage(8, 8, QImage.Format.Format_RGB32).save(bad_bmp)
        with self.assertRaises(InvalidVoice):
            self.engine.import_voice_profile(
                "Broken", wav_path=self.wav, avatar_path=bad_bmp,
                gender="Male")
        after = {p.id for p in self.engine.list_voices()}
        self.assertEqual(after, before, "partial profile left behind")
        # No orphan directory either.
        voices_dir = os.path.join(self.tmp, "voices")
        for d in os.listdir(voices_dir):
            self.assertNotIn(d, after - before)
            # nothing staged left behind
            for f in os.listdir(os.path.join(voices_dir, d)):
                self.assertFalse(f.endswith(".incoming"), f)

    def test_transactional_import_rejects_invalid_wav_preflight(self):
        not_wav = os.path.join(self.work, "not.wav")
        with open(not_wav, "wb") as fh:
            fh.write(b"plain text, not RIFF")
        before = {p.id for p in self.engine.list_voices()}
        with self.assertRaises(InvalidVoice):
            self.engine.import_voice_profile("Bad", wav_path=not_wav)
        self.assertEqual({p.id for p in self.engine.list_voices()}, before)

    # --- rename / transcript / details --------------------------------
    def test_rename_voice_stable_id_and_event(self):
        v = self.engine.create_voice("Anna")
        n0 = self.log.count()
        renamed = self.engine.rename_voice(v.id, "Anna R.")
        self.assertEqual(renamed.id, v.id, "id must stay stable on rename")
        self.assertEqual(renamed.name, "Anna R.")
        self.assertEqual(self.engine.get_voice(v.id).name, "Anna R.")
        self.assertGreater(self.log.count(), n0)

    def test_rename_voice_rejects_empty_name(self):
        v = self.engine.create_voice("Anna")
        with self.assertRaises(InvalidVoice):
            self.engine.rename_voice(v.id, "   ")

    def test_update_voice_transcript_persists_disk_and_event(self):
        v = self.engine.create_voice("Anna")
        n0 = self.log.count()
        self.engine.update_voice_transcript(v.id, "new text")
        self.assertEqual(self.engine.get_voice(v.id).reference_transcript,
                         "new text")
        with open(os.path.join(self.tmp, "voices", v.id, "transcript.txt"),
                  encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "new text")
        self.assertGreater(self.log.count(), n0)

    def test_update_voice_details_set_and_clear(self):
        v = self.engine.create_voice("Anna")
        self.engine.update_voice_details(v.id, "keep", ["x", "y"])
        got = self.engine.get_voice(v.id)
        self.assertEqual(got.description, "keep")
        self.assertEqual(got.tags, ["x", "y"])
        # Clear: empty string / empty list.
        self.engine.update_voice_details(v.id, "", [])
        got = self.engine.get_voice(v.id)
        self.assertEqual(got.description, "")
        self.assertEqual(got.tags, [])
        # None = unchanged.
        self.engine.update_voice_details(v.id, "d", ["t"])
        self.engine.update_voice_details(v.id, None, None)
        got = self.engine.get_voice(v.id)
        self.assertEqual(got.description, "d")
        self.assertEqual(got.tags, ["t"])

    # --- speaker metadata ---------------------------------------------
    def test_metadata_set_and_clear_semantics(self):
        """§7: an EXPLICIT empty value clears; None leaves unchanged."""
        v = self.engine.create_voice("Anna")
        self.engine.set_voice_speaker_metadata(
            v.id, gender="Female", age_range="30s", mood="Warm")
        # Clear gender explicitly.
        self.engine.set_voice_speaker_metadata(v.id, gender="")
        got = self.engine.get_voice(v.id)
        self.assertEqual(got.gender, "")
        self.assertEqual(got.age_range, "30s", "untouched arg must remain")
        # None leaves everything unchanged.
        self.engine.set_voice_speaker_metadata(v.id)
        got = self.engine.get_voice(v.id)
        self.assertEqual(got.age_range, "30s")
        self.assertEqual(got.mood, "Warm")
        # Set again + clear all three.
        self.engine.set_voice_speaker_metadata(
            v.id, gender="Male", age_range="60s", mood="Dark")
        self.engine.set_voice_speaker_metadata(v.id, "", "", "")
        got = self.engine.get_voice(v.id)
        self.assertEqual((got.gender, got.age_range, got.mood), ("", "", ""))

    def test_metadata_clear_reflected_in_profile_json(self):
        v = self.engine.create_voice("Anna")
        self.engine.set_voice_speaker_metadata(v.id, gender="Female")
        self.engine.set_voice_speaker_metadata(v.id, gender="")
        with open(os.path.join(self.tmp, "voices", v.id, "profile.json"),
                  encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data.get("gender", ""), "",
                         "profile.json must reflect the cleared state")

    def test_metadata_mutation_emits_voice_changed(self):
        v = self.engine.create_voice("Anna")
        n0 = self.log.count()
        self.engine.set_voice_speaker_metadata(v.id, gender="Male")
        self.assertGreater(self.log.count(), n0)

    # --- reference audio ----------------------------------------------
    def test_replace_reference_updates_metadata_keeps_transcript(self):
        v = self.engine.import_voice_profile(
            "Anna", wav_path=self.wav, transcript="keep me")
        wav2 = os.path.join(self.work, "ref2.wav")
        _make_wav(wav2, 2.5, rate=24000)
        n0 = self.log.count()
        got = self.engine.import_voice_reference(v.id, wav2, "")
        self.assertEqual(got.sample_rate, 24000)
        self.assertAlmostEqual(got.duration, 2.5, delta=0.02)
        self.assertEqual(got.reference_transcript, "keep me")
        self.assertGreater(self.log.count(), n0)

    def test_replace_reference_rejects_invalid_wav_and_keeps_old(self):
        v = self.engine.import_voice_profile(
            "Anna", wav_path=self.wav, transcript="t")
        not_wav = os.path.join(self.work, "bad.wav")
        with open(not_wav, "wb") as fh:
            fh.write(b"junk")
        with self.assertRaises(InvalidVoice):
            self.engine.import_voice_reference(v.id, not_wav, "")
        got = self.engine.get_voice(v.id)
        self.assertEqual(got.sample_rate, 16000,
                         "old reference must survive a failed replacement")
        self.assertTrue(os.path.isfile(
            os.path.join(self.tmp, "voices", v.id, "reference.wav")))

    def test_voice_reference_path_resolution(self):
        v = self.engine.import_voice_profile("Anna", wav_path=self.wav)
        path = self.engine.voice_reference_path(v.id)
        self.assertTrue(path and os.path.isfile(path))
        self.assertIn(v.id, path)
        empty = self.engine.create_voice("Empty")
        self.assertEqual(self.engine.voice_reference_path(empty.id), "")

    # --- avatar --------------------------------------------------------
    def test_avatar_path_is_app_root_relative(self):
        """§8: one authoritative convention (voices/<id>/avatar.png)."""
        v = self.engine.import_voice_profile(
            "Anna", wav_path=self.wav, avatar_path=self.png)
        expected = os.path.relpath(
            os.path.join(self.tmp, "voices", v.id, "avatar.png"), self.tmp)
        self.assertEqual(v.preview_image, expected)
        resolved = self.engine.resolve_voice_asset(v.preview_image)
        self.assertTrue(resolved and os.path.isfile(resolved))

    def test_avatar_roundtrip_renders(self):
        """§8: the avatar actually renders (correct Qt enum, no crash)."""
        from ui.widgets.avatar import make_rounded_pixmap, AvatarLabel
        v = self.engine.import_voice_profile(
            "Anna", wav_path=self.wav, avatar_path=self.png)
        pm = make_rounded_pixmap(
            self.engine.resolve_voice_asset(v.preview_image), 40, "Anna")
        self.assertIsNotNone(pm)
        self.assertFalse(pm.isNull())
        label = AvatarLabel(size=40)
        label.set_app_root(self.engine.app_root)
        label.set_voice(self.engine.get_voice(v.id))
        self.assertFalse(label.grab().isNull())

    def test_remove_voice_avatar(self):
        v = self.engine.import_voice_profile(
            "Anna", wav_path=self.wav, avatar_path=self.png)
        n0 = self.log.count()
        got = self.engine.remove_voice_avatar(v.id)
        self.assertEqual(got.preview_image, "")
        self.assertFalse(os.path.exists(
            os.path.join(self.tmp, "voices", v.id, "avatar.png")))
        self.assertGreater(self.log.count(), n0)

    def test_avatar_extension_whitelist_consistent(self):
        """§24: the engine rejects what the UI filter no longer offers."""
        bmp = os.path.join(self.work, "x.bmp")
        QImage(8, 8, QImage.Format.Format_RGB32).save(bmp)
        v = self.engine.create_voice("Anna")
        with self.assertRaises(InvalidVoice):
            self.engine.import_voice_avatar(v.id, bmp)

    def test_legacy_bare_avatar_path_migrates_on_load(self):
        """§22: an OLD profile (preview_image='avatar.png') still loads and
        its avatar resolves through the new convention."""
        v = self.engine.import_voice_profile(
            "Anna", wav_path=self.wav, avatar_path=self.png)
        # Hand-write the OLD format: bare filename.
        pdir = os.path.join(self.tmp, "voices", v.id)
        with open(os.path.join(pdir, "profile.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        data["preview_image"] = "avatar.png"
        with open(os.path.join(pdir, "profile.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(data, fh)
        got = self.engine.get_voice(v.id)
        expected = os.path.relpath(
            os.path.join(pdir, "avatar.png"), self.tmp)
        self.assertEqual(got.preview_image, expected,
                         "legacy value must be normalised on load")
        resolved = self.engine.resolve_voice_asset(got.preview_image)
        self.assertTrue(resolved and os.path.isfile(resolved))

    def test_legacy_bare_avatar_missing_file_clears(self):
        v = self.engine.import_voice_profile("Anna", wav_path=self.wav)
        pdir = os.path.join(self.tmp, "voices", v.id)
        with open(os.path.join(pdir, "profile.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        data["preview_image"] = "ghost.png"  # never existed
        with open(os.path.join(pdir, "profile.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(data, fh)
        got = self.engine.get_voice(v.id)
        self.assertEqual(got.preview_image, "",
                         "unresolvable legacy value must clear, not dangle")

    # --- validation ----------------------------------------------------
    def test_validate_voice_profile_facade(self):
        v = self.engine.create_voice("Empty")
        warnings = self.engine.validate_voice_profile(v.id)
        self.assertTrue(any("reference" in w.lower() for w in warnings))
        full = self.engine.import_voice_profile(
            "Full", wav_path=self.wav, transcript="t")
        self.assertEqual(self.engine.validate_voice_profile(full.id), [])

    # --- delete ---------------------------------------------------------
    def test_delete_voice_emits_event_and_removes_dir(self):
        v = self.engine.import_voice_profile("Anna", wav_path=self.wav)
        n0 = self.log.count()
        self.assertTrue(self.engine.delete_voice(v.id))
        self.assertIsNone(self.engine.get_voice(v.id))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "voices", v.id)))
        self.assertGreater(self.log.count(), n0)

    # --- export / re-import --------------------------------------------
    def test_export_reimport_roundtrip(self):
        """§10/§18: everything survives export → delete → reimport."""
        v = self.engine.import_voice_profile(
            "Anna R.", wav_path=self.wav, transcript="the transcript",
            avatar_path=self.png, gender="Female", age_range="30s",
            mood="Warm", description="d1", tags=["t1", "t2"])
        export_root = os.path.join(self.work, "exp")
        os.makedirs(export_root, exist_ok=True)
        path = self.engine.export_voice_profile(v.id, export_root)
        edir = os.path.join(export_root, v.id)
        self.assertTrue({"profile.json", "reference.wav", "transcript.txt",
                         "avatar.png"} <= set(os.listdir(edir)))
        self.engine.delete_voice(v.id)
        # The stored relative paths in the exported JSON point at the OLD
        # app root — the importer must not trust them.
        with open(os.path.join(edir, "profile.json"), encoding="utf-8") as fh:
            self.assertIn("voices/", fh.read())
        v2 = self.engine.import_voice_profile_dir(edir)
        self.assertEqual(v2.id, v.id, "free original id is reused")
        got = self.engine.get_voice(v.id)
        self.assertEqual(got.name, "Anna R.")
        self.assertEqual(got.gender, "Female")
        self.assertEqual(got.age_range, "30s")
        self.assertEqual(got.mood, "Warm")
        self.assertEqual(got.description, "d1")
        self.assertEqual(got.tags, ["t1", "t2"])
        self.assertEqual(got.reference_transcript, "the transcript")
        self.assertEqual(got.sample_rate, 16000)
        self.assertTrue(got.preview_image)
        self.assertTrue(os.path.isfile(
            self.engine.resolve_voice_asset(got.preview_image)))

    def test_reimport_with_existing_id_gets_fresh_id(self):
        v = self.engine.import_voice_profile("Anna", wav_path=self.wav)
        export_root = os.path.join(self.work, "exp2")
        os.makedirs(export_root, exist_ok=True)
        self.engine.export_voice_profile(v.id, export_root)
        v2 = self.engine.import_voice_profile_dir(
            os.path.join(export_root, v.id))
        self.assertNotEqual(v2.id, v.id, "collision must mint a fresh id")
        self.assertEqual(v2.name, "Anna")

    def test_reimport_rejects_non_profile_dir(self):
        empty = tempfile.mkdtemp(prefix="ss_p343_notprofile_")
        try:
            with self.assertRaises(InvalidVoice):
                self.engine.import_voice_profile_dir(empty)
        finally:
            shutil.rmtree(empty, ignore_errors=True)

    # --- security -------------------------------------------------------
    def test_unsafe_profile_id_rejected(self):
        for bad in ("../../etc", "a/b", "..", "a:b", "x" * 200, ""):
            self.assertFalse(is_safe_profile_id(bad), bad)
        self.assertTrue(is_safe_profile_id("anna_ab12"))
        with self.assertRaises(InvalidVoice):
            self.engine.get_voice("../../etc")
        with self.assertRaises(InvalidVoice):
            self.engine.delete_profile if False else self.engine._voices.delete_profile("../x")

    def test_resolve_asset_blocks_traversal(self):
        self.assertEqual(self.engine.resolve_voice_asset("../secret"), "")
        self.assertEqual(self.engine.resolve_asset if False else
                         self.engine.resolve_voice_asset("voices/../../x"), "")


# ===========================================================================
# 2. THE SINGLE EDITOR (Create / Import / Edit)
# ===========================================================================
class TestVoiceEditor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ss_p343_e_")
        self.engine = Engine(app_root=self.tmp)
        self.work = tempfile.mkdtemp(prefix="ss_p343_w_")
        self.wav = os.path.join(self.work, "ref.wav")
        _make_wav(self.wav, 1.5)
        self.png = os.path.join(self.work, "avatar.png")
        _make_png(self.png, (60, 160, 90))
        self.profile = self.engine.import_voice_profile(
            "Anna", wav_path=self.wav, transcript="orig transcript",
            avatar_path=self.png, gender="Female", age_range="30s",
            mood="Warm")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(self.work, ignore_errors=True)

    def _editor(self, mode, profile=None):
        from ui.panels.voice_import_dialog import VoiceImportDialog
        return VoiceImportDialog(mode=mode, engine=self.engine,
                                 profile=profile)

    def _patch_mb(self):
        return patch("ui.panels.voice_import_dialog.QMessageBox", _FakeMB)

    def test_mode_titles_and_header(self):
        e_create = self._editor("create")
        e_import = self._editor("import")
        e_edit = self._editor("edit", self.profile)
        self.assertEqual(e_create.windowTitle(), "Create Voice Profile")
        self.assertEqual(e_import.windowTitle(), "Import Voice Profile")
        self.assertEqual(e_edit.windowTitle(), "Edit Voice Profile")

    def test_unknown_mode_rejected(self):
        from ui.panels.voice_import_dialog import VoiceImportDialog
        with self.assertRaises(ValueError):
            VoiceImportDialog(mode="nonsense")

    def test_edit_requires_profile(self):
        from ui.panels.voice_import_dialog import VoiceImportDialog
        with self.assertRaises(ValueError):
            VoiceImportDialog(mode="edit", engine=self.engine)

    def test_edit_mode_prefills_from_profile(self):
        e = self._editor("edit", self.profile)
        self.assertEqual(e._name_edit.text(), "Anna")
        self.assertEqual(e._gender_combo.currentText(), "Female")
        self.assertEqual(e._age_combo.currentText(), "30s")
        self.assertEqual(e._mood_edit.text(), "Warm")
        self.assertIn("orig transcript", e._transcript_edit.toPlainText())

    def test_import_mode_requires_wav(self):
        e = self._editor("import")
        e._name_edit.setText("X")
        _FakeMB.reset()
        with self._patch_mb():
            e._on_accept()
        self.assertEqual(e.result(), 0, "must not accept without a WAV")
        self.assertTrue(any(c[0] == "warning" for c in _FakeMB.calls))

    def test_create_mode_allows_no_wav(self):
        e = self._editor("create")
        e._name_edit.setText("Bare")
        with self._patch_mb():
            e._on_accept()
        self.assertEqual(e.result(), 1)
        got = self.engine.get_voice(e.applied_profile_id())
        self.assertEqual(got.name, "Bare")
        self.assertFalse(got.has_reference)

    def test_values_contains_all_fields(self):
        e = self._editor("create")
        e._name_edit.setText("V")
        e._description_edit.setText("descr")
        e._tags_edit.setText("a, b")
        vals = e.values()
        self.assertEqual(vals["description"], "descr")
        self.assertEqual(vals["tags"], ["a", "b"])
        self.assertIn("gender", vals)
        self.assertIn("transcript", vals)

    def test_create_mode_full_apply(self):
        e = self._editor("create")
        e._name_edit.setText("Mark")
        e._wav_path = self.wav
        e._gender_combo.setCurrentText("Male")
        e._mood_edit.setText("Bright")
        e._transcript_edit.setPlainText("mark text")
        with self._patch_mb():
            e._on_accept()
        got = self.engine.get_voice(e.applied_profile_id())
        self.assertEqual(got.name, "Mark")
        self.assertEqual(got.gender, "Male")
        self.assertEqual(got.mood, "Bright")
        self.assertEqual(got.reference_transcript, "mark text")
        self.assertTrue(got.has_reference)

    def test_import_mode_applies_transactionally(self):
        e = self._editor("import")
        e._name_edit.setText("Zoe")
        e._wav_path = self.wav
        e._avatar_path = self.png
        e._gender_combo.setCurrentText("Female")
        with self._patch_mb():
            e._on_accept()
        got = self.engine.get_voice(e.applied_profile_id())
        self.assertEqual(got.name, "Zoe")
        self.assertTrue(got.preview_image)

    def test_edit_mode_mutates_existing_profile_stable_id(self):
        """§4: EDIT edits the EXISTING profile — no replacement copy."""
        e = self._editor("edit", self.profile)
        e._name_edit.setText("Anna Renamed")
        e._mood_edit.setText("Calm")
        e._transcript_edit.setPlainText("new transcript")
        e._description_edit.setText("d")
        with self._patch_mb():
            e._on_accept()
        pid = e.applied_profile_id()
        self.assertEqual(pid, self.profile.id, "id must stay stable")
        got = self.engine.get_voice(pid)
        self.assertEqual(got.name, "Anna Renamed")
        self.assertEqual(got.mood, "Calm")
        self.assertEqual(got.reference_transcript, "new transcript")
        # Original reference audio untouched.
        self.assertEqual(got.sample_rate, 16000)
        self.assertEqual([p.id for p in self.engine.list_voices()].count(pid),
                         1, "no duplicate profile created")

    def test_edit_mode_clears_metadata(self):
        e = self._editor("edit", self.profile)
        e._gender_combo.setCurrentText("")
        e._age_combo.setCurrentText("")
        e._mood_edit.setText("")
        with self._patch_mb():
            e._on_accept()
        got = self.engine.get_voice(self.profile.id)
        self.assertEqual((got.gender, got.age_range, got.mood),
                         ("", "", ""), "emptied fields must clear")

    def test_edit_mode_replace_reference(self):
        wav2 = os.path.join(self.work, "ref2.wav")
        _make_wav(wav2, 2.5, rate=24000)
        e = self._editor("edit", self.profile)
        e._wav_path = wav2
        with self._patch_mb():
            e._on_accept()
        got = self.engine.get_voice(self.profile.id)
        self.assertEqual(got.sample_rate, 24000)
        self.assertAlmostEqual(got.duration, 2.5, delta=0.02)
        self.assertEqual(got.reference_transcript, "orig transcript",
                         "transcript must survive a pure reference replace")

    def test_edit_mode_replace_and_remove_avatar(self):
        png2 = os.path.join(self.work, "avatar2.png")
        _make_png(png2, (200, 100, 50))
        e = self._editor("edit", self.profile)
        e._avatar_path = png2
        with self._patch_mb():
            e._on_accept()
        got = self.engine.get_voice(self.profile.id)
        self.assertTrue(got.preview_image.endswith("avatar.png"))
        self.assertIn("voices/", got.preview_image)

        e2 = self._editor("edit",
                          self.engine.get_voice(self.profile.id))
        e2._clear_avatar()
        self.assertTrue(e2.values()["avatar_cleared"])
        with self._patch_mb():
            e2._on_accept()
        got = self.engine.get_voice(self.profile.id)
        self.assertEqual(got.preview_image, "")

    def test_edit_mode_failure_keeps_dialog_open_and_data_intact(self):
        e = self._editor("edit", self.profile)
        not_wav = os.path.join(self.work, "bad.wav")
        with open(not_wav, "wb") as fh:
            fh.write(b"junk bytes")
        e._wav_path = not_wav
        _FakeMB.reset()
        with self._patch_mb():
            e._on_accept()
        self.assertEqual(e.result(), 0, "failed apply must not accept")
        self.assertTrue(any(c[0] == "critical" for c in _FakeMB.calls))
        got = self.engine.get_voice(self.profile.id)
        self.assertEqual(got.sample_rate, 16000,
                         "profile must be untouched after a failed edit")

    def test_avatar_filter_matches_engine_capability(self):
        """§24: the dialog no longer offers BMP the engine rejects."""
        from ui.panels.voice_import_dialog import AVATAR_FILTER
        self.assertNotIn("bmp", AVATAR_FILTER.lower())
        self.assertIn("*.png", AVATAR_FILTER)


# ===========================================================================
# 3. THE UNIFIED MANAGER SCREEN
# ===========================================================================
class TestVoiceManagerScreen(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ss_p343_m_")
        self.engine = Engine(app_root=self.tmp)
        self.work = tempfile.mkdtemp(prefix="ss_p343_w_")
        self.wav = os.path.join(self.work, "ref.wav")
        _make_wav(self.wav, 2.0)
        self.png = os.path.join(self.work, "avatar.png")
        _make_png(self.png)
        self.anna = self.engine.import_voice_profile(
            "Anna", wav_path=self.wav, transcript="anna text",
            avatar_path=self.png, gender="Female", age_range="30s",
            mood="Warm")
        self.mark = self.engine.create_voice("Mark")  # no reference
        _FakeMB.reset()
        _FakeFD.reset()

    def tearDown(self):
        # Close any dialogs still open (unsubscribes engine listeners).
        for w in list(QApplication.topLevelWidgets()):
            if w.__class__.__name__ == "VoiceLibraryDialog":
                try:
                    w.reject()
                except Exception:
                    pass
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(self.work, ignore_errors=True)
        _app.processEvents()

    def _manager(self, scanner=None, clearer=None):
        from ui.panels.voice_library import VoiceLibraryDialog
        return VoiceLibraryDialog(self.engine, None,
                                  dependency_scanner=scanner,
                                  reference_clearer=clearer)

    def test_title_and_terminology(self):
        dlg = self._manager()
        self.assertEqual(dlg.windowTitle(), "Voice Profiles")

    def test_list_rows_built_from_real_profiles(self):
        dlg = self._manager()
        self.assertEqual(dlg._list.count(), 2)
        ids = [dlg._list.item(i).data(Qt.ItemDataRole.UserRole)
               for i in range(dlg._list.count())]
        self.assertIn(self.anna.id, ids)
        self.assertIn(self.mark.id, ids)

    def test_row_widgets_show_metadata_and_status(self):
        dlg = self._manager()
        row0 = dlg._list.itemWidget(dlg._list.item(0))
        labels = [l.text() for l in row0.findChildren(QLabel)]
        self.assertTrue(any("Anna" in t for t in labels),
                        "row must show the name: {0}".format(labels))
        self.assertTrue(any("Female" in t for t in labels),
                        "row must show speaker metadata: {0}".format(labels))

    def test_detail_shows_authoritative_information(self):
        dlg = self._manager()
        dlg._select_by_id(self.anna.id)
        dlg._show_voice_info(self.anna.id)
        _app.processEvents()
        self.assertEqual(dlg._name_label.text(), "Anna")
        self.assertEqual(dlg._gender_label.text(), "Female")
        self.assertEqual(dlg._mood_label.text(), "Warm")
        self.assertEqual(dlg._sr_label.text(), "16000 Hz")
        self.assertIn("anna text", dlg._transcript_view.toPlainText())
        self.assertTrue(dlg._play_btn.isEnabled())

    def test_missing_reference_state_is_visible(self):
        dlg = self._manager()
        dlg._show_voice_info(self.mark.id)
        self.assertFalse(dlg._play_btn.isEnabled())
        self.assertIn("Missing reference", dlg._ref_status_label.text())

    def test_keyboard_navigation_updates_detail(self):
        """§20: keyboard selection updates the detail pane exactly like
        a mouse click — no stale details."""
        dlg = self._manager()
        dlg.show()
        _app.processEvents()
        # Select Anna (index depends on sorted ids — find it).
        anna_row = 0 if dlg._list.item(0).data(Qt.ItemDataRole.UserRole) \
            == self.anna.id else 1
        dlg._list.setCurrentRow(anna_row)
        _app.processEvents()
        self.assertEqual(dlg._name_label.text(), "Anna")
        # Keyboard navigation to the other profile.
        dlg._list.setFocus()
        QTest.keyClick(dlg._list, Qt.Key.Key_Down)
        _app.processEvents()
        self.assertEqual(dlg._name_label.text(), "Mark",
                         "detail pane must follow keyboard selection")

    def test_play_calls_engine_with_reference_path(self):
        dlg = self._manager()
        dlg._show_voice_info(self.anna.id)
        with patch.object(self.engine, "play_audio") as mock_play:
            dlg._on_play()
            mock_play.assert_called_once()
            path = mock_play.call_args[0][0]
            self.assertTrue(os.path.isfile(path))
            self.assertIn(self.anna.id, path)
        # Second click stops playback.
        with patch.object(self.engine, "stop_playback") as mock_stop:
            dlg._on_play()
            mock_stop.assert_called_once()

    def test_delete_unreferenced_plain_confirmation(self):
        dlg = self._manager()
        dlg._show_voice_info(self.mark.id)
        _FakeMB.question_result = QMessageBox.StandardButton.Yes
        with patch("ui.panels.voice_library.QMessageBox", _FakeMB):
            dlg._on_delete()
        self.assertIsNone(self.engine.get_voice(self.mark.id))

    def test_delete_referenced_shows_dependencies_and_clears(self):
        cleared = []
        dlg = self._manager(
            scanner=lambda vid: ["Character: Anna Char", "Scene: S1"]
            if vid == self.anna.id else [],
            clearer=lambda vid: cleared.append(vid))
        dlg._show_voice_info(self.anna.id)
        _FakeMB.question_result = QMessageBox.StandardButton.Yes
        with patch("ui.panels.voice_library.QMessageBox", _FakeMB):
            dlg._on_delete()
        # The confirmation carried the dependency information.
        question_text = _FakeMB.calls[0][1]
        self.assertIn("Anna Char", question_text)
        self.assertIn("Scene: S1", question_text)
        self.assertIn("CLEAR", question_text)
        # Delete happened AND references were cleared (no dangling ids).
        self.assertIsNone(self.engine.get_voice(self.anna.id))
        self.assertEqual(cleared, [self.anna.id])

    def test_delete_cancelled_keeps_profile(self):
        dlg = self._manager()
        dlg._show_voice_info(self.mark.id)
        _FakeMB.question_result = QMessageBox.StandardButton.No
        with patch("ui.panels.voice_library.QMessageBox", _FakeMB):
            dlg._on_delete()
        self.assertIsNotNone(self.engine.get_voice(self.mark.id))

    def test_footer_create_import_open_shared_editor_modes(self):
        dlg = self._manager()
        with patch("ui.panels.voice_import_dialog.VoiceImportDialog") as mock_cls:
            mock_cls.MODE_CREATE = "create"
            mock_cls.MODE_IMPORT = "import"
            dlg._on_create()
            self.assertEqual(mock_cls.call_args.kwargs.get("mode"), "create")
            dlg._on_import()
            self.assertEqual(mock_cls.call_args.kwargs.get("mode"), "import")

    def test_edit_button_opens_editor_in_edit_mode(self):
        dlg = self._manager()
        dlg._show_voice_info(self.anna.id)
        with patch("ui.panels.voice_import_dialog.VoiceImportDialog") as mock_cls:
            mock_cls.MODE_EDIT = "edit"
            dlg._on_edit()
            self.assertEqual(mock_cls.call_args.kwargs.get("mode"), "edit")
            self.assertEqual(mock_cls.call_args.kwargs.get("profile").id,
                             self.anna.id)

    def test_export_button_uses_facade(self):
        dlg = self._manager()
        dlg._show_voice_info(self.anna.id)
        export_root = os.path.join(self.work, "exp")
        os.makedirs(export_root, exist_ok=True)
        _FakeFD.reset(dir_=export_root)
        # P3.44.3: the export confirmation migrated to the structured
        # FeedbackDialog (same information, summary/details hierarchy) —
        # patch the new seam alongside the legacy QMessageBox one.
        with patch("ui.panels.voice_library.QFileDialog", _FakeFD), \
                patch("ui.panels.voice_library.QMessageBox", _FakeMB), \
                patch("ui.panels.voice_library.FeedbackDialog", _FakeMB):
            dlg._on_export()
        self.assertTrue(os.path.isfile(os.path.join(
            export_root, self.anna.id, "reference.wav")))

    def test_live_refresh_on_engine_voice_changed(self):
        """§28: the manager refreshes from VOICE_CHANGED without reopen."""
        dlg = self._manager()
        dlg._select_by_id(self.anna.id)
        dlg._show_voice_info(self.anna.id)
        # Mutate through the facade — the event must refresh the dialog.
        self.engine.rename_voice(self.anna.id, "Anna Renamed")
        _app.processEvents()
        ids = [dlg._list.item(i).data(Qt.ItemDataRole.UserRole)
               for i in range(dlg._list.count())]
        self.assertIn(self.anna.id, ids)
        dlg._show_voice_info(self.anna.id)
        self.assertEqual(dlg._name_label.text(), "Anna Renamed")

    def test_deleted_profile_disappears_from_live_manager(self):
        dlg = self._manager()
        self.assertIn(self.mark.id,
                      [dlg._list.item(i).data(Qt.ItemDataRole.UserRole)
                       for i in range(dlg._list.count())])
        self.engine.delete_voice(self.mark.id)
        _app.processEvents()
        ids = [dlg._list.item(i).data(Qt.ItemDataRole.UserRole)
               for i in range(dlg._list.count())]
        self.assertNotIn(self.mark.id, ids)

    def test_empty_state_visible_without_profiles(self):
        empty_root = tempfile.mkdtemp(prefix="ss_p343_empty_")
        try:
            eng = Engine(app_root=empty_root)
            dlg = self._manager.__func__(self, scanner=None, clearer=None) \
                if False else None
            from ui.panels.voice_library import VoiceLibraryDialog
            d = VoiceLibraryDialog(eng, None)
            self.assertTrue(d._empty_label.isVisibleTo(d))
            self.assertEqual(d._list.count(), 0)
            d.reject()
        finally:
            shutil.rmtree(empty_root, ignore_errors=True)

    def test_no_legacy_qinputdialog_flows_remain(self):
        """§12: the manager contains no chained QInputDialog import/create."""
        src = open(os.path.join(_ROOT, "ui", "panels", "voice_library.py"),
                   encoding="utf-8").read()
        # Actual USAGE (the module docstring mentions the retired flow
        # by name — only executable references count).
        self.assertNotIn("QInputDialog.", src)
        self.assertNotIn("QInputDialog,", src)
        self.assertNotIn("QInputDialog)", src)
        self.assertNotIn("getMultiLineText", src)


# ===========================================================================
# 4. ENTRY POINT ROUTING (real MainWindow)
# ===========================================================================
class _MWHarness:
    """Real MainWindow + real Engine in an isolated tmpdir."""

    @classmethod
    def _boot(cls):
        cls.tmpdir = tempfile.mkdtemp(prefix="ss_p343_mw_")
        cls.engine = Engine(app_root=cls.tmpdir)
        cls._patcher = patch.object(
            type(cls.engine._model), "is_loaded",
            new_callable=PropertyMock, return_value=True)
        cls._patcher.start()
        from ui.main_window import MainWindow
        cls._mw_cls = MainWindow
        cls.win = MainWindow(cls.engine)

    @classmethod
    def _shutdown(cls):
        try:
            cls.win.close()
        except Exception:
            pass
        cls._patcher.stop()
        shutil.rmtree(cls.tmpdir, ignore_errors=True)


class TestEntryRouting(unittest.TestCase, _MWHarness):
    @classmethod
    def setUpClass(cls):
        cls._boot()
        _FakeMB.reset()

    @classmethod
    def tearDownClass(cls):
        cls._shutdown()

    def test_tools_voice_profiles_action_opens_manager(self):
        """§13: Tools → Voice Profiles (and Ctrl+L) opens the unified
        manager — the action was previously DEAD (no handler)."""
        action = self.win._menu_bar.get_action("voice_library")
        self.assertIsNotNone(action)
        with patch("ui.panels.voice_library.VoiceLibraryDialog") as mock_dlg:
            action.trigger()
            _app.processEvents()
            mock_dlg.assert_called()
            # Constructed with the engine (positional) + dependency callbacks.
            args = mock_dlg.call_args.args
            kwargs = mock_dlg.call_args.kwargs
            self.assertIs(args[0], self.engine)
            self.assertTrue(callable(kwargs.get("dependency_scanner")))
            self.assertTrue(callable(kwargs.get("reference_clearer")))

    def test_tools_action_label_uses_new_terminology(self):
        action = self.win._menu_bar.get_action("voice_library")
        self.assertEqual(action.text(), "Voice Profiles…")
        imp = self.win._menu_bar.get_action("import_voice")
        self.assertIn("Import Voice Profile", imp.text())

    def test_file_import_voice_opens_editor_import_mode(self):
        with patch("ui.panels.voice_import_dialog.VoiceImportDialog") as mock_e:
            mock_e.MODE_IMPORT = "import"
            mock_e.return_value.exec.return_value = 0
            self.win._on_import_voice()
            self.assertEqual(mock_e.call_args.kwargs.get("mode"), "import")
            self.assertIs(mock_e.call_args.kwargs.get("engine"), self.engine)

    def test_file_export_voice_performs_actual_export(self):
        """§14: File → Export Voice Profile exports — it must NOT open the
        whole management dialog as a picker."""
        wav = os.path.join(self.tmpdir, "ref.wav")
        _make_wav(wav, 1.0)
        v = self.engine.import_voice_profile("ExportMe", wav_path=wav)
        self.win._control_panel.set_selected_voice_id(v.id)
        export_root = os.path.join(self.tmpdir, "exp")
        os.makedirs(export_root, exist_ok=True)
        _FakeFD.reset(dir_=export_root)
        with patch("ui.main_window.QFileDialog", _FakeFD), \
                patch("ui.main_window.QMessageBox", _FakeMB), \
                patch("ui.main_window.FeedbackDialog", _FakeMB), \
                patch("ui.panels.voice_library.VoiceLibraryDialog") as mock_lib:
            self.win._on_export_voice_menu()
            mock_lib.assert_not_called()
        self.assertTrue(os.path.isfile(
            os.path.join(export_root, v.id, "profile.json")))

    def test_file_export_voice_without_selection_informs_user(self):
        self.win._control_panel.set_selected_voice_id(None)
        _FakeMB.reset()
        with patch("ui.main_window.QFileDialog", _FakeFD), \
                patch("ui.main_window.QMessageBox", _FakeMB), \
                patch("ui.panels.voice_library.VoiceLibraryDialog") as mock_lib:
            self.win._on_export_voice_menu()
            mock_lib.assert_not_called()
        self.assertTrue(any(c[0] == "information" for c in _FakeMB.calls))

    def test_create_voice_opens_editor_create_mode(self):
        """§13: Advanced Create routes into the unified editor (the old
        name-only QInputDialog flow is retired)."""
        with patch("ui.panels.voice_import_dialog.VoiceImportDialog") as mock_e:
            mock_e.MODE_CREATE = "create"
            mock_e.return_value.exec.return_value = 0
            self.win._on_create_voice()
            self.assertEqual(mock_e.call_args.kwargs.get("mode"), "create")

    def test_control_panel_import_signal_routes_to_editor(self):
        with patch("ui.panels.voice_import_dialog.VoiceImportDialog") as mock_e:
            mock_e.MODE_IMPORT = "import"
            mock_e.return_value.exec.return_value = 0
            self.win._control_panel.import_voice_requested.emit()
            self.assertEqual(mock_e.call_args.kwargs.get("mode"), "import")

    def test_friendly_change_opens_lightweight_picker_not_manager(self):
        """§15: the Friendly 'Change' action is a lightweight picker —
        NOT the full management screen."""
        with patch("ui.panels.voice_picker.VoicePickerDialog") as mock_pk, \
                patch("ui.panels.voice_library.VoiceLibraryDialog") as mock_m:
            mock_pk.return_value.exec.return_value = 0
            self.win._on_pick_voice()
            mock_pk.assert_called()
            mock_m.assert_not_called()
            kwargs = mock_pk.call_args.kwargs
            self.assertIn("current_voice_id", kwargs)

    def test_voice_library_requested_signal_wired_to_picker(self):
        """The RightPanel's voice_library_requested (Friendly 'Change')
        is wired to the picker handler, not the manager."""
        with patch("ui.panels.voice_picker.VoicePickerDialog") as mock_pk:
            mock_pk.return_value.exec.return_value = 0
            self.win._control_panel.voice_library_requested.emit()
            mock_pk.assert_called()

    def test_picker_manage_opens_manager(self):
        with patch("ui.panels.voice_library.VoiceLibraryDialog") as mock_m:
            self.win._on_open_voice_library()
            mock_m.assert_called()

    def test_character_dialog_manage_signal_opens_manager_and_refreshes(self):
        with patch("ui.panels.character_dialog.CharacterManagementDialog") as mock_cd:
            instance = mock_cd.return_value
            self.win._on_open_character_management()
            # The manage signal is wired to open the unified manager.
            self.assertTrue(instance.manage_voices_requested.connect.called)

    def test_reference_panel_is_read_only_display(self):
        """§18: the right panel's ReferenceAudioPanel no longer edits."""
        from ui.panels.reference_audio import ReferenceAudioPanel
        rap = None
        for w in self.win._control_panel.findChildren(QWidget):
            if isinstance(w, ReferenceAudioPanel):
                rap = w
                break
        self.assertIsNotNone(rap, "panel must still be embedded (display)")
        self.assertTrue(rap._transcript.isReadOnly())
        buttons = rap.findChildren(QPushButton)
        self.assertEqual(buttons, [],
                         "dead Trim/Normalize/Refresh buttons removed")


# ===========================================================================
# 5. REFRESH + CHARACTER/SCENE INTEGRATION (no restart)
# ===========================================================================
class TestRefreshIntegration(unittest.TestCase, _MWHarness):
    @classmethod
    def setUpClass(cls):
        cls._boot()
        cls.work = tempfile.mkdtemp(prefix="ss_p343_w_")
        cls.wav = os.path.join(cls.work, "ref.wav")
        _make_wav(cls.wav, 1.0)
        cls.png = os.path.join(cls.work, "avatar.png")
        _make_png(cls.png)
        _FakeMB.reset()

    @classmethod
    def tearDownClass(cls):
        cls._shutdown()
        shutil.rmtree(cls.work, ignore_errors=True)

    def _combo_texts(self):
        combo = None
        for w in self.win._control_panel.findChildren(QComboBox):
            if w.itemData(0) is None:
                combo = w
                break
        return [combo.itemText(i) for i in range(combo.count())] if combo else []

    def test_rename_propagates_to_selectors_without_restart(self):
        v = self.engine.import_voice_profile("RenameMe", wav_path=self.wav)
        self.win._control_panel.set_selected_voice_id(v.id)
        self.engine.rename_voice(v.id, "Renamed Live")
        _app.processEvents()
        texts = self._combo_texts()
        self.assertTrue(any("Renamed Live" in t for t in texts),
                        "selector must show the new name: {0}".format(texts))

    def test_deleted_profile_disappears_from_selectors(self):
        v = self.engine.import_voice_profile("DeleteMe", wav_path=self.wav)
        _app.processEvents()
        self.engine.delete_voice(v.id)
        _app.processEvents()
        combo = None
        for w in self.win._control_panel.findChildren(QComboBox):
            if w.itemData(0) is None:
                combo = w
                break
        ids = [combo.itemData(i) for i in range(combo.count())]
        self.assertNotIn(v.id, ids)

    def test_created_profile_appears_in_selectors(self):
        v = self.engine.create_voice("FreshVoice")
        _app.processEvents()
        combo = None
        for w in self.win._control_panel.findChildren(QComboBox):
            if w.itemData(0) is None:
                combo = w
                break
        ids = [combo.itemData(i) for i in range(combo.count())]
        self.assertIn(v.id, ids)

    def test_programmatic_refresh_is_not_a_user_mutation(self):
        """§29: ghost-state guard — refreshing the selectors must not emit
        voice_changed (no silent persistent writes)."""
        emissions = []

        def _on_voice_changed(vid):
            emissions.append(vid)

        self.win._control_panel.voice_changed.connect(_on_voice_changed)
        try:
            self.win._refresh_sidebar()
            _app.processEvents()
            self.assertEqual(emissions, [],
                             "programmatic refresh must stay silent")
        finally:
            self.win._control_panel.voice_changed.disconnect(
                _on_voice_changed)

    def test_character_uses_same_voice_entity(self):
        """§16: the Character dialog assigns the same VoiceProfile ids."""
        v = self.engine.import_voice_profile("CharVoice", wav_path=self.wav)
        project = Project(name="P1")
        char = Character(name="Hero")
        project.characters.append(char)
        from ui.panels.character_dialog import CharacterManagementDialog
        dlg = CharacterManagementDialog(project, self.engine.list_voices())
        # Drive the real selection flow (fills the form from the character).
        dlg._list.setCurrentRow(0)
        _app.processEvents()
        self.assertEqual(dlg._selected_character, char)
        idx = dlg._voice_combo.findData(v.id)
        self.assertGreaterEqual(idx, 0, "voice must be offered")
        dlg._voice_combo.setCurrentIndex(idx)
        dlg._on_apply_changes()
        self.assertEqual(char.voice_profile_id, v.id)
        dlg.reject()

    def test_character_dialog_refresh_voices_drops_deleted_id(self):
        v = self.engine.import_voice_profile("Temp", wav_path=self.wav)
        project = Project(name="P2")
        char = Character(name="Hero", voice_profile_id=v.id)
        project.characters.append(char)
        from ui.panels.character_dialog import CharacterManagementDialog
        dlg = CharacterManagementDialog(project, self.engine.list_voices())
        # Selecting the character row restores its voice in the combo.
        dlg._list.setCurrentRow(0)
        _app.processEvents()
        self.assertEqual(dlg._selected_character, char)
        self.assertEqual(dlg._voice_combo.currentData(), v.id)
        # Simulate: the profile was deleted while the dialog is open.
        self.engine.delete_voice(v.id)
        dlg.refresh_voices(self.engine.list_voices())
        self.assertIsNone(dlg._voice_combo.currentData(),
                          "deleted id must fall back to (no voice)")
        dlg.reject()

    def test_scene_voice_selection_roundtrip(self):
        """§17: Scene.voice_profile_id restores through the selector."""
        v = self.engine.import_voice_profile("SceneVoice", wav_path=self.wav)
        scene = Scene(name="S", status="draft", sort_order=0,
                      voice_profile_id=v.id)
        self.win._control_panel.set_selected_voice_id(scene.voice_profile_id)
        self.assertEqual(self.win._control_panel.get_selected_voice_id(),
                         v.id)
        # And the scene save path captures the selector state.
        scene.voice_profile_id = self.win._control_panel.get_selected_voice_id()
        self.assertEqual(scene.voice_profile_id, v.id)

    def test_generation_resolves_selected_voice_profile(self):
        """§17: VoiceProfile → Character → Scene → Generation request."""
        v = self.engine.import_voice_profile(
            "GenVoice", wav_path=self.wav, transcript="g")
        self.win._control_panel.set_selected_voice_id(v.id)
        project = Project(name="PGen")
        char = Character(name="Speaker", voice_profile_id=v.id)
        project.characters.append(char)
        scene = Scene(name="SGen", status="draft", sort_order=0,
                      voice_profile_id=v.id)
        project.scenes.append(scene)
        self.win._active_project = project
        self.win._active_scene = scene
        self.win._selected_character_id = char.id
        self.win._editor.set_text("generation resolution test")

        captured = {}
        import concurrent.futures as cf

        def fake_generate(request):
            captured["request"] = request
            fut = cf.Future()
            fut.set_result(None)
            return fut

        with patch.object(self.engine, "generate", side_effect=fake_generate):
            with patch("ui.main_window.QMessageBox", _FakeMB):
                self.win._on_generate()
                _app.processEvents()
        req = captured.get("request")
        self.assertIsNotNone(req, "generation request must be captured")
        self.assertEqual(req.voice_id, v.id,
                         "the request must carry the selected VoiceProfile")
        self.assertEqual(req.character_id, char.id)
        # Restore neutral state for other tests.
        self.win._active_project = None
        self.win._active_scene = None
        self.win._selected_character_id = None


# ===========================================================================
# 6. GOLDEN PATH (§26) — the complete end-to-end workflow
# ===========================================================================
class TestGoldenPath(unittest.TestCase, _MWHarness):
    @classmethod
    def setUpClass(cls):
        cls._boot()
        cls.work = tempfile.mkdtemp(prefix="ss_p343_gold_")
        cls.wav1 = os.path.join(cls.work, "anna.wav")
        _make_wav(cls.wav1, 2.0)
        cls.wav2 = os.path.join(cls.work, "anna2.wav")
        _make_wav(cls.wav2, 2.5, rate=24000)
        cls.png = os.path.join(cls.work, "anna.png")
        _make_png(cls.png, (180, 60, 60))
        _FakeMB.reset()

    @classmethod
    def tearDownClass(cls):
        cls._shutdown()
        shutil.rmtree(cls.work, ignore_errors=True)

    def test_full_golden_path(self):
        engine, win = self.engine, self.win
        export_root = os.path.join(self.work, "export")
        os.makedirs(export_root, exist_ok=True)

        # 1-8. Create + reference + transcript + avatar + metadata + save.
        editor_calls = {}
        anna = engine.import_voice_profile(
            "Anna", wav_path=self.wav1, transcript="anna golden transcript",
            avatar_path=self.png, gender="Female", age_range="30s",
            mood="Warm", description="golden", tags=["hero"])
        self.assertTrue(anna.has_reference)

        # 9. Play the reference audio (through the manager's Play action).
        from ui.panels.voice_library import VoiceLibraryDialog
        manager = VoiceLibraryDialog(
            engine, None,
            dependency_scanner=win._find_voice_dependencies,
            reference_clearer=win._clear_voice_references)
        manager._show_voice_info(anna.id)
        with patch.object(engine, "play_audio") as mock_play:
            manager._on_play()
            self.assertTrue(mock_play.called)
            self.assertIn(anna.id, mock_play.call_args[0][0])

        # 10. Select the profile in the manager.
        manager._select_by_id(anna.id)
        self.assertEqual(manager._selected_voice_id, anna.id)

        # 11. Select the same VoiceProfile from Character management.
        project = Project(name="Golden")
        char = Character(name="Anna Char")
        project.characters.append(char)
        win._active_project = project
        from ui.panels.character_dialog import CharacterManagementDialog
        cdlg = CharacterManagementDialog(project, engine.list_voices())
        cdlg._list.setCurrentRow(0)          # real selection flow
        _app.processEvents()
        cdlg._voice_combo.setCurrentIndex(
            cdlg._voice_combo.findData(anna.id))
        cdlg._on_apply_changes()
        self.assertEqual(char.voice_profile_id, anna.id)
        cdlg.reject()

        # 12. Assign the Character to a Narration Block (data level — the
        # editor-level assignment is regression-covered by P3.22/P3.41).
        from engine.narration_blocks import PromptBlock
        block = PromptBlock(start_offset=0, end_offset=5,
                            character_id=char.id)
        self.assertEqual(block.character_id, char.id)

        # 13-14. Generate + verify the request resolves the VoiceProfile.
        scene = Scene(name="GoldenScene", status="draft", sort_order=0,
                      voice_profile_id=anna.id)
        project.scenes.append(scene)
        win._active_scene = scene
        win._selected_character_id = char.id
        win._control_panel.set_selected_voice_id(anna.id)
        win._editor.set_text("golden path generation")
        import concurrent.futures as cf
        captured = {}

        def fake_generate(request):
            captured["request"] = request
            fut = cf.Future()
            fut.set_result(None)
            return fut

        with patch.object(engine, "generate", side_effect=fake_generate), \
                patch("ui.main_window.QMessageBox", _FakeMB):
            win._on_generate()
            _app.processEvents()
        self.assertEqual(captured["request"].voice_id, anna.id)

        # 15-18. Edit the profile: metadata, reference, clear a field.
        from ui.panels.voice_import_dialog import VoiceImportDialog
        editor = VoiceImportDialog(mode="edit", engine=engine,
                                   profile=engine.get_voice(anna.id))
        editor._name_edit.setText("Anna Golden")
        editor._mood_edit.setText("Calm")
        editor._wav_path = self.wav2          # replace reference
        editor._gender_combo.setCurrentText("")  # clear metadata
        with patch("ui.panels.voice_import_dialog.QMessageBox", _FakeMB):
            editor._on_accept()
        got = engine.get_voice(anna.id)
        self.assertEqual(got.id, anna.id)
        self.assertEqual(got.name, "Anna Golden")
        self.assertEqual(got.mood, "Calm")
        self.assertEqual(got.gender, "")
        self.assertEqual(got.sample_rate, 24000)

        # 19. Dependent UI updates without restart (§28).
        _app.processEvents()
        combo = None
        for w in win._control_panel.findChildren(QComboBox):
            if w.itemData(0) is None:
                combo = w
                break
        ids = [combo.itemData(i) for i in range(combo.count())]
        self.assertIn(anna.id, ids)
        self.assertTrue(
            any("Anna Golden" in combo.itemText(i)
                for i in range(combo.count())),
            "renamed profile must appear in the selector: {0}".format(
                [combo.itemText(i) for i in range(combo.count())]))

        # 20. Export.
        path = engine.export_voice_profile(anna.id, export_root)
        self.assertTrue(os.path.isfile(os.path.join(path, "reference.wav")))
        self.assertTrue(os.path.isfile(os.path.join(path, "avatar.png")))

        # 21. Delete the original AFTER dependency handling.
        deps = win._find_voice_dependencies(anna.id)
        self.assertIn("Character: Anna Char", deps)
        self.assertIn("Scene: GoldenScene", deps)
        _FakeMB.question_result = QMessageBox.StandardButton.Yes
        with patch("ui.panels.voice_library.QMessageBox", _FakeMB):
            manager._selected_voice_id = anna.id
            manager._on_delete()
        self.assertIsNone(engine.get_voice(anna.id))
        # No dangling references remain.
        self.assertIsNone(char.voice_profile_id)
        self.assertIsNone(scene.voice_profile_id)
        manager.reject()

        # 22-23. Reimport — everything survives.
        reimp = engine.import_voice_profile_dir(path)
        self.assertEqual(reimp.id, anna.id)
        got = engine.get_voice(anna.id)
        self.assertEqual(got.name, "Anna Golden")
        self.assertEqual(got.mood, "Calm")
        self.assertEqual(got.gender, "")
        self.assertEqual(got.sample_rate, 24000)
        self.assertEqual(got.reference_transcript,
                         "anna golden transcript")
        self.assertTrue(os.path.isfile(
            engine.resolve_voice_asset(got.preview_image)))

        # 24. Select the reimported profile in Character + Scene flows.
        cdlg2 = CharacterManagementDialog(project, engine.list_voices())
        cdlg2._list.setCurrentRow(0)
        _app.processEvents()
        cdlg2._voice_combo.setCurrentIndex(
            cdlg2._voice_combo.findData(anna.id))
        cdlg2._on_apply_changes()
        self.assertEqual(char.voice_profile_id, anna.id)
        cdlg2.reject()
        win._control_panel.set_selected_voice_id(anna.id)
        self.assertEqual(win._control_panel.get_selected_voice_id(), anna.id)

        # 25. Generate again — the reimported profile resolves.
        captured.clear()
        win._active_project = project
        win._active_scene = scene
        win._selected_character_id = char.id
        win._editor.set_text("golden path regeneration")
        with patch.object(engine, "generate", side_effect=fake_generate), \
                patch("ui.main_window.QMessageBox", _FakeMB):
            win._on_generate()
            _app.processEvents()
        self.assertEqual(captured["request"].voice_id, anna.id)

        # Neutral state for other suites.
        win._active_project = None
        win._active_scene = None
        win._selected_character_id = None


# ===========================================================================
# 7. STATIC AUDIT (§34) — no accidental competing implementations
# ===========================================================================
class TestStaticAudit(unittest.TestCase):
    def _src(self, relpath):
        with open(os.path.join(_ROOT, relpath), encoding="utf-8") as fh:
            return fh.read()

    def test_no_private_voice_state_access_in_ui(self):
        """§5: the UI never reaches into engine._voices."""
        import glob
        offenders = []
        for path in glob.glob(os.path.join(_ROOT, "ui", "**", "*.py"),
                              recursive=True):
            if os.path.basename(path) in ("sidebar.py",):
                continue  # dead legacy module (P3.43 §32: not deleted)
            src = open(path, encoding="utf-8").read()
            if "_voices." in src or "engine._voices" in src:
                offenders.append(os.path.relpath(path, _ROOT))
        self.assertEqual(offenders, [],
                         "private voice state accessed from UI: {0}"
                         .format(offenders))

    def test_voice_import_dialog_is_the_single_editor(self):
        """§12: no second editor architecture anywhere."""
        for rel in ("ui/panels/voice_library.py", "ui/panels/voice_picker.py",
                    "ui/panels/right_panel.py", "ui/main_window.py"):
            src = self._src(rel)
            self.assertNotIn("QFormLayout", src.split("class _VoiceRowWidget")[0]
                             if rel == "ui/panels/voice_library.py" else src,
                             "{0} must not build a competing editor form"
                             .format(rel))

    def test_single_manager_construction_sites(self):
        """§12: the manager is opened through the MainWindow routing —
        voice_library.py defines it, MainWindow opens it."""
        mw = self._src("ui/main_window.py")
        self.assertIn("VoiceLibraryDialog", mw)
        # No competing management implementation in the picker.
        pk = self._src("ui/panels/voice_picker.py")
        self.assertNotIn("import_voice_reference", pk)
        self.assertNotIn("delete_voice", pk)
        self.assertNotIn("export_profile", pk)
        self.assertIn("manage_requested", pk)

    def test_reference_panel_has_no_editing_or_dead_controls(self):
        src = self._src("ui/panels/reference_audio.py")
        self.assertNotIn("trim_requested", src)
        self.assertNotIn("normalize_requested", src)
        self.assertNotIn("refresh_requested", src)
        self.assertNotIn("transcript_changed", src)
        self.assertIn("setReadOnly(True)", src)

    def test_terminology_in_user_facing_labels(self):
        """§21: the management surfaces use Voice Profile terminology."""
        vl = self._src("ui/panels/voice_library.py")
        self.assertIn('"Voice Profiles"', vl)
        self.assertNotIn('"Voice Library"', vl)
        mb = self._src("ui/panels/menu_bar.py")
        self.assertIn('"Voice Profiles…"', mb)


if __name__ == "__main__":
    unittest.main(verbosity=2)
