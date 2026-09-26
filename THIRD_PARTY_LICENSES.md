# Third-Party Licences and Attribution

GUINEO bundles or depends on the following third-party components.
This file records **where each component comes from and under which licence
it is distributed**. The legally binding text for the Higgs TTS 3 model is
included verbatim in [`LICENSE_HIGGS_TTS_3.txt`](LICENSE_HIGGS_TTS_3.txt);
for the remaining components the upstream licence text is published at the
linked official sources.

---

## 1. Boson AI — Higgs TTS 3 (Higgs Audio V3) model

| | |
|---|---|
| **Component** | Higgs TTS 3 foundational audio language model (also referred to as "Higgs Audio v3"), used through the community Transformers port `multimodalart/higgs-audio-v3-tts-4b-transformers` |
| **Author** | Boson AI USA, Inc. |
| **Licence** | **Boson Higgs TTS 3 Research and Non-Commercial License** (model weights and model card) |
| **Local copy** | [`LICENSE_HIGGS_TTS_3.txt`](LICENSE_HIGGS_TTS_3.txt) (verbatim, current official text — Last Updated: July 8, 2026) |
| **Official sources** | <https://huggingface.co/bosonai/higgs-tts-3-4b> (canonical upstream model card; `bosonai/higgs-tts-v3-4b` and `bosonai/higgs-audio-v3-tts-4b` redirect to the same repository) · <https://github.com/boson-ai/higgs-audio> (inference code) |
| **Acceptable Use Policy** | <https://boson.ai/acceptable-use> (incorporated by reference into the licence) |
| **Commercial licensing** | <https://boson.ai> · contact@boson.ai |

**Attribution notice** (per Section IV(a) of the licence):

> Boson Higgs TTS 3 is licensed under the Boson Higgs TTS 3 Research and Non-Commercial License, Copyright (c) Boson AI USA, Inc. All Rights Reserved.

**Plain-language summary (not a legal interpretation):**

- GUINEO uses the model **locally, on your machine**; GUINEO itself is a desktop frontend and does not host or serve the model to third parties.
- The licence permits **research and non-commercial use** free of charge.
- The current licence also contains a **Creator Use Grant**: digital creators may create, publish and monetise creative content (podcasts, videos, audiobooks, social-media posts, and similar) on channels they own or control, provided they credit **Boson AI's Higgs Audio** — either in the audio itself or prominently in the accompanying text (for example the video description or show notes). Suggested credit: *"This audio was created with Boson AI's Higgs Audio — https://www.boson.ai/higgs-audio"*.
- The Creator Use Grant explicitly does **not** cover hosting or serving the model via an API/SaaS, redistributing or reselling the model or Derivative Works, or embedding the model in a product made available to third parties. **Any commercial use requires a separate written commercial licence from Boson AI.**
- Downloading the model weights does **not** grant any commercial redistribution right.
- The model card and licence prohibit, among other things: voice cloning or impersonation of real people without their explicit, verifiable consent; deceptive, fraudulent or harassing content; undisclosed synthetic audio where disclosure is required by law; and using outputs to train non-Boson generative models.

See the README ("Higgs TTS 3 Licensing") for the user-facing summary and
[`LICENSE_HIGGS_TTS_3.txt`](LICENSE_HIGGS_TTS_3.txt) for the full licence.

### The Transformers port

The model actually loaded by GUINEO is the community port
[`multimodalart/higgs-audio-v3-tts-4b-transformers`](https://huggingface.co/multimodalart/higgs-audio-v3-tts-4b-transformers),
which redistributes the unchanged Boson checkpoint together with a
format-conversion wrapper. The port's model card states that the licence is
inherited from the upstream checkpoint; the port bundles an older revision of
the same Boson licence (May 21, 2026, without the Creator Use Grant). Because
the weights are unchanged, the governing licence is the upstream Boson Higgs
TTS 3 Research and Non-Commercial License; the current official text of that
licence is what GUINEO ships in [`LICENSE_HIGGS_TTS_3.txt`](LICENSE_HIGGS_TTS_3.txt).

### Boson AI inference code (informational)

The upstream GitHub repository `boson-ai/higgs-audio` carries its *code* under
Apache-2.0; that licence covers the reference implementation, **not** the model
weights or model cards. GUINEO does not use the SGLang/vLLM serving stacks —
it loads the Transformers port directly.

---

## 2. Qt for Python (PySide6)

| | |
|---|---|
| **Component** | PySide6 — the complete desktop user interface (Qt 6) |
| **Author** | The Qt Company / Qt Project |
| **Licence** | LGPL-3.0-or-later (also available under a commercial Qt licence) |
| **Official source** | <https://www.qt.io/product/qt6> · <https://code.qt.io/cgit/pyside/pyside-setup.git> · licence text: <https://doc.qt.io/qt-6/lgpl.html> |

GUINEO links to PySide6 as a Python package; no Qt sources are modified or
redistributed inside this project.

## 3. PyTorch

| | |
|---|---|
| **Component** | PyTorch — tensor computation and model inference (installed by `bootstrap.py` from the official CUDA wheel index) |
| **Author** | PyTorch Foundation / Linux Foundation |
| **Licence** | BSD-3-Clause (BSD-3) |
| **Official source** | <https://pytorch.org> · <https://github.com/pytorch/pytorch/blob/main/LICENSE> |

## 4. Transformers

| | |
|---|---|
| **Component** | Hugging Face Transformers — loads the Higgs Audio V3 model (with `trust_remote_code=True` for the model's custom modelling code) |
| **Author** | Hugging Face |
| **Licence** | Apache-2.0 |
| **Official source** | <https://huggingface.co/docs/transformers> · <https://github.com/huggingface/transformers/blob/main/LICENSE> |

## 5. Hugging Face Hub

| | |
|---|---|
| **Component** | `huggingface_hub` — model download and validation during bootstrap |
| **Author** | Hugging Face |
| **Licence** | Apache-2.0 |
| **Official source** | <https://github.com/huggingface/huggingface_hub/blob/main/LICENSE> |

## 6. NumPy

| | |
|---|---|
| **Component** | NumPy — waveform arrays and audio post-processing |
| **Author** | NumPy developers |
| **Licence** | BSD-3-Clause |
| **Official source** | <https://numpy.org> · <https://github.com/numpy/numpy/blob/main/LICENSE.txt> |

## 7. SoundFile / libsndfile

| | |
|---|---|
| **Component** | `soundfile` — WAV read/write for reference voices and generated output |
| **Author** | Bastian Bechtold et al. (Python wrapper); Erik de Castro Lopo (bundled libsndfile) |
| **Licence** | BSD-3-Clause (Python wrapper); the wheel bundles `libsndfile` under LGPL-2.1-or-later |
| **Official source** | <https://python-soundfile.readthedocs.io> · <https://github.com/bastibe/python-soundfile> |

## 8. sounddevice

| | |
|---|---|
| **Component** | `sounddevice` — in-app audio playback (PortAudio bindings); optional, playback is skipped gracefully if missing |
| **Author** | Bastian Bechtold, Matthias Geier |
| **Licence** | MIT |
| **Official source** | <https://python-soundfile.readthedocs.io/> · <https://github.com/spatialaudio/python-sounddevice/blob/master/LICENSE> |

## 9. PyYAML (optional)

| | |
|---|---|
| **Component** | PyYAML — YAML presets and batch-queue files; **optional** (the app falls back to JSON when absent) |
| **Author** | PyYAML maintainers |
| **Licence** | MIT |
| **Official source** | <https://pyyaml.org> · <https://github.com/yaml/pyyaml/blob/master/LICENSE> |

## 10. Bundled fonts

All fonts are shipped under the **SIL Open Font License 1.1** and are stored
in `assets/fonts/`.

| Font | Weights bundled | Author | Source |
|---|---|---|---|
| Inter | Regular, SemiBold, Bold | Rasmus Andersson / Inter Dry Studio | <https://rsms.me/inter> · <https://github.com/rsms/inter/blob/master/LICENSE.txt> |
| Libre Franklin | SemiBold, Bold | Impallari Type / The Libre Franklin Project | <https://github.com/impallari/Libre-Franklin> |
| JetBrains Mono | Regular | JetBrains | <https://www.jetbrains.com/lp/mono/> · <https://github.com/JetBrains/JetBrainsMono/blob/master/OFL.txt> |

SIL OFL 1.1 text: <https://openfontlicense.org/open-font-license-official-text/>

## 11. FFmpeg (optional, external, not bundled)

MP3 export in Scene→Project Assembly invokes a system-installed `ffmpeg`
(if present) for WAV→MP3 conversion. **FFmpeg is not distributed with
GUINEO.** Depending on the build you install, FFmpeg is licensed under the
LGPL-2.1-or-later or GPL-2.0-or-later (see <https://ffmpeg.org/legal.html>).
WAV output and all other features work without FFmpeg.

---

## Trademarks

"GUINEO" is the product name of this application. "Higgs TTS 3", "Higgs
Audio" and "Boson AI" are marks of Boson AI USA, Inc.; they are referenced
here descriptively, to attribute the underlying model, and their use does not
imply endorsement or affiliation. Per the licence, they must not be used as
the leading or primary element of another product's name.
