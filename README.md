# Solar Forecast AI voor Home Assistant

Een slimme Home Assistant integratie die met behulp van AI **zonopbrengst** en **zelfverbruik** voorspelt. Gratis te gebruiken, geen API-sleutel nodig.

## Hoe het werkt

### Het AI-model

Het model combineert twee technieken:

**1. Fysisch model (basislaag)**
- Haalt elk uur weerdata op via [Open-Meteo](https://open-meteo.com/) (gratis, geen account nodig)
- Open-Meteo levert de verwachte zonnestraling per uur in W/m² — dit houdt al rekening met bewolking
- Formule: `Vermogen (W) = Straling (W/m²) / 1000 × Piekvermogen (kWp) × 1000 × PR × temperatuurfactor × correctiefactor`
- Temperatuurcorrectie: zonnepanelen verliezen ~0,4% vermogen per °C boven 25°C (NOCT-model)

**2. AI-correctielaag (lerende component)**
- Het model vergelijkt dagelijks de voorspelling met de werkelijke opbrengst (via je productiemeter)
- Berekent een **correctiefactor** via een exponentieel voortschrijdend gemiddelde
- Na ~14 dagen is het model gekalibreerd op jouw specifieke installatie
- Nauwkeurigheid neemt toe naarmate het model meer data verzamelt (zie `AI Correctiefactor` sensor)

**3. Zelfverbruik voorspelling**
- Bouwt een verbruiksprofiel op uit je verbruikssensor (168 datapunten: elk uur per dag)
- Berekent het overlap tussen verwachte opbrengst en verwacht verbruik
- Zonder verbruikssensor: gebruikt een standaard Nederlands huishoudprofiel

### Sensoren

| Sensor | Beschrijving | Eenheid |
|--------|-------------|---------|
| Zonopbrengst Vandaag | Verwachte opbrengst vandaag | kWh |
| Zonopbrengst Morgen | Verwachte opbrengst morgen | kWh |
| Zonopbrengst 7 Dagen | Totale verwachte opbrengst komende 7 dagen | kWh |
| Verwacht Zonnestroom Nu | Verwacht vermogen dit uur | W |
| Verwacht Piekvermogen Vandaag | Maximaal verwacht vermogen vandaag | W |
| Verwacht Piekvermogen Tijdstip | Tijdstip van het piekvermogen | HH:MM |
| Verwacht Piekvermogen Morgen | Maximaal verwacht vermogen morgen | W |
| Verwacht Zelfverbruik Vandaag | % opbrengst die direct verbruikt wordt | % |
| Verwachte Zelfredzaamheid Vandaag | % verbruik gedekt door zonne-energie | % |
| AI Correctiefactor | Geleerde correctiefactor (1.0 = perfect) | - |

### Extra attributen

De sensors bevatten rijke extra data:
- **Zonopbrengst Vandaag**: uurlijkse vermogensverwachting, bewolkingspercentage, piekdata
- **Zonopbrengst 7 Dagen**: dagelijks overzicht per datum
- **AI Correctiefactor**: aantal leerdagen, vertrouwensscore, laatste update

## Installatie

### Via HACS (aanbevolen)

1. Ga naar HACS → Integraties → ⋮ → Aangepaste repositories
2. Voeg toe: `kali-linux2008/ai-model-zon-opbrengst-app-home-assistant`
3. Categorie: `Integratie`
4. Klik op installeren
5. Herstart Home Assistant

### Handmatig

1. Download de map `custom_components/solar_forecast_ai`
2. Kopieer naar `/config/custom_components/solar_forecast_ai/`
3. Herstart Home Assistant

## Configuratie

1. Ga naar **Instellingen → Apparaten & Diensten → Integratie toevoegen**
2. Zoek naar "Solar Forecast AI"
3. Vul in:

| Instelling | Uitleg | Voorbeeld |
|-----------|--------|-----------|
| **Piekvermogen (kWp)** | Totaal vermogen van alle panelen | `6.5` |
| **Prestatieratio (PR)** | Systeem rendement (0.50-1.00) | `0.80` |
| **Voorspellingsperiode** | Aantal dagen (1-7) | `3` |
| **Opbrengst sensor** | Sensor met dagopbrengst in kWh | *(optioneel)* |
| **Verbruik sensor** | Sensor met actueel verbruik | *(optioneel)* |

> **Locatie**: automatisch overgenomen uit Home Assistant — stel in via *Instellingen → Systeem → Algemeen*

### Prestatieratio instellen

De PR is een getal tussen 0.50 en 1.00 dat systeemverliezen samenvat:

| Systeem | Typische PR |
|---------|-------------|
| Eenvoudig systeem, ouder | 0.72 - 0.76 |
| Standaard systeem | 0.78 - 0.82 |
| Modern micro-omvormer systeem | 0.83 - 0.87 |
| Optimized/SolarEdge systeem | 0.85 - 0.90 |

Tip: begin met 0.80 en laat het AI-model zichzelf kalibreren.

## Automatiseringen

### Wasmachine plannen op zonnige uren

```yaml
automation:
  - alias: "Wasmachine op zonnig tijdstip"
    trigger:
      - platform: numeric_state
        entity_id: sensor.zonopbrengst_vandaag
        above: 10
    condition:
      - condition: numeric_state
        entity_id: sensor.verwacht_zonnestroom_nu
        above: 2000
    action:
      - service: notify.mobile_app
        data:
          message: "☀️ Goed moment voor de wasmachine! Verwacht {{ states('sensor.verwacht_zonnestroom_nu') }} W zonne-energie."
```

### Melding bij hoge opbrengst morgen

```yaml
automation:
  - alias: "Morgen veel zonne-energie"
    trigger:
      - platform: time
        at: "20:00:00"
    condition:
      - condition: numeric_state
        entity_id: sensor.zonopbrengst_morgen
        above: 15
    action:
      - service: notify.mobile_app
        data:
          message: >
            ☀️ Morgen {{ states('sensor.zonopbrengst_morgen') }} kWh verwacht!
            Piekvermogen om {{ states('sensor.verwacht_piekvermogen_tijdstip') }}.
```

### Dashboard kaart

```yaml
type: glance
title: Zonpanelen Vandaag
entities:
  - entity: sensor.zonopbrengst_vandaag
    name: Vandaag
  - entity: sensor.zonopbrengst_morgen
    name: Morgen
  - entity: sensor.verwacht_zonnestroom_nu
    name: Nu
  - entity: sensor.verwacht_piekvermogen_vandaag
    name: Piek
  - entity: sensor.verwacht_piekvermogen_tijdstip
    name: Tijdstip
  - entity: sensor.verwacht_zelfverbruik_vandaag
    name: Zelfverbruik
```

## AI Model details

### Leerproces

```
Dag 1-3:    Puur fysisch model (geen historische correctie)
Dag 3-14:   Model leert correctiefactor, vertrouwen stijgt geleidelijk
Dag 14+:    Goed gekalibreerd model voor jouw specifieke situatie
```

De `AI Correctiefactor` sensor toont hoe goed het model is gekalibreerd:
- `1.00` = voorspelling klopt perfect met werkelijkheid
- `< 1.00` = model overschatte eerder (bijv. schaduwen die niet in weerdata zaten)
- `> 1.00` = model onderschatte eerder (bijv. reflectie van omgeving)

### Nauwkeurigheid

Typische nauwkeurigheid na voldoende leerdata:
- Zonnige dag: ±5-10%
- Bewolkte dag: ±10-20%
- Overgangsperiode: ±5-15%

### Privacy

Alle data blijft lokaal in Home Assistant. Er worden **geen persoonlijke gegevens** verstuurd naar externe servers. Alleen anonieme weerverzoeken naar Open-Meteo (lat/lon van je locatie).

## Vereisten

- Home Assistant 2023.8.0 of nieuwer
- Internetverbinding (voor Open-Meteo weerdata)
- Locatie ingesteld in Home Assistant

## Bijdragen

Verbeteringen zijn welkom! Open een issue of pull request op GitHub.

## Licentie

MIT License
