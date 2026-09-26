# GUINEO

<p align="center">
  <img src="assets/brand/guineo_logo.png" alt="GUINEO logó" width="180"/>
</p>

**GUINEO** (fejlesztői nevén *SpeechStudio*) egy hordozható asztali
alkalmazás helyi beszédszintézishez, a Boson AI **Higgs TTS 3 (Higgs Audio
V3)** modelljére építve. Minden feldolgozás a saját gépeden történik — felhő
és telemetria nélkül.

[🇬🇧 English documentation](README.md) ·
[GitHub-tároló](https://github.com/AGE-T/GUINEO)

---

<a id="magyar"></a>

# HU | Magyar

<a id="ujdonsagok"></a>
## Újdonságok a P3.44.5-ben

A P3.44.5 egy **stabilizációs és integritási kiadás**: két függetlenül
reprodukált hibát javít a pontos tulajdonosi pontjukon, és mindkettőt új,
állandó regressziós tesztkészlet zárja le. Semmilyen más viselkedés nem
változott.

- **A Batch ablak életciklusa (a kutatási audit B és D jelű hibái).** Egy
  Batch Generation ablak, amelyet bezártál és később újra megnyitottál (ugyanaz
  a jelenet, ugyanaz a mód), korábban nem kapott többé frissítést a futó
  kötegtől: a sorok állapota megfagyott, a Start letiltva, a Pause/Stop
  engedélyezve maradt a befejezés után is, és az ablak csak akkor épült fel
  újra, ha megnyomtad a Pause vagy a Stop gombot. Most az ablak minden
  megjelenítése **idempotensen** újra létrehozza a manager figyelőjét
  (pontosan egy figyelő, sosem duplikátum), és a teljes táblát szinkronizálja
  a manager tényleges állapotából — az újra megnyitott ablak mindig a
  valóságot mutatja, akkor is, ha a köteg az ablak zárva tartása alatt
  fejeződött be. Maga a BatchManager állapotgép végig helyes volt, és nem
  változott.
- **Blokk-tudatos hangproveniencia párosítás.** A generált hang most a
  jelenet várt slot-jaihoz **blokk-azonosság** (`block_id`) szerint párosul,
  nem pusztán globális pozíció szerint. Korábban egy szerkesztés után a régi
  hangfájl, amelynek pozíciója véletlenül egybeesett az új struktúrával,
  csendben lefedettségnek tekintetett — a Combine Scene pedig régi hangot
  tudott az új szöveghez összerakni. A régi hang többé nem tud más
  struktúrájú generálási terv lefedettségének álcázni magát.
- **29 új regressziós teszt**
  (`tests/test_p3_44_5_stabilisation_integrity.py`) rögzíti mindkét hibát:
  17 közülük a P3.44.5 előtti kódon elbukik, a javítás után mind a 29 zöld.

Implementációs jegyzőkönyv: `docs/design/P3_44_5_STABILISATION_NOTES.md`;
bizonyítási alap: `docs/audits/P3_44_5_RESEARCH_AUDIT.md`.

## Tartalom

1. [Újdonságok a P3.44.5-ben](#ujdonsagok)
2. [Mi a GUINEO?](#mi-a-guineo)
3. [Telepítés](#telepítés)
4. [Frissítés](#frissítés)
5. [Hardverkövetelmények](#hardverkövetelmények)
6. [Első lépések — gyors útmutató](#első-lépések)
7. [A munkaterület áttekintése](#a-munkaterület-áttekintése)
8. [Projektek](#projektek)
9. [Jelenetek (Scenes)](#jelenetek)
10. [Szereplők (Characters)](#szereplők)
11. [Hangprofilok (Voice Profiles)](#hangprofilok)
12. [Narrációs blokkok és szerkesztőmódok](#narrációs-blokkok)
13. [Vezérlőpult — Friendly és Advanced mód](#vezérlőpult)
14. [Hanggenerálás](#hanggenerálás)
15. [Hosszú generálás (Long Generation)](#hosszú-generálás)
16. [Batch Generation — két munkafolyamat, egy rendszer](#batch-generation)
17. [Verziók és a jelenet hangkompozíciója](#hangkompozíció)
18. [STALE — az elavult kimenet](#stale)
19. [Projekt összerakás (Project Assembly)](#projekt-összerakás)
20. [Exportálás](#exportálás)
21. [Előzmények (History)](#előzmények)
22. [Recents és Context Lists](#recents)
23. [Témák, teljes képernyő, hangerő](#témák)
24. [A modell és a nyelvek](#a-modell)
25. [Anomáliadetektálás hosszú generálásnál](#anomáliadetektálás)
26. [Hibakezelés és útvonalbiztonság](#hibakezelés)
27. [Adatmegőrzés — mi marad meg újraindítás után?](#adatmegőrzés)
28. [Billentyűparancsok](#billentyűparancsok)
29. [Menük](#menük)
30. [Beállítások (Settings)](#beállítások)
31. [Verzió- és build-információ](#verzió-információ)
32. [Kiadástörténet](#kiadasok)
33. [Higgs TTS 3 licencelés](#higgs-licenc-hu)
34. [Hibaelhárítás](#hibaelhárítás)
35. [Fejlesztőknek — tároló és tesztek](#fejlesztoknek)

<a id="mi-a-guineo"></a>
## Mi a GUINEO?

A GUINEO egy **helyi futó, hordozható Windows asztali alkalmazás** beszélt
hang (speech) generálására. Az alapja a Boson AI **Higgs TTS 3** (más néven
Higgs Audio V3) nyílt súlyú, 4 milliárd paraméteres audio-nyelvi modell,
amelyet a gépeden, NVIDIA GPU-n futtat. Az alkalmazás:

- **projektekbe, jelenetekbe és narrációs blokkokba** szervezi a szöveget,
- **szereplőket (Characters)** kezel mindegyik saját hanggal és színazonosítással,
- **hangprofilokból** (Voice Profiles) zero-shot hangklónozást végez egy rövid
  referenciaminta alapján,
- **hosszú szövegeket** automatikusan darabol és kötegelt (batch) módon
  generál le,
- a jelenetek hangjait **verziózott, visszakövethető (provenance) rendszerben**
  kezeli,
- és a kész jelenetekből **egyetben összerakott audiót** készít.

Minden feldolgozás helyi: nincs felhő, nincs telemetria, a generált fájlok a
te merevlemezeden maradnak. A modell súlyait (~9 GB) az első indításkor
tölti le az alkalmazás, utána internet nélkül is működik.

<a id="telepítés"></a>
## Telepítés

**Követelmények:** Windows 10/11 (64 bites), Python 3.11 (pontosan ezt a
verziót), NVIDIA GPU CUDA-támogatással, kb. 12 GB szabad lemezterület,
internetkapcsolat az első futtatáshoz.

1. Töltsd le a legújabb kiadás ZIP-jét (pl.
   `releases/GUINEO_P3.44.5_current.zip` a
   [GitHub-tárolóból](https://github.com/AGE-T/GUINEO)), és csomagold ki a
   GUINEO mappát tetszőleges helyre (pl. `C:\GUINEO`).
2. Telepítsd a **Python 3.11**-et a
   [python.org](https://www.python.org/downloads/release/python-3119/)
   oldalról — a telepítőben jelöld be az „Add Python to PATH" opciót.
3. Kattints duplán a **`launch.bat`** fájlra.

Az első futtatás automatikusan elvégzi a teljes beállítást:

- létrehozza a saját virtuális környezetet (`venv\`),
- telepíti a PyTorch-ot, Transformerst, PySide6-ot és a többi függőséget,
- letölti a Higgs TTS 3 modellt (~9 GB, egyszeri alkalommal),
- majd elindítja a GUINEO ablakát.

**Az első letöltés időigényes** (az internetkapcsolattól függően 10–60 perc),
de **folytatható**: ha megszakad, egyszerűen futtasd újra a `launch.bat`-ot.
Utána minden indítás gyors, és nem kell internet.

Ha a `launch.bat` nem találja a Pythont, telepítsd a 3.11-es verziót a
fenti linkről, és ellenőrizd, hogy a „Add Python to PATH" be van-e pipálva.

<a id="frissítés"></a>
## Frissítés

Új verzióra frissítés **adatvesztés nélkül**:

1. Töltsd le az új verzió ZIP fájlját a GitHub-tároló `releases/` mappájából
   ([AGE-T/GUINEO](https://github.com/AGE-T/GUINEO)), és nevezd át
   **`SpeechStudio_clean.zip`**-re.
2. Helyezd a GUINEO mappába (a `SpeechStudio.py` mellé).
3. Zárd be a futó GUINEO-t.
4. Futtasd a **`update.bat`** fájlt.

A szkript biztonsági másolatot készít a régi forráskódról, majd **kizárólag
a forrásfájlokat** cseréli ki. Az összes személyes adata megmarad:
`settings\`, `voices\`, `outputs\`, `presets\`, `models\`.

**Soha ne töröld ki a teljes mappát az újracsomagoláshoz** — mindig az
`update.bat`-ot használd.

<a id="hardverkövetelmények"></a>
## Hardverkövetelmények

| Szint | Követelmény |
|---|---|
| **Minimum** | Windows 10/11 64 bites, Python 3.11, CUDA-képes NVIDIA GPU, ~12 GB szabad lemezterület. CPU-n is működik a tartalék üzemmód, de a generálás ott gyakorlatilag túl lassa. |
| **Ajánlott** | Modern NVIDIA RTX GPU ~10 GB szabad VRAM-mal (a modell bfloat16 pontossággal kb. 10 GB VRAM-ot használ). A fejlesztés és tesztelés GeForce RTX 5070-es GPU-n történt. |
| **Hosszú / nagy generálás** | A hosszú szövegek sok rövid részesre vannak darabolva, ezért a VRAM-igény **nem nő** a szöveg hosszával — a gyakorlati korlát az idő és a lemezterület. A státuszsor és az Előzmények valós idejű faktort (RTF) mutatnak. |

<a id="első-lépések"></a>
## Első lépések

Egy teljes kört 5 percben:

1. **Indítsd el** a `launch.bat`-ot. Az első induláskor az alkalmazás felajánlja
   a modell betöltését (kb. 10–30 másodperc, ~10 GB VRAM). Ha most nem töltöd
   be, a **Generate** megnyomásakor automatikusan betöltődik.
2. **Írj a szerkesztőbe** egy kis szöveget (első indításnál már van ott egy
   rövid magyar mintaszöveg).
3. **Válassz hangot**: jobb oldali panel → **VOICE** fül → *Change Voice* →
   válassz hangot, vagy importálj referenciát (lásd
   [Hangprofilok](#hangprofilok)).
4. Nyomj **Ctrl+Enter**-t (vagy kattints a narancssárga **GENERATE** gombra).
5. A kész hang a lenti **hullámforma-lejátszón** jelenik meg — kattints vagy
   húzz rá a tekeréshez, a hangerőcsúszkával állíthatod a hangosítást.

A generált fájl az `outputs\` mappába kerül (24 000 Hz mono WAV). Minden
generálás bekerül az **Előzményekbe** (Ctrl+H), ahonnan később visszahozható
a teljes beállítás.

<a id="a-munkaterület-áttekintése"></a>
## A munkaterület áttekintése

- **Bal oldalsáv**: Projektek / Jelenetek / Előzmények / Szereplők navigáció,
  RECENTS (legutóbbiak) panel, kontextuslisták (ALL PROJECTS, ALL SCENES,
  ALL CHARACTERS, ALL HISTORY), alul rögzített **Assemble Audio** gomb.
- **Középen**: a szövegszerkesztő (Plain Text / Narration Blocks / Preview
  módok) és alatta a hullámforma-lejátszó (transzport).
- **Jobb panel**: **FRIENDLY** és **ADVANCED** fül — hang, érzelem, stílus,
  prozódia, hangeffektek és generálási paraméterek.
- **Felül**: bal sarokban a hivatalos **GUINEO logó** (fehér szójel),
  navigációs menük (File, Edit, View, Project, Help), undo/redo,
  mentés, export és a GENERATE gomb.
- **Alul**: státuszsor — modell állapota, CUDA/GPU, generálási állapot,
  kiadott hang, mért generálási idő (RTF) és a verzió.

<a id="projektek"></a>
## Projektek

A **projekt** a legfelső szintű tároló: jelenetek, szereplők, blokkok,
hangok és előzmények együtt. `Ctrl+N` létrehoz újat, `Ctrl+O` nyit meg,
`Ctrl+S` ment. A projektek a `projects\` mappában élnek (`project.json`),
automatikusan mentődnek a jelenetváltásnál és kilépéskor. A Projects
legördülő menüből átnevezhetők és törölhetők, a bal oldalsávból pedig
gyorsan válthatók. A **Recents** lista az utolsó 5 projektet őrzi.

<a id="jelenetek"></a>
## Jelenetek

A **jelenet (Scene)** egy projekten belüli, önálló szöveg- és hangegység —
pl. egy fejezet, egy párbeszéd vagy egy jelenet a forgatókönyvből.

- **Létrehozás**: File → New Scene (`Ctrl+Shift+N`), vagy a Scene
  legördülő menü / oldalsáv gombjai.
- **Átnevezés**: ceruza ikon a jelenetsor mellett, vagy a Scene menü →
  Rename Scene.
- **Sorrend**: Scene menü → **Move Up / Move Down** (a sorrend perzisztens).
- **Szerkesztés**: kattints a jelenetre — a szöveg, a blokkok, a szereplők
  és a hangbeállítások mind jelenetenként tárolódnak.
- **Állapot** (a lefedettségből számolva, nem kézzel állítva):
  - **NOT GENERATED** — még nincs generált hang,
  - **GENERATING** — éppen fut a generálás,
  - **PARTIAL** — a blokkok egy része kész,
  - **COMPLETE** — minden elvárt blokkhoz van hang,
  - **ERROR** — az utolsó futás hibás volt (a korábban elkészült hangok
    épek maradnak).

Egy jelenet hangjait a **Batch Generation — Scene** ablakban generálod le
(lásd alább), ott lehetővé válik a verzióválasztás, az újragenerálás és a
**Combine Scene** (a jelenet összefűzése). A kész jelenet állapota és
hangkimenete automatikusan frissül.

**Fájlba mentés / visszatöltés**: File → Save Dialogue Scene / Load
Dialogue Scene (`*.scene.json`) — párbeszédes jelenet önálló fájlba mentése
és visszatöltése a Batch Queue ablakba.

<a id="szereplők"></a>
## Szereplők

A **szereplő (Character)** egy projekten belüli hangszemélyiség:

- **Név** — megjelenik az oldalsávban, a blokkok mellett, a batch
  sorokban és az előzményekben.
- **Hangprofil** — a szereplőhöz rendelt hang (lehet üres). A Character
  Management ablakban (`Project` → karakterek az oldalsávban) vagy a
  bal oldalsáv CHARACTERS kontextusában állítható.
- **Színazonosság** — a GUINEO minden szereplőnek **automatikusan stabil
  színt** rendel (12 színes paletta, a szereplő azonosítójából számolva),
  amely minden felületen ugyanaz: oldalsáv-pont, blokk-jelölő, batch sorok.
- **Blokk-szintű hozzárendelés** — egy-egy narrációs blokk saját
  szereplőt kaphat a blokk tulajdonságpaneljén. A szereplőkhöz nem rendelt
  blokkok a jelenet globális hangját öröklik.

**Hangfeloldás (inheritance)** generáláskor:

1. Sima **Generate** esetén a jelenet/szerkesztőpanelen kiválasztott hang
   érvényesül.
2. **Blokkonkénti generálásnál (Generate Long)** a sorrend:
   ① a blokk **szereplőjének** hangja → ② a Speaker-hozzárendelés
   (többbeszélős szövegnél) → ③ a panelen kiválasztott alapértelmezett hang.

A `$SPEAKER:` sorokat (csupa nagybetűs név) a rendszer felismeri, és
minden beszélőhöz külön generálást és külön referenciát használ.

<a id="hangprofilok"></a>
## Hangprofilok

A hangprofil egy **zero-shot hangklónozási referencia**: egy rövid (5–15
másodperces, WAV) minta + opcionális átirat.

- **Hangprofilok**: `Ctrl+L` vagy Project → Voice Profiles — a profilok
  listája, részletei, szerkesztése, exportálása, törlése.
- **Importálás**: File → Import Voice Profile — válassz egy WAV fájlt; a profil
  a `voices\` mappába kerül (referencia + metaadatok).
- **Exportálás**: File → Export Voice — profil átvitele másik gépre.
- A 30 másodpercénél hosszabb referenciáknál az alkalmazás figyelmeztet
  (az optimális 5–15 s).
- A profilt szereplőkhöz, vagy közvetlenül a jelenethez rendelheted.

<a id="narrációs-blokkok"></a>
## Narrációs blokkok

A középső szerkesztőnek három módja van (a váltás fent, rádiógombokkal):

- **Plain Text** — sima szöveg; a hosszú generálás automatikusan darabolja.
- **Narration Blocks** — a szöveg **blokkokra** bontható (bekezdés- és
  mondatszintű heurisztika). Blokkonként szereplő, felülírt érzelem/stílus,
  zárolás, egyesítés, szétvágás, törlés; a **Re-detect** újrarakja a
  blokkokat (a zárolt, kézzel szerkesztett blokkok megmaradnak). A
  blokkokhoz generálás után kész/értékesít jelvények és per-blokk verziók
  tartoznak. 12 beépített sablon is elérhető (YouTube intro, Product
  Description stb.).
- **Preview** — írásvédett, szintaxiskiemelt nézet a végső, tokenekkel
  kiegészített prompttal.

A **Raw Mode** kapcsolóval a promptot te is irányíthatod: a tokeneket
ekkor szó szerint küldi el a rendszer (megerősítő ablakkal).

<a id="vezérlőpult"></a>
## Vezérlőpult

A jobb panel két fülön működik:

**FRIENDLY** — egyszerűsített vezérlés két allapon:

- **VOICE**: a kiválasztott hang, csere gomb; **EMOTION & STYLE** chipek
  (8 elsődleges érzelem + továbbiak: Öröm, Szomorúság, Harag, Nyugalom,
  Félelem, Suttogás, Lelkesedés…); **PROSODY**: Beszédtempó, Hangmagasság
  (alacsony/normál/magas), Kifejezés (visszafogott/naturális/animált).
- **GENERATION**: **AI Freedom** csúszka 5 fokozattal (Strict → Wild:
  a temperature/top_p/top_k előre kalibrált kombinációi), véletlenszám-mag
  (seed), **ALLOW SFX** kapcsoló, hangeffekt- és szünetbeszúró gombok.

**ADVANCED** — teljes, szakértői vezérlés: teljes 21 elemű érzelomrács,
stílusok (Singing / Whispering / Shouting), prozódia (5 sebességfokozat,
hangmagasság, kifejezés), inline szünetek, hangeffektek, generálási
paraméterek (temperature, top_p, top_k, max_new_tokens, seed, csend
hozzáfűzése, normalizálás, automatikus lejátszás), referenciahang és
élő **prompt előnézet**.

**Tokenrendszer** (a Token Guide — Help menü — részletes magyar leírást ad):
a vezérlők `<|kategória:tag|>` alakú tokeneket inline illesztenek a
promptba (pl. `<|emotion:fear|>`, `<|prosody:speed_slow|>`,
`<|sfx:laughter|>Haha`). A mondat eleji tokenek egész mondatra, az inline
tokenek a pontos pozícióra hatnak; az SFX-ekhez onomatopoetikon tartozik.

<a id="hanggenerálás"></a>
## Hanggenerálás

- **Ctrl+Enter / GENERATE gomb**: az aktuális szöveget egy hívással legenerálja
  (modell szükség esetén automatikusan betöltődik).
- **Esc**: futó generálás leállítása (a már kész fájlok megmaradnak), vagy
  teljes képernyő elhagyása, vagy a keresősáv bezárása.
- **Replay** (Edit menü): a legutóbbi hang újrajátszása.
- Kimenet: **24 000 Hz mono, 32-bit float WAV** az `outputs\` mappába,
  automatikus időbélyeges névvel (ütközésnél sorszámmal).
- A státuszsor mutatja a mért időt és a valós idejű faktort (RTF).

<a id="hosszú-generálás"></a>
## Hosszú generálás

A **Generate Long** (`Ctrl+Shift+Return` vagy a szerkesztő gombja) hosszú
szövegekhez készült:

1. A szöveget **biztonságos részekre darabolja** (kb. 400 karakteres cél,
   mondathatáron), felismeri a `$SPEAKER:` sorokat, és becsült hosszat ad.
2. A **Long Narration Generator** ablakban átnézheted a részeket, a
   beszélőkhöz hangot rendelhetsz, és bekapcsolhatod a **Review before
   generating** opciót (alapból kikapcsolt; bekapcsolva a batch ablak
   felülvizsgálati módban nyílik, és csak a **GENERATE CHECKED** gombra
   indul el a munka).
3. A részek a **Batch Generation — Scene** ablakban futnak le sorban
   (0,5 s zárócsenddel); a Batch ablak bezárása a futó köteget leállítja,
   ezért futás közben ne zárd be, ha nem állítod le szándékosan.
4. A futás végeztével a jelenet állapota automatikusan frissül (COMPLETE /
   PARTIAL / ERROR). A részekből a **Combine Scene** gombbal készíthetsz
   egybenhallgatós kimenetet (lásd [Hangkompozíció](#hangkompozíció)).

<a id="batch-generation"></a>
## Batch Generation

A GUINEO **egyetlen Batch Generation rendszerrel** rendelkezik — egy
ablak, egy kötegkezelő (BatchManager), egy generálóháttér —, amely **két
érvényes munkafolyamatot** kínál attól függően, honnan nyitod meg:

| | **Batch Generation — Scene mód** | **Batch Queue (kézi mód)** |
|---|---|---|
| **Belépés** | Generate Long (`Ctrl+Shift+Return`), vagy File → Load Dialogue Scene | Project → Batch Generation… (vagy klasszikus Tools menü) |
| **Mire való** | Egy jelenet blokkjainak lefedése, verziókezelés, kombinálás | Tetszőleges, jelenetektől független hangfeladatok sorba állítása |
| **Cím** | „Batch Generation — <Jelenet neve>" | „Batch Queue" |
| **Táblázat** | 7 oszlop: sorszám, jelölőnégyzet, fájlnév, Generated (verziók), állapot, hossz, műveletek | 5 oszlop: sorszám, fájlnév, állapot, hossz, műveletek |
| **Fejléc** | Lefedettség (pl. „coverage: PARTIAL (3/4)") és futás-azonosító | — |
| **Műveletek** | All/None kijelölés, **GENERATE CHECKED (N)** (pontosan a bejelöltek generálása), sorenkénti Play / Stop playback / **Regen** (új verzió), **Combine Scene**, **Export Audio…**, verzióválasztó („N versions ▾") | **+ Add / Edit / Duplicate / Remove / Up / Down**, **Save Queue… / Load Queue…** (YAML) / Clear, **START BATCH**, sorenkénti Play / Stop playback / Regen, **Merge Completed Parts** |
| **Scene-kimenetek** | SCENE COMBINED OUTPUTS szekció: feloldott kimenet, verziók, STALE jelvény, Use as output / Clear | nincs (a kimenet nem kapcsolódik jelenethez) |

**Közös viselkedés:**

- **Egyszerre csak egy batch ablak él**. Ha már nyitva van, ugyanabban a
  módban újra megnyitva csak fókuszba kerül; másik módot kérve a régi ablak
  tisztán bezáródik (a futó köteg fut tovább), majd az új mód nyílik.
- **Review Before Generating** (a hosszú generálás ablakában): a feladatok
  PENDING állapotban várnak, amíg rá nem nyomsz a generálásra.
- **Pause**: a köteg nem vesz fel új feladatot, az éppen futó befejeződik.
  (Az aktuális buildben nincs Resume gomb — a köteg lezárásához használd a
  **Stop**-ot: a függőben lévő feladatok SKIPPED lesznek, és a Restart
  visszaállítja őket PENDING-re.)
- **Stop**: a várakozó feladatok azonnal SKIPPED, a futó feladat a következő
  biztonságos ponton megszakad; a már kiírt hang fájljai épen maradnak.
- **Regen**: jelenet-módban megerősítés után **új verziós** fájlt készít
  (a régi verzió megmarad), kézi módban a helyén frissíti a fájlt.
- **Merge Completed Parts** (csak kézi módban): a kész feladatokat sorrendben
  egyetlen WAV-ba fűzi (alapból 300 ms csend a részek közé — véletlenszerű
  temporizálással is; keresztátúsztatás csak 0 ms csendnél). Ez a kimenet
  **nem kapcsolódik jelenethez**, és nem érinti a Project Assembly-t.

<a id="hangkompozíció"></a>
## Hangkompozíció

A GUINEO verzió- és eredetkövető (provenance) rendszere pontosan
nyilvántartja, hogy a jelenet végül **melyik blokkverziókból** áll:

1. **Blokk-verziók**: minden egyes generálás új verziót kap
   (`v01`, `v02`, `v03`…). Ha kézzel kiválasztasz egy régebbit, az
   érvényesül — egy újabb generálás önmagában nem írja felül a választásod.
   Kiválasztás nélkül mindig a **legutolsó érvényes verzió** számít.
2. **Jelenet-kompozíció**: a jelenet aktuális kompozíciója egy **származtatott
   leképezés** — melyik blokkhoz éppen melyik verzió tartozik. Például:

   ```
   B1 → v02    B2 → v01    B3 → v04    B4 → v01
   ```

3. **Combine Scene**: a kompozícióból **megváltoztathatatlan pillanatképet**
   (Combined output) készít — ez is verziózott (`Combined v01`, `v02`…),
   és pontosan rögzíti, melyik blokk melyik verziójából készült.
   A régebbi combined kimenetek megmaradnak és később is választhatók.

   ```
   Jelenet kompozíció:            Combine Scene
   B1 → v02                        ───────────▶   Combined v01
   B2 → v01                                        (megváltoztathatatlan
   B3 → v04                                         pillanatkép)
   B4 → v01
   ```

A **Batch Generation — Scene** ablak SCENE COMBINED OUTPUTS szekciójában
látható a feloldott kimenet, a verziók listája (hosszal), a STALE jelvény,
és a **Use as output** / explicit választás törlése. A párbeszédablak
mérete és az oszlopbeállításai (szélesség, sorrend, láthatóság) megjegyzésre
kerülnek, és újraindítás után is visszatérnek.

<a id="stale"></a>
## STALE

A **STALE (elavult)** jelvény azt jelenti, hogy egy kombinált kimenet
**már nem a jelenet aktuális kompozícióját tükrözi**. **Nem** jelent
semmilyen hibát: a fájl épségben van, lejátszható és használható.

Példa:

```
Combined v01 tartalma:        A jelenet KURRENS kompozíciója:
  B1 → v02                      B1 → v02
  B2 → v01                      B2 → v01
  B3 → v01                      B3 → v02   ← új verzió lett generálva
```

Mivel a B3-hoz új verzió készült, a Combined v01 **STALE** lesz.

STALE lesz akkor is, ha **új blokk** kerül a jelenetbe, **törlöl** egy
blokkot, vagy a felhasznált **forrásfájl hiányzik** a lemezről.

**Explicit STALE választás**: egy elavult kimenet tudatosan is
kiválasztható kimenetként — a GUINEO ilyenkor figyelmeztet, és „Use
Anyway" megerősítést kér. A **Combine Scene** mindig friss pillanatképet
készít, és átveszi a kimenet szerepét.

<a id="projekt-összerakás"></a>
## Projekt összerakás

Az **Assemble Audio** (oldalsáv alul, vagy `Ctrl+Shift+A`) több jelenet
kész hangját fűzi egyetlen audióvá:

1. A párbeszédablak **jelenetenként egy sorban** listázza a jeleneteket,
   a feloldott kimenettel („Combined v02", „Latest audio", „Selected ·
   CUSTOM") és STALE / MISSING jelvényekkel. A sorok átrendezhetők
   (Move Up / Down), a kimenet sorenként **Choose Output…**-tal
   felülírható.
2. Beállítások: kimeneti fájlnév, mappa (alap: `outputs\combined`),
   **jelenetek közti csend** (0–3 s, alap 0,5 s), **normalizálás −18 LUFS**
   (alap bekapcsolva), és opcionális **MP3 készítése** (FFmpeg szükséges
   hozzá — lásd [Exportálás](#exportálás)).
3. Kimenet: WAV (+ opcionálisan MP3), a készítés bekerül az Előzményekbe
   és a projekt `combined_outputs` nyilvántartásába.

**Biztonsági szabály**: a rendszer **soha nem fog csendben egyetlen blokk
(rész-) hangot a teljes jelenet kimeneteként használni**. Ha nincs érvényes,
teljes jelenet-kimenet, a sor **REVIEW REQUIRED** állapotba kerül — a
jelzett orvoslások: **Combine Scene Now** (kombinálás azonnal) vagy
**Choose Output** (kimenet explicit kiválasztása). Hiányzó forrásfájl
esetén az összerakás hibával leáll, és megnevezi a hiányzó jelenetet —
semmi nem marad észrevétlenül kimaradva.

<a id="exportálás"></a>
## Exportálás

**Batch Audio Export** (Batch Generation — Scene ablak, **Export Audio…**)
— négy, pontosan definiált hatókör:

| Hatókör | Mit másol |
|---|---|
| **Selected rows (N)** | a kijelölt sorok generált hangjait |
| **All generated parts** | mindazon feladatok kimenetét, amelyek jelenet-részek (part) |
| **Scene combined outputs** | a jelenet **összes** kombinált kimenetét (minden verzió) |
| **Everything in this batch** | a részeket **és** a kombinált kimeneteket együtt |

A másolás **átalakítás nélkül** történik (WAV, provenance-fájlnevekkel);
névütközésnél automatikus „(2)" sorszámot kap; a hiányzó fájlokat a
végeredmény-ablak listázza.

**Projekt export** (File → Export Project…, `Ctrl+Shift+E`) — **mappa**
(nem ZIP) jön létre a kiválasztott célhelyen:

```
<ProjectName>/
├─ project.json              ← jelenet-index, szereplők, combined lineage
├─ scenes/<Jelenet>/
│   ├─ scene.json            ← szöveg, blokkok, verziók, provenance
│   └─ audio/001.wav …       ← a jelenet hangjai (opcionális: „Include
│                               Generated Audio", alapból bekapcsolva)
├─ characters/<Szereplő>/character.json
├─ references/voices/<Profil>/   ← referenciahangok + átiratok
│                                 (opcionális: „Include Referenced Voice
│                                  Assets", alapból bekapcsolva)
└─ combined_outputs/         ← kombinált WAVok (+ projekt-szintű MP3)
```

Minden belső útvonal **relatív**. A modell fájljai **nem** részei az
exportnak. Korlátozás: az exportált mappa visszatöltéséhez az aktuális
buildben **nincs felhasználói menüparancs** — a File → Open Project a
natív `projects\` könyvtárat és a régi `*.sproj` formátumot nyitja meg.

<a id="előzmények"></a>
## Előzmények

**Ctrl+H** vagy Project → History. Minden generálásról — sima, hosszú,
kombinált, összerakott — **reprodukálható JSON bejegyzés** készül
(`settings\history\`): a teljes prompt, paraméterek, hangprofil, mért
idők, GPU, figyelmeztetések, jelenet/szereplő/blokk kontextus és a
kimenet elérési útja. A listában rákattintva egy bejegyzésre az kijelölődik
(a Recent Audio lista frissül); az ALL HISTORY fejléc ikonja nyitja a
részletes nézetet, ahonnan a beállítások visszatölthetők, a hang
lejátszható és exportálható. A bejegyzések törölhetők (a fájl tartalmazza
az útvonalat; a törlés a könyvtáron belüli útvonalakra korlátozódik).

<a id="recents"></a>
## Recents

- **RECENTS panel** (bal oldalsáv): a legutóbbi 5 projekt / jelenet /
  szereplő / előzmény — a kontextustól függően. „+" gombbal új elem
  hozható létre.
- **Context Lists**: ALL PROJECTS / ALL SCENES / ALL CHARACTERS / ALL
  HISTORY listák — a teljes állomány gyors áttekintésére. Minden ALL
  SCENES sorban ceruzaikon: azonnali átnevezés.

<a id="témák"></a>
## Témák

- **Öt téma**: Modern Dark (alapértelmezett), Dark, Light, Synthwave,
  Retro Console — View → Theme menüből azonnal váltható, és elmentődik.
- **Teljes képernyő**: **F11** be/ki; kilépés Esc-cel is.
- **Hangerő**: a transzport csúszkája (0–100); az érték **megmarad**
  újraindítás után.
- **Reset Layout** (View menü): az elrendezés visszaállítása.
- Az ablakméret és a batch ablak mérete/oszlopbeállításai szintén
  megjegyzésre kerülnek.

<a id="a-modell"></a>
## A modell

- **Modell**: Higgs TTS 3 (Higgs Audio V3) — Boson AI. A GUINEO a
  közösségi Transformers-portot tölti be:
  `multimodalart/higgs-audio-v3-tts-4b-transformers` (az eredeti:
  `bosonai/higgs-tts-3-4b`).
- **Betöltés**: indításkor az alkalmazás felajánlja („Load the model
  now?…"), vagy a Generate ütközés nélkül automatikusan betölti. A
  töltés alatt egy **GUINEO animációval** ellátott párbeszédablak fut
  (fázisjelzéssel és eltelt idővel), a művelet megszakítható (Cancel).
- **Pontosság**: bfloat16 (ajánlott) / float16 (kísérleti) / float32 —
  a Beállításokban; a váltás a modell **újratöltése után** él.
- **Nyelvek**: 102 nyelv — 85 gyártási minőség (Tier 1, köztük a
  **magyar**) és 17 használható (Tier 2). Teljes lista: Help →
  Supported Languages.
- **Helyi működés**: az első letöltés után minden helyben fut; nincs
  telemetria, nincs felhő.

<a id="anomáliadetektálás"></a>
## Anomáliadetektálás

A hosszú generálásnál a GUINEO **konzervatívan észleli** a kóros
kimeneteket (P3.27B védelem):

- kórosan hosszú **zárócsend** (farok ≥ 10 s **és** a teljes hossz
  50%-a),
- a vártnál **tartalmában jóval hosszabb** kimenet (≥ 3× várakozás
  **és** ≥ várakozás + 20 s),
- **néma** kimenet (≥ 5 s-ban nincs hallható beszéd).

**Fontos**: az észlelés csak jelzés — a fájlt **soha nem vágja és nem
törli**. Az érintett sor a Batch ablakban ⚠ jelzést kap a hossz mellett
(részletezéssel), az Előzményekbe bekerül az anomália-leírás, és a hang
bármikor **újragenerálható** (új verzióként, a régi megmarad). A modell
nem garantálja a zárócsend hiányát — ez a védelem enyhíti, de nem
szünteti meg.

<a id="hibakezelés"></a>
## Hibakezelés

Minden hiba **olvasható üzenettel** jelenik meg (párbeszédablak +
státuszsor), ahol lehetséges, javaslattal: pl. „a modell nincs betöltve —
nyomj Generate-ot", „nincs elég VRAM (~10 GB kell)", „hiányzik a
referenciafájl". A hibák naplózása a `logs\` mappába történik
(`application.log`, `engine.log`, `crash.log`, `bootstrap.log`). Egy
hiba soha nem okoz azonnali kilépést, és egy sikertelen újragenerálás
soha nem rontja le a meglévő hangokat.

**Útvonalbiztonság**: a kimeneti fájlnevek mindig az `outputs\` mappán
belülre vannak kényszerítve; az importált projektekből a szökő
(relatív/abszolút/traversal) útvonalak ki vannak szűrve; az előzmények
törlése csak a saját könyvtárában érvényesül.

<a id="adatmegőrzés"></a>
## Adatmegőrzés

| Mit | Hol | Megmarad? |
|---|---|---|
| Téma, hangerő, ablak- és batch-ablak méret, oszlopok | `settings\settings.json` | ✅ |
| Build-számláló | `settings\build.json` | ✅ (minden indításkor +1) |
| Előzmények | `settings\history\*.json` | ✅ |
| Projektek, jelenetek, szereplők, verziók, provenance | `projects\<id>\project.json` | ✅ |
| Hangprofilok | `voices\` | ✅ |
| Generált hangok | `outputs\` | ✅ |
| Presetek | `presets\` | ✅ |
| Modell súlyok (~9 GB) | `models\higgs-audio-v3\` | ✅ (nem töltődik újra) |
| Naplók | `logs\` | ✅ |

Újraindítás után minden fenti adat visszatér; a modellt újra be kell
tölteni a VRAM-ba (kb. 10–30 s). A `update.bat`-os frissítés ezeket a
mappákat mindig megőrzi.

<a id="billentyűparancsok"></a>
## Billentyűparancsok

| Parancs | Hatás |
|---|---|
| `Ctrl+Enter` | Hang generálása |
| `Ctrl+Shift+Enter` | Hosszú generálás (Long Narration) |
| `Esc` | Generálás leállítása / teljes képernyő elhagyása / keresősáv bezárása |
| `Space` | Lejátszás / szünet (ha a szerkesztő nincs fókuszban) |
| `Ctrl+N` / `Ctrl+Shift+N` | Új projekt / új jelenet |
| `Ctrl+O` / `Ctrl+S` | Projekt megnyitása / mentése |
| `Ctrl+Shift+E` | Projekt exportálása |
| `Ctrl+Shift+A` | Jelenetek összerakása (Assembly) |
| `Ctrl+L` | Hangkönyvtár |
| `Ctrl+H` | Előzmények |
| `F11` | Teljes képernyő |
| `Ctrl+Q` | Kilépés |

<a id="menük"></a>
## Menük

- **File**: New Project, New Scene, Open Project, Save Project, Export
  Project…, Save / Load Dialogue Scene, Import / Export Voice, Import /
  Export Preset, Exit
  *(Az összerakás nem menüelem: az oldalsáv alján rögzített **Assemble
  Audio** gomb vagy `Ctrl+Shift+A` nyitja — lásd
  [Projekt összerakás](#projekt-összerakás).)*
- **Edit**: Generate, Generate Long Narration, Stop, Replay, Clear
- **View**: Theme (öt téma), Reset Layout, Fullscreen, Show Prompt Blocks
- **Project**: Batch Generation…, Voice Profiles, History, Preset
  Manager…, Open Output Folder
- **Help**: Documentation, Token Guide…, Supported Languages…, Copy Debug
  Information, Font Status…, About, ─, Settings, Benchmark

A felső sáv jobb oldalán: undo/redo, mentés, export és a GENERATE gomb.

<a id="beállítások"></a>
## Beállítások

Help → Settings:

- **General**: automatikus lejátszás generálás után, kimenet normalizálása,
  kimeneti formátum, csend randomizálása összefűzésnél.
- **Performance**: modell pontossága (bfloat16 / float16 / float32) —
  memóriahasználat és sebesség befolyásolása; az új beállítás a modell
  következő betöltésekor él.
- **Application Theme**: témaválasztás.

<a id="verzió-információ"></a>
## Verzió-információ

A GUINEO fejlesztői build: a verzió `v1.0.0`, a **buildszám minden
indításkor eggyel nő** (`settings\build.json`), és a státuszsorban, valamint
a Help → About ablakban látszik. Az About emellett a teljes környezetet
mutatja (Python, PyTorch, Transformers, Qt, CUDA, GPU, modell, pontosság),
és **Copy Debug Info** gombot ad.

A jelenlegi fejlesztési fázis a **P3.44.5** (Stabilizációs / Integritási
Fázis 1) — lásd [Kiadástörténet](#kiadasok).

<a id="kiadasok"></a>
## Kiadástörténet

A közeli fejlesztési fázisok rövid összefoglalója. A teljes, dátumozott
történet a [`DEVELOPMENT_LOG.txt`](DEVELOPMENT_LOG.txt) fájlban található,
és minden leszállított verzió-ZIP megőrzésre került a GitHub-tároló
`releases/` mappájában: [AGE-T/GUINEO](https://github.com/AGE-T/GUINEO).

| Fázis | Téma |
|---|---|
| **P3.44.5** (aktuális) | Stabilizáció / integritás: Batch-ablak életciklus-javítás (B+D), blokk-tudatos proveniencia-párosítás, 29 tesztes regressziós csomag |
| P3.44.4 | Batch futtatási-futás szemantika; visszajelzés-formátum regressziós tesztek |
| P3.44.3 | Visszajelző párbeszédablakok javításai |
| P3.44.2 | SFX-feldolgozási lánc javításai |
| P3.44.1 | Batch állapot-szinkronizálási javítások |
| P3.44 | Batch lejátszási integritás; előzmény-lejátszó betöltés; oldalsáv-méretezés |
| P3.43 | Hangkezelés-egyesítés (könyvtár, választó, import, szerkesztő) |
| P3.41 | Blokk-törlés és szerkesztő-interakció javításai |
| P3.40 | Batch sortördelés; DPI- és vizuális audit-javítások |
| P3.39 | Token-integritás (nincs fantom-token-kibocsátás) |
| P3.38 | GUINEO betűjel és márkaelemek |
| P3.37 | GUINEO újravezetés (rebrand); teljes kétnyelvű dokumentáció |
| ≤ P3.36 | Korábbi fázisok — lásd a fejlesztési naplót |

<a id="higgs-licenc-hu"></a>
## Higgs TTS 3 licencelés

A GUINEO a **Boson AI** **Higgs TTS 3** modelljét használja. A modell a
**Boson Higgs TTS 3 Research and Non-Commercial License** alatt érhető el
— ez **nem nyílt forráskódú licenc**:

- **Kutatási és nem kereskedelmi célra** ingyenesen használható.
- Az aktuális licenc tartalmazza a **Creator Use Grant**-ot: digitális
  alkotók **ingyenesen készíthetnek és monetizálhatnak** kreatív
  tartalmat (podcast, videó, hangoskönyv, közösségi poszt) a saját
  csatornáikon, ha **jelzik a Boson AI Higgs Audio használatát** — pl.
  „This audio was created with Boson AI's Higgs Audio —
  https://www.boson.ai/higgs-audio" a leírásban vagy a hangban.
- **Kereskedelmi célú felhasználás** (production használat, API/SaaS,
  a modell termékbe ágyazása, továbbértékesítés) **külön írásos
  kereskedelmi licencet igényel**: <https://boson.ai> ·
  contact@boson.ai.
- A modell letölthetősége **nem** jelent automatikus jogot a
  kereskedelmi terjesztésére.
- A licenc tiltja többek között a valós személyek beleegyezés nélküli
  hangklónozását, a megtévesztő célú használatot, és a kimenetek
  nem-Boson generatív modellek tanítására való felhasználását.

A teljes hivatalos licencszöveg a projektben:
[`LICENSE_HIGGS_TTS_3.txt`](LICENSE_HIGGS_TTS_3.txt). Harmadik felek
licencei és attribúció: [`THIRD_PARTY_LICENSES.md`](THIRD_PARTY_LICENSES.md).

<a id="hibaelhárítás"></a>
## Hibaelhárítás

| Tünet | Teendő |
|---|---|
| **A modell nem található** / nincs letöltve | Futtasd a `launch.bat`-ot — a bootstrap felismeri és (folytathatóan) letölti a modellt. Részletek: `logs\bootstrap.log`. A modell helye: `models\higgs-audio-v3\`. |
| **Hugging Face / letöltési hiba** | A GUINEO csak nyilvános modellt használ, hitelesítést (token) nem igényel. Tűzfal/proxy esetén ellenőrizd a HTTPS-kapcsolatot; a letöltés újrakezdhető ( `.incomplete` fájlok maradnak). |
| **Hiányzó hangprofil** | Nyisd meg a Hangkönyvtárat (`Ctrl+L`), és ellenőrizd, hogy a profil létezik-e; a hivatkozott referencia WAV ténylegesen a `voices\` mappában van-e. Importáld újra (File → Import Voice). |
| **Hiányzik egy kimeneti fájl** | Az Előzmények bejegyzése mutatja az útvonalat (`outputs\…`). Kézzel törölt fájl esetén a jelenet kimenete MISSING lesz — generáld újra a részt, vagy válassz másik verziót/kimenetet. |
| **STALE jelenetkimenet** | Nem hiba: a kombinált kimenet már nem az aktuális kompozíció. Orvoslás: **Combine Scene** (friss pillanatkép) vagy **Choose Output** (tudatos választás megerősítéssel). |
| **Generálási anomália (⚠)** | A fájl megmaradt, only jelölve. Hallgasd meg; ha rossz, regeneráld a sort — új verzió készül, a régi megmarad. |
| **GPU / CUDA gondok** | Telepítsd a legfrissebb NVIDIA illesztőprogramot. A modell ~10 GB VRAM-ot használ — zárd be a többi VRAM-igényes alkalmazást. CUDA nélkül CPU-tartalékon fut (lassan). Állapot a státuszsorban. |
| **OpenGL / megjelenítési gondok** | Az élő háttér automatikusan visszaesik GPU → statikus CPU → sima sötét háttér szintre; az alkalmazás működőképes marad. Illesztőprogram-frissítés segíthet. |
| **Teljes képernyő** | Belépés: `F11`. Kilépés: `F11` vagy `Esc`. |
| **Nincs hang / lejátszás nem szól** | A lejátszás a rendszer audiószközén megy; ellenőrizd a Windows hangerőt és az alapértelmezett eszközt. Lejátszó-könyvtár hiányában a GUINEO a fájlokat akkor is kiírja, csak éppen nem játssza le őket. |
| **Export hibák** | Az MP3-készítés **FFmpeg-et** igényel (telepítsd és tedd a PATH-ra); enélkül WAV készül, figyelmeztetéssel. Néhány GB szabad hely legyen a célmeghajtón. |
| **A Generate nem csinál semmit** | Várd meg a modell betöltését (státuszsor / `logs\application.log`: „Model loaded"), majd próbáld újra. |
| **Első indítás után fehér/üres modelltöltő ablak** | Ismert régi hiba, a jelenlegi buildben javítva; ha mégis látod, frissítsd a GPU-illesztőprogramot, és küldd el a `logs\crash.log`-ot fejlesztéshez. |

További naplók: `logs\application.log`, `logs\engine.log`,
`logs\bootstrap.log`, `logs\crash.log`.
<a id="fejlesztoknek"></a>
## Fejlesztőknek — tároló és tesztek

A teljes forráskód GitHubon érhető el:
<https://github.com/AGE-T/GUINEO>. A tároló ugyanazt a fát tartalmazza,
mint a futtatható ZIP-ek (lásd a `releases/` mappát), így az alkalmazás
közvetlenül egy klónból is tanulmányozható és futtatható. A támogatott
telepítési út végfelhasználóknak a `launch.bat` / `bootstrap.py`
(Windows), amely létrehozza a virtuális környezetet, telepíti a
függőségeket és letölti a modellt — lásd [Telepítés](#telepítés).

**A tesztcsomag futtatása.** A tesztek tisztán offscreen futnak — GPU és
modellletöltés nélkül:

```
QT_QPA_PLATFORM=offscreen python -m pytest tests/ -q
```

Függőségek: PySide6, NumPy, SoundFile, sounddevice (a `torch`-ot a tesztek
házi mintája stubolja). Headless Linuxon az EGL/GL futásidejű
könyvtáraknak jelen kell lenniük a Qt importhoz.

**Ellenőrző szkriptek** (`tools/`): `verify_compile.py`,
`verify_architecture.py`, `verify_integration.py`,
`verify_functional_integrity.py`, `verify_feature_gate.py`,
`verify_transport_gl.py`.

**Elrendezés**: `engine/` — háttérmodulok (modellkezelő, prompt-építő,
hang, batch, proveniencia, perzisztencia); `ui/` — PySide6 felület;
`tests/` — pytest regressziós csomagok; `tools/` — ellenőrző szkriptek;
`docs/` — tervezési jegyzőkönyvek, auditok és governance; `spec/` — az
eredeti specifikációk; `research/` — upstream referenciadokumentáció;
`assets/` — betűkészletek, ikonok, márkaelemek; `presets/` — mentett
presetek.

**Governance**: fejlesztési szabályok a
[`docs/governance/DEVELOPMENT_RULES.md`](docs/governance/DEVELOPMENT_RULES.md)
fájlban; ismert hibák nyilvántartása a
[`docs/governance/OPEN_BUGS.md`](docs/governance/OPEN_BUGS.md) fájlban; a
teljes fázistörténet a [`DEVELOPMENT_LOG.txt`](DEVELOPMENT_LOG.txt)
fájlban.

---

## ☕ Támogatás / Support


Ha hasznosnak találod a projektet és szeretnéd támogatni a munkámat,
meghívhatsz egy kávéra!

If you find this project helpful and want to support my work, feel free to buy
me a coffee!

[Support me on Ko-fi](https://ko-fi.com/thomashoysgameaudio)
([image](https://camo.githubusercontent.com/12ddacd4b1ffd5473ce102384087761260706d10e6ee9d4c4d4bcf84c9608dcc/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f537570706f72745f6f6e5f4b6f2d2d66692d4631363036313f7374796c653d666f722d7468652d6261646765266c6f676f3d6b6f2d6669266c6f676f436f6c6f723d7768697465))

Vagy kattints az alábbi gombra:

[Ko-fi Support](https://ko-fi.com/thomashoysgameaudio)
([image](https://camo.githubusercontent.com/201ef269611db7eb6b5d08e9f756ab8980df3014b64492770bdf13a6ed924641/68747470733a2f2f6b6f2d66692e636f6d2f696d672f676974687562627574746f6e5f736d2e737667))

https://ko-fi.com/thomashoysgameaudio
