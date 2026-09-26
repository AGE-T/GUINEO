# SpeechStudio V3 — Funkcionális UI/UX Specifikáció

> **Forrás:** `stitch_orange_ambient_desktop_prototype` (9 HTML referencia képernyő)
> **Cél:** A HTML referencia funkcionális lefordítása PySide6 asztali alkalmazásba
> **Fókusz:** Funkcionalitás, interakciók, állapotok, munkafolyamatok (nem csak vizuális styling)

---

## 1. Alkalmazás Állapotgép

### Állapotok

| Állapot ID | Leírás | Képernyő |
|---|---|---|
| `empty` | Nincs script betöltve, nincsenek klipek | empty_state |
| `script_editing` | Script szerkesztése, sor kijelölve | main_window |
| `script_ready` | Minden sor legenerálva, read-only inspector | script_ready |
| `configuring_engine` | Motor paraméterek hangolása | generation_settings |
| `generating` | Egy sor épp generálódik | generation_in_progress |
| `result_modal` | Generálás kész, eredmény modal | generation_result |
| `batch_queue` | Batch modal, több feladat | batch_generation |
| `importing_voice` | Voice import modal | import_voice_profile |

### Átmenetek

```
[app_start] → empty → (paste/upload/type) → script_editing
                                              ↓ (select line + Generate)
                                           generating
                                              ↓ (generation done)
                                           result_modal
                                              ↓ (Save to Timeline)
                                           script_editing
                                              ↓ (all lines done)
                                           script_ready
```

---

## 2. Főbb Funkciók Listája

### 2.1 Script/Editor
- [x] Új script létrehozása (üres állapotból)
- [x] Script beillesztés vágólapról
- [x] Script feltöltés fájlból
- [x] Script megjelenítése táblázatként (#, Speaker, Dialogue, Emotion, Actions)
- [x] Sor hozzáadása ("Add Line")
- [x] Auto-számozás kapcsoló
- [x] Sor kijelölése (inspector frissül)
- [x] Hover-re action gombok megjelenése
- [x] Inline tag-ek (`[sfx:]`, `[pause:Ns]`, `[whisper]`)
- [ ] Sor státusz követés (Pending/Generating/Done/Error)
- [ ] Sor időbélyeg (start time, duration)
- [ ] Soronkénti lejátszás (play_circle hover-re)
- [ ] Script szűrés (Filter button)
- [ ] Keresés projektben (search bar)
- [ ] Scene név inline szerkesztés (ceruza icon)
- [ ] Recent scenes lista (gyors váltás)
- [ ] Több scene projektbenként
- [ ] Scene szintű success badge ("Generated Successfully")

### 2.2 Voice/Speaker
- [x] Speaker lista színkódolt pontokkal
- [x] Avatar megjelenítés
- [x] Speaker demográfia (gender, age, mood)
- [x] Voice profil rendelés ("Change Voice")
- [x] Voice import (modal: avatar, név, audio, gender, age, mood)
- [ ] Voice modell választás (dropdown)
- [ ] Voice modell info (név, ID, gender/age)
- [ ] Voice Lab (voice management view)
- [ ] Voice próba (hover → play_arrow)
- [ ] Karakter menedzsment

### 2.3 Generálás
- [x] Egy sor generálása ("Generate Selected Line")
- [x] Összes sor generálása ("Generate All")
- [x] Generálás paraméterek (stability, similarity, speed, temperature, top_p)
- [ ] Generálás progress bar (soronként, animált csíkokkal)
- [ ] Státusz badge-ek (Done/Generating/Queued)
- [ ] Becsült idő ("Estimated time: 00:00:08 (1.2x real-time)")
- [ ] RTF metrika (Real-Time Factor)
- [ ] Generálás eredmény modal (waveform + metrikák)
- [ ] Regenerate (újra futtatás)
- [ ] Save to Timeline (eredmény mentése)
- [ ] Download WAV (azonnali export)
- [ ] Prompt Override (opcionális emocionális irány)
- [ ] Allow SFX kapcsoló
- [ ] Generálás közbeni skeleton animáció ("COMPUTING_AUDIO_TENSORS...")
- [ ] Pörögő fogaskerék icon generálás közben
- [ ] Pulszó háttér generálás közben
- [ ] Piros playhead generálás közben (vs. lila lejátszás közben)

### 2.4 Timeline/Lejátszás
- [x] Timeline idővonal (0:00, 0:05, 0:10, 0:15, 0:20, 0:25)
- [x] Speaker sávok (per-speaker tracks)
- [x] Audio klipek inline SVG hullámformával
- [x] Kéttonusú hullámforma (játszott vs. nem játszott)
- [x] Aktív klip kiemelés (border-primary + glow)
- [x] Playhead (függőleges vonal, ponttal)
- [x] Transport controls (skip_previous, play, skip_next)
- [x] Időbélyeg kijelzés (current/total, mono font)
- [x] Hangerő slider
- [ ] Zoom slider
- [ ] Hover brightness klipeken
- [ ] Kattintható klipek (cursor-pointer)
- [ ] TIMELINE/PREVIEW tabok
- [ ] Üres timeline placeholder ("Add content to view timeline")
- [ ] Timeline grid background pattern
- [ ] Klip számok (1, 2, 3) overlay
- [ ] Speaker címkék sáv fejléceken

### 2.5 Batch
- [x] Batch queue modal (2 oszlop: táblázat + összegzés)
- [x] Elem hozzáadása
- [x] Elem szerkesztése
- [x] Elem duplikálása
- [x] Elem eltávolítása
- [x] Queue ürítése
- [x] Queue mentés (fájlba)
- [x] Queue betöltés (fájlból)
- [x] Batch indítás (orange CTA)
- [x] Batch szüneteltetés
- [x] Batch leállítás
- [x] Concatenate (összes klip egyesítése)
- [x] Elem státusz (Done/Generating/Queued)
- [x] Elem duration kijelzés
- [x] Hover action gombok (play/refresh/stop)
- [x] Queue progress bar
- [x] Total generated audio
- [x] RTF batch szinten
- [x] Színkódolt státusz pontok
- [ ] Sticky table header

### 2.6 Beállítások
- [x] Motor paraméterek: Temperature, Top-P, Repetition Penalty
- [x] Output format: WAV/FLAC/MP3
- [x] Sample Rate: 24/44.1/48 kHz
- [x] Bit Depth: 16/24-bit
- [x] Normalize Audio kapcsoló
- [x] Hardware status (Model, GPU%, VRAM)
- [x] Preset Manager (create, select, import, save)
- [ ] Live slider value binding
- [ ] Segmented button groups
- [ ] Preset audition (hover → play)
- [ ] Engine version display
- [ ] "Changes Saved" indicator

### 2.7 Navigáció/UI
- [x] Háromzónás layout (SideNav + Main + Right Panel)
- [x] TopNav (menu + actions)
- [x] SideNav (Script Editor / Voice Lab / Generation / Batch Queue / Export)
- [x] Tabok: SCRIPT/SCENE_SETTINGS, TIMELINE/PREVIEW, VOICE/GENERATION
- [x] Footer (engine info + status)
- [x] GPU ambient background (purple + orange glows)
- [x] Dark mode (default)
- [ ] Empty state placeholders (icon + helper text)
- [ ] Read-only inspector state
- [ ] Modal-over-dimmed-background pattern
- [ ] Micro-interactions (slide-down-fade, slide-up-fade, pulse-subtle)
- [ ] Status badges (success/warning/error dots)
- [ ] Search bar
- [ ] Filter button
- [ ] Undo/Redo
- [ ] Notifications
- [ ] User avatar

---

## 3. Adatmodell

### Entitások

```
Project (1) ──< Scene (M) ──< ScriptLine (M) ──(1) AudioClip
   │                │              │
   │                │              └─< GenerationJob (M) >── VoiceProfile
   │                │
   │                └─< Speaker (M) >── (1) VoiceProfile
   │
   └─< Preset (M)
   └─< BatchQueue (1) ──< BatchJob (M) ──(1) ScriptLine
   └─< VoiceProfile (M)
```

### Fő entitások

- **Project**: name, version, scenes[], presets[], voice_profiles[]
- **Scene**: name, order, script_lines[], speakers[], timeline
- **ScriptLine**: order, speaker, dialogue, emotion, status (pending/generating/done/error), progress, start_time, duration, audio_clip
- **Speaker**: name, color, avatar, demographics, voice_profile
- **VoiceProfile**: name, version, gender, age_group, moods[], source_audio, avatar
- **GenerationParams**: stability, similarity, speed, temperature, top_p, repetition_penalty, allow_sfx, prompt_override
- **AudioClip**: start_time, duration, waveform_samples, sample_rate, format, played_progress
- **BatchJob**: name, status, duration, color_dot
- **Preset**: name, is_active, params snapshot

---

## 4. Hiányzó Funkcionalitás (implementálandó)

### Magas prioritás
1. **Sor státusz követés** — Pending/Generating/Done/Error badge-ek
2. **Generálás progress bar** — animált csíkok, százalék
3. **Generálás eredmény modal** — waveform + metrikák + Regenerate/Save
4. **Empty state** — ikon + helper text + Paste/Upload gombok
5. **Read-only inspector** — amikor minden generálva van
6. **Prompt Override** — opcionális emocionális irány textarea

### Közepes prioritás
7. **Timeline javítás** — zoom, hover brightness, kattintható klipek
8. **Script szűrés** — Filter button
9. **Scene név szerkesztés** — inline ceruza icon
10. **Preset audition** — hover → play_arrow
11. **Hardware status card** — GPU/VRAM progress bar-ok
12. **Output format választó** — segmented button groups

### Alacsony prioritás
13. **Micro-interactions** — slide animations, pulse effects
14. **Search bar** — projekt szintű keresés
15. **Undo/Redo** — history stack
16. **Notifications** — toast üzenetek
17. **User avatar** — account menu
18. **Drag-and-drop** — sor átrendezés, fájl feltöltés

---

## 5. Implementációs Stratégia

### Fázis 1: Funkcionális alapok (jelenleg is)
- [x] Ambient GPU renderer (QOpenGLWidget + 2-blob shader)
- [x] Modern Dark téma (DESIGN.md színek)
- [x] Transzparens panelek (ambient látszik át)
- [x] Transport controls (kör alakú play gomb)
- [x] Status bar (floating pill stílus)
- [x] Orange gradient CTA gombok (Generate, Generate Long)

### Fázis 2: Állapotok és munkafolyamatok
- [ ] Empty state képernyő (ikon + helper text + Paste/Upload)
- [ ] Sor státusz követés (badge-ek + progress)
- [ ] Generálás eredmény modal
- [ ] Read-only inspector (script_ready állapot)
- [ ] Prompt Override textarea

### Fázis 3: Haladó funkciók
- [ ] Timeline javítás (zoom, klipek, hover)
- [ ] Script szűrés és keresés
- [ ] Scene menedzsment (név szerkesztés, recent scenes)
- [ ] Hardware status card
- [ ] Output format választó
- [ ] Preset audition

### Fázis 4: Polish
- [ ] Micro-interactions (slide, pulse, shrink)
- [ ] Status badges (success/warning/error dots)
- [ ] Modal-over-dimmed-background pattern
- [ ] Empty state placeholders
- [ ] Material Symbols ikonok
- [ ] Google Fonts betöltés
