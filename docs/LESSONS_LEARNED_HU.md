# SpeechStudio – Fejlesztési Napló és Tapasztalatok

**Projekt:** SpeechStudio – Higgs Audio V3 TTS asztali alkalmazás  
**Időszak:** 2025-06-28 – 2025-06-29  
**Verzió:** Verified Build 6

---

## 1. Projekt Áttekintés

A SpeechStudio egy hordozható Windows asztali alkalmazás, amely a Higgs Audio V3 TTS modellt használja helyi beszédszintézisre. A projekt Python 3.11, PySide6 (Qt 6), PyTorch (CUDA) és Transformers alapokon nyugszik.

### Fő funkciók
- Higgs Audio V3 modell betöltése és kezelése (float16/bfloat16/float32 precízió)
- Hangklónozás referencia audióból
- Narration Blocks rendszer (per-bekezdés érzelm/stílus/prozódia vezérlés)
- Batch Generation (több fájl automatikus generálása)
- Preset rendszer (menthető generációs konfigurációk)
- Fejlett prompt szerkesztő (szintaxis-kiemelés, sorszámozás, keresés/csere)
- Voice Library (hangprofil-kezelés)
- History (generálási előzmények)
- LUFS normalizálás, 32-bit float WAV írás

---

## 2. Architekturális Döntések

### 2.1. Unified Prompt Pipeline
A prompt építése egyetlen metódusban (`MainWindow._build_prompt()`) történik, amelyet a Prompt Preview és a generálás is használ. Ez garantálja, hogy a felhasználó által látott prompt byte-onként megegyezik a modellnek küldött prompttal.

**Tanulság:** A preview és a generálás különböző kódútvonalon történő prompt építése garantáltan szinkronizációs hibákhoz vezet. Egyetlen forrás szükséges.

### 2.2. Engine nem módosítja a promptot
Az Engine `_execute_generation()` metódusa a `request.text`-et változatlanul használja — nem futtat PromptBuilder-t. A UI mindig készíti el a teljes promptot (tokenekkel együtt).

**Tanulság:** Ha az engine módosítja a promptot, a preview és a generálás közötti eltérés nehezen debugolható. Az enginenek csak végre kell hajtania, nem építenie.

### 2.3. SHA-256 Pipeline Verification
Minden pipeline szakaszon SHA-256 hash naplózás történik:
- Stage 1: Prompt Preview
- Stage 2: GenerationRequest.text
- Stage 3: Engine._execute_generation
- Stage 4/5: GenerationManager (model.generate_speech előtt)

**Tanulság:** Ha a modell nem reagál a prompt változásaira, a hash-ek összehasonlítása azonnal megmutatja, hogy a hiba a pipeline-ban van-e vagy a modellben.

### 2.4. ControlPanel batch_update()
Tranzakciós mező-frissítés context managerrel. Több mező egyidejű beállításakor (pl. Preset alkalmazás) csak egyetlen `parameters_changed` signal van.

**Tanulság:** Signal-ok blokkolása (`blockSignals`) hibalehetőséget rejt magában. Egy explicit context manager biztonságosabb és olvashatóbb.

### 2.5. Preset rendszer — tiszta szétválasztás
- `PresetManager` (engine): csak perzisztencia (load/save/list/delete/validate), nincs PySide6 import
- `MainWindow` (UI): apply logika, `ControlPanel.batch_update()` használatával
- `Preset` dataclass: tiszta adattároló, viselkedés nélkül

**Tanulság:** A manager ne tudjon a UI-ról. Az apply logika a UI orkesztrációs rétegben marad.

---

## 3. Kritikus Hibák és Megoldások

### 3.1. QTimer.singleShot worker thread-ből — NEM MŰKÖDIK

**Probléma:** A Batch Generation első feladat után leállt. A logok mutatták, hogy `_on_job_done` lefut a worker thread-en, de a `QTimer.singleShot(0, fn)` hívás, amely a UI thread-re akart marsallálni, sosem hajtódott végre.

**Gyökérok:** A `ThreadPoolExecutor` worker thread-jének nincs Qt event loop-ja. A `QTimer.singleShot` a hívó thread-en hoz létre timert, de az csak akkor fires, ha a thread-nek van event loop-ja. A worker thread sosem dolgozza fel a timert.

**Megoldás:** Qt signal-alapú marsalláló (`_UiMarshaler`):
```python
class _UiMarshaler(QObject):
    call = Signal(object)
    def marshal(self, fn):
        self.call.emit(fn)  # bármely thread-ből → fn a UI thread-en fut
```

**Általános tanulság:** `QTimer.singleShot(0, fn)` NEM thread-safe a worker thread-ekből. Qt signalokat kell használni cross-thread marsallálásra.

### 3.2. QPlainTextEdit.ExtraSelection nem létezik

**Probléma:** `AttributeError: type object 'PySide6.QtWidgets.QPlainTextEdit' has no attribute 'ExtraSelection'`

**Gyökérok:** PySide6-ban az `ExtraSelection` a `QTextEdit`-en van definiálva, nem a `QPlainTextEdit`-en (bár `QPlainTextEdit` örököl `QTextEdit`-ből).

**Megoldás:** `QTextEdit.ExtraSelection()` használata `QPlainTextEdit.ExtraSelection()` helyett.

**Általános tanulság:** PySide6 API-k ellenőrzése kötelező — az öröklés nem garantálja, hogy egy attribútum elérhető az alosztályon.

### 3.3. BlockAwarePlainTextEdit init order

**Probléma:** `AttributeError: 'BlockAwarePlainTextEdit' object has no attribute '_show_blocks'`

**Gyökérok:** A `CodeEditor.__init__()` (szülő osztály) meghívja a `_update_line_number_area_width()`-et, amit a gyerek felülír és `_update_margins()`-t hív, ami hivatkozik `self._show_blocks`-ra. De ezek az attribútumok a `super().__init__()` UTÁN lettek beállítva.

**Megoldás:** Attribútumok beállítása `super().__init__()` ELŐTT.

**Általános tanulság:** Ha a szülő `__init__`-je metódusokat hív, amiket a gyerek felülír, a gyerek attribútumait ELŐBB kell beállítani.

### 3.4. LineNumberArea pozícionálás

**Probléma:** A sorszámozó átfedte a szöveget amikor a block gutter is aktív volt.

**Gyökérok:** A `CodeEditor.resizeEvent` a `LineNumberArea`-t `cr.left()` pozícióra tette. Amikor a `BlockAwarePlainTextEdit` hozzáadta a gutter szélességet a viewport margóhoz, `cr.left()` jobbra tolódott.

**Megoldás:** `resizeEvent` felülírása — `LineNumberArea` mindig `x=0` pozícióra.

### 3.5. Batch WorkerPool "already running" hiba

**Probléma:** A `submit_generation` `RuntimeError("A generation is already running.")` kivételt dobott, amikor a második batch feladat elindult.

**Gyökérok:** A `ThreadPoolExecutor` worker thread-je még a done-callback láncot futtatta, amikor a következő `submit_generation` meghívódott.

**Megoldás:** RuntimeError specifikus elkapás + retry mechanizmus (10 retry, 300ms késleltetéssel). A feladat státusza visszaáll PENDING-re, majd újrapróbálkozik.

### 3.6. Preset könyvtár elérési út

**Probléma:** A preset-ek nem jelentek meg a sidebar-ban.

**Gyökérok:** A `PresetManager` a `settings/presets/` könyvtárat használta, de a `presets/` könyvtár a projekt gyökerében volt.

**Megoldás:** `os.path.join(APP_ROOT, "presets")` a `os.path.join(APP_ROOT, "settings", "presets")` helyett.

---

## 4. Governance Rendszer

### 4.1. Architektúra

A governance rendszer 4 kapuból áll:

```
Architecture Gate → Compile Gate → Feature Gate → Integration Gate → Acceptance Gate → Verified Build
```

### 4.2. Feature Registry mint specifikáció

A Feature Registry nem egy leltár, hanem egy **specifikáció**. Ha egy "implemented" státuszú funkció hiányzik a kódból, az regressziónak minősül.

### 4.3. Verified Build rendszer

Minden build, amely átment minden kapun, git tag-ként van rögzítve (`verified-build-N`). A következő fejlesztés mindig a legutolsó Verified Build-ből indul.

### 4.4. Logger konfiguráció

**Probléma:** A `logging.getLogger("speechstudio.batch")` nem konfigurált handler-eket, így minden log üzenet csendben eldobódott.

**Megoldás:** A projekt `get_logger(component)` függvényének használata, amely megfelelő handler-eket konfigurál.

**Általános tanulság:** Mindig a projekt logger infrastruktúráját használni, nem közvetlen `logging.getLogger()`-t.

---

## 5. Fejlesztési Folyamat Tanulságok

### 5.1. Környezeti visszaállások

A felhőalapú sandbox környezet többször visszaállította a fájlokat egy korábbi pillanatképre. Ez a teljes governance rendszer, git történet és új fájlok elvesztéséhez vezetett.

**Megoldás:** Rendszeres zip export. A végleges védelemhez távoli repository (GitHub/GitLab) szükséges.

### 5.2. Inkrementális fejlesztés

Nagy funkciókat kis, egymástól független mérföldkövekre bontani. Minden mérföldkő önállóan fordítható és tesztelhető.

### 5.3. Impact Analysis kötelező

Minden fájlmódosítás előtt Impact Analysis: mely fájlok érintettek, mely rendszerek függenek tőlük, milyen regressziós kockázatok vannak.

### 5.4. Olvasás módosítás előtt

Mindig `Read` egy fájlt `Edit` előtt. Soha nem feltételezni a fájl tartalmát.

---

## 6. Technológiai Stack Értékelés

### PySide6 (Qt 6)
- **Előny:** Erős signal/slot rendszer, gazdag widget készlet
- **Hátrány:** Cross-thread műveletek trükkösek (QTimer.singleShot nem működik worker thread-ből)
- **Tanulság:** Cross-thread marsallálásra mindig Qt signal-t használni

### ThreadPoolExecutor
- **Előny:** Egyszerű, beépített
- **Hátrány:** A done-callback a worker thread-en fut, nincs event loop
- **Tanulság:** A done-callback-ben csak thread-safe műveleteket végezni (lock védett adatmódosítás), UI frissítést signal-on keresztül

### PyTorch + Transformers
- **Előny:** Higgs Audio V3 modell betöltése straightforward
- **Hátrány:** Nagy VRAM igény (~10GB), float16 precízió szükséges
- **Tanulság:** Model precision load-time paraméter, nem runtime

### YAML konfiguráció
- **Előny:** Ember-olvasható, többsoros string-ek (block scalar), kommentek
- **Hátrány:** Behúzási érzékeny
- **Tanulság:** YAML jobb mint JSON ember-szerkeszthető konfigurációkhoz

---

## 7. Funkcionális Integritás — Hang és Preset Rendszer (Task ID 56)

### 7.1. Néma hang-fallback hiba (CRITICAL)

**Gyökérok:** A `GenerationManager._load_reference_audio()` metódus
`(None, 0)` értéket adott vissza, ha a referencia hang fájl hiányzott,
nem dekódolható, vagy üres volt az útvonal. A hívó (`generate()`) ezután
csendben továbbment a generálással hangklónozás nélkül — a felhasználó
azt hitte, hogy a kiválasztott hang karakterével generál, de valójában
a alapértelmezett (klón nélküli) hangot kapta.

**Hatás:** A felhasználó nem kapott hibaüzenetet, a generálás
"sikeresnek" tűnt, de a hang karakter helytelen volt. Ez a
legveszélyesebb hiba egy TTS alkalmazásban — a felhasználó nem tudja
megmondani, hogy a generált hang rossz-e, amíg túl késő nem lesz.

**Megoldás:**
- `_load_reference_audio()` mostantól **mindig** `ReferenceAudioMissing`
  kivételt dob hiba esetén (üres útvonal, hiányzó fájl, dekódolási hiba).
- Soha nem tér vissza `(None, 0)`.
- A hívó a kivételt továbbterjeszti, ami egy sikertelen `GenerationResult`
  objektumot eredményez, amit a `MainWindow._on_generation_failed_ui`
  `QMessageBox.critical`-gal jelenít meg a felhasználónak.
- A hibaüzenet tartalmazza a hang nevét, a várt útvonalat, és egy
  javasolt műveletet ("Re-import the reference WAV for voice 'X'").

**Regressziós kockázat:** Alacsony. A korábbi `(None, 0)` visszatérési
érték soha nem volt dokumentálva specifikációban, és a hívó kód nem
támaszkodott rá tudatosan.

### 7.2. Dupla preset signal csatlakozások

**Gyökérok:** A `MainWindow._connect_signals()` metódusban az öt preset
signal (`apply_preset_requested`, `save_preset_requested`, stb.) mindegyike
**kétszer** volt csatlakoztatva ugyanahhoz a handler-hez. Egy korábbi
refaktor maradéka volt a második blokk.

**Hatás:** Minden preset művelet (mentés, alkalmazás, törlés, átnevezés)
kétszer hajtódott végre. A "Save Preset" dialógus kétszer jelent meg; az
"Apply Preset" kétszer alkalmazta a beállításokat (idempotens volt, de
felesleges munka); a "Delete Preset" kétszer kérdezte rá a törlésre.

**Megoldás:** A duplikált blokk (korábbi 368-372 sorok) eltávolítása.
Minden signal mostantól **pontosan egyszer** van csatlakoztatva.

**Validáció:** A `tools/verify_functional_integrity.py` G szekciója
ellenőrzi, hogy minden preset signal pontosan egyszer szerepeljen a
forráskódban.

### 7.3. Sidebar hang kiválasztás — privát widget belső elérés

**Gyökérok:** A `MainWindow._on_sidebar_voice_selected()` metódus
közvetlenül elérte a `ControlPanel` privát belső widget-jeit:

```python
for i in range(self._control_panel._voice_section._voice_combo.count()):
    if self._control_panel._voice_section._voice_combo.itemData(i) == voice_id:
        self._control_panel._voice_section._voice_combo.setCurrentIndex(i)
```

Ez három szintű privát attribútum-láncolás volt, és megkerülte a
`ControlPanel.set_selected_voice_id()` publikus API-t, ami signal-blokkolást
is végez.

**Megoldás:** A privát elérés lecserélése a publikus API-ra:

```python
self._control_panel.set_selected_voice_id(voice_id)
```

A `set_selected_voice_id()` blokkolja a `voice_changed` signalt, így nincs
visszacsatolás a sidebar handler-be.

### 7.4. Preset lista — voice_id helyett voice_name

**Gyökérok:** A sidebar preset listája a belső `voice_id`-t mutatta
(`"PresetName  [voice: captain_4c6e8c96]"`). A `voice_id` egy obscures
engine azonosító, amit a felhasználó nem ismer fel.

**Megoldás:**
- Új `_voice_lookup` dict (`{voice_id: voice_name}`) a sidebar-ben és a
  `PresetManagerDialog`-ban.
- `MainWindow._refresh_presets()` felépíti a lookup-ot a
  `Engine.list_voices()` segítségével, és betölti a sidebar-be.
- A preset lista mostantól `"PresetName\n   Voice: Captain"` formátumot
  mutat. Ha a hang hiányzik: `"(voice missing)"`. Ha nincs hang:
  `"(no voice)"`.
- A `voice_id` csak a tooltip-ben jelenik meg (debug célra).

### 7.5. Hiányzó hang profil — néma helyettesítés

**Gyökérok:** A `MainWindow._on_apply_preset()` csendben figyelmen kívül
hagyta, ha a preset `voice_id`-je egy már nem létező hangra hivatkozott.
A kód egyszerűen `if voice:` feltétellel ellenőrzött, és `None` esetén
nem csinált semmit — a felhasználó nem kapott tájékoztatást.

**Megoldás:**
- A preset alkalmazása előtt a `voice_id` feloldása.
- Ha a hang hiányzik: `QMessageBox.warning` a felhasználónak, a voice combo
  "(No voice)"-ra állítása, és a többi preset beállítás (emotion, style,
  prosody, parameters) továbbra is alkalmazása.
- **Soha** nincs néma helyettesítés másik hanggal.
- Engine log: `"Preset '%s' references missing voice_id=%s — applied
  non-voice settings only."`

### 7.6. Preset tárolási útvonal inkonzisztencia

**Gyökérok:** A `F-301_preset_system.md` specifikáció `"settings/presets/"`
útvonalat dokumentált, de a kód `os.path.join(APP_ROOT, "presets")`-t
használt (a projekt gyökérben). A specifikáció és a kód nem egyezett.

**Megoldás:** A specifikáció frissítve, hogy `<app_root>/presets/`
útvonalat dokumentáljon (ami egyezik a kóddal). A
`PresetManagerDialog` a header-ben mutatja a tényleges tárolási útvonalat,
hogy a felhasználó megtalálja a preset fájljait.

### 7.7. VoiceManager.validate() integráció a preflight-ba

**Gyökérok:** Az `Engine._validate()` csak `voice.has_reference`-et
ellenőrizte (ami csak `bool(reference_audio_path) and sample_rate > 0`).
Ez nem védett le olyan eseteket, amikor a fájl ÚTVONALA létezett a
profile-ban, de maga a FÁJL hiányzott a lemezről.

**Megoldás:** Az `Engine._validate()` mostantól meghívja a
`VoiceManager.validate(request.voice_id)` metódust (a meglévő
validációs logikát — nincs duplikáció). A warning-ok kétkategóriásak:

- **BLOKKOLÓ** (generálás megakadályozása):
  - "Reference audio file is missing from disk."
  - "No reference audio file."
  - "Reference audio sample rate not detected."
  - "Voice profile not found."

- **TANÁCSADÓ** (csak loggolva, nem blokkol):
  - "No reference transcript (optional but recommended)."
  - "Reference audio is Xs long; 5-15s is optimal."

### 7.8. VOICE VERIFY / VOICE RESOLVE / VOICE GENERATION loggolás

Új diagnosztikai logok a hangfeloldási pipeline három kritikus
pontján:

1. **VOICE VERIFY** — az `Engine._execute_generation()` legelején,
   mielőtt bármilyen validáció megtörténne. Ez garantálja, hogy a
   felhasználó által kiválasztott hang ID a logba kerüljön, még ha a
   kérés később elbukik is.

2. **VOICE RESOLVE** — a `voice_id` feloldása `VoiceProfile`-já.
   Sikeres esetben: `requested_voice_id`, `resolved_voice_id`,
   `resolved_voice_name`, `reference_audio`. Sikertelen esetben
   (profile nem található): `requested_voice_id NOT FOUND`.

3. **VOICE GENERATION** — azonnal a `model.generate_speech()` hívás
   előtt. Mezők: `voice_id`, `voice_name`, `reference_audio`,
   `reference_sample_rate`. Ez bizonyítja, hogy a ténylegesen a
   modellnek átadott referencia hang egyezik a kiválasztott hanggal.

Ez a három log vonal teljesíthető a `logs/engine.log`-ban, és
ellenőrizhető, hogy a három érték (VERIFY, RESOLVE, GENERATION)
konkordál-e végig a pipeline-on.

### 7.9. Új PresetManagerDialog

A `ProjectSceneSidebar` nem renderelt preset listát — csak a
`presets_clicked` signalt bocsátotta ki, ami a "Save Current" dialógust
nyitotta meg. A preset alkalmazás/törlés/átnevezés/exportálás nem volt
elérhető a felhasználó számára az új sidebar-on.

**Megoldás:** Új `PresetManagerDialog` (`ui/panels/preset_manager_dialog.py`):
- Lista a presetekkel, hang NÉVVEL (nem voice_id).
- Header mutatja a tárolási útvonalat.
- Apply / Save / Delete / Rename / Export / Import gombok.
- Dupla-kattintás = Apply.
- Jobb-klikk context menu (Apply / Rename / Export / Delete).
- Signalok (apply_preset_requested, stb.) továbbítva a MainWindow
  meglévő handler-eibe — nincs duplikált logika.

### 7.10. Teszt lefedettség

Új `tools/verify_functional_integrity.py` teszt suite — 80 teszt, 8
szekcióban (A-H). Nem igényli a Higgs modell betöltését; a PresetManager,
VoiceManager és GenerationManager._load_reference_audio-t közvetlenül
teszteli temp könyvtárakkal. Minden teszt passzol.
