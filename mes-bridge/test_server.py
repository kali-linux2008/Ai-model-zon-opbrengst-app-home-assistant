"""Test van de hele keten: app → server → database → PLC-simulator, inclusief beveiliging.

    python test_server.py

Start zelf een PLC-simulator (OPC UA) en de server met een tijdelijke database.
"""
from __future__ import annotations

import asyncio
import os
import tempfile

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from asyncua import Server

import beveiliging as bv
import server as srv
from db import Db
from plc import NS_URI, Plc
from plc_sim import Sim

WW = "Test-wachtwoord-123"
geslaagd = 0


def check(voorwaarde, tekst):
    global geslaagd
    assert voorwaarde, "MISLUKT: " + tekst
    geslaagd += 1
    print("  ok  ", tekst)


async def login(client, naam, ww=WW):
    r = await client.post("/api/login", json={"naam": naam, "wachtwoord": ww})
    return r.status, await r.json()


async def main():
    # --- PLC-simulator ---------------------------------------------------
    sim_server = Server()
    await sim_server.init()
    sim_server.set_endpoint("opc.tcp://127.0.0.1:48410")
    sim = Sim(sim_server, await sim_server.register_namespace(NS_URI))
    await sim.maak()
    await sim_server.start()
    sim_taak = asyncio.create_task(sim.lus())

    # --- server met tijdelijke database --------------------------------------
    pad = os.path.join(tempfile.mkdtemp(), "test.db")
    db = Db(pad)
    for naam, rol, klant in [("beheer", "beheerder", ""), ("planner", "planner", ""),
                             ("noord", "klant", "Klant Noord"), ("zuid", "klant", "Klant Zuid")]:
        db.maak_gebruiker(naam, bv.hash_wachtwoord(WW), rol, klant)
    sleutel = "mz_" + bv.nieuw_token()
    db.maak_api_sleutel("mes", bv.token_hash(sleutel))
    app = srv.maak_app(db, Plc("opc.tcp://127.0.0.1:48410"))

    ts = TestServer(app)
    await ts.start_server()
    async with TestClient(ts) as noord, TestClient(ts) as zuid, TestClient(ts) as beheer, TestClient(ts) as anoniem:
        print("Beveiliging")
        r = await anoniem.get("/api/orders")
        check(r.status == 401, "zonder login geen toegang tot orders")
        r = await anoniem.get("/")
        check("default-src 'self'" in r.headers.get("Content-Security-Policy", "")
              and r.headers.get("X-Frame-Options") == "DENY", "beveiligingsheaders aanwezig")
        s, d = await login(anoniem, "noord", "fout-wachtwoord")
        check(s == 401 and "onjuiste" in d["fout"], "fout wachtwoord geweigerd")
        s, d = await login(anoniem, "bestaat-niet", "x")
        check(s == 401 and "onjuiste" in d["fout"], "onbekende naam krijgt dezelfde melding")
        r = await anoniem.post("/api/orders", headers={"Authorization": "Bearer mz_fout"}, json={})
        check(r.status == 401, "ongeldige API-sleutel geweigerd")

        r = await noord.post("/api/login", json={"naam": "noord", "wachtwoord": WW})
        d = await r.json()
        check(r.status == 200 and d["gebruiker"]["rol"] == "klant", "klant logt in")
        csrf_noord = d["csrf"]
        cookie = r.headers.get("Set-Cookie", "")
        check("HttpOnly" in cookie and "SameSite=Strict" in cookie, "sessiecookie is HttpOnly en SameSite=Strict")
        r = await noord.post("/api/orders", json={"soort": 1, "aantal": 2})
        check(r.status == 403, "order zonder CSRF-token geweigerd")
        r = await noord.post("/api/orders", json={"soort": 1, "aantal": 2},
                             headers={"X-CSRF-Token": csrf_noord, "Origin": "https://kwaadaardig.example"})
        check(r.status == 403, "order vanaf andere website geweigerd")
        h = {"X-CSRF-Token": csrf_noord}
        r = await noord.post("/api/orders", data="kapot", headers=h)
        check(r.status == 400, "kapotte JSON geweigerd")
        r = await noord.post("/api/orders", json={"soort": 7, "aantal": 2}, headers=h)
        check(r.status == 422, "onbekend product geweigerd")
        r = await noord.post("/api/orders", json={"soort": 1, "aantal": 500}, headers=h)
        check(r.status == 422, "klant max 50 pallets")
        r = await noord.get("/api/gebruikers")
        check(r.status == 403, "klant kan geen gebruikers zien")

        print("Orders")
        r = await noord.post("/api/orders", json={"soort": 1, "aantal": 2, "prio": 1, "klant": "Iemand anders"}, headers=h)
        d = await r.json()
        check(r.status == 201 and d["nr"].startswith("W"), f"klant plaatst order {d.get('nr')}")
        nr_noord = d["nr"]
        o = db.order(nr_noord)
        check(o["klant"] == "Klant Noord" and o["prio"] == 2, "klantnaam en prio niet door klant te kiezen")

        s, d = await login(zuid, "zuid")
        r = await zuid.get("/api/orders")
        check(not (await r.json())["orders"], "klant ziet geen orders van een andere klant")
        r = await zuid.post(f"/api/orders/{nr_noord}/annuleren", headers={"X-CSRF-Token": d["csrf"]})
        check(r.status == 404, "klant kan order van een ander niet annuleren")

        r = await anoniem.post("/api/orders", headers={"Authorization": f"Bearer {sleutel}"},
                               json={"nr": "MES-1", "klant": "Klant Oost", "soort": 12, "aantal": 3, "prio": 1})
        check(r.status == 201, "MES plaatst order met API-sleutel")
        r = await anoniem.post("/api/orders", headers={"Authorization": f"Bearer {sleutel}"},
                               json={"nr": "MES-1", "soort": 12, "aantal": 3})
        check(r.status == 409, "dubbel ordernummer geweigerd")

        print("PLC-koppeling (wacht op simulator)")
        for _ in range(60):
            await asyncio.sleep(0.5)
            if db.order(nr_noord)["status"] in ("bezig", "gereed") and db.order("MES-1")["status"] in ("bezig", "gereed"):
                break
        check(db.order(nr_noord)["status"] in ("bezig", "gereed"), f"order {nr_noord} via PLC actief")
        check(db.order("MES-1")["status"] in ("bezig", "gereed"), "MES-order via PLC actief")
        for _ in range(40):
            await asyncio.sleep(0.5)
            if db.order(nr_noord)["status"] == "gereed":
                break
        o = db.order(nr_noord)
        check(o["status"] == "gereed" and o["gepickt"] == 2, "voortgang uit de PLC terug in de app (gereed 2/2)")

        r = await anoniem.post("/api/orders/MES-1/annuleren", headers={"Authorization": f"Bearer {sleutel}"})
        check(r.status == 200, "MES annuleert order")
        for _ in range(20):
            await asyncio.sleep(0.5)
            if db.order("MES-1")["status"] in ("geannuleerd", "gereed"):
                break
        check(db.order("MES-1")["status"] in ("geannuleerd", "gereed"), "annulering verwerkt door PLC")

        print("Beheer en blokkeren")
        s, d = await login(beheer, "beheer")
        hb = {"X-CSRF-Token": d["csrf"]}
        r = await beheer.post("/api/gebruikers", json={"naam": "west", "rol": "klant", "klantnaam": "Klant West",
                                                        "wachtwoord": "kort"}, headers=hb)
        check(r.status == 422, "te kort wachtwoord geweigerd")
        r = await beheer.post("/api/gebruikers", json={"naam": "west", "rol": "klant", "klantnaam": "Klant West",
                                                        "wachtwoord": WW}, headers=hb)
        check(r.status == 201, "beheerder maakt klant aan")
        zuid_id = db.gebruiker_op_naam("zuid")["id"]
        r = await beheer.post(f"/api/gebruikers/{zuid_id}/actief", json={"actief": False}, headers=hb)
        check(r.status == 200, "beheerder blokkeert gebruiker")
        r = await zuid.get("/api/orders")
        check(r.status == 401, "geblokkeerde gebruiker direct uitgelogd")
        r = await beheer.get("/api/logboek")
        check(any("geblokkeerd" in x["actie"] for x in (await r.json())["logboek"]), "acties staan in het logboek")

        for _ in range(5):
            await login(anoniem, "west", "fout-fout-fout")
        s, _ = await login(anoniem, "west")
        check(s == 401, "account na 5 foute pogingen geblokkeerd, ook met juist wachtwoord")
        db.x("UPDATE gebruikers SET geblokkeerd_tot = 0 WHERE naam = 'west'")
        for _ in range(20):
            await login(anoniem, "west", "fout")
        s, d = await login(anoniem, "west")
        check(s == 429, "IP-adres afgeremd na 20 foute pogingen")

        r = await noord.post("/api/logout", headers=h)
        r = await noord.get("/api/orders")
        check(r.status == 401, "na uitloggen geen toegang meer")

    sim_taak.cancel()
    await sim_server.stop()
    print(f"\nAlle {geslaagd} controles geslaagd.")


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("asyncua").setLevel(logging.ERROR)
    asyncio.run(main())
