"""
SpeechStudio Token Guide Dialog.

Shows the complete Higgs Audio V3 technical reference: all 43 tokens,
combination matrix, SFX reference table, scope/sampling analysis, and
practical recommendations — all in a scrollable, themed dialog.

Accessible via Help → Token Guide...
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QPushButton, QTextEdit, QLabel,
    QHBoxLayout,
)

from ui.theme import Palette


# ---------------------------------------------------------------------------
# Guide content (HTML for rich formatting)
# ---------------------------------------------------------------------------

# P3.25 (audit SS-M13): the HTML is NO LONGER formatted at module
# import. The template is kept RAW; TokenGuideDialog builds the final
# HTML at OPEN time with the CURRENT Palette — theme switches now
# recolour the guide (previously the import-time colours were frozen
# forever).
_GUIDE_HTML_TEMPLATE = """\
<h1 style="color: {accent};">Higgs Audio V3 — Technikai Referencia</h1>
<p style="color: {text_secondary}; font-size: 11px;">
Forrás: Boson AI PROMPTING.md, Higgs Audio V3 Model Card, GUINEO verified experiments.<br>
Dokumentum verzió: 2.0 | Utolsó frissítés: 2026-07
</p>

<h2 style="color: {accent};">1. Executive Summary</h2>
<p style="color: {text_primary}; line-height: 1.5;">
A Higgs Audio V3 <b>43 hivatalos kontrolltokent</b> támogat 4 kategóriában:
<b>21 emotion</b>, <b>3 style</b>, <b>10 prosody</b> (4 speed + 2 pitch + 2 delivery + 2 pause),
és <b>9 SFX</b>. A tokenek kétféle elhelyezést igényelnek:
<b>mondat-szintű</b> (emotion, style, speed/pitch/delivery — a mondat elejére) és
<b>inline</b> (pause, long_pause, sfx — a pontos pozícióba).
Az SFX tokenek <b>kötelezően</b> tartalmaznak egy onomatopoeta szót
(pl. <code>&lt;|sfx:laughter|&gt;Haha</code>), amely a modell számára az akusztikus cue-t adja.
A tokenek kombinálhatók (max ~3 mondat-szintű / mondat ajánlott), de azonos
kategórián belüli konfliktusok (pl. pitch_low + pitch_high) kerülendők.
Multi-speaker támogatás <b>nincs</b> — a modell csak egy referencia hangot fogad hívásonként.
Token-gazdag promptokhoz ajánlott: temperature=1.2–1.4, top_k=300, top_p=0.95, max_new_tokens=4096, dtype=bfloat16.
</p>

<h2 style="color: {accent};">2. Token Formátum és Elhelyezési Szabályok</h2>
<p style="color: {text_primary};">
<b>Univerzális szintaxis:</b> <code>&lt;|kategória:tag|&gt;</code>
</p>
<table cellspacing="6" cellpadding="4" style="color: {text_primary}; font-size: 12px;" border="1" bordercolor="{border}">
<tr style="color: {accent}; font-weight: bold; background-color: {bg_surface};">
  <td>Típus</td><td>Kategóriák</td><td>Elhelyezés</td><td>Szabály</td>
</tr>
<tr>
  <td><b>Mondat-szintű</b></td>
  <td>emotion, style, speed, pitch, delivery</td>
  <td>A mondat <b>elején</b></td>
  <td>A teljes mondatot befolyásolja</td>
</tr>
<tr>
  <td><b>Inline</b></td>
  <td>sfx, pause, long_pause</td>
  <td>A <b>pontos pozícióban</b></td>
  <td>Az adott pozícióban fejti ki hatását</td>
</tr>
</table>

<h2 style="color: {accent};">3. Teljes Token Katalógus (43 token)</h2>

<h3 style="color: {info};">3.1 Emotion (21, mondat-szintű)</h3>
<table cellspacing="4" cellpadding="3" style="color: {text_primary}; font-size: 11px;" border="1" bordercolor="{border}">
<tr style="color: {accent}; font-weight: bold; background-color: {bg_surface};">
  <td>Tag</td><td>Token</td><td>BosonAI leírás</td>
</tr>
<tr><td><code>elation</code></td><td><code>&lt;|emotion:elation|&gt;</code></td><td>Öröm, izgatottság</td></tr>
<tr><td><code>amusement</code></td><td><code>&lt;|emotion:amusement|&gt;</code></td><td>Szórakozás, játék</td></tr>
<tr><td><code>enthusiasm</code></td><td><code>&lt;|emotion:enthusiasm|&gt;</code></td><td>Lelkesedés</td></tr>
<tr><td><code>determination</code></td><td><code>&lt;|emotion:determination|&gt;</code></td><td>Határozottság</td></tr>
<tr><td><code>pride</code></td><td><code>&lt;|emotion:pride|&gt;</code></td><td>Büszkeség</td></tr>
<tr><td><code>contentment</code></td><td><code>&lt;|emotion:contentment|&gt;</code></td><td>Elégedettség, nyugalom</td></tr>
<tr><td><code>affection</code></td><td><code>&lt;|emotion:affection|&gt;</code></td><td>Meg szeret, melegség</td></tr>
<tr><td><code>relief</code></td><td><code>&lt;|emotion:relief|&gt;</code></td><td>Megkönnyebbülés</td></tr>
<tr><td><code>contemplation</code></td><td><code>&lt;|emotion:contemplation|&gt;</code></td><td>Elgondolkodás</td></tr>
<tr><td><code>confusion</code></td><td><code>&lt;|emotion:confusion|&gt;</code></td><td>Zavarodottság</td></tr>
<tr><td><code>surprise</code></td><td><code>&lt;|emotion:surprise|&gt;</code></td><td>Meglepetés</td></tr>
<tr><td><code>awe</code></td><td><code>&lt;|emotion:awe|&gt;</code></td><td>Áhítat, csodálat</td></tr>
<tr><td><code>longing</code></td><td><code>&lt;|emotion:longing|&gt;</code></td><td>Vágyódás</td></tr>
<tr><td><code>arousal</code></td><td><code>&lt;|emotion:arousal|&gt;</code></td><td>Fokozott intenzitás</td></tr>
<tr><td><code>anger</code></td><td><code>&lt;|emotion:anger|&gt;</code></td><td>Düh</td></tr>
<tr><td><code>fear</code></td><td><code>&lt;|emotion:fear|&gt;</code></td><td>Félelem</td></tr>
<tr><td><code>disgust</code></td><td><code>&lt;|emotion:disgust|&gt;</code></td><td>Undor</td></tr>
<tr><td><code>bitterness</code></td><td><code>&lt;|emotion:bitterness|&gt;</code></td><td>Keserűség</td></tr>
<tr><td><code>sadness</code></td><td><code>&lt;|emotion:sadness|&gt;</code></td><td>Szomorúság</td></tr>
<tr><td><code>shame</code></td><td><code>&lt;|emotion:shame|&gt;</code></td><td>Szégyen</td></tr>
<tr><td><code>helplessness</code></td><td><code>&lt;|emotion:helplessness|&gt;</code></td><td>Tehetetlenség</td></tr>
</table>

<h3 style="color: {info};">3.2 Style (3, mondat-szintű)</h3>
<table cellspacing="4" cellpadding="3" style="color: {text_primary}; font-size: 11px;" border="1" bordercolor="{border}">
<tr style="color: {accent}; font-weight: bold; background-color: {bg_surface};">
  <td>Tag</td><td>Token</td><td>Leírás</td>
</tr>
<tr><td><code>singing</code></td><td><code>&lt;|style:singing|&gt;</code></td><td>Éneklés</td></tr>
<tr><td><code>shouting</code></td><td><code>&lt;|style:shouting|&gt;</code></td><td>Kiáltás, hangerő-projekció</td></tr>
<tr><td><code>whispering</code></td><td><code>&lt;|style:whispering|&gt;</code></td><td>Suttogás</td></tr>
</table>

<h3 style="color: {info};">3.3 Prosody (10: 8 mondat-szintű + 2 inline)</h3>
<table cellspacing="4" cellpadding="3" style="color: {text_primary}; font-size: 11px;" border="1" bordercolor="{border}">
<tr style="color: {accent}; font-weight: bold; background-color: {bg_surface};">
  <td>Tag</td><td>Token</td><td>Pontos hatás</td><td>Ehelyezés</td>
</tr>
<tr><td><code>speed_very_slow</code></td><td><code>&lt;|prosody:speed_very_slow|&gt;</code></td><td>≈0.65× sebesség</td><td>mondat eleje</td></tr>
<tr><td><code>speed_slow</code></td><td><code>&lt;|prosody:speed_slow|&gt;</code></td><td>≈0.85× sebesség</td><td>mondat eleje</td></tr>
<tr><td><code>speed_fast</code></td><td><code>&lt;|prosody:speed_fast|&gt;</code></td><td>≈1.2× sebesség</td><td>mondat eleje</td></tr>
<tr><td><code>speed_very_fast</code></td><td><code>&lt;|prosody:speed_very_fast|&gt;</code></td><td>≈1.4× sebesség</td><td>mondat eleje</td></tr>
<tr><td><code>pitch_low</code></td><td><code>&lt;|prosody:pitch_low|&gt;</code></td><td>≈−3 félhang</td><td>mondat eleje</td></tr>
<tr><td><code>pitch_high</code></td><td><code>&lt;|prosody:pitch_high|&gt;</code></td><td>≈+2.5 félhang</td><td>mondat eleje</td></tr>
<tr><td><code>expressive_high</code></td><td><code>&lt;|prosody:expressive_high|&gt;</code></td><td>Kifejezőbb előadás</td><td>mondat eleje</td></tr>
<tr><td><code>expressive_low</code></td><td><code>&lt;|prosody:expressive_low|&gt;</code></td><td>Laposabb előadás</td><td>mondat eleje</td></tr>
<tr><td><code>pause</code></td><td><code>&lt;|prosody:pause|&gt;</code></td><td>≈400–700 ms szünet</td><td><b>inline</b></td></tr>
<tr><td><code>long_pause</code></td><td><code>&lt;|prosody:long_pause|&gt;</code></td><td>≈700–1500 ms szünet</td><td><b>inline</b></td></tr>
</table>

<h3 style="color: {info};">3.4 Sound Effects (9, inline + kötelező onomatopoeia)</h3>
<p style="color: {warning}; font-weight: bold;">
⚠️ A token és az onomatopoeia között <b>NINCS szóköz</b>:<br>
<code>&lt;|sfx:laughter|&gt;Haha</code> — helyes<br>
<code>&lt;|sfx:laughter|&gt; Haha</code> — helytelen
</p>
<table cellspacing="4" cellpadding="3" style="color: {text_primary}; font-size: 11px;" border="1" bordercolor="{border}">
<tr style="color: {accent}; font-weight: bold; background-color: {bg_surface};">
  <td>Tag</td><td>Token</td><td>BosonAI onomatopoeia</td><td>Példa</td>
</tr>
<tr><td><code>cough</code></td><td><code>&lt;|sfx:cough|&gt;</code></td><td>Ahem</td><td><code>&lt;|sfx:cough|&gt;Ahem, welcome everyone.</code></td></tr>
<tr><td><code>laughter</code></td><td><code>&lt;|sfx:laughter|&gt;</code></td><td>Haha / Hehe</td><td><code>&lt;|sfx:laughter|&gt;Haha, so glad you could make it!</code></td></tr>
<tr><td><code>crying</code></td><td><code>&lt;|sfx:crying|&gt;</code></td><td>Boohoo / Sob</td><td><code>&lt;|sfx:crying|&gt;Sob, I can't believe it's over.</code></td></tr>
<tr><td><code>screaming</code></td><td><code>&lt;|sfx:screaming|&gt;</code></td><td>Ahh / Aaah</td><td><code>&lt;|sfx:screaming|&gt;Ahh, what was that?!</code></td></tr>
<tr><td><code>burping</code></td><td><code>&lt;|sfx:burping|&gt;</code></td><td>Burp</td><td><code>&lt;|sfx:burping|&gt;Burp, excuse me.</code></td></tr>
<tr><td><code>humming</code></td><td><code>&lt;|sfx:humming|&gt;</code></td><td>Hmm / Mmm</td><td><code>&lt;|sfx:humming|&gt;Hmm, let me think about that.</code></td></tr>
<tr><td><code>sigh</code></td><td><code>&lt;|sfx:sigh|&gt;</code></td><td>Uh / Ahh</td><td><code>&lt;|sfx:sigh|&gt;Ahh, what a day.</code></td></tr>
<tr><td><code>sniff</code></td><td><code>&lt;|sfx:sniff|&gt;</code></td><td>Sff</td><td><code>&lt;|sfx:sniff|&gt;Sff, I think I'm getting a cold.</code></td></tr>
<tr><td><code>sneeze</code></td><td><code>&lt;|sfx:sneeze|&gt;</code></td><td>Achoo</td><td><code>&lt;|sfx:sneeze|&gt;Achoo! Bless me.</code></td></tr>
</table>
<p style="color: {text_secondary}; font-size: 11px;">
<b>Az onomatopoeia kötelező.</b> A BosonAI dokumentáció szerint "a cue szó adja az akusztikus jelet a hatás megvalósításához".
Onomatopoeia nélkül az SFX valószínűleg nem vagy csak gyengén működik.
</p>

<h2 style="color: {accent};">4. Kombinációs Mátrix</h2>
<table cellspacing="4" cellpadding="4" style="color: {text_primary}; font-size: 11px;" border="1" bordercolor="{border}">
<tr style="color: {accent}; font-weight: bold; background-color: {bg_surface};">
  <td>Kombináció</td><td>Támogatott?</td><td>Dokumentált példa?</td><td>Megjegyzés</td>
</tr>
<tr style="background-color: #1a2a1a;">
  <td><b>emotion + SFX</b></td>
  <td style="color: {success};">✅ SUPPORTED</td>
  <td>✅ Igen</td>
  <td><code>&lt;|emotion:elation|&gt;&lt;|sfx:laughter|&gt;Haha, welcome!</code></td>
</tr>
<tr style="background-color: #1a2a1a;">
  <td><b>emotion + prosody (speed)</b></td>
  <td style="color: {success};">✅ SUPPORTED</td>
  <td>✅ Igen</td>
  <td><code>&lt;|emotion:fear|&gt;&lt;|prosody:speed_slow|&gt;</code></td>
</tr>
<tr style="background-color: #1a2a1a;">
  <td><b>emotion + prosody (pitch)</b></td>
  <td style="color: {success};">✅ SUPPORTED</td>
  <td>✅ Igen</td>
  <td><code>&lt;|emotion:fear|&gt;&lt;|prosody:pitch_low|&gt;</code></td>
</tr>
<tr style="background-color: #1a2a1a;">
  <td><b>emotion + prosody (delivery)</b></td>
  <td style="color: {success};">✅ SUPPORTED</td>
  <td>✅ Igen</td>
  <td><code>&lt;|emotion:elation|&gt;&lt;|prosody:expressive_high|&gt;</code></td>
</tr>
<tr style="background-color: #1a2a1a;">
  <td><b>emotion + style</b></td>
  <td style="color: {success};">✅ SUPPORTED</td>
  <td>✅ Igen</td>
  <td><code>&lt;|emotion:anger|&gt;&lt;|style:shouting|&gt;</code></td>
</tr>
<tr style="background-color: #1a2a1a;">
  <td><b>style + prosody</b></td>
  <td style="color: {success};">✅ SUPPORTED</td>
  <td>✅ Igen</td>
  <td><code>&lt;|style:whispering|&gt;&lt;|prosody:speed_slow|&gt;</code></td>
</tr>
<tr style="background-color: #1a2a1a;">
  <td><b>emotion + style + prosody</b></td>
  <td style="color: {success};">✅ SUPPORTED</td>
  <td>✅ Igen</td>
  <td><code>&lt;|emotion:fear|&gt;&lt;|prosody:pitch_low|&gt;&lt;|prosody:speed_slow|&gt;</code></td>
</tr>
<tr style="background-color: #2a2a1a;">
  <td><b>emotion + prosody + SFX</b></td>
  <td style="color: {warning};">⚠️ POSSIBLE</td>
  <td>❌ Nincs</td>
  <td>Technikailag működik, de nincs hivatalos példa</td>
</tr>
<tr style="background-color: #2a2a1a;">
  <td><b>style + emotion + prosody + SFX</b></td>
  <td style="color: {warning};">⚠️ POSSIBLE</td>
  <td>❌ Nincs</td>
  <td>4+ token — a modell fókusza csökkenhet</td>
</tr>
<tr style="background-color: #2a1a1a;">
  <td><b>pitch_low + pitch_high</b></td>
  <td style="color: {error};">❌ CONFLICT</td>
  <td>—</td>
  <td>Ellentmondó magasság — kerüld</td>
</tr>
<tr style="background-color: #2a1a1a;">
  <td><b>speed_slow + speed_fast</b></td>
  <td style="color: {error};">❌ CONFLICT</td>
  <td>—</td>
  <td>Ellentmondó tempó — kerüld</td>
</tr>
<tr style="background-color: #2a1a1a;">
  <td><b>expressive_high + expressive_low</b></td>
  <td style="color: {error};">❌ CONFLICT</td>
  <td>—</td>
  <td>Ellentmondó kifejezés — kerüld</td>
</tr>
<tr style="background-color: #2a1a1a;">
  <td><b>singing + whispering</b></td>
  <td style="color: {error};">❌ CONFLICT</td>
  <td>—</td>
  <td>Ellentmondó stílus — kerüld</td>
</tr>
<tr style="background-color: #2a1a1a;">
  <td><b>Több emotion egy mondatban</b></td>
  <td style="color: {error};">❌ CONFLICT</td>
  <td>—</td>
  <td>A modell összezavarodik — 1 emotion / mondat</td>
</tr>
</table>

<h2 style="color: {accent};">5. Token Sorrend és Scope</h2>
<h3 style="color: {info};">5.1 Sorrend</h3>
<ul style="color: {text_primary}; line-height: 1.6;">
<li><b>Mondat-szintű tokenek a mondat elejére</b> — a szöveg előtt</li>
<li><b>Inline tokenek a pontos pozícióba</b> — ahol a hatás bekövetkezik</li>
<li>A mondat-szintű tokenek <b>belső sorrendje nem kötött</b> — a GUINEO
emotion → style → speed → pitch → delivery sorrendet használja, de a modell
elfogad más sorrendet is</li>
<li><b>SFX formátum:</b> <code>&lt;|sfx:tag|&gt;onomatopoeia</code> — nincs szóköz,
az onomatopoeia után jöhet normál szöveg</li>
</ul>

<h3 style="color: {info};">5.2 Scope (érvényességi tartomány)</h3>
<table cellspacing="4" cellpadding="4" style="color: {text_primary}; font-size: 11px;" border="1" bordercolor="{border}">
<tr style="color: {accent}; font-weight: bold; background-color: {bg_surface};">
  <td>Forrás</td><td>Megfogalmazás</td><td>Értelmezés</td>
</tr>
<tr>
  <td>BosonAI PROMPTING.md</td>
  <td>"colors the <b>whole sentence</b>"</td>
  <td>Aktuális mondat</td>
</tr>
<tr>
  <td>BosonAI README (SGLang)</td>
  <td>"shape the <b>whole turn</b>"</td>
  <td>Teljes API kérés</td>
</tr>
</table>
<p style="color: {text_secondary}; font-size: 11px;">
<b>Gyakorlati ajánlás:</b> Kezeld úgy, mintha a tokenek a teljes promptban érvényesek lennének,
amíg felül nem írják őket. Új emotion beállításához <b>kezdj új mondatot</b> az új emotion tokennel.
<br><i>[Empirikus megfigyelés a GUINEO optimizer viselkedéséből, nem hivatalos modellgarancia]</i>
</p>

<h3 style="color: {info};">5.3 Reset / Normal</h3>
<p style="color: {text_primary};">
<b>Nincs hivatalos "reset" token.</b> A HIGGS V3 hivatalos referenciája
<b>nem tartalmaz</b> <code>speed_normal</code>, <code>pitch_normal</code>
vagy <code>expressive_normal</code> tokeneket. Ezek az alkalmazásban
"Normal" állapotot jelentenek — ilyenkor <b>nem kerül token kibocsátásra</b>.
A reset technika: új mondat kezdése új tokenekkel.
</p>

<h2 style="color: {accent};">6. Sampling Paraméterek</h2>
<h3 style="color: {info};">6.1 Ajánlott értékek token-gazdag promptokhoz</h3>
<table cellspacing="4" cellpadding="4" style="color: {text_primary}; font-size: 11px;" border="1" bordercolor="{border}">
<tr style="color: {accent}; font-weight: bold; background-color: {bg_surface};">
  <td>Paraméter</td><td>Ajánlott érték</td><td>Indoklás</td>
</tr>
<tr><td><b>temperature</b></td><td><b>1.2–1.4</b></td><td>Alacsonyabb temp kevésbé követi a tokeneket</td></tr>
<tr><td><b>top_k</b></td><td><b>300</b></td><td>Széles sampling szükséges a tokenek érvényesüléséhez</td></tr>
<tr><td><b>top_p</b></td><td><b>0.95</b></td><td>Több forrás is ezt ajánlja</td></tr>
<tr><td><b>max_new_tokens</b></td><td><b>4096</b></td><td>Hosszabb generálásokhoz (25 fps audióval ~164s a felső korlát — P3.45.2A)</td></tr>
<tr><td><b>dtype</b></td><td><b>bfloat16</b></td><td>float16 kevésbé stabil, ronthatja a token-követést</td></tr>
<tr><td><b>append_silence</b></td><td><b>0.5–1.0s</b></td><td>Természetesebb mondatzárás</td></tr>
</table>
<p style="color: {text_secondary}; font-size: 11px;">
<i>⚠️ Ezek GUINEO általi empirikus ajánlások, nem BosonAI hivatalos dokumentáció.
A BosonAI példái temperature=0.7–0.8, top_k=50 értékeket használnak, de ezek
egyszerű TTS-hez vannak, nem token-gazdag promptokhoz.</i>
</p>

<h3 style="color: {info};">6.2 Nincs dokumentált kapcsolat tokenek és sampling között</h3>
<p style="color: {text_primary};">
A BosonAI dokumentáció <b>nem írja le</b>, hogy például <code>anger + shouting</code>
alacsony temperature mellett stabilabb lenne. Ez <b>empirikus megfigyelés</b>
a GUINEO tesztjeiből.
</p>

<h2 style="color: {accent};">7. Multi-Speaker</h2>
<p style="color: {text_primary};">
<b>A Higgs V3 modell NEM támogat multi-speaker generálást egyetlen hívásban.</b>
</p>
<ul style="color: {text_primary}; line-height: 1.6;">
<li>A Transformers port <code>generate_speech()</code> <b>egyetlen</b> <code>reference_audio</code> tensort fogad</li>
<li>Az SGLang API <code>"references"</code> listája formailag elfogad több elemet, de
<b>nincs dokumentáció</b> arról, mi történik 2+ referenciával</li>
<li>Nincs <code>"speaker"</code> mező, nincs turn-taking mechanizmus, nincs dialógus API</li>
</ul>
<p style="color: {text_primary};">
<b>A <code>$SPEAKER:</code> szintaxis a GUINEO Narration rendszerének konvenciója</b> —
a modell nem ismeri. A rendszer minden speaker sort külön <code>generate_speech()</code> hívással
generál, különböző <code>reference_audio</code>-val.
</p>
<p style="color: {text_primary};">
A tokenek <b>hívásonként</b> érvényesek — nem "szivárognak át" egyik speaker-ről a másikra.
</p>

<h2 style="color: {accent};">8. Gyakorlati Ajánlás a Narration Rendszerhez</h2>

<h3 style="color: {info};">8.1 Mely tokeneket érdemes UI-ból vezérelni?</h3>
<table cellspacing="4" cellpadding="4" style="color: {text_primary}; font-size: 11px;" border="1" bordercolor="{border}">
<tr style="color: {accent}; font-weight: bold; background-color: {bg_surface};">
  <td>Token kategória</td><td>UI vezérlés?</td><td>Indoklás</td>
</tr>
<tr><td>Emotion (21)</td><td>✅ Dropdown</td><td>A legfontosabb kontroll</td></tr>
<tr><td>Style (3)</td><td>✅ Gombok</td><td>3 opció, egyszerű</td></tr>
<tr><td>Speed (4)</td><td>✅ Dropdown</td><td>Pontos sebesség</td></tr>
<tr><td>Pitch (2)</td><td>✅ Gombok</td><td>Csak 2 opció</td></tr>
<tr><td>Delivery (2)</td><td>✅ Gombok</td><td>Csak 2 opció</td></tr>
<tr><td>Pause / Long Pause</td><td>✅ Insert gombok</td><td>Inline pozíció, marker rendszer</td></tr>
<tr><td>SFX (9)</td><td>✅ Insert gombok</td><td>Onomatopoeia automatikus</td></tr>
</table>

<h3 style="color: {info};">8.2 Raw mód használata</h3>
<p style="color: {text_primary};">
Raw mód hasznos:
</p>
<ul style="color: {text_primary}; line-height: 1.6;">
<li><b>Komplex kombinációkhoz</b> (4+ token egy mondatban)</li>
<li><b>Per-line emotion/style override</b> multi-speaker dialógusban</li>
<li><b>Egyedi onomatopoeia szavakhoz</b> (pl. "Hahaha" "Haha" helyett)</li>
</ul>

<h3 style="color: {info};">8.3 Stabil default preset</h3>
<table cellspacing="4" cellpadding="4" style="color: {text_primary}; font-size: 11px;" border="1" bordercolor="{border}">
<tr style="color: {accent}; font-weight: bold; background-color: {bg_surface};">
  <td>Paraméter</td><td>Érték</td>
</tr>
<tr><td>Emotion</td><td>contentment (nyugodt, kellemes)</td></tr>
<tr><td>Style</td><td>(nincs)</td></tr>
<tr><td>Speed</td><td>Normal</td></tr>
<tr><td>Pitch</td><td>Normal</td></tr>
<tr><td>Delivery</td><td>expressive_high (kifejező, de nem túlzó)</td></tr>
<tr><td>Temperature</td><td>1.2</td></tr>
<tr><td>Top K</td><td>300</td></tr>
<tr><td>Top P</td><td>0.95</td></tr>
<tr><td>Max New Tokens</td><td>4096</td></tr>
<tr><td>Append Silence</td><td>0.5s</td></tr>
<tr><td>Precision</td><td>bfloat16</td></tr>
</table>
<p style="color: {text_primary};">
Ez egy <b>természetes, kifejező alapállapot</b>, amelytől könnyen lehet eltérni
emotion/speed/pitch változtatásával.
</p>

<h3 style="color: {info};">8.4 Maximális token sűrűség</h3>
<table cellspacing="4" cellpadding="4" style="color: {text_primary}; font-size: 11px;" border="1" bordercolor="{border}">
<tr style="color: {accent}; font-weight: bold; background-color: {bg_surface};">
  <td>Token szám / mondat</td><td>Javaslat</td>
</tr>
<tr style="background-color: #1a2a1a;"><td>1–3</td><td style="color: {success};">✅ Ajánlott — optimális modell fókusz</td></tr>
<tr style="background-color: #2a2a1a;"><td>4</td><td style="color: {warning};">⚠️ Működik, de a fókusz csökkenhet</td></tr>
<tr style="background-color: #2a1a1a;"><td>5+</td><td style="color: {error};">❌ Nem ajánlott — a modell elveszítheti a fókuszt</td></tr>
</table>

<h2 style="color: {accent};">9. Források és Bizonytalanságok</h2>
<h3 style="color: {success};">Hivatalosan dokumentált (BosonAI)</h3>
<ul style="color: {text_primary}; line-height: 1.6;">
<li>43 token pontos listája és szintaxisa ✅</li>
<li>Token elhelyezési szabályok (mondat-szintű vs inline) ✅</li>
<li>SFX onomatopoeia kötelezősége ✅</li>
<li>Speed/pitch/delivery pontos értékei ✅</li>
<li>Pause időtartamok (400–700ms, 700–1500ms) ✅</li>
<li>Kombinációs példák (emotion + SFX, emotion + prosody) ✅</li>
<li>"Delivery tokens first" szabály ✅</li>
</ul>

<h3 style="color: {warning};">GUINEO empirikus megfigyelések</h3>
<ul style="color: {text_primary}; line-height: 1.6;">
<li>Temperature 1.2–1.4 és top_k 300 ajánlás ⚠️</li>
<li>"Max 2–3 token / mondat" ajánlás ⚠️</li>
<li>Token scope = "teljes prompt" ⚠️</li>
<li>bfloat16 stabilabb, mint float16 ⚠️</li>
<li>Saját onomatopoeia szó használata ⚠️</li>
</ul>

<h3 style="color: {error};">Nem dokumentált / ismeretlen</h3>
<ul style="color: {text_primary}; line-height: 1.6;">
<li>Onomatopoeia nélküli SFX viselkedése ❓</li>
<li>Az SGLang <code>references</code> lista több elemmel ❓</li>
<li>A tokenek pontos scope-ja (mondat vs teljes prompt) ❓</li>
<li>5+ mondat-szintű token biztonságos kombinálása ❓</li>
</ul>

<hr style="border-color: {border};">
<p style="color: {text_secondary}; font-size: 10px; text-align: center;">
GUINEO Higgs Audio V3 Technikai Referencia v2.0<br>
Források: Boson AI PROMPTING.md, Higgs Audio V3 Model Card, Transformers Port README, GUINEO verified experiments
</p>
"""


def _build_guide_html() -> str:
    """Format the guide HTML with the CURRENT theme palette (SS-M13)."""
    return _GUIDE_HTML_TEMPLATE.format(
        accent=Palette.ACCENT,
        text_primary=Palette.TEXT_PRIMARY,
        text_secondary=Palette.TEXT_SECONDARY,
        text_disabled=Palette.TEXT_DISABLED,
        bg_base=Palette.BG_BASE,
        bg_surface=Palette.BG_SURFACE,
        bg_surface_alt=Palette.BG_SURFACE_ALT,
        bg_raised=Palette.BG_RAISED,
        border=Palette.BORDER,
        success=Palette.SUCCESS,
        warning=Palette.WARNING,
        error=Palette.ERROR,
        info=Palette.INFO,
    )


# Backwards-compatible alias (holds the import-time HTML; dialogs must
# call _build_guide_html() so theme changes are picked up).
GUIDE_HTML = _build_guide_html()


class TokenGuideDialog(QDialog):
    """Scrollable dialog showing the complete Higgs V3 technical reference."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Higgs Audio V3 — Technikai Referencia")
        self.setMinimumSize(850, 700)
        self.setStyleSheet("background-color: {0};".format(Palette.BG_BASE))
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # Rich text viewer
        self._text = QTextEdit()
        self._text.setReadOnly(True)
        self._text.setHtml(_build_guide_html())
        self._text.setStyleSheet(
            "QTextEdit {{ background-color: {0}; color: {1}; "
            "border: 1px solid {2}; border-radius: 6px; padding: 12px; }}".format(
                Palette.BG_SURFACE, Palette.TEXT_PRIMARY, Palette.BORDER))
        layout.addWidget(self._text, 1)

        # Close button
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)
