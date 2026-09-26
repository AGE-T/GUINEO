> **Archived provenance note (P3.44.5 release).** This is the complete,
verbatim research-audit report of the read-only diagnostic round
(P3.44.4-C) that provided the runtime-proven evidence base for the
P3.44.5 stabilisation phase. The reproduction probes it references
(`ss/p3445_probe_*.py`) were session artifacts outside the application
tree; their findings are permanently captured by
`tests/test_p3_44_5_stabilisation_integrity.py` and summarised in
`docs/design/P3_44_5_STABILISATION_NOTES.md`. No production file was
modified by the audit round itself.

---

# GUINEO — FULL BUG + ARCHITECTURE + DESIGN-FLAW RESEARCH AUDIT

**Kör:** P3.44.4-C — READ-ONLY diagnosztikai / kutatási kör (NEM javítási kör)
**Bázis:** GUINEO P3.44.4-B (working tree `55a78f4`, GUINEO_P3.44.4_current.zip SHA-256 `2575a703…`, 279 fájl, full regression 1292/1292 + launch smoke 11/11 a kör előtt)
**Módszer:** statikus forrás-audit (2 párhuzamos teljes térkép) → futásidejű reprodukció valódi MainWindow/Engine/BatchManager/NarrationEditor offscreen környezetben (csak a modell-határvonal fake-elve) → külső kutatás. **Egyetlen production fájl sem módosult.** A próbák: `ss/p3445_probe_*.py` (a projekt-fán kívül, házikonvenció szerint).

---

## 1. EXECUTIVE SUMMARY

A P3.44.4-B állapot **manager-oldalon tiszta** (a P3.44.4-B futási-szemantika és a P3.44.3 visszajavítás sértetlen — újra lefuttatva mind zöld), de a kutatás **hét, futásidőben bizonyított defektust** és **öt rendszerintű tervezési hibát** tárt fel, amelyek korábban rejtve maradtak, mert a meglévő 1292 teszt a manager- és a prompt-réteget fedi, nem pedig (a) a dialógus-újramegnyitás életciklusát, (b) a szövegszerkesztés→offset számítást, (c) a Re-detect szemantikai megőrzést, (d) a régi/nem generálási struktúra identitás-keveredését.

**A három legsúlyosabb, bizonyított tétel:**

1. **Identitás-keveredés (§11, futásidőben bizonyítva):** a szerkesztés+újranyitás után a `slot_of_asset` a `part_index` pozíció szerint illeszt (a `block_id`-t nem ellenőrzi) → a **régi szövegből generált hang lefedi az ÚJ struktúra slotjait**, a Combine zavartalanul régi hangot szerkeszt az új szerkezetbe, a lefedettség „COMPLETE" — mindez túléli a mentés/újratöltést. Ez a felhasználó által jelentett „Narration Blocks → Batch → szerkesztés → Batch" aggodalom pontos, bizonyított gyökere.
2. **Re-detect csendes szemantikai adatvesztés (Issue E, bizonyítva):** kizárólag Character-hozzárendelés után a Re-detect **párbeszéd nélkül** (`rebuild_everything` direkt ág) törli az összes karaktert — még `lost_character_id` figyelmeztetés sem készül; a „Save" útvonal sima szövegszerkesztésnél is nyomtalanul veszti el a karaktereket; SFX/pause beszúrás **minden** útvonalon elvész; az összes útvonal **minden blokk-ID-t cserél** (a régi assetek/holelyek holt ID-kre hivatkoznak).
3. **Batch-dialógus close→reopen Life-cycle hiba (Issue B + D, bizonyítva):** a `closeEvent` leszedi a change-listenert (P3.44.1 anti-theft), de a `_focus_existing_batch_dialog` ** ugyanazt a dialógust** újra-mutatja a `_connect_manager` újrakötése nélkül → a következő futás alatt a táblázat befagy (csak az induláskori állapot látszik), a futás után a Start gomb beragad tiltottba, a Pause/Stop engedélyezve marad — **a Pause/Stop megnyomására helyreáll** (a jelentett tünet pontos mechanizmusa).

**Egy jelentős regresszió is kiderült:** az Issue C („GLM 5.2" darabolódás) a **P3.44.2-es splitter-átírás által bevezetett regresszió** — a régi `(?<=[.!?…])\s+` szabály véletlenül védte a tizedesvonalakat; az új `[.!?…]+` minden írásjelnél vág, emellett a `_group_sentences` `" ".join()` **szóközt ékel** a törött számokba (a modell mutált szöveget kap: „A GLM 5. 2-t használtam.").

---

## 2. VERIFIED BUGS (futásidőben bizonyítva)

### BUG-1 — Issue B: Batch reopen / sorfrissítés kiesése
- **Reprodukciónk pontosan a spec forgatókönyve** (15 elem, első 3 generálva, ablak bezárása, újranyitás, új generálás).
- **Gyökér:** `batch_generation.closeEvent → _release_manager()` eltávolítja a listenert; `main_window._focus_existing_batch_dialog()` (mw:3178–3182) ugyanazt a dialógust `show()`-olja **újrakötés nélkül**; `_alive_batch_dialog` (mw:3123) csak shiboken-életet vizsgál. A `marshal_to_ui` visszakerül a MainWindow-marshalerre, de a listenerlista üres — minden `_emit_changed` hatástalan.
- **Timeline-bizonyíték (probe B/D):** futás közben `jobs=[GENERATING,PENDING,PENDING] → [COMPLETED,PENDING,PENDING] → … → mind COMPLETED]` miközben a pill-ek végig `['Gen','Queued','Queued']`-ban maradnak; a táblázat csak a futás végén frissül (scene-módban a completion-callbacken át).
- **Elérési utak:** scene-mód (Generate Long ugyanarra a scene-re) ÉS manual-mód (Tools→Batch Generation) is.

### BUG-2 — Issue D: „Batch beragadva Running állapotba"
- **Bizonyítva:** a futás után `manager.is_running == False`, de `Start` gomb letiltva, `Pause/Stop` engedélyezve; **Pause megnyomására** `{start:True, pause:False, stop:False}` — pontosan a jelentett „a UI a Pause/Stop lenyomására magához tér".
- **Gyökér: azonos a BUG-1-gyel** (a gombok a futás-indulási állapotban fagynak be, mert a `_finish_batch` végső `_emit_changed`-je nem ér el senkit). A **BatchManager állapotgépe nem hibás** — a P3.44.4-B szemantika helyes (minden tranzient mező tisztul; ez újra lefuttatva igazolódott).
- **Lejátszás-interakció (A–J mátrix):** strukturális csatolás nincs — a WavePlayer soha nem ír batch-képességi állapotot (statikus audit); a beépülő `'batch' auto_play=False`. Az A–J lejátszási mátrix futásidejű teljes lefuttatása a sandboxban nem történt meg (GL/audió hiány) — **nem reprodukált**, de a statikus analízis szerint a beragadás egyetlen bizonyított útja a stale-listener.
- **Kiegészítő (valószínű, nem a beragadás oka):** a fő Generate gomb per-job villódzik batch közben (`_on_generation_started/finished_ui` jobonként tüzel; a `refresh_generate_capability` szándékosan ignorálja `bm.is_running`-et — dokumentált szerződés, de a mid-batch ablak valós).

### BUG-3 — Issue C: mondatdöntés tizedesvonal/rövidítés nélkül (REGRESSZIÓ)
- **Minden spec-eset darabolódik:** „GLM 5.2"→[`GLM 5.`,`2`], „v1.2", „v2.0", „3.14", „12.50", „U.S.A."→[`U.`,`S.`,`A.`], „test.hu"→[`test.`,`hu`]. Mindkét implementációban (NarrationSplitter ÉS HeuristicBlockDetector).
- **Új altétel — SZÖVEGMUTÁCIÓ:** a csonkok `" ".join()`-ja ékel: a modell `"A GLM 5. 2-t használtam."`, `"A honlap a test. hu címen…"`, `"Az U. S. A. -ban készült."` szöveget kap (a „-” is levált!). A „szöveg nem változhat" invariáns sérül.
- **Regresszió-besorolás (git 9391051):** a P3.44.2 előtti `SENTENCE_END_RE = (?<=[.!?…])\s+` whitespace-követelménye véletlenül védte ezeket; a P3.44.2 marker-ATTACHMENT implementációja a whitespace-követelményt az összes pontuációra eltörölte.
- **Könynyítő körülmény:** normális mondathatárok továbbra is helyesek; a detektor blokk-szintje többnyire „összeesik" egy blokkra (a `_boundary_score` kompenzál), de a mondat-lista és a 400+ karakteres blokkok part-határai sérülnek.

### BUG-4 — Issue A: blokk-offset korrupció szövegtörléskor (új gyökér, mélyebb, mint a bejelentett tünet)
- **A bejelentett Delete-Button folyamat maga tiszta** (modellből eltávolítja, a szöveg marad, a fennmaradó ID-k/címkék sértetlenek — A1–A5 PASS).
- **A valódi gyökér — `NarrationBlockManager.on_text_changed` (engine:371–381):** az első ág feltétele `old_change_start <= block.start_offset` — **a határesetet (törlés pontosan a blokk kezdetén kezdődik) és a balról átfedő törlést is teljes eltolásként kezeli**:
  - középső blokk szövegének törlése → `[53:99]` → **`[7:53]`**: átfedés a szomszéddal, **más blokk szövegének birtoklása**, és a batch-részekben **a B14 szövegének töredék-duplikátuma** generálódik („st block text with a couple…");
  - blokkon átnyúló törlés → `[53:53]` **láthatatlan üres árva**, ami mentés/újratöltésben is fennmarad (case D) és kijelölhetetlen;
  - **Pozicionális újraszámozás (case E):** címkézetlen blokkok auto-címkéje sorszám — a középső törlése után a régi „B16" most „Block 2/B15" néven jelenik meg: **a bejelentett „a B15 azonosító ottmarad" tünet**.
- **Invariáns-sértések (§6):** átfedés, tulajdonosi hiba, töredék/duplikátum generálás.

### BUG-5 — Issue E: Re-detect csendes szemantikai adatvesztés (részben BUG, részben DESIGN FLAW)
- **(a) Párbeszéd nélküliCharacter-pusztítás:** `character_id` nincs a `has_overrides()`-ben, és a `_on_character_changed` (narration_editor:1516–1557) nem állít `manually_edited`-et → `has_manual_edits=False` → a Re-detect **közvetlenül** `rebuild_everything`-et hív (narration_editor:1890–1893) → **összes karakter csendben törölve**, `lost_character_id` sem készül.
- **(b) `preserve_overrides` (Save) sima szerkesztésnél is veszít:** a pontos- és a ≥80%-os substring-illeszkedés kihullik → char_anna/char_mark + emotion/style/label **nyomtalanul** elvesznek (a P3.23 „lost character" figyelmeztetés nem aktiválódik — a lost-lista `[None,None,None,None]`).
- **(c) Split-eset:** a régi B7 „s1. s2. s3." (char+style+emotion) három új blokkra esik → **egyetlen új blokk sem kapja meg** az állapotot (a ≥80%-os pass nem talál illeszkedést) — a spec által feltett „hova kerüljön" kérdésre a jelenlegi válasz: sehova, csendben.
- **(d) SFX/pause beszúrás MINDEN útvonalon elvész** (nincs benne a `_transfer_overrides`-ben sem a `rebuild_automatic_only`-ben).
- **(e) Minden útvonal minden blokk-ID-t cserél** — az asset-/slot-referenciák elhalnak (a §11-keveredés előszobája).
- **(f) Mi működik:** zárt+Apply helyesen megőrzi a karaktert (E10); a szöveg-invariánsok (veszteség/duplikáció/offset/átfedés) minden útvonalon rendben.

### BUG-6 — §11/§15: régi hang fedi az új struktúrát (a „Batch-struktúra érvénytelenné válása")
- **Futásidőben bizonyítva a valós függvényekkel** (`materialize_expected_slots`, `register_generation_result`, `is_slot_covered`, `resolved_slot_sources`, `next_slot_version`):
  - szerkesztés + újranyitás után, **újragenerálás ELŐTT**: minden új slot fedett (S2-1), a slot „latest" assetje a **régi run** hangja (S2-2);
  - a **Combine** (`resolved_slot_sources`) blokkolás nélkül régi szövegű hangot rak az új struktúrába (S2-3);
  - a lefedettség **COMPLETE** „2/3 slot sosem generálták az új szövegből" állapotban (S2-4);
  - a keveredés **túléli a mentést/újratöltést** (S3-4);
  - GP-001 és GP-002 **egyaránt rekonstruálható** külön (az assetek `generation_run`+`block_id`-t hordanak — S3-1/2) — a **historikus rekord rendben van, a származtatott lefedettség/Combine kever**.
- **Gyökér:** a `slot_of_asset` (audio_provenance:174–204) joinja **csak `part_index` szerint** illeszt az AKTUÁLIS `expected_audio_slots`-hoz; a `block_id` mindkét oldalon jelen van, de nincs ellenőrizve. Nincs struktúra-ujjlenyomat → a „ez a batch ugyanabból a struktúrából készült-e, amit most látok?" kérdés megválaszolhatatlan.

---

## 3. LIKELY BUGS (statikusan erős, futásidőben nem vagy csak közvetve igazolt)

- **L-1:** `_on_generate_selected` (mw:3880–3906) nem használ `_scene_owner`-útvonalztatást (szemben `_on_batch_regen` mw:3762) → projektváltás után a COMPLETED jobok **verzió/futtatás-allokáció nélkül** indulnak újrare (felülírási kockázat a verziózott fájl-konvencióhoz képest).
- **L-2:** A fő Generate gomb mid-batch villódzása (per-job started/finished jelek; a képesség-felderítő tudatosan nem nézi a `bm.is_running`-et). A gomb ilyenkor egy kattintásra „already running" figyelmeztetést ad (mw:2445–2447).
- **L-3:** `_check_states` index-kulcsos a slot-kulcsosságot állító kommentek ellenére (bg:779–781 vs 2088) — sor törlése/mozgatása után a felhasználói pipálások **más jobokra** csúszhatnak át.
- **L-4:** Mid-word SFX-beszúrás: az offset nyers karaktpozíció, a materializáció `" "+marker+" "` paddinggal ékel → **szó kettévágása** a modellpromptban (probe §20 megfigyelés: „Ez egy r {sfx:Sigh:Ahh} övid szöveg").

## 4. NOT REPRODUCED

- **Az A–J lejátszási mátrix** (Issue D) teljes futásidejű lefuttatása — a sandboxban nincs GL/valós audió-kimenet; a statikus analízis szerint a WavePlayer és a batch-képességek között **nincs** állapot-író csatolás. A beragadás egyetlen bizonyított útja a stale-listener (BUG-1/2).
- **A „Delete Block gomb → üres B15 marad a MODELLBEN"** — a gombfolyamat tiszta (A1–A5); a tünet a szövegtörlési offset-hibából (BUG-4) és a pozicionális címkézésből (case E) ered.
- **GPU-háttér A/B (§21 J/K)** — offscreen környezetben a QOpenGLWidget kontextus-teremtése sikertelen (a launch smoke-ban is látszik: „QOpenGLWidget: Failed to create context").

## 5. DESIGN FLAWS

1. **A NarrationBlock ≠ GenerationPart láthatósági szakadéka (§7/§8):** az 1:N viszony a **motorban létezik** (`SplitPart.part_of_block/total_parts_in_block`, `BatchJob.slot_id/source_block_id`), és végigfut az assetekig/historyig — de a **felhasználó csak „Part 001/002/…" globális pozíciókat lát**, blokk-csoportosítás nélkül; az `expected_audio_slots` LAPOS lista. A „19 blokk → 31 part" jelenség tehát adatban jelen van, UI-ban láthatatlan.
2. **Pozicionális identitás:** a `part_index` globális sorszám; a `slot_id` = `block_id:part_of_block` — de a join pozíció szerint; címkék sorszámok; a „P001@r001 vs P001@r002" közti **egyetlen** diszkriminátor a `generation_run`.
3. **Nincs struktúra-ujjlenyomat** → a stale-batch szemantika (§14) megvalósíthatatlan: nem lehet észlelni, hogy „megváltozott a forrás azóta".
4. **Több igazságforrás a „generált" állapotra:** `scene.status` + `expected_audio_slots` + `audio_assets` + UI-sor-állapot — a manager-oldali (P3.44.4) rendben, de a provenance-oldali lefedettség a keverő joinból származtatva „hazudik".
5. **Concatenate vs Combine kettősség (§17):** Combine = provenance-vezérelt, verziózott, lineage-bejegyzéses, staleness-ellenőrzős (rendben); **Concatenate** = sorrend a **aktuális queue sorrendjéből**, rögzített fájlnév, **felülír**, nincs scene-bejegyzés, csak history — a „Combine sorrendje soha ne az UI-sorrendből következzen" elv a Concatenate-re nem teljesül (ő ÉPP az UI-sorrend).
6. **Re-detect = teljes ID-csere** (a detekció mindig új objektumokat gyárt) — garantálja a holt hivatkozásokat; a megőrzési passok csak részlegesek (BUG-5).
7. **Túlméretett kézi blokk (§9):** a >400 karakteres blokk csendben több part-ra esik — a felhasználó nem értesül róla (UX-rés), és nincs preflight.
8. **/15 hosszbecslés (§10):** a **prompt** hosszát számolja (token-infláció +102% bizonyítva), a konstans kalibrálatlan (valós adat a workspace-ben nincs — a meglévő 4 asset validációs futtatás műterméke).

## 6. ARCHITECTURAL RISKS (technikai adósság, duplikáció)

- **Három mondat-vágó implementáció:** `NarrationSplitter._split_sentences` (marker-tudatos), `HeuristicBlockDetector._split_sentences` (párhuzamos algoritmus, eltérő kimeneti alak és `\r`/whitespace-részletek), `PromptBuilder._detect_conflicts` `re.split(r'(?<=[.!?])\s+')` (harmadik, divergens szemantika — probe §20e: **leválasztja az SFX-markert a mondatáról**). A `_INLINE_MARKER_RE` **három helyről** van kézzel szinkronban tartva.
- Két token-parszer eltérő konfliktus-szemantikával; `_TOKEN_RE` duplikált; WAV-I/O + LUFS duplikálva; **három** konkatenációs motor; **két** scene-modell (models.Scene vs scene_persistence.DialogueScene); `Engine.app_root` kétszer definiálva (engine.py:482 és 777 — a második árnyékol).
- Halott/nyugdíjazott kód: `modern_control_panel.py` (nincs production importőr), `transport_gl.py` (kihívva), `SENTENCE_END_RE` (kompatibilitási maradvány), a splitterben használatlan `PromptOptimizer`/`PromptBuilder` importok.

## 7. STATE / IDENTITY / PROVENANCE RISKS (összefoglaló)

- **Kié az identitás?** Blokk: `PromptBlock.id` (stabil, de Re-detect/split/create-csorán cserélődik); Part: `slot_id` (származtatott, a `part_index`-szal együtt pozíció-függő); Asset: `AudioAsset.id` (stabil, append-only — **az egyetlen valódi historikus igazságforrás**); Run: `generation_run` (monoton, perzisztált).
- **Kié az állapot?** A futási állapot a BatchManageré (P3.44.4 óta determinisztikusan tisztul); a lefedettség **származtatandó** lengy az assetekből, de a join pozicionális → hamosit.
- **Nyílt tétel (OPEN GAP):** a Scene **current** struktúrája és a **generálási** struktúra között nincs explicit, ellenőrizhető kötés — egyetlen implicit (`expected_audio_slots` felülíródik minden Generate Long-gal).

## 8. RE-DETECT INTEGRITY FINDINGS → l. BUG-5 + §6 mátrix (fenti).

A §6 invariánsok közül a **szövegbiztonság (A–E)** minden útvonalon teljesül; a **szemantikai migrációs invariánsok (F–L)** rendszerint sérülnek: a split-térkép „semmelyik új blokk", a merged/exact térkéép csak zárt blokkra működik, az ambiguous-t a ≥80%-os pass **csendben eldönti** (ha talál illeszkedést — a spec által tiltott vak döntés, jelenleg inkább az ellenkezője: vak elveszés).

## 9. NARRATION BLOCK → GENERATION PART FINDINGS

A lánc **adat-szinten teljes**: `PromptBlock.id` → `SplitPart.source_block_id` (+ `part_of_block/total_parts_in_block`, eff_* öröklés — probe §19: 8 part, mind örökli a character/emotion/style/speed/pitch/delivery-t) → `BatchJob.source_block_id/slot_id/part_index/part_version/generation_run` → `GenerationRequest/Result` → `AudioAsset` → `HistoryEntry`. **Ami hiányzik:** (a) UI-csoportosítás, (b) a slot-lista futtatáshoz/ujjlenyomathoz kötése, (c) a join blokk-diszciplínája.

## 10. BATCH REOPEN / LIFECYCLE FINDINGS → l. BUG-1/2 + §18.

A §18 elv **kódban implementált** (ugyanaz scene+mód → fókusz/újrahasználat; eltérő → tiszta csere + friss dialógus) — de az újrahasználati ág a stale-listener hibát hordozza, és a bezárt-dialógus-életet a `_alive_batch_dialog` nem vizsgálja. Scene-váltás batch közben: a nyitott dialógus a régi Scene-hez kötött marad (dokumentált, elfogadott P3.41-viselkedés).

## 11. COMBINE / SESSION SAFETY FINDINGS

- Combine: expected-slot sorrend, blokkolók fedetlen/hiányzó fájlra, verziózott név, lineage, staleness-ellenőrzés a combined-bejegyzésre — **jó**; de a bemenetát a keverő join adja (BUG-6) → a jó assembler rossz bemenetet kaphat.
- Concatenate: queue-sorrend + rögzített név + felülír + nincs lineage — high-risk a vegyes verziókhoz.
- Cross-session (§15): mindkét plan rekonstruálható; az identitás-ütközés (P001 r001 vs r002) csak a run-mezőn múlik — ami perzisztál, tehát az **historikus** elkülönítés lehetséges, a **származtatott** (coverage/Combine) nem használja.

## 12. RENDERING / FONT FINDINGS

- **Mérhető (offscreen):** a 3 rétegű rgba(.,.,200) felületstack a szöveg-kontrasztot 229→219-re viszi (a glyph-AA éleken a háttér felvilágosodik), az akkumulált felület-opacitás 0,99 — az editor-szöveg egy **háromszorosan komponált átlátszó stacken** úszik, amelynek alját a ambient GPU-shader **minden képkockán** mozgatja.
- **Dokumentált lánc:** QSurfaceFormat alphaBufferSize(8) az QApplication ELŐTT; central_shell/main_area/splitter/editor = WA_TranslucentBackground + WA_NoSystemBackground + autoFillBackground(False); QSS-ben a QWidget/QMainWindow/QDialog háttér transparent; a MainWindow maga NEM translucens (mw:106–108 deadlock-megjegyzés).
- **Nem mérhető itt (GL):** a QOpenGLWidget-gyermek kényszeríti a GL/RHI-komponált ablakfelületet → a raszter-widgetek tartalma textúrán át komponálódik → **Windowsen subpixel-AA (ClearType) elvesztése + frakcionális DPI újramintavételezés** — külső forrásokkal alátámasztva (l. §13), futásidőben itt nem bizonyítható.
- **Nem a betűtípus a hibás** (a spec tiltja a kozmetikai javítást): a jelenség a komponálási láncé.

## 13. EXTERNAL RESEARCH FINDINGS (különbséget téve: FORRÁS-TÉNY vs MÉRNÖKI KÖVETKEZTETÉS vs AJÁNLÁS)

- **[FORRÁS-TÉNY — Qt forum, topic/85525]**: „Sub-Pixel-Antialiasing performs bad on some transparent backgrounds especially when rendering small fonts" — a subpixel-AA és az átlátszó háttér interakciója ismert Qt-jelenség.
- **[FORRÁS-TÉNY — Red Hat Bugzilla 1645763]**: Qt subpixel fallback szűrő erős színtörésekről; sok stack grayscale-be esik vissza.
- **[FORRÁS-TÉNY — Qt hivatalos dokumentáció (doc.qt.io, QOpenGLWidget)]**: a QOpenGLWidget jelenlétekor a **egész ablak** felülete GL-alapú komponálást kap, a testvér-widgetek korlátozottan keverhetők vele — a hivatalos „Limitations" szakasz ismerete alapján (a keresőtalálatok gyenge minősége miatt ez **emlékezetből idézett dokumentációs tartalom**, nem élő link-bizonyíték — a körben nem futtattunk böngészőt).
- **[MÉRNÖKI KÖVETKEZTETÉS]** a GUINEO-láncra: GL-háttér + 3 átlátszó réteg + frakcionális DPI (PassThrough rounding) együtt okozhatnak szöveg-puhulást Windowsen; a standalone demo tisztább szövege ezt **összhangban** van ezzel a mechanizmussal (opaque root → nem GL-komponált raszterút). **Nem bizonyított** ok-okozat ebben a körben.
- **[FORRÁS-TÉNY — NLP-irodalom]**: a mondathatáron-detektálás szakirodalma (SATZ, Palmer 1995; Maximum-Entropy SBD, Le és mtsai 2008; NLTK Punkt) **kifejezetten** kezeli a rövidítéseket/tizedeseket; modern összefoglalók (pl. EPH4 RAG-architektúra útmutató) egyenesen úgy fogalmaznak: „SBD requires more than splitting on periods — abbreviations (Dr., Inc., U.S.), decimal numbers (3.14)…" → az Issue C **a szakmai最佳-gyakorlat ismert követelményének** a hiánya.
- **[FORRÁS-TÉNY — SLSA provenance spec (slsa.dev)]**: immutable tartalom-identitás + meghívás-rögzítés a build-eredményekhez — az §12/§24 „generation plan = immutable snapshot" elv iparági mintája (lakeFS „immutable snapshot with unique ID" modell szintén).
- **[AJÁNLÁS]** ezekből: a fingerprint+immutable-plan javaslat nem divat, hanem a SLSA-mintára épül.

## 14. CURRENT AUTHORITATIVE STATE MAP (WHO OWNS…)

| Szakasz | Identitás | Állapot | Igazság | Artefakt |
|---|---|---|---|---|
| PromptBlock (editor) | `PromptBlock.id` (uuid) — de Re-detect/split/select-create cseréli | offsetek a editorban élnek | **az editor szövege** (a blokk sosem tárol szöveget) | — |
| SplitPart | `source_block_id`+`part_of_block` | eff_* öröklés (bizonyítottan jó) | a splitter futása (efemer) | — |
| BatchJob | `slot_id`/`part_index`/`part_version`/`generation_run` | `JobStatus` | **BatchManager** (futás) | prompt (bekerített szöveg) |
| AudioAsset | `id` (uuid, append-only) | — | **Scene.audio_assets = az egyetlen historikus forrás** | WAV a lemezen |
| HistoryEntry | timestamp-id | — | HistoryManager JSON | bejegyzés |
| Coverage/Combine | — | `scene.status`, slot-fedettség | **SÁRMZTATOTT** — a part_index-joinból (kever!) | combined WAV |

## 15. RECOMMENDED FUTURE MODEL (fogalmi)

A spec §24-modellje **adat-szinten már majdnem** létezik; ami hiányzik, az nem új entitás, hanem **kötés-fegyelem**: (1) a slot↔asset join `block_id`-tudatos legyen; (2) az `expected_audio_slots`-hoz kerüljön `generation_plan_id` (= a már létező `generation_run` első allokációja) és egy `source_structure_fingerprint`; (3) a Batch-dialógus nyitása/Generate Checked/Combine ELŐTT a fingerprint-egyezés gate-elve („Batch structure has changed…" → [Rebuild] [Keep Previous Results] [Cancel]); (4) Re-detect után a régi slotlista *történeti* marad, az új struktúra új plan-t nyit.

## 16. MINIMAL CHANGE OPTIONS (javítási sorrendben — NEM implementálva)

1. **Join-fegyelem (pár sor):** `slot_of_asset`-ben a `part_index`-illesztés mellettkötelező `block_id`-egyezés (legacy fallback a present). Megszünteti a legdurvább keveredést. Kockázat: alacsony; régi projectek (block_id nélküli assetek) a legacy-ágra esnek.
2. **Dialógus-életciklus (kis):** `_focus_existing_batch_dialog`-ban a újramegjelenített dialógusnak **újra kell kötni** a listenert (vagy a close eseményezze a dialógus megsemmisítését). Megszünteti B+D-t. Kockázat: alacsony (a P3.44.1 anti-theft fegyelem sértetlenül tartható).
3. **Splitter rövidítés/tizedes-szabály (kis-nagyobb):** SHIELDING kiterjesztése: a `.` NEM határ, ha szám→szám (`5.2`), nagybetű→nagybetű egymás után (`U.S.A.`), vagy ismert rövidítéslista-tag; ÉS a `_group_sentences` csak eredeti whitespace-szel joinoljon (a mutáció megszüntetése). A HÁROM vágó egy közös utilba vonása. Kockázat: közepes (a P3.44.2 tesztek védik a regressziót).
4. **`on_text_changed` határeset-javítás (kis):** az első ág feltétele szigorú `old_change_start < block.start_offset` legyen + átfedő esetben start-clamp; üres árva törlése a modellből.
5. **Re-detect (kis-nagyobb):** `character_id` be a `has_overrides()`-be (vagy `_on_character_changed` állítson `manually_edited`-et); SFX/pause átvitel a `_transfer_overrides`-be; a split-esetnél explicit döntés (minden új blokk örököljön, vagy lost-jelölés).
6. **Fingerprint + stale-gate (közepes):** l. §15/16. — a „legkisebb stabil mechanizmus".
7. **/15 becslés:** szöveg-hosszra (token nélkül) + tartomány (`~25–35s`), később empirikus kalibráció.

## 17. LARGE / HIGH-RISK CHANGE OPTIONS (csak ha a minimális nem elég)

- GenerationPlan mint önálló, perzisztált entitás (plan_id, fingerprint, rögzített partlista) — jelenleg **nem szükséges** (a slotlista+run már hordozza az információt).
- A három vágó/parse-réteg teljes egyesítése új modulba (nagy tesztfelület).
- A renderelési lánc átírása (opaque editor-felület / RHI-váltás) — csak akkor, ha az A/B bizonyítja, hogy a textúra-komponálás a domináns ok.

## 18. OPEN ARCHITECTURAL DECISIONS

1. Split-eset-szemantika: minden utódblokk örökli az állapotot? Felhasználói döntés?
2. A régi assetek fedettsége strukturaváltás után: „történeti, nem fed" — elfogadható-e a radikális fordulat a join-fegyelemben?
3. Re-detect UTÁN a nyitott Batch sorai: auto-rebuild vagy explicit?
4. Concatenate megtartása előnek vagy unify a Combine-nal?
5. Fingerprint-hash tartalma (szöveg? offset? szemantikai mezők?) — l. §13/§15.

## 19. REQUIRED FOLLOW-UP TESTS (a javító körben)

- A hét probe (`ss/p3445_probe_*.py`) kulcs-assertjeinek regressziós tesztté alakítása: close→reopen→listener újrakötve; gomb-állapot a futás után; „GLM 5.2" mondat-egység + szöveg-byte-azonosság; offset-határesetek (átlapoló/teljes törlés); Re-detect mátrix + character-only útvonal; keveredési mátrix (block_id-join); fingerprint-gate.
- E2E: edit→Batch→generate→edit→Batch again (a spec §25 V–Y soraival) a javítás UTÁN.

## 20. RECOMMENDED FIX ORDER

1. BUG-1/2 (dialógus-életciklus) — a leglátványosabb felhasználói tünet, legkisebb változtatás.
2. BUG-6 join-fegyelem (pár sor) + fingerprint-gate (a végkérdés mechanizmusa).
3. BUG-3 splitter (rövidítés/tizedes + mutáció).
4. BUG-4 offset-határesetek + üres árva takarítás.
5. BUG-5 Re-detect szemantika (character a has_overrides-ben; SFX/pause átvitel; split-döntés).
6. L-1/L-3/L-4 + a /15 becslés.
7. A duplikáció-csökkentés (három vágó egyesítése) utolsóként, nagy tesztfedással.

---

## REQUIRED TABLE

| Issue | Típus | Reprodukált | Gyökérok | Igazságforrás | Identitás-kockázat | Provenance-kockázat | Javítás bonyolultsága | Bizalmi szint |
|---|---|---|---|---|---|---|---|---|
| A (blokk-törlés árva) | BUG | IGEN (runtime) | on_text_changed határeset-ág + clamp; pozicionális címkék | block-modell (offsetek) | MAGAS (ID/offset korrupció, átfedés) | KÖZEPES (töredék/duplikátum part) | ALACSNY-KÖZEPES | MAGAS |
| B (reopen sorfrissítés) | BUG | IGEN (runtime, timeline) | closeEvent→listener-le; fókusz-újrahasználat újrakötés nélkül | BatchManager (jó) vs dialógus-sorok (fagyott) | ALACSNY | ALACSNY | ALACSNY | MAGAS |
| C (GLM 5.2 vágás) | BUG (regresszió) | IGEN (runtime, minden eset) | `[.!?…]+` kontextus nélkül + `" ".join` mutáció | nincs (regex) | ALACSNY | KÖZEPES (mutált szöveg a modellnek) | KÖZEPES | MAGAS |
| D (beragadt Running) | BUG | IGEN (runtime) | azonos stale-listener; gombok futó-állapotban fagynak | BatchManager._running (helyes, használhatatlan) | ALACSNY | ALACSNY | ALACSNY | MAGAS |
| E (Re-detect) | DESIGN FLAW + BUG | IGEN (runtime, mátrix) | character_id nincs a manual-jelzőkben; átviteli passok hiányosak; ID-csere | block-modell | MAGAS (minden ID cserél) | MAGAS (SFX/pause/character vesztes) | KÖZEPES | MAGAS |
| F/§7 (block≠part) | DESIGN FLAW | IGEN (1:N bizonyítva; UI-hiány statikus) | lapos slotlista, nincs csoport/ujjlenyomat | assetek (append-only) | MAGAS | MAGAS | KÖZEPES-NAGY | MAGAS |
| §11 (keveredés) | DESIGN FLAW | IGEN (runtime, end-to-end) | part_index-join block_id nélkül; nincs fingerprint | assetek — de a származtatás kever | MAGAS | MAGAS | ALACSNY (join) + KÖZEPES (gate) | MAGAS |
| §17 (Concatenate) | DESIGN FLAW / ADÓSSÁG | STATIKUS | queue-sorrend, rögzített név, felülír | nincs | KÖZEPES | MAGAS | KÖZEPES | MAGAS (statikus) |
| §20 (harmadik vágó) | TECHNICAL DEBT | IGEN (divergencia bizonyítva) | duplikált mondat-logika | nincs | ALACSNY | ALACSNY | ALACSNY | MAGAS |
| §21 (renderelés) | DESIGN FLAW | RÉSZLEGES (offscreen matematika; GL nem fut) | GL-komponált ablakfelület + 3×translucens réteg | nincs | ALACSNY | ALACSNY | KÖZEPES-NAGY | KÖZEPES |
| §10 (/15 becslés) | UX GAP | IGEN (token-infláció) | prompt-hossz/15 kalibrálatlanul | nincs | ALACSNY | ALACSNY | ALACSNY | MAGAS |
| B–D lejátszás-mátrix (A–J) | UNKNOWN→nem reprodukált | NEM | (statikusan nincs csatolás) | — | — | — | — | statikus: nincs |

---

## IMPORTANT FINAL QUESTION — válasz

**„Mi a legkisebb stabil mechanizmus, amely megakadályozza, hogy a Narration Blocks → Batch → szerkesztés → Batch munkafolyamat egy korábbi generálás struktúráját és identitását egy újonnan létrehozottal keverje?"**

**A jelenlegi mezők (generation_run, slot_id, source_block_id, part_index, part_version, expected_audio_slots) önmagukban NEM elegendők — de majdnem.** Az adat mindehol jelen van (bizonyított: mindkét plan rekonstruálható, a run az egyetlen és perzisztált diszkriminátor); ami hiányzik, **két konkrét dolog**:

1. **A join-diszciplína:** a `slot_of_asset` (audio_provenance.py:174–204) a `part_index` pozíció szerint illeszt és a `block_id`-t figyelmen kívül hagyja — pedig mindkét oldalon ott van. **Egy feltétel** (a part_index-illesztés csak akkor érvényes, ha `asset.block_id == slot.block_id`, különben legacy-ág) megszünteti a keveredés legnagyobb részét anélkül, hogy bármilyen sémaváltás kellene.
2. **A struktúra-kötés:** az `expected_audio_slots`-hoz egy `generation_plan_id` (egyszerűen a slotlistát elsőként allokáló `generation_run` — **nem új entitás, csak egy mező**) és egy **`source_structure_fingerprint`** (determinista hash: szöveg + blokkhatárok + stabil blokk-ID-k + character/emotion/style/speed/pitch/delivery/sfx/pause/speaker) tartozzon, amely **csak Batch-struktúra-fogyasztáskor** (dialógusnyitás / Generate Checked / Combine / verziópopover) van ellenőrizve — a spec §13 szerint, NEM billentyűleütésenként. Egyezés → minden mehet a mai módon; eltérés → a Batch STALE, a [Rebuild Batch]/[Keep Previous Results]/[Cancel] kapu jelenik meg; a régi assetek append-only történetként maradnak (SLSA-minta).

**Nem javasolt nagy refaktor** (új plan-entitás, BatchManager-csere) — az architektúrállag elegánsabb lenne, de a bizonyított defektusokhoz képest **nem必要**, és a stabilitást csökkentené. A fenti két lépés + a dialógus-újrakötés (BUG-1) együtt lefedi a jelentett munkafolyamat-keveredést, a maradék (splitter, offset, Re-detect) önálló, kisebb javítások.

---

## DONE CONDITION — teljesülés

- **Hol készül a blokk:** detekció (HeuristicBlockDetector.detect), split (NarrationBlockManager.split_block), kézi (create_from_selection), deszerializáció (from_dict) — mindegyik új uuid-t ver. **Módosítás:** on_text_changed/on_multi_replace offsettolás (hibás határesetekkel — BUG-4), split/merge, override-setterek. **Part-té válás:** NarrationSplitter._split_with_blocks (oversized → mondatcsoportok, 1:N bizonyítva). **BatchJob:** _start_long_narration (mw:3513–3542) / dialog _on_add / DialogueScene-betöltés. **AudioAsset:** register_generation_result — az EGYETLEN író, append-only. **History:** Engine._execute_generation 5. lépés + Combine/Concatenate-bejegyzések. **Combine:** resolved_slot_sources (expected-slot sorrend). **Re-detect után:** minden blokk-ID halott → join part_index-ra esik → keveredés (bizonyítva). **Szerkesztés után:** azonos (bizonyítva). **Batch-reopen után:** manager állapot tiszta, UI-listener halott (bizonyítva). **Új sessionben:** a keveredés perzisztál (bizonyítva).
- **Minden szakaszra a tulajdonos:** l. §14 táblázat; egyetlen valódi **OPEN GAP**: a jelenlegi struktúra és a generálási struktúra közti explicit, ellenőrizhető kötés (a fingerprint mező hiánya).
