"""PLC-simulator voor het testen van de MES-bridge zonder echte PLC.

Start een OPC UA-server met dezelfde nodes als DB "Mes" op een S7-1500/1200
(namespace http://www.siemens.com/simatic-s7-opcua) en voert dezelfde logica uit
als FB_Mes en FB_KraanSim in tia-v20/plc/03_Blokken.scl:
handshake nieuwe order / annuleren, levensteken, MesOnline, automatisch starten
van de volgende order per gang, en elke 3 s een uitgeslagen pallet per actieve gang.

    python plc_sim.py               # opc.tcp://0.0.0.0:4840
    python mes_bridge.py --plc opc.tcp://localhost:4840
"""
from __future__ import annotations

import argparse
import asyncio
import logging

from asyncua import Server, ua

from mes_bridge import AANTAL_ORDERS, NS_URI, VELDEN

log = logging.getLogger("plc-sim")

LOS = {
    '"Mes"."In"."Nieuw"': (False, ua.VariantType.Boolean),
    '"Mes"."In"."Resultaat"': (0, ua.VariantType.Int16),
    '"Mes"."Annuleer"."Nieuw"': (False, ua.VariantType.Boolean),
    '"Mes"."Annuleer"."Nr"': ("", ua.VariantType.String),
    '"Mes"."Annuleer"."Resultaat"': (0, ua.VariantType.Int16),
    '"Mes"."MesLevensteken"': (0, ua.VariantType.Int16),
    '"Mes"."PlcLevensteken"': (0, ua.VariantType.Int16),
    '"Mes"."MesOnline"': (False, ua.VariantType.Boolean),
}
LEEG = {ua.VariantType.String: "", ua.VariantType.Int16: 0}


class Sim:
    def __init__(self, server: Server, ns: int):
        self.server, self.ns, self.n = server, ns, {}

    async def maak(self):
        obj = await self.server.nodes.objects.add_object(self.ns, "Mes")
        namen = dict(LOS)
        for veld, typ in VELDEN.items():
            if veld not in ("Gepickt", "Status"):
                namen[f'"Mes"."In"."Order"."{veld}"'] = (LEEG[typ], typ)
            for i in range(1, AANTAL_ORDERS + 1):
                namen[f'"Mes"."Order"[{i}]."{veld}"'] = (LEEG[typ], typ)
        for naam, (w, typ) in namen.items():
            v = await obj.add_variable(ua.NodeId(naam, self.ns), naam, ua.Variant(w, typ))
            await v.set_writable()
            self.n[naam] = (v, typ)

    async def r(self, naam):
        return await self.n[naam][0].read_value()

    async def w(self, naam, waarde):
        v, typ = self.n[naam]
        await v.write_value(ua.Variant(waarde, typ))

    async def order(self, i):
        return {veld: await self.r(f'"Mes"."Order"[{i}]."{veld}"') for veld in VELDEN}

    async def lus(self):
        tik, vorig, stil, pick = 0, None, 0.0, 0.0
        while True:
            await asyncio.sleep(0.1)
            tik += 1
            # levensteken PLC -> MES, bewaking MES -> PLC (5 s)
            if tik % 10 == 0:
                await self.w('"Mes"."PlcLevensteken"', (await self.r('"Mes"."PlcLevensteken"') + 1) % 30000)
            ls = await self.r('"Mes"."MesLevensteken"')
            stil = 0 if ls != vorig else stil + 0.1
            vorig = ls
            await self.w('"Mes"."MesOnline"', stil < 5)

            orders = [await self.order(i) for i in range(1, AANTAL_ORDERS + 1)]

            if await self.r('"Mes"."In"."Nieuw"'):
                nieuw = {v: await self.r(f'"Mes"."In"."Order"."{v}"') for v in VELDEN if v not in ("Gepickt", "Status")}
                dubbel = any(o["Status"] != 0 and o["Nr"] == nieuw["Nr"] for o in orders)
                vrij = next((i for i, o in enumerate(orders, 1) if o["Status"] == 0), 0) or \
                    next((i for i, o in enumerate(orders, 1) if o["Status"] >= 4), 0)
                if nieuw["Soort"] not in (1, 2, 11, 12) or nieuw["Aantal"] <= 0 or not nieuw["Nr"]:
                    res = -3
                elif dubbel:
                    res = -2
                elif not vrij:
                    res = -1
                else:
                    for v, w in nieuw.items():
                        await self.w(f'"Mes"."Order"[{vrij}]."{v}"', w)
                    await self.w(f'"Mes"."Order"[{vrij}]."Gepickt"', 0)
                    await self.w(f'"Mes"."Order"[{vrij}]."Status"', 1)
                    if nieuw["Prio"] not in (1, 2, 3):
                        await self.w(f'"Mes"."Order"[{vrij}]."Prio"', 2)
                    res = 1
                    log.info("order %s in plaats %s", nieuw["Nr"], vrij)
                await self.w('"Mes"."In"."Resultaat"', res)
                await self.w('"Mes"."In"."Nieuw"', False)

            if await self.r('"Mes"."Annuleer"."Nieuw"'):
                nr, res = await self.r('"Mes"."Annuleer"."Nr"'), -4
                for i, o in enumerate(orders, 1):
                    if 1 <= o["Status"] <= 3 and o["Nr"] == nr:
                        await self.w(f'"Mes"."Order"[{i}]."Status"', 5)
                        res = 1
                await self.w('"Mes"."Annuleer"."Resultaat"', res)
                await self.w('"Mes"."Annuleer"."Nieuw"', False)

            orders = [await self.order(i) for i in range(1, AANTAL_ORDERS + 1)]
            # per gang de volgende wachtende order starten (laagste Prio eerst)
            for g in (1, 2):
                gang = [(i, o) for i, o in enumerate(orders, 1) if (1 if o["Soort"] < 10 else 2) == g]
                if not any(o["Status"] == 2 for _, o in gang):
                    wacht = [(o["Prio"], i) for i, o in gang if o["Status"] == 1]
                    if wacht:
                        await self.w(f'"Mes"."Order"[{min(wacht)[1]}]."Status"', 2)

            # kraansimulatie: elke 3 s een pallet uitslaan voor de actieve order per gang
            pick += 0.1
            if pick >= 3:
                pick = 0
                for g in (1, 2):
                    for i, o in enumerate(orders, 1):
                        if o["Status"] == 2 and (1 if o["Soort"] < 10 else 2) == g:
                            gp = o["Gepickt"] + 1
                            await self.w(f'"Mes"."Order"[{i}]."Gepickt"', gp)
                            if gp >= o["Aantal"]:
                                await self.w(f'"Mes"."Order"[{i}]."Status"', 4)
                                log.info("order %s gereed", o["Nr"])
                            break


async def main(poort: int):
    server = Server()
    await server.init()
    server.set_endpoint(f"opc.tcp://0.0.0.0:{poort}")
    server.set_server_name("PLC-simulator Magazijn")
    ns = await server.register_namespace(NS_URI)
    sim = Sim(server, ns)
    await sim.maak()
    async with server:
        log.info("PLC-simulator op opc.tcp://0.0.0.0:%s (ns=%s)", poort, ns)
        await sim.lus()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--poort", type=int, default=4840)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("asyncua").setLevel(logging.WARNING)
    asyncio.run(main(ap.parse_args().poort))
