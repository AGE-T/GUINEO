r"""GUINEO P3.44.8 — SENTENCE SPLITTER INTEGRITY (runtime tests).

Regression coverage for the proven sentence-boundary defect class and
the exact-text/marker invariants:

  ROOT CAUSE (proven against the pre-P3.44.8 tree, commit b5d3c10):
  P3.44.2 (commit 9acefbe) replaced

      SENTENCE_END_RE.split(text)      # (?<=[.!?…])\s+

  with a bare punctuation-run scan

      _PUNCT_RUN_RE = re.compile(r"[.!?…]+")

  to gain SFX/pause marker shielding+attachment — and in doing so LOST
  the requirement that sentence-ending punctuation be followed by
  whitespace. Every "." glued to a digit/letter became a "sentence
  boundary":

      "GLM 5.2"  -> "GLM 5." / "2"
      "3.14"     -> "3." / "14"
      "v1.2"     -> "v1." / "2"
      "test.hu"  -> "test." / "hu"
      "U.S.A."   -> "U." / "S." / "A."

  A SECOND defect in the same path: _group_sentences() joined sentence
  fragments with " ".join(...), so a text split mid-number was
  reconstructed with an INSERTED space:

      "A GLM 5.2-t használtam."  ->  "A GLM 5. 2-t használtam."

  A THIRD (divergence): PromptBuilder._detect_conflicts() splits the
  compiled prompt with a THIRD rule ((?<=[.!?])\s+, no "…", no marker
  awareness) which separates an inline SFX token from the sentence it
  annotates, while NarrationSplitter/BlockDetector attach it.

  FIX UNDER TEST (engine/sentence_boundaries.py — the ONE authoritative
  boundary scanner used by all three implementations):
    1. SHIELDING  — punctuation inside {sfx:...}/{pause} markers never
                    ends a sentence (P3.44.2 rule, kept).
    2. ATTACHMENT — a marker chain after the punctuation run belongs to
                    the sentence being ended; the boundary lands after
                    the chain (P3.44.2 rule, kept).
    3. CONTEXT    — without an attached marker chain, a run is a
                    boundary ONLY if whitespace or end-of-text follows
                    ("3.14", "v1.2", "test.hu", "U.S.A." stay whole).
    4. ABBREV     — a single ASCII "." followed by whitespace + a
                    lowercase letter is an abbreviation continuation,
                    not a boundary ("U.S.A. is known...", "stb. ez...").
    5. EXACT TEXT — sentences are exact source slices; inter-sentence
                    whitespace is carried as the separator, never
                    normalised: "".join(text+sep for units) == source.

Run:
    LD_LIBRARY_PATH=/tmp QT_QPA_PLATFORM=offscreen \
    /home/z/.venv/bin/python -m pytest \
        tests/test_p3_44_8_sentence_splitter_integrity.py -v
"""
from __future__ import annotations

import os
import re
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from engine.sentence_boundaries import (
    INLINE_MARKER_RE,
    TOKEN_SPAN_RE,
    find_sentence_ends,
    split_sentence_units,
    split_sentences,
    split_tokenized_sentences,
)
from engine.narration_splitter import NarrationSplitter
from engine.block_detector import HeuristicBlockDetector
from engine.prompt_builder import PromptBuilder


# ---------------------------------------------------------------------------
# Defect corpus (the proven cases + realistic variants)
# ---------------------------------------------------------------------------
DEFECT_CORPUS = [
    "A GLM 5.2-t használtam.",
    "Az érték 3.14.",
    "Az érték pontosan 3.14 volt.",
    "Ez a v1.2 verzió.",
    "A test.hu domain.",
    "Nézd meg a docs.python.org oldalt.",
    "U.S.A. is known for this.",
    "Az ára 12.50 volt.",
    "Az IP cím 192.168.1.1.",
    "A config.yaml fájl.",
    "Az 5.2-t használtam.",
]

# Spans that must remain INSIDE one sentence (no boundary may cut them).
UNCUTTABLE_SPANS = {
    "A GLM 5.2-t használtam.": ["5.2"],
    "Az érték 3.14.": ["3.14"],
    "Ez a v1.2 verzió.": ["v1.2"],
    "A test.hu domain.": ["test.hu"],
    "U.S.A. is known for this.": ["U.S.A."],
    "Az IP cím 192.168.1.1.": ["192.168.1.1"],
    "Nézd meg a docs.python.org oldalt.": ["docs.python.org"],
}

# The full invariant battery: every input must split losslessly.
INVARIANT_CORPUS = DEFECT_CORPUS + [
    "Ez egy mondat. Ez a következő.",
    "Első mondat! Második mondat.",
    "Első mondat? Második mondat?",
    "Első mondat… Második mondat.",
    "Első mondat.  Második mondat.",            # two spaces
    "Első mondat.\nMásodik mondat.",            # newline boundary
    "Első mondat.\n\nMásodik mondat.",          # paragraph break
    "Ez egy mondat {sfx:Sigh:Ahh} és folytatódik.",
    "Ez zseniális.{sfx:Sigh:Ahhj}",
    "Ez zseniális, már kész is???{sfx:Laughter:Haha} - Plain textben látom!",
    "Első {pause} és {long_pause} és megy tovább. Még egy mondat.",
    "Ez a szám 3.14 és a v1.2 verzión fut {sfx:Sigh:Ahh} rendesen. Vége.",
    "Első mondat. és kisbetűvel folytatódik. Harmadik.",
    "Ismerem az U.S.A.-t. Ez utána jön.",
    "stb. ez a folytatás.",
    "Az ára 12.50 volt. Majd 3.14 jött. Vége.",
]


class _Base(unittest.TestCase):
    """Shared helpers."""

    def _assert_uncut(self, text, needles):
        """No sentence boundary may fall strictly inside any needle."""
        ends = find_sentence_ends(text)
        for needle in needles:
            i = text.find(needle)
            self.assertGreaterEqual(i, 0, "corpus bug: %r not in %r" % (needle, text))
            inner = range(i + 1, i + len(needle))
            for cut in ends:
                self.assertNotIn(cut, inner,
                                 "%r was cut by a sentence boundary inside %r "
                                 "(cuts=%r)" % (needle, text, ends))

    def _assert_reconstruction(self, text):
        """§5 exact-text invariant: units tile the source exactly."""
        units = split_sentence_units(text)
        joined = "".join(t + sep for t, sep in units)
        self.assertEqual(joined, text.strip(),
                         "Reconstruction mutated the text:\n  joined : %r\n  source : %r"
                         % (joined, text.strip()))
        for t, _ in units:
            self.assertIn(t, text,
                          "Sentence %r is not an exact source slice" % t)
        return units


# ---------------------------------------------------------------------------
# §9 Core boundaries (1-4)
# ---------------------------------------------------------------------------
class TestCoreBoundaries(_Base):

    def test_01_normal_sentence_boundary(self):
        self.assertEqual(split_sentences("Ez egy mondat. Ez a következő."),
                         ["Ez egy mondat.", "Ez a következő."])

    def test_02_exclamation_boundary(self):
        self.assertEqual(split_sentences("Első mondat! Második mondat."),
                         ["Első mondat!", "Második mondat."])

    def test_03_question_boundary(self):
        self.assertEqual(split_sentences("Első mondat? Második mondat?"),
                         ["Első mondat?", "Második mondat?"])

    def test_04_ellipsis_boundary_current_semantics(self):
        # Current intended semantics (pre-P3.44.2 AND P3.44.2): a "…"
        # followed by whitespace IS a boundary.
        self.assertEqual(split_sentences("Első mondat… Második mondat."),
                         ["Első mondat…", "Második mondat."])

    def test_punctuation_run_still_one_boundary(self):
        # "???" is ONE run -> ONE boundary (P3.44.2 behaviour kept).
        self.assertEqual(split_sentences("Hogy??? Tessék!"),
                         ["Hogy???", "Tessék!"])


# ---------------------------------------------------------------------------
# §9 Decimals (5-7)
# ---------------------------------------------------------------------------
class TestDecimals(_Base):

    def test_05_decimal_3_14(self):
        self.assertEqual(split_sentences("Az érték 3.14 volt."), ["Az érték 3.14 volt."])
        self._assert_uncut("Az érték 3.14 volt.", ["3.14"])

    def test_06_decimal_12_50(self):
        self.assertEqual(split_sentences("Az ára 12.50 volt."), ["Az ára 12.50 volt."])
        self._assert_uncut("Az ára 12.50 volt.", ["12.50"])

    def test_07_decimal_at_sentence_end(self):
        text = "Az érték 3.14. Ez a vége."
        self.assertEqual(split_sentences(text), ["Az érték 3.14.", "Ez a vége."])
        self._assert_uncut(text, ["3.14"])


# ---------------------------------------------------------------------------
# §9 Versions (8-10)
# ---------------------------------------------------------------------------
class TestVersions(_Base):

    def test_08_version_glm_5_2(self):
        self.assertEqual(split_sentences("GLM 5.2-t használtam."),
                         ["GLM 5.2-t használtam."])
        self._assert_uncut("GLM 5.2-t használtam.", ["5.2"])

    def test_09_version_v1_2(self):
        self.assertEqual(split_sentences("Ez a v1.2 verzió."), ["Ez a v1.2 verzió."])
        self._assert_uncut("Ez a v1.2 verzió.", ["v1.2"])

    def test_10_version_suffix_5_2_t(self):
        text = "Az 5.2-t használtam. Ez jön."
        self.assertEqual(split_sentences(text), ["Az 5.2-t használtam.", "Ez jön."])
        self._assert_uncut(text, ["5.2"])


# ---------------------------------------------------------------------------
# §9 Domain / identifier (11-12)
# ---------------------------------------------------------------------------
class TestDomains(_Base):

    def test_11_domain_test_hu(self):
        self.assertEqual(split_sentences("A test.hu domain."), ["A test.hu domain."])
        self._assert_uncut("A test.hu domain.", ["test.hu"])

    def test_12_realistic_dotted_identifiers(self):
        for text, needle in [
            ("Nézd meg a docs.python.org oldalt.", "docs.python.org"),
            ("Az IP cím 192.168.1.1.", "192.168.1.1"),
            ("A config.yaml fájl.", "config.yaml"),
        ]:
            self.assertEqual(split_sentences(text), [text], text)
            self._assert_uncut(text, [needle])


# ---------------------------------------------------------------------------
# §9 Abbreviation (13-15)
# ---------------------------------------------------------------------------
class TestAbbreviations(_Base):

    def test_13_usa_does_not_fragment(self):
        # The initials stay inside ONE sentence and the lowercase
        # continuation does not become an orphan fragment.
        self.assertEqual(split_sentences("U.S.A. is known for this."),
                         ["U.S.A. is known for this."])
        self._assert_uncut("U.S.A. is known for this.", ["U.S.A."])

    def test_14_abbreviation_examples(self):
        # Lowercase continuation after a single "." -> abbreviation
        # continuation, no boundary (smallest deterministic rule set).
        self.assertEqual(split_sentences("stb. ez a folytatás."),
                         ["stb. ez a folytatás."])
        # Period inside "U.S.A.-t" is glued to "-" -> no boundary.
        self.assertEqual(split_sentences("Ismerem az U.S.A.-t. Ez utána jön."),
                         ["Ismerem az U.S.A.-t.", "Ez utána jön."])

    def test_15_title_abbreviation_baseline_split(self):
        # DOCUMENTED EDGE: "Mr. Smith" splits exactly as it did BEFORE
        # P3.44.2 and during P3.44.2 (period + whitespace + uppercase
        # word = boundary). No abbreviation dictionary is introduced in
        # this phase; the deferred edge case is recorded in the design
        # doc §10.
        self.assertEqual(split_sentences("Mr. Smith arrives."),
                         ["Mr.", "Smith arrives."])


# ---------------------------------------------------------------------------
# §9 Whitespace / exact text (16-20)
# ---------------------------------------------------------------------------
class TestExactText(_Base):

    def test_16_glm_sentence_text_byte_identical(self):
        # THE proven mutation: the old pipeline produced
        # "A GLM 5. 2-t használtam." (space inserted inside 5.2).
        self.assertEqual(split_sentences("A GLM 5.2-t használtam."),
                         ["A GLM 5.2-t használtam."])
        self._assert_reconstruction("A GLM 5.2-t használtam.")

    def test_17_multiple_spaces_preserved(self):
        text = "Első mondat.  Második mondat."
        units = self._assert_reconstruction(text)
        self.assertEqual(len(units), 2)
        self.assertEqual(units[0][1], "  ", "two-space separator normalised")
        self.assertEqual(split_sentences(text),
                         ["Első mondat.", "Második mondat."])

    def test_18_newline_boundary(self):
        text = "Első mondat.\nMásodik mondat."
        units = self._assert_reconstruction(text)
        self.assertEqual(len(units), 2)
        self.assertEqual(units[0][1], "\n")
        # Paragraph break too.
        text2 = "Első mondat.\n\nMásodik mondat."
        units2 = self._assert_reconstruction(text2)
        self.assertEqual(units2[0][1], "\n\n")

    def test_19_punctuation_then_whitespace_is_boundary(self):
        ends = find_sentence_ends("Első mondat. Második.")
        self.assertEqual(len(ends), 2)
        # Also with tab and newline separators.
        self.assertEqual(len(find_sentence_ends("Első.\tMásodik.")), 2)
        self.assertEqual(len(find_sentence_ends("Első.\nMásodik.")), 2)

    def test_20_punctuation_then_nonspace_never_boundary(self):
        for text in DEFECT_CORPUS:
            for needle in UNCUTTABLE_SPANS.get(text, []):
                self._assert_uncut(text, [needle])
        # Hard proof on the raw scanner: "5.2" contributes ONE cut
        # (the sentence end), never two.
        ends = find_sentence_ends("GLM 5.2")
        self.assertEqual(ends, [len("GLM 5.2")] if ends else [])
        # A period glued to a letter mid-token never cuts.
        self.assertEqual(find_sentence_ends("test.hu"), [])


# ---------------------------------------------------------------------------
# §9 Inline markers (21-25)
# ---------------------------------------------------------------------------
class TestInlineMarkers(_Base):

    def test_21_sfx_inside_sentence(self):
        text = "Ez egy mondat {sfx:Sigh:Ahh} és folytatódik."
        self.assertEqual(split_sentences(text), [text])
        self._assert_reconstruction(text)

    def test_22_pause_inside_sentence(self):
        text = "Ez egy mondat {pause} és folytatódik {long_pause} aztán kész."
        self.assertEqual(split_sentences(text), [text])
        self._assert_reconstruction(text)

    def test_23_marker_near_punctuation(self):
        # Marker glued to the punctuation ENDS the sentence (P3.44.2
        # attachment kept) — and never becomes an orphan fragment.
        text = "Ez zseniális.{sfx:Sigh:Ahhj} Következik."
        self.assertEqual(split_sentences(text),
                         ["Ez zseniális.{sfx:Sigh:Ahhj}", "Következik."])
        text2 = "Harmadik mondat következik most???{sfx:Laughter:Haha} És ez jön."
        sents = split_sentences(text2)
        self.assertTrue(any(s.endswith("{sfx:Laughter:Haha}") for s in sents),
                        "marker detached from its sentence: %r" % (sents,))
        self.assertFalse(any("{sfx:Laughter:Haha} És" in s for s in sents),
                         "two sentences glued by an adjacent marker")
        # Marker ACROSS whitespace from the punctuation also attaches.
        text3 = "Első mondat. {sfx:Sigh:Ahhj} Második mondat."
        self.assertEqual(split_sentences(text3),
                         ["Első mondat. {sfx:Sigh:Ahhj}", "Második mondat."])

    def test_24_multiple_markers_one_sentence(self):
        text = ("Ez {sfx:Sigh:Ahh} és {pause} még {long_pause} megy "
                "tovább rendesen.")
        self.assertEqual(split_sentences(text), [text])
        # Punctuation inside onomatopoeia stays shielded (P3.44.2).
        text2 = "Valami vicces {sfx:Laughter:Ha!ha} történt. Még egy."
        self.assertEqual(split_sentences(text2),
                         ["Valami vicces {sfx:Laughter:Ha!ha} történt.",
                          "Még egy."])

    def test_25_marker_plus_decimal_version(self):
        text = "A GLM 5.2-t {sfx:Sigh:Ahh} használtam. Ez jön."
        self.assertEqual(split_sentences(text),
                         ["A GLM 5.2-t {sfx:Sigh:Ahh} használtam.", "Ez jön."])
        self._assert_uncut(text, ["5.2"])


# ---------------------------------------------------------------------------
# §9 Multiple sentences (26-30)
# ---------------------------------------------------------------------------
class TestMultipleSentences(_Base):

    def test_26_normal_multi_sentence_paragraph(self):
        text = "Első mondat. Második mondat. Harmadik mondat. Negyedik."
        self.assertEqual(split_sentences(text),
                         ["Első mondat.", "Második mondat.",
                          "Harmadik mondat.", "Negyedik."])

    def test_27_paragraph_decimals_and_boundaries(self):
        text = "Az érték 3.14. A másik 2.71. Vége."
        self.assertEqual(split_sentences(text),
                         ["Az érték 3.14.", "A másik 2.71.", "Vége."])
        self._assert_reconstruction(text)

    def test_28_paragraph_abbreviation_and_boundary(self):
        text = "U.S.A. is known. Ez a vége."
        self.assertEqual(split_sentences(text),
                         ["U.S.A. is known.", "Ez a vége."])
        self._assert_reconstruction(text)

    def test_29_paragraph_marker_and_boundary(self):
        text = "Első mondat {sfx:Sigh:Ahh}. Második jön. Harmadik zár."
        self.assertEqual(split_sentences(text),
                         ["Első mondat {sfx:Sigh:Ahh}.",
                          "Második jön.", "Harmadik zár."])
        self._assert_reconstruction(text)

    def test_30_all_classes_together(self):
        text = ("A GLM 5.2-t használtam {sfx:Sigh:Ahh} tesztként. "
                "Az érték 3.14 lett. U.S.A. is known for this. "
                "Látogasd meg a test.hu oldalt. Vége.")
        self.assertEqual(split_sentences(text), [
            "A GLM 5.2-t használtam {sfx:Sigh:Ahh} tesztként.",
            "Az érték 3.14 lett.",
            "U.S.A. is known for this.",
            "Látogasd meg a test.hu oldalt.",
            "Vége.",
        ])
        self._assert_reconstruction(text)


# ---------------------------------------------------------------------------
# §10 Cross-implementation consistency
# ---------------------------------------------------------------------------
class TestCrossImplementation(_Base):
    """NarrationSplitter, BlockDetector and the PromptBuilder conflict
    splitter must agree on sentence boundaries for the same input."""

    AGREEMENT_CORPUS = INVARIANT_CORPUS

    def test_splitter_and_detector_boundaries_agree(self):
        det = HeuristicBlockDetector()
        for text in self.AGREEMENT_CORPUS:
            splitter_sents = split_sentences(text)
            det_sents = [d["text"] for d in det._split_sentences(text)]
            self.assertEqual(splitter_sents, det_sents,
                             "NarrationSplitter and BlockDetector disagree "
                             "on %r:\n  splitter: %r\n  detector: %r"
                             % (text, splitter_sents, det_sents))

    def test_conflict_splitter_boundaries_agree(self):
        # The PromptBuilder conflict splitter operates on the COMPILED
        # prompt (markers -> tokens); sentence boundaries must agree
        # with the text-level scanner for the same source.
        pb = PromptBuilder()
        for text in self.AGREEMENT_CORPUS:
            prompt = pb.build(text).final_prompt
            tokenized = split_tokenized_sentences(prompt)
            expected = split_sentences(text)
            self.assertEqual(len(tokenized), len(expected),
                             "Conflict splitter boundary count diverges for "
                             "%r:\n  text-level : %r\n  tokenized   : %r\n"
                             "  prompt: %r"
                             % (text, expected, tokenized, prompt))

    def test_sfx_token_stays_with_its_sentence(self):
        # THE known divergence: "Első mondat. {sfx:Sigh:Ahhj} Második..."
        # compiled to "... <|sfx:sigh|>Ahhj Második..." used to put the
        # SFX TOKEN into the NEXT sentence for conflict attribution.
        pb = PromptBuilder()
        prompt = pb.build("Első mondat. {sfx:Sigh:Ahhj} Második mondat itt van.").final_prompt
        sents = split_tokenized_sentences(prompt)
        self.assertTrue(
            any("<|sfx:sigh|>Ahhj" in s and "Első mondat." in s for s in sents),
            "SFX token separated from its sentence (divergence): %r" % (sents,))

    def test_pause_token_stays_with_its_sentence(self):
        pb = PromptBuilder()
        prompt = pb.build("Első mondat. {pause} Második mondat.").final_prompt
        sents = split_tokenized_sentences(prompt)
        self.assertTrue(
            any("<|prosody:pause|>" in s and "Első mondat." in s for s in sents),
            "Pause token separated from its sentence: %r" % (sents,))

    def test_tokenized_marker_shields_punctuation(self):
        # Onomatopoeia punctuation must stay shielded in the compiled
        # representation too: {sfx:Laughter:Ha!ha} -> <|sfx:laughter|>Ha!ha
        pb = PromptBuilder()
        prompt = pb.build("Vicces {sfx:Laughter:Ha!ha} volt. Még egy.").final_prompt
        sents = split_tokenized_sentences(prompt)
        self.assertTrue(
            any("Ha!ha" in s and "Vicces" in s for s in sents),
            "Onomatopoeia punctuation cut the compiled sentence: %r" % (sents,))


# ---------------------------------------------------------------------------
# §11 Source-span testing
# ---------------------------------------------------------------------------
class TestSourceSpans(_Base):

    def test_span_decimal_inside_one_sentence(self):
        self._assert_uncut("A GLM 5.2-t használtam.", ["5.2"])
        self._assert_uncut("Az érték 3.14 volt pontosan.", ["3.14"])

    def test_span_domain_inside_one_sentence(self):
        self._assert_uncut("A test.hu domain érdekelt.", ["test.hu"])

    def test_span_usa_initials_inside_one_sentence(self):
        self._assert_uncut("U.S.A. is known for this.", ["U.S.A."])

    def test_detector_span_offsets_valid(self):
        det = HeuristicBlockDetector()
        for text in INVARIANT_CORPUS:
            for d in det._split_sentences(text):
                self.assertEqual(text[d["start"]:d["end"]].strip(), d["text"],
                                 "detector span/text mismatch on %r" % text)


# ---------------------------------------------------------------------------
# §13 Invariants (property-style over the battery)
# ---------------------------------------------------------------------------
class TestInvariants(_Base):

    def test_no_character_insertion(self):
        for text in INVARIANT_CORPUS:
            for sent in split_sentences(text):
                self.assertIn(sent, text,
                              "Sentence %r is not a source slice of %r — "
                              "characters were inserted" % (sent, text))

    def test_no_silent_deletion(self):
        for text in INVARIANT_CORPUS:
            units = split_sentence_units(text)
            kept = "".join(t for t, _ in units)
            stripped = "".join(text.split())
            self.assertEqual("".join(kept.split()), stripped,
                             "Non-whitespace text lost on %r:\n  kept: %r"
                             % (text, kept))

    def test_no_reordering(self):
        for text in INVARIANT_CORPUS:
            units = split_sentence_units(text)
            cursor = 0
            for t, _ in units:
                i = text.find(t, cursor)
                self.assertGreaterEqual(i, 0,
                                        "Sentence %r out of order in %r" % (t, text))
                cursor = i + len(t)

    def test_marker_conservation(self):
        for text in INVARIANT_CORPUS:
            units = split_sentence_units(text)
            starts = []
            cursor = 0
            spans = []
            for t, _ in units:
                i = text.find(t, cursor)
                spans.append((i, i + len(t)))
                cursor = i + len(t)
            for m in INLINE_MARKER_RE.finditer(text):
                owners = [s for s, e in spans if s <= m.start() and m.end() <= e]
                self.assertEqual(len(owners), 1,
                                 "marker %r not attached to exactly one "
                                 "sentence in %r" % (m.group(0), text))

    def test_boundary_conservation(self):
        # A punctuation mark that is not a genuine boundary must never
        # create a break.
        for text, needles in UNCUTTABLE_SPANS.items():
            self._assert_uncut(text, needles)

    def test_reconstruction_roundtrip(self):
        for text in INVARIANT_CORPUS:
            self._assert_reconstruction(text)


# ---------------------------------------------------------------------------
# §12 Old-rule divergence witness (pre-P3.44.8 rule characterisation)
# ---------------------------------------------------------------------------
_OLD_PUNCT_RUN_RE = re.compile(r"[.!?…]+")


def _old_rule_sentences(text):
    """Characterisation of the PRE-P3.44.8 NarrationSplitter boundary
    rule: a bare punctuation-run scan with P3.44.2 marker rules but
    WITHOUT the whitespace/context requirement (git evidence: commit
    9acefbe replaced SENTENCE_END_RE.split with _PUNCT_RUN_RE.finditer).
    Kept as the permanent old-code witness for divergence proofs."""
    text = text.strip()
    if not text:
        return []
    marker_spans = [(m.start(), m.end()) for m in INLINE_MARKER_RE.finditer(text)]

    def _marker_at(pos):
        for s, e in marker_spans:
            if s == pos:
                return e
        return None

    cuts = []
    for m in _OLD_PUNCT_RUN_RE.finditer(text):
        if any(s <= m.start() < e for s, e in marker_spans):
            continue
        end = m.end()
        while end < len(text):
            j = end
            while j < len(text) and text[j] in " \t":
                j += 1
            mk_end = _marker_at(j) if j < len(text) else None
            if mk_end is None:
                break
            end = mk_end
        cuts.append(end)
    sentences = []
    pos = 0
    for cut in cuts:
        seg = text[pos:cut].strip()
        if seg:
            sentences.append(seg)
        pos = cut
    tail = text[pos:].strip()
    if tail:
        sentences.append(tail)
    return sentences


class TestOldRuleDivergence(unittest.TestCase):
    """Old rule fails the defect detectors; new rule passes; genuine
    boundary controls agree between old and new."""

    def test_old_rule_splits_defect_corpus(self):
        # The OLD rule fragmented every defect case.
        for text in ["A GLM 5.2-t használtam.", "Az érték 3.14.",
                     "Ez a v1.2 verzió.", "A test.hu domain.",
                     "U.S.A. is known for this.", "Az ára 12.50 volt."]:
            self.assertGreater(len(_old_rule_sentences(text)), 1,
                               "old-rule witness no longer reproduces the "
                               "defect on %r" % text)

    def test_new_rule_passes_defect_corpus(self):
        for text in ["A GLM 5.2-t használtam.", "Az érték 3.14.",
                     "Ez a v1.2 verzió.", "A test.hu domain.",
                     "Az ára 12.50 volt."]:
            self.assertEqual(split_sentences(text), [text])
        self.assertEqual(split_sentences("U.S.A. is known for this."),
                         ["U.S.A. is known for this."])

    def test_old_rule_mutates_text_new_rule_preserves(self):
        old = " ".join(_old_rule_sentences("A GLM 5.2-t használtam."))
        self.assertEqual(old, "A GLM 5. 2-t használtam.",
                         "old-rule witness no longer reproduces the "
                         "text mutation")
        units = split_sentence_units("A GLM 5.2-t használtam.")
        self.assertEqual("".join(t + sep for t, sep in units),
                         "A GLM 5.2-t használtam.")

    def test_controls_agree_on_genuine_boundaries(self):
        # On genuine boundaries (period + single space + capital) the
        # old and new rules produce IDENTICAL sentence lists.
        controls = [
            "Ez egy mondat. Ez a következő.",
            "Első mondat! Második mondat.",
            "Első mondat? Második mondat?",
            "Első mondat… Második mondat.",
            "Mr. Smith arrives.",
        ]
        for text in controls:
            self.assertEqual(_old_rule_sentences(text), split_sentences(text),
                             "control regression on %r" % text)


# ---------------------------------------------------------------------------
# Pipeline-level exactness (SplitPart production surfaces)
# ---------------------------------------------------------------------------
class TestSplitPipeline(unittest.TestCase):

    def setUp(self):
        self.splitter = NarrationSplitter()

    def test_split_plain_part_text_exact(self):
        text = "A GLM 5.2-t használtam."
        parts = self.splitter.split(text=text)
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0].text, text)

    def test_split_plain_long_text_exact_and_uncut(self):
        text = ("A GLM 5.2-t használtam, mert gyors. Az érték 3.14 lett "
                "belőle. Látogasd meg a test.hu oldalt. U.S.A. is known "
                "for this too. Az ára 12.50 volt. Még egy mondat jön most "
                "ide. Egy újabb mondat következik. Vége a szövegnek.")
        parts = self.splitter.split(text=text)
        self.assertGreater(len(parts), 1)
        for p in parts:
            # every part is an exact source slice
            self.assertIn(p.text, text,
                          "part text is not a source slice: %r" % p.text)
        joined = "".join(p.text for p in parts)
        self.assertEqual("".join(joined.split()), "".join(text.split()),
                         "part texts lost user text")

    def test_split_raw_mode_exact(self):
        text = "A GLM 5.2-t használtam <|prosody:pause|> és még valami."
        parts = self.splitter.split(text=text, raw_mode=True)
        self.assertGreaterEqual(len(parts), 1)
        # Raw mode: the part text IS the user's literal prompt — must be
        # byte-identical source slices.
        for p in parts:
            self.assertIn(p.text, text)
            self.assertEqual(p.prompt, p.text, "raw part rebuilt its prompt")

    def test_grouping_preserves_original_separator(self):
        # Two short sentences grouped into ONE part keep the original
        # separator exactly (no whitespace normalisation).
        text = "Első mondat.  Második mondat."
        parts = self.splitter.split(text=text)
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0].text, text)

    def test_grouping_preserves_newline_separator(self):
        text = "Első mondat.\nMásodik mondat."
        parts = self.splitter.split(text=text)
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0].text, text)

    def test_oversize_sentence_own_part(self):
        # A single >MAX_CHARS sentence still gets its own part.
        long_sentence = "A " + "nagyon hosszú " * 40 + "mondat."
        parts = self.splitter.split(text=long_sentence)
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0].text, long_sentence)

    def test_parts_end_at_sentence_boundaries(self):
        text = ". ".join(["Ez a {0}. mondat tartalma".format(i) for i in range(30)]) + "."
        parts = self.splitter.split(text=text)
        self.assertGreater(len(parts), 1)
        for p in parts:
            self.assertTrue(p.text.rstrip().endswith(('.', '!', '?', '…')),
                            "part not sentence-final: %r" % p.text[-30:])

    def test_blocks_mapping_intact(self):
        # §14: block -> part identity/ordering is untouched by the
        # boundary fix (source_block_id, part ordering).
        from engine.narration_blocks import PromptBlock
        text = ("Első blokk GLM 5.2-ről szól. Rövid. " * 3) + \
               "Második blokk 3.14-ről. " + "Harmadik blokk vége."
        b1_end = len("Első blokk GLM 5.2-ről szól. Rövid. " * 3)
        b2_end = b1_end + len("Második blokk 3.14-ről. ")
        blocks = [PromptBlock(start_offset=0, end_offset=b1_end),
                  PromptBlock(start_offset=b1_end, end_offset=b2_end),
                  PromptBlock(start_offset=b2_end, end_offset=len(text))]
        parts = self.splitter.split(text=text, blocks=blocks)
        self.assertEqual(len(parts), 3)
        self.assertEqual([p.source_block_id for p in parts],
                         [b.id for b in blocks])
        self.assertEqual([p.block_index for p in parts], [0, 1, 2])
        for p in parts:
            self.assertIn(p.text, text)

    def test_decimal_no_longer_inflates_part_count(self):
        # Intended consequence of the boundary fix (design doc §8): text
        # whose spurious decimal boundaries previously fragmented into
        # 4 "sentences" per repetition now yields the correct sentence
        # count, so Generate Long produces far fewer, larger parts.
        text = "Az 1.1 és a 2.2 és a 3.3. " * 12
        # Old-rule witness: 4 fragments per repetition ("Az 1." /
        # "1 és a 2." / "2 és a 3." / "3.") x 12 = 48 fragments.
        self.assertEqual(len(_old_rule_sentences(text)), 48,
                         "old-rule witness drifted")
        # New rule: exactly 12 real sentences.
        self.assertEqual(len(split_sentences(text)), 12)
        new_parts = self.splitter.split(text=text)
        # 12 sentences / TARGET_SENTENCES(3) -> 4 parts, never 48/3=16.
        self.assertEqual(len(new_parts), 4)
        self.assertLess(len(new_parts), 16)
        for p in new_parts:
            self.assertIn(p.text, text)
        # The decimals were never cut across parts.
        joined = "\n".join(p.text for p in new_parts)
        for needle in ["1.1", "2.2", "3.3"]:
            self.assertIn(needle, joined)


class TestTokenSpanRegex(unittest.TestCase):
    """The compiled-prompt marker-equivalence spans."""

    def test_sfx_token_span(self):
        m = TOKEN_SPAN_RE.search("<|sfx:laughter|>Haha vége")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(0), "<|sfx:laughter|>Haha")

    def test_pause_token_span(self):
        m = TOKEN_SPAN_RE.search("Első <|prosody:pause|> Második")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(0), "<|prosody:pause|>")
        m2 = TOKEN_SPAN_RE.search("<|prosody:long_pause|>x")
        self.assertEqual(m2.group(0), "<|prosody:long_pause|>")

    def test_sentence_tokens_not_spans(self):
        self.assertIsNone(TOKEN_SPAN_RE.search("<|emotion:elation|>Első mondat."))


if __name__ == "__main__":
    unittest.main()
