# Magazijn-HMI in TIA Portal V20

Voorbeeld om het ontwerp te bouwen in TIA Portal V20. Gebruik `magazijn-hmi/industrieel.html`
als referentie: dat ontwerp is 1280 × 800 en volgt exact de pixelindeling hieronder.
De PLC doet alle logica; de HMI toont alleen tags. Er zit een kraansimulatie in,
zodat je het geheel met PLCSIM kunt testen zonder echte installatie.

```
plc/
  01_Types.udt      typeVak, typeKraan, typeBediening
  02_Magazijn.db    globale DB "Magazijn"
  03_Blokken.scl    FC_Magazijn, FC_InitTest, FB_KraanSim
  04_Instanties.db  instance-DB's iKraanSim1/2
  05_Cyclus.scl     FC_Cyclus (roept alles aan)
```

Werkt op S7-1500 en S7-1200 (geen LInt/LReal gebruikt).

---

## 1. PLC-code importeren

1. Projectboom → PLC → **Externe bronbestanden** → *Nieuw extern bestand toevoegen*.
   Voeg de vijf bestanden uit `plc/` toe.
2. Rechtsklik op elk bestand → **Blokken genereren uit bron**, in volgorde 01 → 05.
3. Open **Main [OB1]** en roep de cyclus aan:
   - SCL: `"FC_Cyclus"();`
   - LAD/FBD: sleep `FC_Cyclus` in een netwerk.
4. Compileren, laden in PLCSIM.

### Testen in PLCSIM
Zet in een watch-tabel:

| Tag | Actie |
|---|---|
| `"Magazijn".Bediening.InitTest` | op TRUE → testvulling, vak 137 in storing |
| `"Magazijn".Bediening.Start` | op TRUE → automatisch bedrijf, kranen gaan rijden |
| `"Magazijn".Kraan[1].X / .Y / .Status` | bekijk de kraan |
| `"Magazijn".BezettingPct[1..2]` | bezetting per gang |
| `"Magazijn".Bediening.Reset` | op TRUE → vak 137 vrijgeven, alarm weg |

---

## 2. Datamodel

| Veld | Betekenis |
|---|---|
| `Vak[s, i]` | s = stelling 1..4, i = 1..54. **Vaknummer = s·100 + i** |
| `i` | `(Y − 1)·9 + X`, met X = kolom 1..9, Y = laag 1..6 |
| `Vak[].Weergave` | **kleurcode voor de HMI**: 0 leeg, 1 Base blauw, 2 Lid blauw, 3 Base groen, 4 Lid groen, 9 storing |
| `Kraan[g]` | g = 1 gang blauw (K1), 2 gang groen (K2). `Stelling` 0 = links, 1 = rechts |
| `AlarmWoord` bit 0 | W-104: vak met onbekend product |

Doordat de PLC de kleurcode `Weergave` berekent, heeft de HMI per vak maar
één eenvoudige bereiklijst nodig. Dat werkt op Unified en Comfort.

---

## 3. HMI-scherm (1280 × 800, bv. MTP1200 Unified)

### Indeling
| Element | X | Y | B × H |
|---|---|---|---|
| Kopregel | 0 | 0 | 1280 × 40 |
| Alarmregel | 0 | 40 | 1280 × 30 |
| Paneel gang blauw | 10 | 80 | 1000 × 300 |
| Paneel gang groen | 10 | 390 | 1000 × 300 |
| Zijbalk (KPI, bediening, legenda) | 1020 | 80 | 250 × 610 |
| Functietoetsen F1–F6 | 0 | 750 | 1280 × 50 |

### Raster van de stellingen
Vak 50 × 34 px, steek **52 × 36** px. Een stelling is 9 × 6 vakken = 468 × 216 px.

| Stelling | Oorsprong X0, Y0 |
|---|---|
| 1 Blauw links (101–154) | 30, 130 |
| 2 Blauw rechts (201–254) | 518, 130 |
| 3 Groen links (301–354) | 30, 440 |
| 4 Groen rechts (401–454) | 518, 440 |

Positie vak (X, Y): **Left = X0 + (9 − X)·52**, **Top = Y0 + (6 − Y)·36**
(kolom 9 staat links en laag 6 bovenaan, net als in het ontwerp).

### Faceplate "fpVak"
1. Bibliotheek → Typen → **Nieuw faceplate-type** `fpVak`, 50 × 34 px.
2. Interface: `Vak` (HMI-datatype van `typeVak`) en `Nummer` (Int).
3. Inhoud: rechthoek + tekstveld met `Nummer`.
4. Rechthoek **Achtergrondkleur** dynamiseren op `Vak.Weergave` (bereiklijst):

   | Waarde | Achtergrond (RGB) | Tekst (RGB) |
   |---|---|---|
   | 0 | 230,228,220 | 136,135,128 |
   | 1 | 24,95,165 | 255,255,255 |
   | 2 | 133,183,235 | 4,44,83 |
   | 3 | 59,109,17 | 255,255,255 |
   | 4 | 151,196,89 | 4,44,83 |
   | 9 | 226,75,74 | 255,255,255 |

5. **Randkleur / randbreedte** op `Vak.Gereserveerd`: FALSE → 180,178,169 / 1 px,
   TRUE → 239,159,39 / 2 px.
6. Plaats 216 instanties en koppel ze aan `"Magazijn".Vak[s, i]` met
   `Nummer = s·100 + i`. Tip: maak één stelling, kopieer die en verander de koppeling,
   of genereer ze met TIA Openness.

> **Comfort-paneel:** gebruik dezelfde faceplate en zet de kleuren onder
> *Animaties → Weergave* met tag `Vak.Weergave`, type *Bereik*.

### Stapelkraan (per gang)
Twee objecten per stelling: een **mast** (rechthoek 4 × 224 px, kleur 186,117,23)
en een **slede** (rechthoek 54 × 38 px, rand 186,117,23, transparant).

| Eigenschap | Formule (gang blauw, stelling links; X0 = 30, Y0 = 130) |
|---|---|
| Mast Left | `X0 + (9 − X)·52 + 23` |
| Mast Top | `Y0 − 4` |
| Slede Left | `X0 + (9 − X)·52 − 2` |
| Slede Top | `Y0 + (6 − Y)·36 − 2` |
| Visible (linker stelling) | `Stelling = 0` |
| Visible (rechter stelling) | `Stelling = 1` |

**Unified**: dynamiseer `Left` met een script, trigger op tagwijziging.
Vervang de tagnaam door de HMI-tag die naar `"Magazijn".Kraan[1].X` wijst:

```js
export function Left_Trigger(item) {
  const x = Tags("Magazijn_Kraan{1}_X").Read();
  return 30 + (9 - x) * 52 + 23;
}
```

```js
export function Visible_Trigger(item) {
  return Tags("Magazijn_Kraan{1}_Stelling").Read() === 0;
}
```

**Comfort**: *Animaties → Horizontale beweging* met tag `Kraan[1].X`,
bereik 1 → rechts (X0 + 8·52), 9 → links (X0). Hetzelfde verticaal met `Kraan[1].Y`:
1 → onder (Y0 + 5·36), 6 → boven (Y0).

Statustekst naast "KRAAN K1": tekstlijst op `Kraan[1].Status`
(0 *Gereed*, 1 *▶ Inslag*, 2 *◀ Uitslag*) plus een IO-veld met `DoelVak`.

### Zijbalk en overige
| Ontwerp | Object | Tag |
|---|---|---|
| BEZETTING Blauw/Groen | Balk (0–100), grens 85 % → rood | `BezettingPct[1]`, `[2]` |
| DOORVOER In/Uit | IO-velden | `InTeller`, `UitTeller` |
| OUDSTE PRODUCT | IO-veld + datum/tijd-veld | `OudsteVak`, `OudsteTijd` |
| START / STOP / RESET / HAND | Knop, gebeurtenis *Indrukken* → **SetBit** | `Bediening.Start` … `.Hand` (PLC zet terug) |
| AUTO-chip | Tekst zichtbaar bij | `Bediening.Auto` |
| Alarmbalk | Alarmregel / Alarm Control (compact) | Bitmelding op `AlarmWoord` bit 0 |
| Klok | Klok- of datum/tijd-veld met systeemtijd | – |
| F1–F6 | Knoppen in een vast sjabloon, *Activeer scherm* | – |

**Melding W-104**: HMI-meldingen → Bitmeldingen → nieuw, ID 104, klasse *Warnings*,
tekst `Vak met onbekend product (Soort 0)`, triggertag `AlarmWoord`, bit 0.

### Kleurenpalet (RGB)
| Rol | RGB |
|---|---|
| Rand / frame | 44,44,42 |
| Kopregel / F-balk | 68,68,65 |
| Schermachtergrond | 211,209,199 |
| Paneel | 241,239,232 |
| Lijnen | 180,178,169 |
| Tekst secundair / toetsen | 95,94,90 |
| START | 59,109,17 |
| STOP | 163,45,45 |
| RESET | 133,79,11 |
| Alarmbalk | 250,238,218 (rand 239,159,39, tekst 99,56,6) |
| Kraan / pallets | 186,117,23 |

---

## 4. Schermen (F1–F6)

Het ontwerp `magazijn-hmi/industrieel.html` bevat alle schermen. Elk scherm is
1280 × 800 met dezelfde kopregel (0–40), alarmregel (40–70) en F-toetsenbalk (750–800).
Maak die drie in een **schermsjabloon** (Unified: *Screen* met *Screen window* voor de inhoud).

| Toets | Scherm | Inhoud |
|---|---|---|
| F1 | Fabriek | 4 KPI-tegels, productiestroom (lijnen → wikkelaar → scanner → verdeler → invoer → magazijn → uitvoer → docks), productie per uur, dienst, lijnsnelheid |
| F2 | Magazijn | Stellingen, kranen, bezetting, bediening (zie hoofdstuk 3) |
| F3 | Orders | Ordertabel met voortgang en status, knoppen Nieuw/Start/Pauze/Annuleren, orderdetail met FIFO-picklijst |
| F4 | Kraan | Tabs K1/K2, zijaanzicht van de actieve stelling met mast/slede/vork, rijas/hefas in mm, status, jog-knoppen, onderhoudsteller |
| F5 | Alarmen | Tabs Actief / Historie / Bedieningslog, filter per klasse, Bevestigen / Alles bevestigen |
| F6 | Diagnose | Tabs Systeem (Profinet-overzicht, CPU-belasting, cyclustijd), Gebruikers, Rechten |

**TIA-objecten per scherm**
- F1: mach-blokken = rechthoek + tekst + cirkel (LED, kleur via bereiklijst op een statustag). Lijnen = *Polyline*; stroom zichtbaar via knipperen of kleur op `Bediening.Auto`. Productie per uur = *Trend control* of 8 balken.
- F3: *Parameter set control* of een eigen tabel met faceplates; ordergegevens komen uit een DB `Orders : Array[1..20] of typeOrder`.
- F4: zelfde formules als hoofdstuk 3 met vak 66 × 60 px, oorsprong (60, 190). Jog-knoppen zetten bits `Kraan[n].JogXPlus` enz.; alleen zichtbaar/bedienbaar in HAND.
- F5: **Alarm control** (Unified) met filters op alarmklasse. Bedieningslog = **Audit trail** (Unified, optie) of een eigen tekstlog.
- F6: *System diagnostics control* voor het Profinet-overzicht; gebruikers via *User management control*.

## 5. Gebruikers en rechten

| Rol | Rechten (cumulatief) |
|---|---|
| Gast (niet aangemeld) | Alleen bekijken |
| Operator | Start/stop, meldingen bevestigen, orders aanmaken/starten/pauzeren |
| Technicus | + handbedrijf en kraan verfahren, storing resetten, order annuleren, onderhoudsteller resetten |
| Beheerder | + gebruikers beheren |

**Instellen in TIA V20**
- **Unified**: projectboom → *Security settings* → *Users and roles*. Maak functierechten
  (bv. `Bedienen`, `Alarm`, `Orders`, `Hand`, `Reset`, `Annuleren`, `Service`, `Gebruikers`)
  en de rollen Operator / Technicus / Beheerder. Wijs per knop onder *Security → Authorization* het
  functierecht toe. Automatisch afmelden: *Runtime settings → Security → Logoff time* = 5 min.
- **Comfort**: *Gebruikersbeheer* → gebruikersgroepen en autorisaties met dezelfde namen;
  per knop *Eigenschappen → Beveiliging → Autorisatie*. Afmeldtijd per gebruiker = 5 min.
- Knop zonder recht: in TIA verschijnt bij aantikken automatisch het aanmelddialoog,
  net als in het ontwerp.
- Gebruikersnaam in de kopregel: systeemtag *Current user* (Unified) / *@CurrentUser* (Comfort).
