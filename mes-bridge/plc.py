"""OPC UA-koppeling met de S7-PLC (DB "Mes") en synchronisatie met de orderdatabase.

De webserver schrijft orders alleen in de database (status "nieuw"). Deze module
zet ze één voor één in de PLC via de handshake in DB "Mes" en leest status en
voortgang terug. Valt de PLC weg, dan blijven orders veilig in de database staan
en worden ze verstuurd zodra de verbinding terug is.
"""
from __future__ import annotations

import asyncio
import logging

from asyncua import Client, ua

NS_URI = "http://www.siemens.com/simatic-s7-opcua"
AANTAL_ORDERS = 20
VELDEN = {
    "Nr": ua.VariantType.String,
    "Klant": ua.VariantType.String,
    "Soort": ua.VariantType.Int16,
    "Aantal": ua.VariantType.Int16,
    "Gepickt": ua.VariantType.Int16,
    "Dock": ua.VariantType.Int16,
    "Prio": ua.VariantType.Int16,
    "Status": ua.VariantType.Int16,
}
PLC_STATUS = {1: "wachtend", 2: "bezig", 3: "gepauzeerd", 4: "gereed", 5: "geannuleerd"}
PRODUCT = {1: "Base blauw", 2: "Lid blauw", 11: "Base groen", 12: "Lid groen"}
RESULTAAT = {
    1: "ok",
    -1: "geen vrije plaats in de PLC-orderlijst",
    -2: "ordernummer bestaat al in de PLC",
    -3: "ongeldige ordergegevens",
    -4: "order niet gevonden of niet meer actief",
}

log = logging.getLogger("plc")


class PlcFout(Exception):
    pass


def order_pad(i: int, veld: str) -> str:
    return f'"Mes"."Order"[{i}]."{veld}"'


class Plc:
    def __init__(self, url: str, gebruiker: str | None = None, wachtwoord: str | None = None):
        self.url = url
        self.gebruiker = gebruiker
        self.wachtwoord = wachtwoord
        self.client: Client | None = None
        self.ns: int | None = None
        self.online = False
        self.mes_levensteken = 0
        self.plc_levensteken: int | None = None
        self._handshake = asyncio.Lock()

    def node(self, naam: str):
        if not self.online or self.client is None:
            raise PlcFout("geen verbinding met de PLC")
        return self.client.get_node(f"ns={self.ns};s={naam}")

    async def lees(self, naam: str):
        return await self.node(naam).read_value()

    async def schrijf(self, naam: str, waarde, typ: ua.VariantType):
        # Zonder tijdstempels: S7-PLC's weigeren writes met SourceTimestamp.
        await self.node(naam).write_value(ua.DataValue(ua.Variant(waarde, typ)))

    async def verbind_lus(self):
        """Houdt de verbinding in stand en verhoogt elke seconde het levensteken."""
        while True:
            try:
                self.client = Client(self.url, timeout=4)
                if self.gebruiker:
                    self.client.set_user(self.gebruiker)
                    self.client.set_password(self.wachtwoord or "")
                await self.client.connect()
                self.ns = await self.client.get_namespace_index(NS_URI)
                self.online = True
                log.info("verbonden met %s", self.url)
                while True:
                    self.mes_levensteken = (self.mes_levensteken + 1) % 30000
                    await self.schrijf('"Mes"."MesLevensteken"', self.mes_levensteken, ua.VariantType.Int16)
                    self.plc_levensteken = await self.lees('"Mes"."PlcLevensteken"')
                    await asyncio.sleep(1)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if self.online:
                    log.warning("verbinding met PLC verbroken: %s", e)
                self.online = False
                try:
                    await self.client.disconnect()
                except Exception:
                    pass
                await asyncio.sleep(3)

    async def _wacht_bit_laag(self, naam: str, timeout: float) -> bool:
        lus = asyncio.get_running_loop()
        eind = lus.time() + timeout
        while lus.time() < eind:
            if not await self.lees(naam):
                return True
            await asyncio.sleep(0.1)
        return False

    async def nieuwe_order(self, o: dict) -> int:
        async with self._handshake:
            if not await self._wacht_bit_laag('"Mes"."In"."Nieuw"', 3):
                raise PlcFout("PLC verwerkt de vorige order niet (In.Nieuw blijft hoog)")
            for veld in ("Nr", "Klant", "Soort", "Aantal", "Dock", "Prio"):
                await self.schrijf(f'"Mes"."In"."Order"."{veld}"', o[veld.lower()], VELDEN[veld])
            await self.schrijf('"Mes"."In"."Nieuw"', True, ua.VariantType.Boolean)
            if not await self._wacht_bit_laag('"Mes"."In"."Nieuw"', 3):
                raise PlcFout("geen antwoord van de PLC binnen 3 s")
            return await self.lees('"Mes"."In"."Resultaat"')

    async def annuleer(self, nr: str) -> int:
        async with self._handshake:
            if not await self._wacht_bit_laag('"Mes"."Annuleer"."Nieuw"', 3):
                raise PlcFout("PLC verwerkt de vorige annulering niet")
            await self.schrijf('"Mes"."Annuleer"."Nr"', nr, ua.VariantType.String)
            await self.schrijf('"Mes"."Annuleer"."Nieuw"', True, ua.VariantType.Boolean)
            if not await self._wacht_bit_laag('"Mes"."Annuleer"."Nieuw"', 3):
                raise PlcFout("geen antwoord van de PLC binnen 3 s")
            return await self.lees('"Mes"."Annuleer"."Resultaat"')

    async def orders(self) -> list[dict]:
        namen = [order_pad(i, v) for i in range(1, AANTAL_ORDERS + 1) for v in VELDEN]
        waarden = await self.client.read_values([self.node(n) for n in namen])
        lijst = []
        for i in range(AANTAL_ORDERS):
            rij = dict(zip([v.lower() for v in VELDEN], waarden[i * len(VELDEN):(i + 1) * len(VELDEN)]))
            if rij["status"] != 0:
                lijst.append(rij)
        return lijst


async def synchroniseer(plc: Plc, db) -> None:
    """Eén ronde: nieuwe orders versturen, annuleringen versturen, status terugschrijven."""
    for o in db.orders_met_status("nieuw"):
        r = await plc.nieuwe_order(o)
        if r in (1, -2):  # -2: stond er al in (bv. na herstart van de server)
            db.zet_status(o["nr"], "in_plc")
            db.audit(None, "plc", f"order {o['nr']} in PLC gezet")
        elif r == -1:
            break  # orderlijst in de PLC is vol: later opnieuw proberen
        else:
            db.zet_status(o["nr"], "geweigerd", RESULTAAT.get(r, f"PLC-code {r}"))
            db.audit(None, "plc", f"order {o['nr']} geweigerd door PLC: {RESULTAAT.get(r, r)}")

    for o in db.orders_met_status("annuleren"):
        r = await plc.annuleer(o["nr"])
        if r == 1:
            db.zet_status(o["nr"], "geannuleerd")
            db.audit(None, "plc", f"order {o['nr']} geannuleerd in PLC")
        else:  # al gereed of onbekend in de PLC: de status uit de PLC wint
            db.zet_status(o["nr"], "in_plc", "annuleren niet meer mogelijk")

    for p in await plc.orders():
        db.update_vanuit_plc(p["nr"], PLC_STATUS.get(p["status"], "in_plc"), p["gepickt"])


async def sync_lus(plc: Plc, db) -> None:
    while True:
        await asyncio.sleep(1)
        if not plc.online:
            continue
        try:
            await synchroniseer(plc, db)
        except PlcFout as e:
            log.warning("synchronisatie: %s", e)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("synchronisatie mislukt")
