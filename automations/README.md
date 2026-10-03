# Automatiseringen

## PC aanzetten via NFC tag (`pc_aan_via_nfc_lamp.yaml`)

Scan een NFC tag → lamp knippert 2× → jouw PC gaat aan.

### Stap-voor-stap installatie

#### 1. NFC Tag ID ophalen
1. Open **Home Assistant app** op je telefoon
2. Ga naar **Instellingen → NFC Tags**
3. Tik **Tag toevoegen** → scan je NFC tag
4. Kopieer het **Tag ID** (ziet eruit als `a1b2c3d4`)

#### 2. Lamp entity ID vinden
1. Ga naar **Instellingen → Apparaten & Diensten → Entiteiten**
2. Zoek jouw lamp op
3. Kopieer de **Entity ID** (bijv. `light.woonkamer_lamp`)

#### 3. Automatisering toevoegen
1. Ga naar **Instellingen → Automatiseringen & Scènes**
2. Klik **Automatisering toevoegen** (rechtsonder +)
3. Kies **Automatisering maken**
4. Klik de **⋮ menu** rechtsboven → **YAML bewerken**
5. Plak de inhoud van `pc_aan_via_nfc_lamp.yaml`
6. Vervang:
   - `JOUW_NFC_TAG_ID` → jouw gekopieerde Tag ID
   - `light.jouw_lamp` → jouw lamp entity ID
   - Verwijder de `notify` actie als je geen melding wil
7. Klik **Opslaan**

#### 4. NFC tag schrijven (optioneel voor snellere werking)
- In de HA app: ga naar de NFC tag → **Acties bewerken**
- Voeg toe: **Navigeer naar loophole** of laat leeg (tag ID volstaat)

### Testen
Tik je telefoon op de NFC tag → lamp knippert 2× → PC gaat aan!
