# GUINEO

<p align="center">
  <img src="assets/brand/guineo_logo.png" alt="GUINEO logo" width="180"/>
</p>

**GUINEO** is a portable desktop application for local speech synthesis, built
on the Higgs TTS 3 (Higgs Audio V3) model from Boson AI. All processing
happens on your machine — no cloud, no telemetry.

**GUINEO** (formerly known under the development name *SpeechStudio*) egy
hordozható asztali alkalmazás helyi beszédszintézishez, a Boson AI
**Higgs TTS 3 (Higgs Audio V3)** modelljére építve. Minden feldolgozás a
saját gépeden történik — felhő és telemetria nélkül.

[🇭🇺 Magyar dokumentáció](#magyar) · [🇬🇧 English documentation](#english)

---

<a id="magyar"></a>

# HU | Magyar

## Tartalom

1. [Mi a GUINEO?](#mi-a-guineo)
2. [Telepítés](#telepítés)
3. [Frissítés](#frissítés)
4. [Hardverkövetelmények](#hardverkövetelmények)
5. [Első lépések — gyors útmutató](#első-lépések)
6. [A munkaterület áttekintése](#a-munkaterület-áttekintése)
7. [Projektek](#projektek)
8. [Jelenetek (Scenes)](#jelenetek)
9. [Szereplők (Characters)](#szereplők)
10. [Hangprofilok (Voice Profiles)](#hangprofilok)
11. [Narrációs blokkok és szerkesztőmódok](#narrációs-blokkok)
12. [Vezérlőpult — Friendly és Advanced mód](#vezérlőpult)
13. [Hanggenerálás](#hanggenerálás)
14. [Hosszú generálás (Long Generation)](#hosszú-generálás)
15. [Batch Generation — két munkafolyamat, egy rendszer](#batch-generation)
16. [Verziók és a jelenet hangkompozíciója](#hangkompozíció)
17. [STALE — az elavult kimenet](#stale)
18. [Projekt összerakás (Project Assembly)](#projekt-összerakás)
19. [Exportálás](#exportálás)
20. [Előzmények (History)](#előzmények)
21. [Recents és Context Lists](#recents)
22. [Témák, teljes képernyő, hangerő](#témák)
23. [A modell és a nyelvek](#a-modell)
24. [Anomáliadetektálás hosszú generálásnál](#anomáliadetektálás)
25. [Hibakezelés és útvonalbiztonság](#hibakezelés)
26. [Adatmegőrzés — mi marad meg újraindítás után?](#adatmegőrzés)
27. [Billentyűparancsok](#billentyűparancsok)
28. [Menük](#menük)
29. [Beállítások (Settings)](#beállítások)
30. [Verzió- és build-információ](#verzió-információ)
31. [Higgs TTS 3 licencelés](#higgs-licenc-hu)
32. [Hibaelhárítás](#hibaelhárítás)

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

1. Csomagold ki a GUINEO mappát tetszőleges helyre (pl. `C:\GUINEO`).
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

1. Töltsd le az új verzió ZIP fájlját, és nevezd át
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

---

## ☕ Támogatás / Support

[svg](https://github.com/AGE-T/Animal-audio-detector#-t%C3%A1mogat%C3%A1s--support)

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

---

<a id="english"></a>

# EN | British English

## Contents

1. [What is GUINEO?](#what-is-guineo)
2. [Installation](#installation)
3. [Updating](#updating)
4. [Hardware requirements](#hardware-requirements)
5. [Getting started — a quick tour](#getting-started)
6. [The workspace at a glance](#the-workspace-at-a-glance)
7. [Projects](#projects)
8. [Scenes](#scenes)
9. [Characters](#characters)
10. [Voice Profiles](#voice-profiles)
11. [Narration Blocks and editor modes](#narration-blocks)
12. [Control panel — Friendly and Advanced mode](#control-panel)
13. [Generating speech](#generating-speech)
14. [Long Generation](#long-generation)
15. [Batch Generation — two workflows, one system](#batch-generation-en)
16. [Versions and the Scene audio composition](#audio-composition)
17. [STALE — the outdated output](#stale-en)
18. [Project Assembly](#project-assembly)
19. [Exporting](#exporting)
20. [History](#history)
21. [Recents and Context Lists](#recents-en)
22. [Themes, fullscreen, volume](#themes)
23. [The model and supported languages](#the-model-en)
24. [Generation anomaly detection](#anomaly-detection)
25. [Error handling and path safety](#error-handling)
26. [Persistence — what survives a restart?](#persistence)
27. [Keyboard shortcuts](#keyboard-shortcuts)
28. [Menus](#menus)
29. [Settings](#settings)
30. [Version and build information](#version-info)
31. [Higgs TTS 3 Licensing](#higgs-licensing)
32. [Troubleshooting](#troubleshooting)

<a id="what-is-guineo"></a>
## What is GUINEO?

GUINEO is a **portable, local-first desktop application for speech
synthesis** on Windows. It is built on the **Higgs TTS 3** (also known as
Higgs Audio V3) open-weight 4-billion-parameter audio language model from
Boson AI, running on your own NVIDIA GPU. The application:

- organises your text into **Projects, Scenes and Narration Blocks**,
- manages **Characters**, each with its own voice and colour identity,
- performs **zero-shot voice cloning** from short reference clips
  (Voice Profiles),
- automatically splits and **batch-generates long texts**,
- keeps every scene's audio in a **versioned, fully traceable provenance
  system**, and
- **assembles finished scenes** into a single combined audio file.

All processing is local: no cloud, no telemetry — generated files stay on
your disk. The application downloads the model weights (~9 GB) once on
first run and works offline afterwards.

<a id="installation"></a>
## Installation

**Requirements:** Windows 10/11 (64-bit), Python 3.11 (exactly this
version), an NVIDIA GPU with CUDA support, about 12 GB of free disk space,
and an internet connection for the first run.

1. Extract the GUINEO folder to any location (e.g. `C:\GUINEO`).
2. Install **Python 3.11** from
   [python.org](https://www.python.org/downloads/release/python-3119/) —
   tick “Add Python to PATH” during installation.
3. Double-click **`launch.bat`**.

The first run performs the entire setup automatically:

- it creates a private virtual environment (`venv\`),
- installs PyTorch, Transformers, PySide6 and the remaining dependencies,
- downloads the Higgs TTS 3 model (~9 GB, one-time), and
- starts the GUINEO window.

**The first download takes time** (10–60 minutes depending on your
connection) but is **resumable** — if it is interrupted, simply run
`launch.bat` again. Every subsequent start is fast and needs no internet.

If `launch.bat` reports that Python was not found, install version 3.11
from the link above and make sure “Add Python to PATH” is ticked.

<a id="updating"></a>
## Updating

To update to a new version **without losing your data**:

1. Download the new release ZIP and rename it to **`SpeechStudio_clean.zip`**.
2. Place it in the GUINEO folder (next to `SpeechStudio.py`).
3. Close GUINEO if it is running.
4. Run **`update.bat`**.

The script backs up your current source code, then replaces **only the
source files**. All personal data is preserved: `settings\`, `voices\`,
`outputs\`, `presets\`, `models\`.

**Never delete the whole folder and re-extract** — always use `update.bat`.

<a id="hardware-requirements"></a>
## Hardware requirements

| Level | Requirement |
|---|---|
| **Minimum** | Windows 10/11 64-bit, Python 3.11, a CUDA-capable NVIDIA GPU, ~12 GB free disk space. A CPU-only fallback exists, but generation is impractically slow there. |
| **Recommended** | A modern NVIDIA RTX GPU with ~10 GB of free VRAM (the model uses roughly 10 GB of VRAM at bfloat16 precision). Development and testing were done on a GeForce RTX 5070. |
| **Large / long generation** | Long texts are split into many short parts, so the VRAM requirement **does not grow** with text length — the practical limits are time and disk space. The status bar and History show the measured real-time factor (RTF). |

<a id="getting-started"></a>
## Getting started

A complete round trip in five minutes:

1. **Launch** `launch.bat`. On first start the application offers to load
   the model (about 10–30 seconds, ~10 GB of VRAM). If you skip it, it
   loads automatically when you press **Generate**.
2. **Type something** in the editor (a short Hungarian sample text is
   pre-loaded on the very first run).
3. **Pick a voice**: right panel → **VOICE** tab → *Change Voice* → choose
   a voice, or import a reference clip (see [Voice Profiles](#voice-profiles)).
4. Press **Ctrl+Enter** (or click the orange **GENERATE** button).
5. The finished audio appears in the **waveform player** at the bottom —
   click or drag on the waveform to seek; adjust loudness with the volume
   slider.

The generated file lands in `outputs\` (24,000 Hz mono WAV). Every
generation is recorded in **History** (Ctrl+H), from which the full
settings can be restored later.

<a id="the-workspace-at-a-glance"></a>
## The workspace at a glance

- **Left sidebar**: Projects / Scenes / History / Characters navigation,
  a RECENTS panel, context lists (ALL PROJECTS, ALL SCENES, ALL
  CHARACTERS, ALL HISTORY), and a pinned **Assemble Audio** button at the
  bottom.
- **Centre**: the text editor (Plain Text / Narration Blocks / Preview
  modes) with the waveform transport underneath.
- **Right panel**: **FRIENDLY** and **ADVANCED** tabs — voice, emotion,
  style, prosody, sound effects and generation parameters.
- **Top**: the official **GUINEO logo** (white wordmark) in the left
  corner, navigation menus (File, Edit, View, Project, Help),
  undo/redo, save, export and the GENERATE button.
- **Bottom**: the status bar — model state, CUDA/GPU, generation state,
  active voice, measured generation time (RTF) and the version.

<a id="projects"></a>
## Projects

A **Project** is the top-level container: scenes, characters, blocks,
audio and history travel together. `Ctrl+N` creates a new one, `Ctrl+O`
opens, `Ctrl+S` saves. Projects live in the `projects\` folder
(`project.json`) and are saved automatically on scene switches and exit.
The Projects dropdown renames and deletes projects; the sidebar switches
between them quickly. The **Recents** list keeps the last five.

<a id="scenes"></a>
## Scenes

A **Scene** is a self-contained text and audio unit inside a project —
for example a chapter, a dialogue, or one scene of a screenplay.

- **Create**: File → New Scene (`Ctrl+Shift+N`), or the Scene dropdown /
  sidebar buttons.
- **Rename**: the pencil icon next to every scene row, or the Scene menu →
  Rename Scene.
- **Order**: Scene menu → **Move Up / Move Down** (the order persists).
- **Editing**: click a scene — its text, blocks, characters and voice
  settings are all stored per scene.
- **Status** (derived from coverage, never hand-set):
  - **NOT GENERATED** — no audio yet,
  - **GENERATING** — generation is running,
  - **PARTIAL** — some blocks have audio,
  - **COMPLETE** — every expected block has audio,
  - **ERROR** — the last run failed (previously generated audio stays
    intact).

A scene's audio is generated in the **Batch Generation — Scene** window
(see below), where you can select versions, regenerate parts, and
**Combine Scene**. The scene's status and resolved output update
automatically.

**Saving / loading to a file**: File → Save Dialogue Scene / Load Dialogue
Scene (`*.scene.json`) — saves a dialogue scene to a standalone file and
loads it back into the Batch Queue window.

<a id="characters"></a>
## Characters

A **Character** is a voice personality within a project:

- **Name** — appears in the sidebar, next to blocks, in batch rows and in
  History.
- **Voice Profile** — the voice assigned to the character (may be empty).
  Assign it in Character Management or the sidebar's CHARACTERS context.
- **Colour identity** — GUINEO automatically assigns every character a
  **stable colour** (a 12-colour palette derived from the character's id),
  identical everywhere: sidebar dot, block markers, batch rows.
- **Block-level assignment** — an individual narration block can carry its
  own character in the block properties panel. Blocks without a character
  inherit the scene's global voice.

**Voice resolution** during generation:

1. A plain **Generate** uses the voice selected in the scene/control panel.
2. For **per-block generation (Generate Long)** the chain is:
   ① the block's **character's** voice → ② the speaker mapping
   (multi-speaker text) → ③ the default voice from the panel.

Lines of the form `$SPEAKER:` (an ALL-CAPS name) are recognised, and each
speaker is generated with its own reference audio.

<a id="voice-profiles"></a>
## Voice Profiles

A voice profile is a **zero-shot voice-cloning reference**: a short
(5–15 seconds, WAV) sample plus an optional transcript.

- **Voice Profiles**: `Ctrl+L` or Project → Voice Profiles — manage
  (create/import/edit/export/delete) voice profiles.
- **Import**: File → Import Voice — choose a WAV file; the profile is
  stored in `voices\` (reference + metadata).
- **Export**: File → Export Voice — move a profile to another machine.
- References longer than 30 seconds trigger a warning (5–15 s is optimal).
- Assign profiles to characters, or directly to the scene.

<a id="narration-blocks"></a>
## Narration Blocks

The central editor has three modes (switched with the radio buttons at
the top):

- **Plain Text** — a simple text area; long generation splits it
  automatically.
- **Narration Blocks** — the text can be broken into **blocks**
  (paragraph- and sentence-level heuristics). Each block can have its own
  character, overridden emotion/style, lock flag; blocks can be merged,
  split, deleted; **Re-detect** rebuilds them (locked and manually edited
  blocks are preserved). After generation, blocks carry done/generating
  badges and per-block versions. Twelve built-in templates are available
  (YouTube Intro, Product Description, and so on).
- **Preview** — a read-only, syntax-highlighted view of the final prompt
  with tokens.

With **Raw Mode** you own the prompt yourself: tokens are sent literally
(with a confirmation dialog).

<a id="control-panel"></a>
## Control panel

The right panel works in two tabs:

**FRIENDLY** — simplified control on two sub-tabs:

- **VOICE**: the selected voice, a change button; **EMOTION & STYLE**
  chips (8 primary emotions and more: Happy, Sad, Anger, Calm, Fear,
  Whisper, Joy…); **PROSODY**: Pace, Pitch (low/normal/high) and
  Expression (subtle/natural/animated) segmented controls.
- **GENERATION**: the **AI Freedom** control with five levels
  (Strict → Wild: pre-calibrated temperature/top_p/top_k combinations),
  a seed field, the **ALLOW SFX** toggle, and sound-effect/pause insert
  buttons.

**ADVANCED** — full expert control: the complete 21-emotion grid, styles
(Singing / Whispering / Shouting), prosody (five speed levels, pitch,
delivery), inline pauses, sound effects, generation parameters
(temperature, top_p, top_k, max_new_tokens, seed, appended silence,
normalisation, auto-play), reference audio and a live **prompt preview**.

**The token system** (the Token Guide in the Help menu provides a full
description, in Hungarian): the controls insert `<|category:tag|>` tokens
into the prompt (for example `<|emotion:fear|>`, `<|prosody:speed_slow|>`,
`<|sfx:laughter|>Haha`). Sentence-start tokens apply to the whole
sentence; inline tokens apply at the exact position; SFX tokens carry an
onomatopoeia.

<a id="generating-speech"></a>
## Generating speech

- **Ctrl+Enter / the GENERATE button**: generates the current text in one
  call (the model loads automatically if needed).
- **Esc**: stop a running generation (finished files are kept), leave
  fullscreen, or close the search bar.
- **Replay** (Edit menu): replay the most recent audio.
- Output: **24,000 Hz mono, 32-bit float WAV** in `outputs\`, with an
  automatic timestamped name (suffixed on collision).
- The status bar shows the measured time and the real-time factor (RTF).

<a id="long-generation"></a>
## Long Generation

**Generate Long** (`Ctrl+Shift+Return`, or the editor button) is designed
for long texts:

1. The text is split into **safe parts** (a target of roughly 400
   characters at sentence boundaries), `$SPEAKER:` lines are recognised,
   and a duration estimate is shown.
2. In the **Long Narration Generator** dialog you can review the parts,
   map speakers to voices, and enable **Review before generating**
   (off by default; when enabled, the batch window opens in review mode
   and only starts once you press **GENERATE CHECKED**).
3. The parts run in sequence in the **Batch Generation — Scene** window
   (with 0.5 s of trailing silence). Closing the batch window stops the
   running batch, so do not close it accidentally while generating.
4. When the run finishes, the scene's status updates automatically
   (COMPLETE / PARTIAL / ERROR). Use **Combine Scene** to produce a
   single listenable output from the parts (see
   [Audio composition](#audio-composition)).

<a id="batch-generation-en"></a>
## Batch Generation

GUINEO has **one Batch Generation system** — one window, one batch
manager (BatchManager), one generation backend — that exposes **two valid
workflows** depending on where you open it from:

| | **Batch Generation — Scene mode** | **Batch Queue (manual mode)** |
|---|---|---|
| **Entry point** | Generate Long (`Ctrl+Shift+Return`), or File → Load Dialogue Scene | Project → Batch Generation… (or the classic Tools menu) |
| **Purpose** | Covering a scene's blocks, version management, combining | Queuing arbitrary audio jobs, independent of Scenes |
| **Title** | “Batch Generation — <Scene name>” | “Batch Queue” |
| **Table** | 7 columns: number, checkbox, file name, Generated (versions), status, duration, actions | 5 columns: number, file name, status, duration, actions |
| **Header** | Coverage (e.g. “coverage: PARTIAL (3/4)”) and a run id | — |
| **Actions** | All/None selection, **GENERATE CHECKED (N)** (generates exactly the checked rows), per-row Play / Stop playback / **Regen** (new version), **Combine Scene**, **Export Audio…**, a version picker (“N versions ▾”) | **+ Add / Edit / Duplicate / Remove / Up / Down**, **Save Queue… / Load Queue…** (YAML) / Clear, **START BATCH**, per-row Play / Stop playback / Regen, **Merge Completed Parts** |
| **Scene outputs** | SCENE COMBINED OUTPUTS section: resolved output, versions, STALE badge, Use as output / Clear | none (the output is not linked to a Scene) |

**Shared behaviour:**

- **Only one batch window is live at a time.** Opening the same mode again
  simply focuses the existing window; requesting the other mode closes the
  old window cleanly (a running batch keeps running) and opens the new
  mode.
- **Review Before Generating** (in the long narration dialog): jobs stay
  PENDING until you press the generate button.
- **Pause**: the batch stops picking up new jobs; the current job
  finishes. (The current build has no Resume button — use **Stop** to end
  the batch: pending jobs become SKIPPED, and Restart resets them to
  PENDING.)
- **Stop**: waiting jobs become SKIPPED immediately; the running job is
  cancelled at the next safe checkpoint; audio already written to disk is
  preserved.
- **Regen**: in scene mode it creates a **new versioned** file after
  confirmation (the previous version is kept); in manual mode it
  overwrites the file in place.
- **Merge Completed Parts** (manual mode only): merges the completed jobs
  in queue order into one WAV (300 ms of silence between parts by
  default — optionally randomised; a crossfade applies only at 0 ms
  silence). This output is **not linked to a Scene** and does not affect
  Project Assembly.

<a id="audio-composition"></a>
## Audio composition

GUINEO's provenance system records exactly **which block versions** make
up a scene:

1. **Block versions**: every generation produces a new version
   (`v01`, `v02`, `v03`…). If you explicitly select an older one, that
   choice stands — a newer generation alone never overrides it. Without
   an explicit selection the **latest valid version** is used.
2. **Scene composition**: the scene's current composition is a **derived
   mapping** — which version belongs to each block right now. For
   example:

   ```
   B1 → v02    B2 → v01    B3 → v04    B4 → v01
   ```

3. **Combine Scene**: produces an **immutable snapshot** of the
   composition — a Combined output, also versioned (`Combined v01`,
   `v02`…) — recording exactly which block version each slot used. Older
   combined outputs remain available and selectable.

   ```
   Scene composition:           Combine Scene
   B1 → v02                     ───────────▶   Combined v01
   B2 → v01                                     (immutable snapshot)
   B3 → v04
   B4 → v01
   ```

The SCENE COMBINED OUTPUTS section of the Batch Generation — Scene window
shows the resolved output, the version list (with durations), the STALE
badge, and **Use as output** / clear-explicit-selection. The dialog's
geometry and column configuration (widths, order, visibility) are
remembered and restored after a restart.

<a id="stale-en"></a>
## STALE

The **STALE** badge means that a combined output **no longer reflects the
scene's current composition**. It does **not** mean the file is corrupt,
broken or unusable — it is intact, playable and selectable.

Example:

```
Combined v01 contains:     The scene's CURRENT composition:
  B1 → v02                   B1 → v02
  B2 → v01                   B2 → v01
  B3 → v01                   B3 → v02   ← a new version was generated
```

Because B3 now has a newer version, Combined v01 is **STALE**.

A combined output also becomes STALE when a **new block** is added to the
scene, a block is **removed**, or a source **file is missing** from disk.

**Explicit stale selection**: an outdated output can deliberately be
chosen as the scene's output — GUINEO warns and asks for a “Use Anyway”
confirmation. **Combine Scene** always produces a fresh snapshot and
takes over as the output.

<a id="project-assembly"></a>
## Project Assembly

**Assemble Audio** (bottom of the sidebar, or `Ctrl+Shift+A`) stitches
the finished audio of several scenes into one file:

1. The dialog lists **one row per scene** with the resolved output
   (“Combined v02”, “Latest audio”, “Selected · CUSTOM”) and STALE /
   MISSING badges. Rows can be reordered (Move Up / Down); each row's
   output can be overridden with **Choose Output…**.
2. Settings: output file name, folder (default `outputs\combined`), the
   **gap between scenes** (0–3 s, default 0.5 s), **normalisation to
   −18 LUFS** (on by default), and an optional **MP3 copy** (requires
   FFmpeg — see [Exporting](#exporting)).
3. Output: WAV (+ optionally MP3); the assembly is recorded in History
   and in the project's `combined_outputs` lineage.

**Safety rule**: the system will **never silently use a single part
(block) audio as a whole scene's output**. If no valid complete scene
output exists, the row shows **REVIEW REQUIRED** — with the named
remedies: **Combine Scene Now** or **Choose Output**. If a source file is
missing, the assembly stops with an error naming the scene — nothing is
silently skipped.

<a id="exporting"></a>
## Exporting

**Batch Audio Export** (Batch Generation — Scene window,
**Export Audio…**) — four precisely defined scopes:

| Scope | What it copies |
|---|---|
| **Selected rows (N)** | the outputs of the checked rows |
| **All generated parts** | the outputs of every job that is a scene part |
| **Scene combined outputs** | **all** combined outputs of the scene (every version) |
| **Everything in this batch** | the parts **and** the combined outputs together |

The export is a **copy without transcoding** (WAV, provenance file
names); collisions get an automatic “(2)” suffix; missing files are
listed in the summary dialog.

**Project Export** (File → Export Project…, `Ctrl+Shift+E`) creates a
**folder** (not a ZIP) at the destination you choose:

```
<ProjectName>/
├─ project.json              ← scene index, characters, combined lineage
├─ scenes/<Scene>/
│   ├─ scene.json            ← text, blocks, versions, provenance
│   └─ audio/001.wav …       ← the scene's audio (optional: “Include
│                               Generated Audio”, on by default)
├─ characters/<Character>/character.json
├─ references/voices/<Profile>/   ← reference audio + transcripts
│                                 (optional: “Include Referenced Voice
│                                  Assets”, on by default)
└─ combined_outputs/         ← combined WAVs (+ project-level MP3)
```

All internal paths are **relative**. Model files are **not** part of the
export. Limitation: the current build has **no user-facing command to
reimport** an exported project directory — File → Open Project opens the
native `projects\` directory and the legacy `*.sproj` format.

<a id="history"></a>
## History

**Ctrl+H** or Project → History. Every generation — single, long,
combined, assembled — creates a **reproducible JSON entry** in
`settings\history\`: the full prompt, parameters, voice profile, measured
timings, GPU, warnings, scene/character/block context and the output
path. Clicking an entry selects it (the Recent Audio list refreshes); the
ALL HISTORY header icon opens the detailed view, from which settings can
be restored and audio played or exported. Entries can be deleted
(deletion is containment-checked within the history directory).

<a id="recents-en"></a>
## Recents

- **The RECENTS panel** (left sidebar): the last 5 projects / scenes /
  characters / history entries, depending on the context. A “+” button
  creates new items.
- **Context Lists**: ALL PROJECTS / ALL SCENES / ALL CHARACTERS / ALL
  HISTORY — a quick overview of everything. Every ALL SCENES row has a
  pencil icon for instant renaming.

<a id="themes"></a>
## Themes

- **Five themes**: Modern Dark (the default), Dark, Light, Synthwave and
  Retro Console — switchable from the View → Theme menu, persisted
  immediately.
- **Fullscreen**: **F11** toggles; Esc also leaves fullscreen.
- **Volume**: the transport slider (0–100); the value **persists** across
  restarts.
- **Reset Layout** (View menu): restores the default layout.
- Window geometry and the batch window's size and column configuration
  are remembered as well.

<a id="the-model-en"></a>
## The model

- **Model**: Higgs TTS 3 (Higgs Audio V3) by Boson AI. GUINEO loads the
  community Transformers port
  `multimodalart/higgs-audio-v3-tts-4b-transformers` (the original:
  `bosonai/higgs-tts-3-4b`).
- **Loading**: at startup the application offers to load it (“Load the
  model now?…”); otherwise Generate loads it automatically. While loading,
  a dialog with the **GUINEO animation** runs (phase messages and an
  elapsed timer) and can be cancelled.
- **Precision**: bfloat16 (recommended) / float16 (experimental) /
  float32 — set in Settings; takes effect after the model reloads.
- **Languages**: 102 languages — 85 at production quality (Tier 1,
  including **Hungarian**) and 17 usable (Tier 2). The full list: Help →
  Supported Languages.
- **Local processing**: after the first download everything runs locally;
  there is no telemetry and no cloud.

<a id="anomaly-detection"></a>
## Anomaly detection

During long generation GUINEO **conservatively detects** pathological
outputs (the P3.27B protection):

- a runaway **trailing silence** (tail ≥ 10 s **and** ≥ 50% of the total
  length),
- an output far **longer than expected** (≥ 3× the estimate **and** ≥
  estimate + 20 s),
- a **silent** output (no audible speech in ≥ 5 s).

**Important**: detection is advisory — the file is **never trimmed or
deleted**. Affected rows show a ⚠ marker next to the duration in the
batch window (with a detailed breakdown), History records the anomaly,
and the audio can be **regenerated** at any time (as a new version; the
old one is kept). The model cannot guarantee the absence of excessive
trailing silence — this protection mitigates, but does not eliminate,
the effect.

<a id="error-handling"></a>
## Error handling

Every error surfaces with a **readable message** (dialog plus status
bar), with a suggested action where possible: for example “the model is
not loaded — press Generate”, “not enough VRAM (~10 GB required)”,
“reference audio missing”. Errors are logged to `logs\`
(`application.log`, `engine.log`, `crash.log`, `bootstrap.log`). A failure
never crashes the application, and a failed regeneration never degrades
existing audio.

**Path safety**: output file names are always contained inside
`outputs\`; imported projects are stripped of escaping
(relative/absolute/traversal) paths; history deletion is
containment-checked.

<a id="persistence"></a>
## Persistence

| What | Where | Survives restart? |
|---|---|---|
| Theme, volume, window and batch-window geometry, columns | `settings\settings.json` | ✅ |
| Build counter | `settings\build.json` | ✅ (+1 on every launch) |
| History | `settings\history\*.json` | ✅ |
| Projects, scenes, characters, versions, provenance | `projects\<id>\project.json` | ✅ |
| Voice profiles | `voices\` | ✅ |
| Generated audio | `outputs\` | ✅ |
| Presets | `presets\` | ✅ |
| Model weights (~9 GB) | `models\higgs-audio-v3\` | ✅ (never re-downloaded) |
| Logs | `logs\` | ✅ |

After a restart everything above returns; the model must be loaded into
VRAM again (about 10–30 s). The `update.bat` update path always preserves
these folders.

<a id="keyboard-shortcuts"></a>
## Keyboard shortcuts

| Shortcut | Action |
|---|---|
| `Ctrl+Enter` | Generate speech |
| `Ctrl+Shift+Enter` | Generate Long Narration |
| `Esc` | Stop generation / leave fullscreen / close the search bar |
| `Space` | Play / pause (when the editor is not focused) |
| `Ctrl+N` / `Ctrl+Shift+N` | New project / new scene |
| `Ctrl+O` / `Ctrl+S` | Open / save project |
| `Ctrl+Shift+E` | Export project |
| `Ctrl+Shift+A` | Assemble scenes |
| `Ctrl+L` | Voice Profiles |
| `Ctrl+H` | History |
| `F11` | Fullscreen |
| `Ctrl+Q` | Exit |

<a id="menus"></a>
## Menus

- **File**: New Project, New Scene, Open Project, Save Project, Export
  Project…, Save / Load Dialogue Scene, Import / Export Voice, Import /
  Export Preset, Exit
  *(Assembly is not a menu item: the pinned **Assemble Audio** button at
  the bottom of the sidebar — or `Ctrl+Shift+A` — opens it; see
  [Project Assembly](#project-assembly).)*
- **Edit**: Generate, Generate Long Narration, Stop, Replay, Clear
- **View**: Theme (five themes), Reset Layout, Fullscreen, Show Prompt
  Blocks
- **Project**: Batch Generation…, Voice Profiles, History, Preset
  Manager…, Open Output Folder
- **Help**: Documentation, Token Guide…, Supported Languages…, Copy Debug
  Information, Font Status…, About, ─, Settings, Benchmark

The right side of the top bar holds undo/redo, save, export and the
GENERATE button.

<a id="settings"></a>
## Settings

Help → Settings:

- **General**: auto-play after generation, output normalisation, output
  format, randomised silence when concatenating.
- **Performance**: model precision (bfloat16 / float16 / float32) —
  affects memory use and speed; takes effect the next time the model
  loads.
- **Application Theme**: theme selection.

<a id="version-info"></a>
## Version info

GUINEO is a development build: the version is `v1.0.0` and the **build
number increments on every launch** (`settings\build.json`), shown in the
status bar and the Help → About dialog. About also displays the full
environment (Python, PyTorch, Transformers, Qt, CUDA, GPU, model,
precision) and offers a **Copy Debug Info** button.

<a id="higgs-licensing"></a>
## Higgs TTS 3 Licensing

GUINEO uses **Higgs TTS 3 from Boson AI**. The model is provided under
the **Boson Higgs TTS 3 Research and Non-Commercial License** — this is
**not an open source licence**:

- **Research and non-commercial use** is free of charge.
- The current licence includes a **Creator Use Grant**: digital creators
  may create, publish and **monetise creative content** (podcasts,
  videos, audiobooks, social-media posts and similar) on channels they
  own or control, provided they **credit Boson AI's Higgs Audio** — for
  example, “This audio was created with Boson AI's Higgs Audio —
  https://www.boson.ai/higgs-audio” in the description or in the audio
  itself.
- **Commercial use** — production deployment, hosted API use, embedding
  the model in a product or application, redistribution or reselling the
  model — **requires the appropriate commercial licence**:
  <https://boson.ai> · contact@boson.ai.
- Being able to download the model weights does **not** grant any
  commercial redistribution right.
- The licence also prohibits, among other things: voice cloning or
  impersonation of real people without explicit, verifiable consent;
  deceptive or fraudulent use; and using outputs to train non-Boson
  generative models.

The complete official licence text ships with the project:
[`LICENSE_HIGGS_TTS_3.txt`](LICENSE_HIGGS_TTS_3.txt). Third-party licences
and attribution: [`THIRD_PARTY_LICENSES.md`](THIRD_PARTY_LICENSES.md).

<a id="troubleshooting"></a>
## Troubleshooting

| Symptom | What to do |
|---|---|
| **Model not found / not downloaded** | Run `launch.bat` — the bootstrap detects the missing model and downloads it (resumable). Details: `logs\bootstrap.log`. The model lives in `models\higgs-audio-v3\`. |
| **Hugging Face / download errors** | GUINEO uses only a public model and never needs an authentication token. Behind a firewall or proxy, check HTTPS connectivity; the download resumes from `.incomplete` files. |
| **Missing voice profile** | Open the Voice Library (`Ctrl+L`) and check that the profile exists and that its reference WAV is really inside `voices\`. Re-import it if needed (File → Import Voice). |
| **A generated file is missing** | The History entry shows the path (`outputs\…`). If the file was deleted manually, the scene output becomes MISSING — regenerate the part, or choose another version/output. |
| **STALE scene output** | Not an error: the combined output no longer reflects the current composition. Remedies: **Combine Scene** (a fresh snapshot) or **Choose Output** (a deliberate selection, confirmed with “Use Anyway”). |
| **Generation anomaly (⚠)** | The file was kept and merely flagged. Listen to it; if it is wrong, regenerate the row — a new version is created and the old one is kept. |
| **GPU / CUDA issues** | Install the latest NVIDIA driver. The model uses ~10 GB of VRAM — close other VRAM-heavy applications. Without CUDA the app falls back to the CPU (slow). The status bar shows the CUDA state. |
| **OpenGL / display issues** | The animated background falls back automatically through GPU → static CPU → plain dark; the application remains usable. Updating the graphics driver usually helps. |
| **Fullscreen** | Enter with `F11`; leave with `F11` or `Esc`. |
| **No sound / playback silent** | Playback uses the system audio device; check the Windows volume and the default device. If the playback library is unavailable, GUINEO still writes the files — it simply cannot play them. |
| **Export errors** | MP3 creation requires **FFmpeg** (install it and put it on PATH); without it, a WAV is produced with a warning. Ensure a few GB of free space on the target drive. |
| **Generate does nothing** | Wait for the model to finish loading (status bar / `logs\application.log`: “Model loaded”), then try again. |
| **White/empty model-load window on first run** | A known historical defect, fixed in the current build; if you ever see it, update your graphics driver and send `logs\crash.log` to the developer. |

Further logs: `logs\application.log`, `logs\engine.log`,
`logs\bootstrap.log`, `logs\crash.log`.

---

## ☕ Support

[svg](https://github.com/AGE-T/Animal-audio-detector#-t%C3%A1mogat%C3%A1s--support)

If you find this project helpful and want to support my work, feel free to buy
me a coffee!

[Support me on Ko-fi](https://ko-fi.com/thomashoysgameaudio)
([image](https://camo.githubusercontent.com/12ddacd4b1ffd5473ce102384087761260706d10e6ee9d4c4d4bcf84c9608dcc/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f537570706f72745f6f6e5f4b6f2d2d66692d4631363036313f7374796c653d666f722d7468652d6261646765266c6f676f3d6b6f2d6669266c6f676f436f6c6f723d7768697465))

Or click the button below:

[Ko-fi Support](https://ko-fi.com/thomashoysgameaudio)
([image](https://camo.githubusercontent.com/201ef269611db7eb6b5d08e9f756ab8980df3014b64492770bdf13a6ed924641/68747470733a2f2f6b6f2d66692e636f6d2f696d672f676974687562627574746f6e5f736d2e737667))

https://ko-fi.com/thomashoysgameaudio
