# Magazijn-server — bestel-app, MES-API en PLC-koppeling

Een mini-server (laptop, mini-pc of Raspberry Pi) waarop klanten en planners orders plaatsen
via een app in de browser. De server bewaart de orders en zet ze via OPC UA in de PLC; status en
voortgang komen live terug in de app.

```
telefoon / laptop ──HTTPS──▶ server.py ──▶ SQLite (magazijn.db) ──▶ plc.py ──OPC UA──▶ PLC ──▶ Factory I/O
MES-systeem      ──HTTPS + API-sleutel──▶ ┘
```

| Bestand | Wat |
|---|---|
| `server.py` | webserver, login, rollen, API, opstartcommando's |
| `plc.py` | OPC UA-koppeling met DB `"Mes"` en synchronisatie met de database |
| `db.py` | SQLite: gebruikers, sessies, API-sleutels, orders, logboek |
| `beveiliging.py` | wachtwoord-hashing (scrypt), tokens, afremmen van inlogpogingen |
| `static/` | de app (HTML/CSS/JS, installeerbaar op het beginscherm) |
| `plc_sim.py` | PLC-simulator voor testen zonder PLC |
| `test_server.py` | test van de hele keten inclusief beveiliging (32 controles) |

## Starten

```bash
python -m venv venv
venv\Scripts\activate                 # Windows   (Linux/Mac: source venv/bin/activate)
pip install -r requirements.txt

python server.py gebruiker --naam beheer --rol beheerder        # vraagt om een wachtwoord
python server.py start --plc opc.tcp://192.168.0.1:4840
```

Open `http://127.0.0.1:8443` op dezelfde computer. Standaard luistert de server **alleen lokaal**.

### Op het netwerk (telefoon) — alleen met HTTPS

```bash
openssl req -x509 -newkey rsa:2048 -nodes -keyout key.pem -out cert.pem -days 825 \
  -subj "/CN=magazijn.local" -addext "subjectAltName=DNS:magazijn.local,IP:192.168.0.50"

python server.py start --host 0.0.0.0 --cert cert.pem --key key.pem --plc opc.tcp://192.168.0.1:4840
```

Vervang `192.168.0.50` door het IP-adres van de server. Zonder `--cert/--key` weigert de server
om op het netwerk te starten. Een eigen certificaat geeft op de telefoon eenmalig een waarschuwing;
installeer `cert.pem` op de telefoon als vertrouwd certificaat, of gebruik
[Tailscale](https://tailscale.com) met `tailscale cert` voor een geldig certificaat en veilige toegang
van buitenaf **zonder poorten open te zetten in de router**.

Op de telefoon: open de site → menu → *Toevoegen aan beginscherm*. Dan werkt het als een app.

## Rollen

| Rol | Mag |
|---|---|
| **klant** | orders plaatsen (max 50 pallets, max 10 open orders), alleen eigen orders zien en annuleren zolang ze wachten |
| **planner** | alle orders zien, orders plaatsen voor elke klant met prioriteit en eigen ordernummer, ook lopende orders annuleren |
| **beheerder** | alles van planner + gebruikers aanmaken/blokkeren + logboek |
| **API-sleutel** (MES) | als planner, via `Authorization: Bearer …` |

```bash
python server.py gebruiker --naam noord --rol klant --klant "Klant Noord"
python server.py api-sleutel --naam mes        # sleutel wordt één keer getoond
```

## Beveiliging — wat zit erin en waarom

| Maatregel | Beschermt tegen |
|---|---|
| Wachtwoorden met **scrypt** + salt, minimaal 10 tekens | uitlekken van de database |
| Sessietoken in cookie **HttpOnly + SameSite=Strict + Secure**, alleen hash in de database | stelen van sessies via scripts of databaselek |
| Sessie verloopt na 30 min inactiviteit en na 8 uur | vergeten uit te loggen |
| **CSRF-token** + Origin-controle bij elke wijziging | een andere website die namens jou een order plaatst |
| Account 15 min geblokkeerd na 5 foute pogingen, max 20 pogingen per IP per 15 min, gelijke foutmelding en reactietijd | wachtwoorden raden, namen uitproberen |
| Rollen worden **op de server** gecontroleerd; klant kan klantnaam/prio niet zelf kiezen | knoeien met verzoeken |
| Strikte **Content-Security-Policy**, geen inline scripts, alle tekst via `textContent` | XSS (scripts in ordergegevens) |
| Invoercontrole op elk veld, max 16 kB per verzoek | rare of te grote invoer |
| Alleen HTTPS op het netwerk, HSTS | afluisteren van wachtwoorden |
| API-sleutels alleen als hash opgeslagen | uitlekken van de database |
| Logboek van inloggen, orders en beheer | achteraf nagaan wie wat deed |
| Orders eerst in de database, daarna naar de PLC | orderverlies als de PLC even weg is |

Nog **niet** in deze versie: tweestapsverificatie, OPC UA-certificaten (gebruik minstens een
OPC UA-gebruiker met `--plc-gebruiker`/`--plc-wachtwoord`). Zet de server nooit direct open naar
internet via port forwarding.

## API voor het MES

```bash
curl -X POST https://server:8443/api/orders -H "Authorization: Bearer mz_..." \
     -H "Content-Type: application/json" \
     -d '{"nr":"ORD-5001","klant":"Klant Noord","soort":1,"aantal":12,"prio":2}'
```

| Methode | Pad | |
|---|---|---|
| POST | `/api/orders` | `soort` 1/2/11/12, `aantal`; planner/MES ook `klant`, `prio` 1–3, `nr` |
| GET | `/api/orders` | lijst (klant: alleen eigen) |
| POST | `/api/orders/{nr}/annuleren` | |
| GET | `/api/status` | PLC-verbinding, levenstekens |

Statussen: `nieuw` (in wachtrij, nog niet in PLC) → `in_plc` → `wachtend` → `bezig` → `gereed`,
of `gepauzeerd`, `annuleren` → `geannuleerd`, `geweigerd`.

## Testen

```bash
python test_server.py                  # hele keten + beveiliging, zonder PLC
python plc_sim.py                      # losse simulator op opc.tcp://localhost:4840
python server.py start --plc opc.tcp://localhost:4840
```
