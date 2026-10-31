# MES-bridge (laptop ↔ PLC)

Klein servertje dat op de MES-laptop draait. Het MES stuurt orders als JSON naar de bridge;
de bridge zet ze via **OPC UA** in DB `"Mes"` van de PLC en leest status en voortgang terug.

```
MES ──HTTP/JSON──▶ mes_bridge.py (laptop, poort 8080) ──OPC UA──▶ PLC  DB "Mes"
                                                    ◀── status / voortgang
```

## Installeren en starten

```bash
python -m venv venv
venv\Scripts\activate            # Windows   (Linux: source venv/bin/activate)
pip install -r requirements.txt

python mes_bridge.py --plc opc.tcp://192.168.0.1:4840
```

Open daarna `http://localhost:8080` voor de testpagina (orderlijst + formulier nieuwe order).

| Optie | Standaard | |
|---|---|---|
| `--plc` | `opc.tcp://192.168.0.1:4840` | OPC UA-adres van de PLC |
| `--gebruiker` / `--wachtwoord` | – | als de PLC OPC UA-gebruikers gebruikt |
| `--host` | `0.0.0.0` | gebruik `127.0.0.1` als het MES op dezelfde laptop draait |
| `--poort` | `8080` | HTTP-poort |

### Testen zonder PLC

```bash
python plc_sim.py                                  # simuleert DB "Mes" op opc.tcp://localhost:4840
python mes_bridge.py --plc opc.tcp://localhost:4840
```

De simulator voert dezelfde logica uit als `FB_Mes` en slaat elke 3 s een pallet uit per actieve order.

## API voor het MES

**Nieuwe order**
```bash
curl -X POST http://laptop:8080/api/orders -H "Content-Type: application/json" \
     -d '{"nr":"ORD-5001","klant":"Klant Noord","soort":1,"aantal":12,"dock":1,"prio":2}'
```

| Veld | Type | Waarden |
|---|---|---|
| `nr` | tekst, max 20 | uniek ordernummer |
| `klant` | tekst, max 30 | optioneel |
| `soort` | getal | 1 Base blauw, 2 Lid blauw, 11 Base groen, 12 Lid groen |
| `aantal` | getal | 1–999 pallets |
| `dock` | getal | 1 of 2 (standaard: blauw → 1, groen → 2) |
| `prio` | getal | 1 hoog, 2 normaal (standaard), 3 laag |

Antwoorden: `201` ok · `409` ordernummer bestaat al · `422` ongeldige gegevens ·
`507` orderlijst in PLC vol · `503` PLC niet bereikbaar. Altijd JSON met `ok` en bij fouten `fout`.

**Orderlijst** — `GET /api/orders`
```json
{"ok": true, "orders": [{"nr": "ORD-5001", "klant": "Klant Noord", "soort": 1, "product": "Base blauw",
  "aantal": 12, "gepickt": 3, "dock": 1, "prio": 2, "status": 2, "statustekst": "bezig", "plaats": 1}]}
```
Status: 1 wachtend · 2 bezig · 3 gepauzeerd · 4 gereed · 5 geannuleerd.

**Annuleren** — `POST /api/orders/ORD-5001/annuleren` → `200` of `404`.

**Status** — `GET /api/status` → verbinding en levenstekens.

## Hoe het werkt in de PLC

1. Bridge schrijft `"Mes".In.Order.*` en zet `"Mes".In.Nieuw = TRUE`.
2. `FB_Mes` controleert de order, zet hem in een vrije plaats van `"Mes".Order[1..20]`
   met status *wachtend*, schrijft `"Mes".In.Resultaat` en zet `In.Nieuw = FALSE`.
3. Per gang start de PLC automatisch de wachtende order met de hoogste prioriteit.
4. De kraan slaat voor de actieve order het oudste pallet van die soort uit (FIFO);
   `FC_OrderPick` telt `Gepickt` op en zet de order op *gereed*.
5. Levensteken: de bridge verhoogt elke seconde `"Mes".MesLevensteken`.
   Staat dat 5 s stil, dan zet de PLC `MesOnline = FALSE` en melding S-501.

## In productie

- De API heeft geen login: draai de bridge alleen op het fabrieksnetwerk, of met
  `--host 127.0.0.1` als het MES op dezelfde laptop staat.
- Zet in de PLC OPC UA-gebruikersauthenticatie aan en start met `--gebruiker` / `--wachtwoord`.
  OPC UA-versleuteling met certificaten zit nog niet in deze bridge.
- Automatisch starten op Windows: Taakplanner → *Bij opstarten* →
  `venv\Scripts\python.exe mes_bridge.py --plc opc.tcp://…`.
