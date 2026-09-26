# SpeechStudio UI Design Specifikáció
# "Modern Dark" — Fázis 1-3

> **Verzió:** 1.0 | **Dátum:** 2026-08-11
> **Cél:** A jelenlegi UI "feel" modernizálása új téma, kártyás layout,
> voice avatarok és emotion ikonok segítségével — a funkcionalitás megtartásával.

---

## 1. Színrendszer

### 1.1 Új téma: "Modern Dark"

A mockup alapján deep charcoal + lila accent színrendszert használunk.
Ez egy új téma lesz a `theme.py` `THEMES` dict-jében.

```
ThemePalette(
    name="modern-dark",

    # Hátterek — deep charcoal, nem kékes-szürke
    bg_base="#0F0F0F",          # fő háttér (majdnem fekete)
    bg_surface="#1A1A1A",       # kártyák, panelok háttér
    bg_surface_alt="#242424",   # másodlagos kártyák, input mezők
    bg_raised="#2E2E2E",        # gombok, kiemelt elemek

    # Szegélyek — finom, alig látható
    border="#333333",
    border_focus="#8B5CF6",     # lila focus szegély

    # Szövegek
    text_primary="#F5F5F5",     # fő szöveg (világos szürke, nem tiszta fehér)
    text_secondary="#A0A0A0",   # másodlagos szöveg
    text_disabled="#555555",    # inaktív szöveg

    # Accent — lila (a mockup elsődleges színe)
    accent="#8B5CF6",           # lila (#8B5CF6)
    accent_hover="#7C3AED",     # sötétebb lila
    accent_pressed="#6D28D9",   # még sötétebb
    accent_text="#FFFFFF",      # lila gombokon fehér szöveg

    # Státusz színek
    success="#10B981",          # zöld
    warning="#F59E0B",          # narancs
    error="#EF4444",            # piros
    info="#3B82F6",             # kék

    # Betűtípus
    font_family='"Segoe UI", "SF Pro Text", "DejaVu Sans", sans-serif',
    font_mono='"JetBrains Mono", "Consolas", "Courier New", monospace',
    font_size="13px",

    # Kerekítés — nagyobb, modernebb
    border_radius="10px",

    # Selection (Retro Console-hoz hasonlóan felülírva)
    selection_bg="#2E2E2E",     # nem lila, mert sötét szöveggel rossz lenne
    selection_text="#F5F5F5",
)
```

### 1.2 Speaker színkódok

Minden speaker saját színt kap a táblázatban és a timeline-ban:

```python
SPEAKER_COLORS = [
    "#3B82F6",  # kék (CAPTAIN)
    "#8B5CF6",  # lila (ENGINEER)
    "#F59E0B",  # narancs (RADIO)
    "#10B981",  # zöld (SCIENTIST)
    "#EF4444",  # piros (AI UNIT)
    "#EC4899",  # rózsaszín
    "#06B6D4",  # cián
    "#F97316",  # sötét narancs
]
# Ciklikus használat: speaker index % len(SPEAKER_COLORS)
```

---

## 2. Layout

### 2.1 Fő ablak (megmarad, csak színek és spacing frissül)

```
┌─────────────────────────────────────────────────────────────┐
│ Menu Bar                                                      │
├─────────────────────────────────────────────────────────────┤
│ Toolbar (Generate gomb nagy lila gomb)                        │
├──────────┬──────────────────────────┬────────────────────────┤
│ Sidebar  │ NarrationEditor          │ ControlPanel           │
│ (tabs)   │ (script editor)          │ (voice/emotion/params) │
│          │                          │                        │
│ 280px    │ stretch                  │ 360px                  │
│          │                          │                        │
│          │                          │ ┌────────────────────┐ │
│          │                          │ │ VOICE CARD         │ │
│          │                          │ │ [avatar] CAPTAIN   │ │
│          │                          │ │ Male, 40s, Calm    │ │
│          │                          │ │ [Change Voice ▼]   │ │
│          │                          │ └────────────────────┘ │
│          │                          │                        │
│          │                          │ EMOTION (ikon grid)    │
│          │                          │ 😊 😢 😡 😨 😌 😎    │
│          │                          │                        │
│          │                          │ SPEED PITCH DELIVERY   │
│          │                          │ [slider] [slider]      │
│          │                          │                        │
│          │                          │ ┌────────────────────┐ │
│          │                          │ │ GENERATE SELECTED  │ │
│          │                          │ │    (nagy lila)     │ │
│          │                          │ └────────────────────┘ │
├──────────┴──────────────────────────┴────────────────────────┤
│ WaveformPlayer (waveform + transport + volume)                │
├─────────────────────────────────────────────────────────────┤
│ StatusBar: "Ready" | GPU: RTX 5070 | VRAM: 8.2/12 GB | 24kHz│
└─────────────────────────────────────────────────────────────┘
```

### 2.2 Kártyás QSS stílus

Minden `QGroupBox` kártyaként jelenik meg:

```css
QGroupBox {
    background-color: #1A1A1A;          /* kártya háttér */
    border: 1px solid #333333;          /* finom szegély */
    border-radius: 10px;                /* nagy kerekítés */
    margin-top: 16px;
    padding: 12px 12px 12px 12px;
    font-weight: 600;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 14px;
    padding: 0 6px;
    color: #8B5CF6;                     /* lila cím */
    background-color: #0F0F0F;          /* háttér a cím mögött */
    text-transform: uppercase;
    font-size: 11px;
    letter-spacing: 0.5px;
}
```

---

## 3. Voice Profil Képek (Avatarok)

### 3.1 Adatmodell kiegészítés

```python
# engine/models.py — VoiceProfile kiegészítése

@dataclass
class VoiceProfile:
    id: str
    name: str
    reference_audio_path: str = ""
    reference_transcript: str = ""
    sample_rate: int = 0
    channels: int = 0
    duration: float = 0.0
    description: str = ""
    tags: List[str] = field(default_factory=list)
    preview_image: str = ""             # ÚJ: kép útvonal (relatív)
    # ÚJ mezők a mockup alapján:
    gender: str = ""                    # "Male", "Female", "Neutral"
    age_range: str = ""                 # "30s", "40s", "Young", "Old"
    mood: str = ""                      # "Calm", "Energetic", "Tired"
```

### 3.2 Tárolás

```
voices/
  <profile_id>/
    profile.json          # metadata (image_path, gender, age_range, mood)
    reference.wav         # referencia audió
    transcript.txt        # opcionális transcript
    avatar.png            # ÚJ: profil kép (opcionális)
```

### 3.3 Importálás

A voice import dialog-ba új mezők:

```
┌─────────────────────────────────────┐
│ Import Voice Profile                 │
│                                      │
│ Name:     [Captain                 ] │
│ WAV file: [reference.wav    ] [Browse]│
│ Avatar:   [avatar.png       ] [Browse]│  ← ÚJ
│ Gender:   [Male ▼]                   │  ← ÚJ
│ Age:      [40s ▼]                    │  ← ÚJ
│ Mood:     [Calm           ]          │  ← ÚJ
│ Transcript (optional):               │
│ [                             ]      │
│                                      │
│         [Cancel]  [Import]           │
└─────────────────────────────────────┘
```

### 3.4 Sidebar — Voice lista kerek avatarokkal

```
┌──────────────────────────────┐
│ VOICES                        │
│                               │
│ ┌──┐ Captain         ● Ready │
│ │🖼│ Male, 40s, Calm         │
│ └──┘                          │
│ ┌──┐ Engineer        ● Ready │
│ │🖼│ Female, 30s, Tired      │
│ └──┘                          │
│ ┌──┐ Radio           ● Ready │
│ │🖼│ Male, 50s, Deep         │
│ └──┘                          │
│                               │
│ [+ Import Voice]              │
└──────────────────────────────┘
```

**QLabel avatar implementáció:**

```python
# Kerek avatar QLabel-ben
avatar_label = QLabel()
pixmap = QPixmap(avatar_path)
# Kerekítés: maszkolt pixmap
rounded = QPixmap(pixmap.size())
rounded.fill(Qt.transparent)
painter = QPainter(rounded)
painter.setRenderHint(QPainter.Antialiasing)
path = QPainterPath()
path.addEllipse(0, 0, 36, 36)  # 36x36 kerek
painter.setClipPath(path)
painter.drawPixmap(0, 0, pixmap.scaled(36, 36, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))
painter.end()
avatar_label.setPixmap(rounded)
avatar_label.setFixedSize(36, 36)
```

### 3.5 Control Panel — Voice kártya

A kiválasztott voice profil nagy kártyaként jelenik meg a control panel tetején:

```
┌────────────────────────────────┐
│  ┌──────┐                       │
│  │      │  CAPTAIN              │
│  │ 🖼   │  Male · 40s · Calm   │
│  │      │  Reference: 3.2s      │
│  └──────┘                       │
│  [Change Voice ▼]               │
└────────────────────────────────┘
```

**Státusz indikátor:** Zöld pont = voice betöltött, szürke = hiányzik.

---

## 4. Emotion Ikon Grid

### 4.1 Ikonos gombok a 21 emotion-hez

A jelenlegi szöveges toggle gombok helyett ikonos gombokat használunk.
Mivel 21 emotion van, a 3×7 vagy 4×6 grid túl sűrű lenne.
**Megoldás:** Két szint — 8 fő emotion ikonos gomb + "More..." dropdown a többinek.

```
┌─────────────────────────────────────────┐
│ EMOTION                                  │
│                                          │
│  😊        😢        😡        😨       │
│ Amusement  Sadness   Anger     Fear      │
│                                          │
│  😌        😎        😮        😐       │
│ Contentment Pride     Surprise  Contempl.│
│                                          │
│  [More emotions ▼]                       │
│  → Elation, Enthusiasm, Determination,  │
│    Affection, Relief, Awe, Longing,     │
│    Arousal, Confusion, Disgust,         │
│    Bitterness, Shame, Helplessness      │
└─────────────────────────────────────────┘
```

### 4.2 Emotion → Emoji leképezés

```python
EMOTION_EMOJIS = {
    "Amusement":     "😊",
    "Sadness":       "😢",
    "Anger":         "😡",
    "Fear":          "😨",
    "Contentment":   "😌",
    "Pride":         "😎",
    "Surprise":      "😮",
    "Contemplation": "😐",
    "Elation":       "😄",
    "Enthusiasm":    "🤩",
    "Determination": "😤",
    "Affection":     "🥰",
    "Relief":        "😮‍💨",
    "Awe":           "😲",
    "Longing":       "🥺",
    "Arousal":       "😰",
    "Confusion":     "😕",
    "Disgust":       "🤢",
    "Bitterness":    "😒",
    "Shame":         "😳",
    "Helplessness":  "😭",
}
```

### 4.3 Gomb stílus

```css
/* Emotion ikon gomb */
QPushButton[role="emotion"] {
    background-color: #242424;
    border: 1px solid #333333;
    border-radius: 8px;
    padding: 8px 4px;
    font-size: 24px;            /* nagy emoji */
    min-width: 56px;
    min-height: 56px;
}
QPushButton[role="emotion"]:checked {
    background-color: #8B5CF6;  /* lila kijelöléskor */
    border: 1px solid #8B5CF6;
}
QPushButton[role="emotion"]:hover:!checked {
    background-color: #2E2E2E;
    border: 1px solid #8B5CF6;
}
```

---

## 5. Generate Gomb

### 5.1 Control panel alján — nagy lila gomb

A control panel legalsó eleme egy nagy, feltűnő Generate gomb:

```
┌────────────────────────────────┐
│ ... (többi control)             │
│                                 │
│ ┌─────────────────────────────┐│
│ │                             ││
│ │    ▶ GENERATE SELECTED      ││
│ │       (Ctrl+Enter)          ││
│ │                             ││
│ └─────────────────────────────┘│
└────────────────────────────────┘
```

### 5.2 Stílus

```css
QPushButton#generate-main {
    background-color: #8B5CF6;
    color: #FFFFFF;
    border: none;
    border-radius: 10px;
    padding: 14px 24px;
    font-size: 15px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}
QPushButton#generate-main:hover {
    background-color: #7C3AED;
}
QPushButton#generate-main:pressed {
    background-color: #6D28D9;
}
QPushButton#generate-main:disabled {
    background-color: #2E2E2E;
    color: #555555;
}
```

### 5.3 Toolbar Generate gomb megmarad

A toolbar Generate gombja is működik (Ctrl+Enter shortcut), de a control panel
gomb a fő vizuális elem.

---

## 6. Rendszer Státusz Bar

### 6.1 StatusBar kiegészítés

A jelenlegi statusBar-ba új elemek (jobb oldalon):

```
[Ready]                              [GPU: RTX 5070] [VRAM: 8.2/12 GB ▓▓▓▓░] [24 kHz] [WAV]
```

### 6.2 VRAM progress bar

```python
# StatusBar-ban egy mini progress bar
vram_bar = QProgressBar()
vram_bar.setRange(0, 100)
vram_bar.setMaximumWidth(120)
vram_bar.setMaximumHeight(12)
vram_bar.setFormat("%p%")
# Stílus: vékony, lila kitöltés
vram_bar.setStyleSheet("""
    QProgressBar {
        background-color: #242424;
        border: 1px solid #333333;
        border-radius: 4px;
        text-align: center;
        font-size: 10px;
    }
    QProgressBar::chunk {
        background-color: #8B5CF6;
        border-radius: 3px;
    }
""")
```

---

## 7. Batch Generation Dialog

### 7.1 Modernebb kártyás megjelenés

A batch dialog táblázata megmarad, de a gombok és a summary kártyás stílust kapnak:

```
┌─────────────────────────────────────────────────────────────┐
│ Batch Generation                                             │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│ ┌───┬──────────────┬──────────┬──────────┬────────────────┐│
│ │ # │ Name         │ Status   │ Duration │ Action         ││
│ ├───┼──────────────┼──────────┼──────────┼────────────────┤│
│ │ 1 │ CAPTAIN: 1   │ ✓ Done   │ 2.84s    │ ▶  ■  ↻       ││
│ │ 2 │ ENGINEER: 2  │ ✓ Done   │ 1.92s    │ ▶  ■  ↻       ││
│ │ 3 │ CAPTAIN: 3   │ ⏳ Gen   │ —        │ ▶  ■  ↻       ││
│ └───┴──────────────┴──────────┴──────────┴────────────────┘│
│                                                              │
│ ┌──────────────────────────────────────────────────────────┐│
│ │ Summary: 2/3 completed · 4.76s audio · RTF 2.34         ││
│ └──────────────────────────────────────────────────────────┘│
│                                                              │
│  [+ Add] [Edit] [- Remove] [Dup]    [⬆] [⬇]                 │
│  [Save Queue] [Load Queue] [Clear]                          │
│                                                              │
│  [▶ Start]  [⏸ Pause]  [⏹ Stop]    [⚓ Concatenate]        │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### 7.2 Speaker színkód a táblázatban

A Name oszlopban a speaker neve színes háttérrel (badge):

```css
/* Speaker badge a táblázat cellájában */
QLabel[role="speaker-badge"] {
    background-color: #3B82F6;  /* speaker színe */
    color: #FFFFFF;
    border-radius: 4px;
    padding: 2px 8px;
    font-size: 10px;
    font-weight: 600;
}
```

---

## 8. Long Narration Dialog

### 8.1 Speaker mapping UI modernizálása

```
┌─────────────────────────────────────────────────────────────┐
│ 🎙 Multi-speaker dialogue  |  5 parts  |  2 speakers        │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  SPEAKER → VOICE MAPPING                                     │
│                                                              │
│  ┌──┐ CAPTAIN    → [Thomas (3.2s)          ▼]               │
│  │🖼│ Male, 40s, Calm                                      │
│  └──┘                                                        │
│  ┌──┐ ENGINEER   → [Maria (2.1s)           ▼]               │
│  │🖼│ Female, 30s, Tired                                    │
│  └──┘                                                        │
│                                                              │
│  DIALOGUE PARTS                                              │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ Part 1 | 🎙 CAPTAIN → Thomas  | ~2s | 45 chars         │ │
│  │ ┌────────────────────────────────────────────────────┐ │ │
│  │ │ Mi a franc volt ez?! Láttad?!                      │ │ │
│  │ └────────────────────────────────────────────────────┘ │ │
│  │ + 0.5s silence                                         │ │
│  └────────────────────────────────────────────────────────┘ │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ Part 2 | 🎙 ENGINEER → Maria  | ~1s | 25 chars         │ │
│  │ ┌────────────────────────────────────────────────────┐ │ │
│  │ │ Nem! Nem láttam semmit!                            │ │ │
│  │ └────────────────────────────────────────────────────┘ │ │
│  │ + 0.5s silence                                         │ │
│  └────────────────────────────────────────────────────────┘ │
│                                                              │
│                    [Cancel]  [Generate]                      │
└─────────────────────────────────────────────────────────────┘
```

---

## 9. Waveform Player

### 9.1 Modernebb transport bar

```
┌─────────────────────────────────────────────────────────────┐
│ 3.8s                                          9.0s          │
│ ┌──────────────────────────────────────────────────────────┐│
│ │ ▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  ││
│ │              ▲ playhead                                   ││
│ └──────────────────────────────────────────────────────────┘│
│  [▶ Play]  [⏸ Pause]  [⏹ Stop]        Vol: ▓▓▓▓▓░░░░ 80%  │
└─────────────────────────────────────────────────────────────┘
```

A waveform színei:
- **Lejátszott rész:** lila (`#8B5CF6`)
- **Nem játszott rész:** sötét szürke (`#333333`)
- **Playhead:** lila függőleges vonal (`#8B5CF6`, 2px)
- **Hover:** halvány lila szaggatott vonal

---

## 10. Implementációs Terv

### Fázis 1 — "Modern Feel" (alacsony kockázat)

| # | Feladat | Fájlok | Munka |
|---|---------|--------|-------|
| 1.1 | Új "Modern Dark" téma hozzáadása | `ui/theme.py` | 30 min |
| 1.2 | Kártyás QSS: border-radius 10px, nagy padding | `ui/theme.py` | 30 min |
| 1.3 | Generate gomb a control panel alján (nagy lila) | `ui/panels/control_panel.py` | 1 óra |
| 1.4 | StatusBar: GPU/VRAM/SampleRate/Format | `ui/panels/status_bar.py` | 1 óra |
| 1.5 | Waveform színek: lila playhead, lila played | `ui/panels/waveform_player.py` | 30 min |

### Fázis 2 — Voice Avatarok (közepes kockázat)

| # | Feladat | Fájlok | Munka |
|---|---------|--------|-------|
| 2.1 | VoiceProfile: image_path, gender, age_range, mood mezők | `engine/models.py`, `engine/voice_manager.py` | 1 óra |
| 2.2 | Import dialog: avatar/gender/age/mood mezők | `ui/main_window.py` | 2 óra |
| 2.3 | Sidebar: kerek avatar a voice listában | `ui/panels/sidebar.py` | 2 óra |
| 2.4 | Control panel: voice kártya képpel | `ui/panels/control_panel.py` | 2 óra |

### Fázis 3 — Emotion Ikonok (alacsony kockázat)

| # | Feladat | Fájlok | Munka |
|---|---------|--------|-------|
| 3.1 | EMOTION_EMOJIS mapping | `engine/higgs_tokens.py` | 15 min |
| 3.2 | Ikonos gombok a 8 fő emotion-hez + "More" dropdown | `ui/panels/control_panel.py` | 2 óra |
| 3.3 | Gomb QSS stílus (kerekített, lila checked) | `ui/theme.py` | 30 min |

### Összesen: ~2-3 nap

---

## 11. Amit NEM csinálunk ebben a fázisban

- ❌ Script tábla nézet (QTableWidget szerkesztő) — túl nagy kockázat
- ❌ Timeline / hullámforma blokkok — új komponens
- ❌ Scene editor UI — külön projekt
- ❌ Character management rendszer — külön projekt
- ❌ A jelenlegi NarrationEditor lecserélése
- ❌ A jelenlegi BatchGeneration lecserélése

---

## 12. Kompatibilitás

- ✅ Minden 4 meglévő téma (Dark, Light, Synthwave, Retro Console) megmarad
- ✅ A "Modern Dark" egy 5. téma lesz
- ✅ A View → Theme menü ciklikusan vált a 5 téma között
- ✅ Minden funkcionalität működik — csak vizuális változások
- ✅ A `update.bat` biztonságosan frissíti az új fájlokat

---

## 13. Példa: A teljes Modern Dark QSS (részlet)

```css
/* ============================================================
   Modern Dark Theme QSS
   ============================================================ */

/* ---- Alap háttér ---- */
QWidget {
    background-color: #0F0F0F;
    font-family: "Segoe UI", "SF Pro Text", sans-serif;
    font-size: 13px;
    color: #F5F5F5;
    outline: none;
}

/* ---- Kártyák (QGroupBox) ---- */
QGroupBox {
    background-color: #1A1A1A;
    border: 1px solid #333333;
    border-radius: 10px;
    margin-top: 16px;
    padding: 12px;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 14px;
    padding: 0 6px;
    color: #8B5CF6;
    background-color: #0F0F0F;
    text-transform: uppercase;
    font-size: 11px;
    letter-spacing: 0.5px;
}

/* ---- Gombok ---- */
QPushButton {
    background-color: #2E2E2E;
    border: 1px solid #333333;
    border-radius: 8px;
    padding: 8px 16px;
    color: #F5F5F5;
    font-weight: 500;
}
QPushButton:hover {
    background-color: #383838;
    border: 1px solid #8B5CF6;
}
QPushButton:pressed {
    background-color: #242424;
}
QPushButton:disabled {
    background-color: #1A1A1A;
    color: #555555;
    border: 1px solid #333333;
}

/* ---- Accent gomb (Generate) ---- */
QPushButton[accent="true"] {
    background-color: #8B5CF6;
    color: #FFFFFF;
    border: none;
    border-radius: 10px;
    font-weight: 700;
    padding: 14px 24px;
    font-size: 14px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}
QPushButton[accent="true"]:hover {
    background-color: #7C3AED;
}
QPushButton[accent="true"]:pressed {
    background-color: #6D28D9;
}

/* ---- Input mezők ---- */
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    background-color: #242424;
    border: 1px solid #333333;
    border-radius: 8px;
    padding: 8px 12px;
    selection-background-color: #8B5CF6;
    selection-color: #FFFFFF;
}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus,
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {
    border: 1px solid #8B5CF6;
}

/* ---- Scrollbar ---- */
QScrollBar:vertical {
    background: transparent;
    width: 14px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #2E2E2E;
    border-radius: 7px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover {
    background: #8B5CF6;
}

/* ---- Táblázat ---- */
QTableWidget {
    background-color: #0F0F0F;
    alternate-background-color: #1A1A1A;
    border: 1px solid #333333;
    border-radius: 10px;
    gridline-color: #333333;
}
QTableWidget::item:selected {
    background-color: #8B5CF6;
    color: #FFFFFF;
}
QHeaderView::section {
    background-color: #1A1A1A;
    color: #8B5CF6;
    padding: 8px 12px;
    border: 1px solid #333333;
    text-transform: uppercase;
    font-weight: 600;
    font-size: 11px;
}

/* ---- Tabs ---- */
QTabWidget::pane {
    border: 1px solid #333333;
    border-radius: 10px;
    background: #0F0F0F;
}
QTabBar::tab {
    background: #1A1A1A;
    color: #A0A0A0;
    padding: 8px 16px;
    border: 1px solid #333333;
    border-bottom: none;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    text-transform: uppercase;
    font-size: 11px;
}
QTabBar::tab:selected {
    background: #0F0F0F;
    color: #F5F5F5;
    border-bottom: 2px solid #8B5CF6;
}

/* ---- Emotion ikon gombok ---- */
QPushButton[role="emotion"] {
    background-color: #242424;
    border: 1px solid #333333;
    border-radius: 8px;
    padding: 8px 4px;
    font-size: 24px;
    min-width: 56px;
    min-height: 56px;
}
QPushButton[role="emotion"]:checked {
    background-color: #8B5CF6;
    border: 1px solid #8B5CF6;
}

/* ---- Status bar ---- */
QStatusBar {
    background-color: #1A1A1A;
    border-top: 1px solid #333333;
    color: #A0A0A0;
    font-size: 11px;
}

/* ---- VRAM progress bar ---- */
QProgressBar {
    background-color: #242424;
    border: 1px solid #333333;
    border-radius: 4px;
    text-align: center;
    font-size: 10px;
    color: #A0A0A0;
}
QProgressBar::chunk {
    background-color: #8B5CF6;
    border-radius: 3px;
}
```

---

## VÉGE

Ez a specifikáció tartalmazza a teljes Modern Dark téma és a voice avatar /
emotion ikon rendszer tervezését. Az implementáció a fázisok szerint
fokozatosan történhet — minden fázis önállóan tesztelhető.
