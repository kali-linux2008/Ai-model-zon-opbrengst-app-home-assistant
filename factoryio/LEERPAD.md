# Leerpad: het magazijn in Factory I/O

Doel: stap voor stap een compleet geautomatiseerd magazijn bouwen in **Factory I/O**, bestuurd door
een **S7-PLC in TIA Portal V20 (PLCSIM)**, met een **WinCC-HMI**, een **bestel-app** op je telefoon
en orders via het **MES**. Elk niveau levert iets werkends op en leert je één vak.

```
telefoon (bestel-app) ─▶ mini-server ─OPC UA─▶ PLC (PLCSIM) ─▶ Factory I/O (3D-fabriek)
                                                    ▲
                                       HMI (WinCC Unified, F1–F6)
```

> Tip: houd een **logboek** bij (wat gebouwd, wat ging mis, hoe opgelost) en zet je project in git
> na elk niveau. Dat is precies hoe het in de praktijk gaat bij inbedrijfstelling.

---

## Wat je nodig hebt

| | |
|---|---|
| Factory I/O | **Siemens Edition** of **Ultimate Edition** (nodig voor de S7-PLCSIM-driver) |
| TIA Portal V20 + S7-PLCSIM V20 | Factory I/O ondersteunt PLCSIM V20–21 |
| Template-project | Download het Factory I/O-template voor V20 (S7-1200, S7-1200 G2 of S7-1500). **Zonder template werkt de koppeling niet.** Pak de zip eerst uit. **Verwijder Network 1 in OB1 niet.** |
| Voor de bestel-app (niveau 9) | OPC UA-server in de PLC. Met **PLCSIM Advanced** (S7-1500, instantienaam `factoryio`) werkt OPC UA via het virtuele netwerk. Of gewone PLCSIM V20 OPC UA doorgeeft, test je zelf; zo niet, stap dan over op PLCSIM Advanced. |

**Advies:** begin met **S7-1200** + PLCSIM (simpelst). Bij een S7-1500 moet je in PLCSIM een SimView
maken en de sequence `FactoryIOConnectSequenceForS7PlcSim1500.xlsx` uit de templatemap importeren en
starten.

Koppelen: Factory I/O → *FILE → Driver Configuration* → **Siemens S7-PLCSIM** → *Configuration* →
model kiezen → *Connect*. Groen icoon = verbonden.

---

## Niveau 0 — I/O-lijst en eerste verbinding
**Bouw:** leeg scenario met één band, één sensor, één drukknop met lampje.
**PLC:** PLC-tagtabel met **symbolische namen** (`B_Invoer_Aanwezig`, `M_Band1`, `S_Start`, `H_Start`).
**Leer:** adressering (I/Q), tagtabellen, waarom je nooit met losse adressen programmeert.
**Uitdaging:** maak in Excel een I/O-lijst met kolommen *Tag · Adres · Type · Omschrijving · Getest (ja/nee)* en test elke I/O door in Factory I/O te **forceren** (rechtsklik op een tag).

## Niveau 1 — Transport en sensoren
**Bouw:** invoerband → sensor aan het eind → tweede band.
**PLC:** flankdetectie (`R_TRIG`), timers (`TON`), tellers (`CTU`). Band stopt als het product bij de sensor is; volgende band neemt over.
**Leer:** de PLC-cyclus, waarom een flank nodig is om één keer te tellen.
**Uitdaging:** tel producten per minuut en toon dat op een **Digital Display** in Factory I/O.

## Niveau 2 — Bedienpaneel en veiligheid
**Bouw:** drukknoppen START (groen, met lamp), STOP (rood), RESET (blauw), **noodstop**, en lampen voor een signaalzuil.
**PLC:** modi AUTO / STOP / HAND, noodstop haalt alle uitgangen af, na de noodstop alleen verder na **RESET + START** (herstartbeveiliging). Signaalzuil: groen = auto, geel knipperend = wacht op reset, rood = storing.
**Leer:** waarom STOP en noodstop als *verbreekcontact* (NC) worden aangesloten (draadbreuk = veilig), en het verschil tussen functionele stop en noodstop.
**Uitdaging:** schrijf op welke 3 dingen er mis kunnen gaan als je de noodstop als NO-contact programmeert.

## Niveau 3 — De stapelkraan met de hand
**Bouw:** **Stacker Crane** met aan beide kanten een rek (links = stelling 1, rechts = stelling 2). Configuratie **Numerical**.

| Factory I/O-tag | Richting | Type | |
|---|---|---|---|
| Target Position | PLC → kraan | Int | 1–54 = vak, 0 = blijf staan, >54 = rustpositie 55 |
| Left / Right | PLC → kraan | Bool | vork uit naar links / rechts |
| Lift | PLC → kraan | Bool | platform iets omhoog om een pallet op te tillen |
| Moving X / Moving Z | kraan → PLC | Bool | kraan rijdt / heft |
| Left / Middle / Right Limit | kraan → PLC | Bool | vork staat links / midden / rechts |

**PLC + HMI:** gebruik scherm **F4 Kraan** (jog-knoppen, alleen Technicus in HAND) om een doelpositie te kiezen en de vork te bedienen.
**Leer:** het verschil tussen "commando gegeven" en "beweging klaar" — Moving X/Z worden pas na een paar cycli hoog.
**Uitdaging:** zoek uit welk vaknummer linksonder zit en hoe de nummering loopt. Klopt het met `index = (Y−1)·9 + X` uit `tia-v20/README.md`? Zo niet: schrijf een omreken-FC.

## Niveau 4 — Automatisch inslaan en uitslaan
**PLC:** `FB_StapelKraan` in `tia-v20/plc/06_FactoryIO.scl` — een **stappenketen** (state machine):
`0 rust → 10–14 ophalen → 20–24 afzetten → 30 terug → 0`, met een **watchdog** (20 s per stap → stap 900 = fout).
**HMI:** toon het stapnummer en de stapnaam op F4 (tekstlijst op `Stap`).
**Leer:** stappenketens, interlocks (vork pas uit als de kraan stil staat), timeouts.
**Uitdaging:** teken de stappenketen eerst op papier als **GRAFCET** en vergelijk met de code.

## Niveau 5 — Magazijnbeheer (WMS-light)
**PLC:** vervang `FB_KraanSim` door een eigen `FB_Kraan` die
1. de opdracht kiest zoals `FB_KraanSim` (order actief → oudste pallet van die soort, anders inslag in een leeg vak),
2. de opdracht uitvoert met `FB_StapelKraan` (`Vak = index`, `VakZijde = stelling links/rechts`),
3. pas bij `Gereed` het vak in `"Magazijn".Vak[]` bijwerkt en `FC_OrderPick` aanroept.
**Leer:** scheiding tussen **logica** (welk vak) en **besturing** (hoe de kraan rijdt) — de basis van elk goed PLC-programma.
**Uitdaging:** wat gebeurt er met de vakadministratie als de watchdog afgaat halverwege een uitslag? Bedenk een oplossing (tip: status "in beweging" per vak).

## Niveau 6 — Productherkenning
**Bouw:** **Vision Sensor** (All Numerical) op de invoerband + een afkeurband.
**PLC:** `FC_VisionNaarSoort`: Blue Lid → 2, Blue Base → 1, Green Lid → 12, Green Base → 11, de rest → 0.
Soort 0 = **afkeuren** + melding **W-104 "product onbekend"** (precies de storing uit het HMI-ontwerp).
**Leer:** sensordata vertalen naar procesdata, afkeurstroom ontwerpen.
**Uitdaging:** leg ook grondstof (raw material) op de band en controleer dat die nooit in het magazijn komt.

## Niveau 7 — Twee gangen en sorteren
**Bouw:** verdeler (pop-up wieltjes of draaitafel) die blauw naar **gang blauw (K1)** en groen naar **gang groen (K2)** stuurt, elk met een eigen kraan en twee rekken.
**PLC:** twee instanties van `FB_Kraan`, één `FB_Verdeler`. Productvolgen met een **FIFO-buffer** op de band (array of `FIFO`-instructie).
**Leer:** hergebruik via FB-instanties, productvolging (tracking).
**Uitdaging:** wat als gang blauw vol is? Laat de verdeler wachten en geef een melding.

## Niveau 8 — HMI (WinCC Unified)
Bouw de schermen uit `magazijn-hmi/industrieel.html` na volgens `tia-v20/README.md`:
F1 Fabriek, F2 Magazijn, F3 Orders, F4 Kraan, F5 Alarmen, F6 Diagnose, met **gebruikers en rechten**.
**Leer:** faceplates, dynamisering, alarmklassen, gebruikersbeheer — en ISA-101-stijl (grijs, kleur alleen voor afwijkingen).
**Uitdaging:** laat iemand anders zonder uitleg een order starten op je HMI. Waar twijfelt diegene? Verbeter dat.

## Niveau 9 — Bestel-app en MES
**Server:** `mes-bridge/` (zie de README daar): bestel-app op je telefoon, rollen klant/planner/beheerder, API voor het MES.
**PLC:** OPC UA-server aan, DB `"Mes"`, `FB_Mes` (hoofdstuk 6 van `tia-v20/README.md`).
**Leer:** IT/OT-koppeling, handshakes, levenstekens, waarom orders eerst in een database staan.
**Uitdaging:** trek tijdens het uitslaan de netwerkkabel eruit (stop de server). Wat ziet de operator? Komt alles goed als de server terug is?

## Niveau 10 — Storingen oefenen (het leerzaamste niveau)
Forceer in Factory I/O sensoren en kijk of je programma veilig reageert:

| Storing | Hoe forceren | Verwacht |
|---|---|---|
| Vork blijft hangen | *Middle Limit* forceren op FALSE | watchdog stap 14/24 → kraan in fout, melding, operator ziet stapnummer |
| Kraan rijdt niet | *Moving X* forceren op TRUE | watchdog stap 11/21 |
| Product onbekend | metaal op de band | afkeur + W-104 |
| Sensor defect | invoersensor forceren op TRUE | band mag niet eindeloos draaien → time-out |
| Noodstop | noodstop indrukken tijdens uitslag | alles stil, na reset netjes verder of handmatig afmaken |
| MES weg | server stoppen | S-501, geen nieuwe orders, lopende orders gaan door |

**Leer:** foutafhandeling, herstelprocedures, meldingen die de operator echt helpen.
**Uitdaging:** schrijf per melding een **herstelinstructie** (wat moet de operator/technicus doen) en zet die als infotekst bij de melding in WinCC.

## Niveau 11 — Prestaties
**PLC:** meet de cyclustijd per kraanopdracht (`RUNTIME` of tijdstempels), tel pallets per uur, bereken bezettingsgraad.
**HMI:** trend op F1, productie per uur.
**Uitdaging (gevorderd):** **dubbelspel** — combineer een inslag en een uitslag in één rit (pallet afzetten, op de terugweg een pallet meenemen). Hoeveel sneller wordt het magazijn?

## Niveau 12 — Traceerbaarheid met RFID (optioneel)
Pallets in Factory I/O hebben een RFID-tag (grondstof, lids en bases niet). Zet bij inslag het
**volgnummer en de soort** op de tag met een **RFID-lezer/schrijver** en controleer bij uitslag of de
juiste pallet de kraan verlaat.
**Leer:** track & trace, datakwaliteit, verificatie.

---

## Zo maak je het extra gaaf
- **Camera's:** zet in Factory I/O vaste camera's per gang en wissel ertussen tijdens een demo.
- **Echte bediening:** knoppen, noodstop en signaalzuil in de 3D-fabriek, gekoppeld aan dezelfde PLC-logica als de HMI.
- **Demo-scenario:** bestel op je telefoon → order verschijnt op F3 → kraan rijdt in Factory I/O → pallet komt bij het dock → app springt op *gereed*.
- **Inbedrijfstellingsrapport:** I/O-lijst afgevinkt, storingstests uit niveau 10 met resultaat, screenshots. Dit is wat bedrijven willen zien.

## Bronnen
- Factory I/O — S7-PLCSIM V20–21 instellen: <https://docs.factoryio.com/tutorials/siemens/setting-up-s7-plcsim-v20/>
- Factory I/O — S7-PLCSIM-driver: <https://docs.factoryio.com/manual/drivers/s7-plcsim/>
- Factory I/O — Stacker Crane en andere stations: <https://docs.factoryio.com/manual/parts/stations/>
- Factory I/O — sensoren (Vision Sensor, RFID): <https://docs.factoryio.com/manual/parts/sensors/>
- Factory I/O — bedieningselementen: <https://docs.factoryio.com/manual/parts/operators/>
