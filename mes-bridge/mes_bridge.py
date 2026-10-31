"""MES-bridge: koppelt het MES (REST/JSON) aan de S7-PLC via OPC UA.

Draait op de laptop/server naast het MES.

    MES  --HTTP/JSON-->  mes_bridge.py  --OPC UA-->  PLC (DB "Mes")

- POST /api/orders                 nieuwe order naar de PLC (handshake via "Mes".In.Nieuw)
- POST /api/orders/{nr}/annuleren  order annuleren
- GET  /api/orders                 orderlijst met status en voortgang uit de PLC
- GET  /api/status                 verbinding, levenstekens
- GET  /                           eenvoudige webpagina voor test en bediening

Elke seconde verhoogt de bridge "Mes".MesLevensteken; de PLC zet MesOnline = FALSE
(alarm S-501) als dat 5 s stilstaat.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import pathlib

from aiohttp import web
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
STATUS = {0: "vrij", 1: "wachtend", 2: "bezig", 3: "gepauzeerd", 4: "gereed", 5: "geannuleerd"}
PRODUCT = {1: "Base blauw", 2: "Lid blauw", 11: "Base groen", 12: "Lid groen"}
RESULTAAT = {
    1: "ok",
    -1: "geen vrije plaats in de PLC-orderlijst",
    -2: "ordernummer bestaat al",
    -3: "ongeldige ordergegevens",
    -4: "order niet gevonden of niet meer actief",
}

log = logging.getLogger("mes-bridge")


def order_pad(i: int, veld: str) -> str:
    return f'"Mes"."Order"[{i}]."{veld}"'


class PlcFout(Exception):
    pass


class Plc:
    def __init__(self, url: str, gebruiker: str | None, wachtwoord: str | None):
        self.url = url
        self.gebruiker = gebruiker
        self.wachtwoord = wachtwoord
        self.client: Client | None = None
        self.ns: int | None = None
        self.online = False
        self.mes_levensteken = 0
        self.plc_levensteken: int | None = None
        self.mes_online_in_plc: bool | None = None
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
        """Houdt de verbinding in stand en verzorgt het levensteken."""
        while True:
            try:
                self.client = Client(self.url, timeout=4)
                if self.gebruiker:
                    self.client.set_user(self.gebruiker)
                    self.client.set_password(self.wachtwoord or "")
                await self.client.connect()
                self.ns = await self.client.get_namespace_index(NS_URI)
                self.online = True
                log.info("verbonden met %s (ns=%s)", self.url, self.ns)
                while True:
                    self.mes_levensteken = (self.mes_levensteken + 1) % 30000
                    await self.schrijf('"Mes"."MesLevensteken"', self.mes_levensteken, ua.VariantType.Int16)
                    self.plc_levensteken = await self.lees('"Mes"."PlcLevensteken"')
                    self.mes_online_in_plc = await self.lees('"Mes"."MesOnline"')
                    await asyncio.sleep(1)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # verbinding weg, PLC in STOP, netwerk ...
                if self.online:
                    log.warning("verbinding verbroken: %s", e)
                else:
                    log.info("PLC niet bereikbaar (%s), opnieuw over 3 s", e)
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
        nodes = [self.node(n) for n in namen]
        waarden = await self.client.read_values(nodes)
        lijst = []
        for i in range(AANTAL_ORDERS):
            rij = dict(zip([v.lower() for v in VELDEN], waarden[i * len(VELDEN):(i + 1) * len(VELDEN)]))
            if rij["status"] == 0:
                continue
            rij["plaats"] = i + 1
            rij["statustekst"] = STATUS.get(rij["status"], "?")
            rij["product"] = PRODUCT.get(rij["soort"], "?")
            lijst.append(rij)
        return lijst


def controleer(data: dict) -> dict:
    """Valideert een order uit het MES; geeft een genormaliseerde dict terug."""
    try:
        o = {
            "nr": str(data["nr"]).strip(),
            "klant": str(data.get("klant", "")).strip(),
            "soort": int(data["soort"]),
            "aantal": int(data["aantal"]),
            "dock": int(data.get("dock", 1 if int(data["soort"]) < 10 else 2)),
            "prio": int(data.get("prio", 2)),
        }
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError(f"veld ontbreekt of is ongeldig: {e}") from None
    if not o["nr"] or len(o["nr"]) > 20:
        raise ValueError("nr: 1 t/m 20 tekens")
    if len(o["klant"]) > 30:
        raise ValueError("klant: maximaal 30 tekens")
    if o["soort"] not in PRODUCT:
        raise ValueError("soort: 1, 2, 11 of 12")
    if not 1 <= o["aantal"] <= 999:
        raise ValueError("aantal: 1 t/m 999")
    if o["dock"] not in (1, 2):
        raise ValueError("dock: 1 of 2")
    if o["prio"] not in (1, 2, 3):
        raise ValueError("prio: 1 (hoog), 2 (normaal) of 3 (laag)")
    return o


def maak_app(plc: Plc) -> web.Application:
    routes = web.RouteTableDef()

    def fout(status: int, tekst: str):
        return web.json_response({"ok": False, "fout": tekst}, status=status)

    @routes.get("/")
    async def index(_):
        return web.FileResponse(pathlib.Path(__file__).with_name("index.html"))

    @routes.get("/api/status")
    async def status(_):
        return web.json_response({
            "plc_url": plc.url,
            "plc_verbonden": plc.online,
            "mes_levensteken": plc.mes_levensteken,
            "plc_levensteken": plc.plc_levensteken,
            "plc_ziet_mes_online": plc.mes_online_in_plc,
        })

    @routes.get("/api/orders")
    async def orders(_):
        try:
            return web.json_response({"ok": True, "orders": await plc.orders()})
        except PlcFout as e:
            return fout(503, str(e))

    @routes.post("/api/orders")
    async def nieuw(request):
        try:
            data = await request.json()
        except Exception:
            return fout(400, "body moet JSON zijn")
        if not isinstance(data, dict):
            return fout(400, "body moet een JSON-object zijn")
        try:
            o = controleer(data)
        except ValueError as e:
            return fout(422, str(e))
        try:
            r = await plc.nieuwe_order(o)
        except PlcFout as e:
            return fout(503, str(e))
        if r != 1:
            return fout(409 if r == -2 else 422 if r == -3 else 507, RESULTAAT.get(r, f"PLC-code {r}"))
        log.info("order %s naar PLC (%s x %s)", o["nr"], o["aantal"], PRODUCT[o["soort"]])
        return web.json_response({"ok": True, "nr": o["nr"]}, status=201)

    @routes.post("/api/orders/{nr}/annuleren")
    async def annuleren(request):
        nr = request.match_info["nr"]
        try:
            r = await plc.annuleer(nr)
        except PlcFout as e:
            return fout(503, str(e))
        if r != 1:
            return fout(404, RESULTAAT.get(r, f"PLC-code {r}"))
        log.info("order %s geannuleerd", nr)
        return web.json_response({"ok": True, "nr": nr})

    app = web.Application()
    app.add_routes(routes)

    async def start_plc(app):
        app["plc_taak"] = asyncio.create_task(plc.verbind_lus())

    async def stop_plc(app):
        app["plc_taak"].cancel()

    app.on_startup.append(start_plc)
    app.on_cleanup.append(stop_plc)
    return app


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plc", default="opc.tcp://192.168.0.1:4840", help="OPC UA-adres van de PLC")
    ap.add_argument("--gebruiker", help="OPC UA-gebruiker (als de PLC gebruikersauthenticatie gebruikt)")
    ap.add_argument("--wachtwoord")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--poort", type=int, default=8080)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("asyncua").setLevel(logging.WARNING)
    web.run_app(maak_app(Plc(a.plc, a.gebruiker, a.wachtwoord)), host=a.host, port=a.poort)


if __name__ == "__main__":
    main()
