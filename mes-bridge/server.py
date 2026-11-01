"""Magazijn-server: bestel-app + API voor het MES + koppeling met de PLC.

    telefoon / laptop / MES ──HTTPS──▶ server.py ──▶ SQLite ──▶ plc.py ──OPC UA──▶ PLC ──▶ Factory I/O

Eerste keer:
    python server.py gebruiker --naam beheer --rol beheerder
    python server.py start --plc opc.tcp://192.168.0.1:4840

Zie README.md voor HTTPS, rollen en API-sleutels.
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import ipaddress
import logging
import pathlib
import re
import ssl
import sys
import time

from aiohttp import web

import beveiliging as bv
from db import Db
from plc import PRODUCT, Plc, sync_lus

HIER = pathlib.Path(__file__).parent
STATIC = HIER / "static"
IDLE_S = 30 * 60          # sessie verloopt na 30 min zonder activiteit
MAX_S = 8 * 3600          # en altijd na 8 uur
MAX_OPEN_KLANT = 10       # open orders per klant
ROL_NIVEAU = {"klant": 1, "planner": 2, "beheerder": 3}
NAAM_RE = re.compile(r"^[a-z0-9._-]{3,32}$")
ORDERNR_RE = re.compile(r"^[A-Za-z0-9_-]{1,20}$")
TEKST_RE = re.compile(r"^[\w .,&'()/-]{0,30}$")

log = logging.getLogger("server")


# ---------------------------------------------------------------- hulpjes
def fout(status: int, tekst: str):
    return web.json_response({"ok": False, "fout": tekst}, status=status)


def ok(**data):
    return web.json_response({"ok": True, **data})


class ApiFout(Exception):
    def __init__(self, status: int, tekst: str):
        self.status, self.tekst = status, tekst


async def json_body(request) -> dict:
    try:
        data = await request.json()
    except Exception:
        raise ApiFout(400, "body moet JSON zijn")
    if not isinstance(data, dict):
        raise ApiFout(400, "body moet een JSON-object zijn")
    return data


def zet_headers(request, h) -> None:
    h["Content-Security-Policy"] = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                                    "connect-src 'self'; manifest-src 'self'; frame-ancestors 'none'; "
                                    "base-uri 'none'; form-action 'self'")
    h["X-Content-Type-Options"] = "nosniff"
    h["X-Frame-Options"] = "DENY"
    h["Referrer-Policy"] = "no-referrer"
    h["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if request.secure:
        h["Strict-Transport-Security"] = "max-age=31536000"
    if request.path.startswith("/api/"):
        h["Cache-Control"] = "no-store"


def vereist(min_rol: str):
    """Decorator: alleen voor ingelogde gebruikers met minimaal deze rol."""
    def deco(handler):
        async def wrapper(request):
            g = request.get("gebruiker")
            if not g:
                return fout(401, "niet ingelogd")
            if ROL_NIVEAU[g["rol"]] < ROL_NIVEAU[min_rol]:
                return fout(403, "onvoldoende rechten")
            return await handler(request)
        return wrapper
    return deco


def publieke_gebruiker(g: dict) -> dict:
    return {"naam": g["naam"], "rol": g["rol"], "klantnaam": g.get("klantnaam", "")}


def order_uit(o: dict) -> dict:
    return {k: o[k] for k in ("nr", "klant", "soort", "aantal", "gepickt", "dock", "prio", "status", "fout",
                              "bron", "aangemaakt", "bijgewerkt")} | {"product": PRODUCT.get(o["soort"], "?")}


# ---------------------------------------------------------------- middlewares
@web.middleware
async def headers_mw(request, handler):
    try:
        resp = await handler(request)
    except ApiFout as e:
        resp = fout(e.status, e.tekst)
    except web.HTTPException as e:  # 404, 405, 413 ...
        zet_headers(request, e.headers)
        raise
    zet_headers(request, resp.headers)
    return resp


@web.middleware
async def auth_mw(request, handler):
    db: Db = request.app["db"]
    request["gebruiker"] = None
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        sleutel = db.api_sleutel(bv.token_hash(auth[7:].strip()))
        if not sleutel:
            return fout(401, "ongeldige API-sleutel")
        request["gebruiker"] = {"id": None, "naam": "api:" + sleutel["naam"], "rol": "planner", "bron": "api"}
    else:
        token = request.cookies.get("sessie")
        if token:
            th = bv.token_hash(token)
            s = db.sessie(th)
            g = db.gebruiker(s["gebruiker_id"]) if s else None
            if s and g and g["actief"]:
                db.verleng_sessie(th, IDLE_S)
                request["gebruiker"] = {**g, "bron": "sessie", "csrf": s["csrf"], "token_hash": th}

    # CSRF: wijzigende verzoeken met een sessiecookie moeten het CSRF-token meesturen
    # en van dezelfde site komen. (API-sleutels gaan niet via cookies, dus geen CSRF-risico.)
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("Origin")
        if origin and origin.split("://", 1)[-1] != request.host:
            return fout(403, "verzoek van een andere website geweigerd")
        g = request["gebruiker"]
        if (g and g["bron"] == "sessie" and request.path != "/api/login"
                and not bv.gelijk(request.headers.get("X-CSRF-Token", ""), g["csrf"])):
            return fout(403, "CSRF-token ontbreekt of klopt niet")
    return await handler(request)


# ---------------------------------------------------------------- routes
routes = web.RouteTableDef()


@routes.get("/")
async def index(_):
    return web.FileResponse(STATIC / "index.html")


@routes.get("/manifest.webmanifest")
async def manifest(_):
    return web.FileResponse(STATIC / "manifest.webmanifest", headers={"Content-Type": "application/manifest+json"})


@routes.post("/api/login")
async def login(request):
    db: Db = request.app["db"]
    limiet: bv.IpLimiet = request.app["ip_limiet"]
    ip = request.remote or "?"
    if not limiet.mag(ip):
        return fout(429, "te veel inlogpogingen, probeer het over 15 minuten opnieuw")
    data = await json_body(request)
    naam = str(data.get("naam", "")).strip().lower()[:64]
    wachtwoord = str(data.get("wachtwoord", ""))[:256]
    g = db.gebruiker_op_naam(naam)
    geblokkeerd = bool(g and g["geblokkeerd_tot"] > time.time())
    juist = await asyncio.to_thread(bv.check_wachtwoord, wachtwoord, g["wachtwoord"] if g else bv.DUMMY_HASH)
    if not (g and juist and g["actief"] and not geblokkeerd):
        limiet.mislukt(ip)
        if g and not geblokkeerd:
            db.login_mislukt(g["id"])
        db.audit(ip, naam or "?", "inloggen mislukt")
        await asyncio.sleep(0.5)
        # Eén algemene melding: verraadt niet of de naam bestaat of geblokkeerd is.
        return fout(401, "onjuiste naam of wachtwoord (na 5 pogingen 15 min geblokkeerd)")
    db.login_ok(g["id"])
    token, csrf = bv.nieuw_token(), bv.nieuw_token()
    db.maak_sessie(bv.token_hash(token), g["id"], csrf, IDLE_S, MAX_S)
    db.audit(ip, g["naam"], "ingelogd")
    resp = ok(gebruiker=publieke_gebruiker(g), csrf=csrf)
    resp.set_cookie("sessie", token, httponly=True, samesite="Strict", secure=request.secure, max_age=MAX_S, path="/")
    return resp


@routes.post("/api/logout")
async def logout(request):
    g = request["gebruiker"]
    if g and g["bron"] == "sessie":
        request.app["db"].verwijder_sessie(g["token_hash"])
        request.app["db"].audit(request.remote, g["naam"], "uitgelogd")
    resp = ok()
    resp.del_cookie("sessie", path="/")
    return resp


@routes.get("/api/ik")
@vereist("klant")
async def ik(request):
    g = request["gebruiker"]
    return ok(gebruiker=publieke_gebruiker(g), csrf=g.get("csrf"))


@routes.post("/api/wachtwoord")
@vereist("klant")
async def wachtwoord_wijzigen(request):
    db: Db = request.app["db"]
    g = request["gebruiker"]
    if g["bron"] != "sessie":
        return fout(403, "alleen voor ingelogde gebruikers")
    data = await json_body(request)
    if not await asyncio.to_thread(bv.check_wachtwoord, str(data.get("oud", "")), g["wachtwoord"]):
        return fout(403, "huidig wachtwoord klopt niet")
    nieuw = str(data.get("nieuw", ""))
    if p := bv.wachtwoord_probleem(nieuw):
        return fout(422, p)
    db.zet_wachtwoord(g["id"], await asyncio.to_thread(bv.hash_wachtwoord, nieuw))
    db.verwijder_sessies_van(g["id"], behalve=g["token_hash"])
    db.audit(request.remote, g["naam"], "wachtwoord gewijzigd")
    return ok()


@routes.get("/api/status")
@vereist("klant")
async def status(request):
    plc: Plc = request.app["plc"]
    return ok(plc_verbonden=plc.online, plc_levensteken=plc.plc_levensteken, mes_levensteken=plc.mes_levensteken)


@routes.get("/api/producten")
@vereist("klant")
async def producten(_):
    return ok(producten=[{"soort": s, "naam": n} for s, n in PRODUCT.items()])


@routes.get("/api/orders")
@vereist("klant")
async def orders(request):
    db: Db = request.app["db"]
    g = request["gebruiker"]
    lijst = db.orders(gebruiker_id=g["id"]) if g["rol"] == "klant" else db.orders()
    return ok(orders=[order_uit(o) for o in lijst])


def controleer_order(data: dict, g: dict) -> dict:
    try:
        soort = int(data["soort"])
        aantal = int(data["aantal"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("soort en aantal zijn verplicht en moeten getallen zijn")
    if soort not in PRODUCT:
        raise ValueError("onbekend product")
    if g["rol"] == "klant":
        if not 1 <= aantal <= 50:
            raise ValueError("aantal: 1 t/m 50 pallets")
        klant, prio, nr = g["klantnaam"] or g["naam"], 2, None
    else:
        if not 1 <= aantal <= 999:
            raise ValueError("aantal: 1 t/m 999 pallets")
        klant = str(data.get("klant", "")).strip() or "Intern"
        if not TEKST_RE.match(klant):
            raise ValueError("klant: maximaal 30 tekens, letters/cijfers/leestekens")
        prio = int(data.get("prio", 2))
        if prio not in (1, 2, 3):
            raise ValueError("prio: 1, 2 of 3")
        nr = str(data.get("nr", "")).strip() or None
        if nr and not ORDERNR_RE.match(nr):
            raise ValueError("nr: 1 t/m 20 tekens, alleen letters, cijfers, - en _")
    return {"nr": nr, "klant": klant, "soort": soort, "aantal": aantal, "dock": 1 if soort < 10 else 2, "prio": prio}


@routes.post("/api/orders")
@vereist("klant")
async def nieuwe_order(request):
    db: Db = request.app["db"]
    g = request["gebruiker"]
    data = await json_body(request)
    try:
        o = controleer_order(data, g)
    except ValueError as e:
        return fout(422, str(e))
    if g["rol"] == "klant" and db.aantal_open(g["id"]) >= MAX_OPEN_KLANT:
        return fout(429, f"maximaal {MAX_OPEN_KLANT} open orders per klant")
    if o["nr"] and db.order(o["nr"]):
        return fout(409, "ordernummer bestaat al")
    nr = db.maak_order(o, g["id"], g["bron"])
    db.audit(request.remote, g["naam"], f"order {nr} geplaatst: {o['aantal']} x {PRODUCT[o['soort']]} voor {o['klant']}")
    return web.json_response({"ok": True, "nr": nr}, status=201)


@routes.post("/api/orders/{nr}/annuleren")
@vereist("klant")
async def annuleren(request):
    db: Db = request.app["db"]
    g = request["gebruiker"]
    o = db.order(request.match_info["nr"])
    if not o or (g["rol"] == "klant" and o["gebruiker_id"] != g["id"]):
        return fout(404, "order niet gevonden")
    toegestaan = ("nieuw", "in_plc", "wachtend") if g["rol"] == "klant" else ("nieuw", "in_plc", "wachtend",
                                                                               "bezig", "gepauzeerd")
    if o["status"] not in toegestaan:
        return fout(409, f"order is {o['status']} en kan niet meer geannuleerd worden")
    db.zet_status(o["nr"], "geannuleerd" if o["status"] == "nieuw" else "annuleren")
    db.audit(request.remote, g["naam"], f"order {o['nr']} annuleren")
    return ok()


@routes.get("/api/gebruikers")
@vereist("beheerder")
async def gebruikers(request):
    return ok(gebruikers=request.app["db"].gebruikers())


@routes.post("/api/gebruikers")
@vereist("beheerder")
async def gebruiker_maken(request):
    db: Db = request.app["db"]
    data = await json_body(request)
    naam = str(data.get("naam", "")).strip().lower()
    rol = str(data.get("rol", ""))
    klantnaam = str(data.get("klantnaam", "")).strip()
    wachtwoord = str(data.get("wachtwoord", ""))
    if not NAAM_RE.match(naam):
        return fout(422, "naam: 3–32 tekens, kleine letters, cijfers, . _ -")
    if rol not in ROL_NIVEAU:
        return fout(422, "rol: klant, planner of beheerder")
    if rol == "klant" and not (klantnaam and TEKST_RE.match(klantnaam)):
        return fout(422, "klantnaam is verplicht voor een klant (max 30 tekens)")
    if p := bv.wachtwoord_probleem(wachtwoord):
        return fout(422, p)
    if db.gebruiker_op_naam(naam):
        return fout(409, "naam bestaat al")
    db.maak_gebruiker(naam, await asyncio.to_thread(bv.hash_wachtwoord, wachtwoord), rol, klantnaam)
    db.audit(request.remote, request["gebruiker"]["naam"], f"gebruiker {naam} ({rol}) aangemaakt")
    return web.json_response({"ok": True}, status=201)


@routes.post("/api/gebruikers/{gid}/actief")
@vereist("beheerder")
async def gebruiker_actief(request):
    db: Db = request.app["db"]
    g = request["gebruiker"]
    doel = db.gebruiker(int(request.match_info["gid"]))
    if not doel:
        return fout(404, "gebruiker niet gevonden")
    if doel["id"] == g["id"]:
        return fout(409, "je kunt jezelf niet blokkeren")
    actief = bool((await json_body(request)).get("actief"))
    db.zet_actief(doel["id"], actief)
    if not actief:
        db.verwijder_sessies_van(doel["id"])
    db.audit(request.remote, g["naam"], f"gebruiker {doel['naam']} {'vrijgegeven' if actief else 'geblokkeerd'}")
    return ok()


@routes.post("/api/gebruikers/{gid}/wachtwoord")
@vereist("beheerder")
async def gebruiker_wachtwoord(request):
    db: Db = request.app["db"]
    doel = db.gebruiker(int(request.match_info["gid"]))
    if not doel:
        return fout(404, "gebruiker niet gevonden")
    nieuw = str((await json_body(request)).get("wachtwoord", ""))
    if p := bv.wachtwoord_probleem(nieuw):
        return fout(422, p)
    db.zet_wachtwoord(doel["id"], await asyncio.to_thread(bv.hash_wachtwoord, nieuw))
    db.verwijder_sessies_van(doel["id"])
    db.audit(request.remote, request["gebruiker"]["naam"], f"wachtwoord van {doel['naam']} opnieuw ingesteld")
    return ok()


@routes.get("/api/logboek")
@vereist("beheerder")
async def logboek(request):
    return ok(logboek=request.app["db"].logboek())


# ---------------------------------------------------------------- app
def maak_app(db: Db, plc: Plc) -> web.Application:
    app = web.Application(middlewares=[headers_mw, auth_mw], client_max_size=16 * 1024)
    app["db"], app["plc"], app["ip_limiet"] = db, plc, bv.IpLimiet()
    app.add_routes(routes)
    app.router.add_static("/static", STATIC, show_index=False, follow_symlinks=False)

    async def start_taken(app):
        app["taken"] = [asyncio.create_task(plc.verbind_lus()), asyncio.create_task(sync_lus(plc, db))]

    async def stop_taken(app):
        for t in app["taken"]:
            t.cancel()

    app.on_startup.append(start_taken)
    app.on_cleanup.append(stop_taken)
    return app


def is_lokaal(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def lees_wachtwoord(stdin: bool) -> str:
    if stdin:
        return sys.stdin.readline().rstrip("\n")
    w1 = getpass.getpass("Wachtwoord (min. 10 tekens): ")
    if w1 != getpass.getpass("Nogmaals: "):
        sys.exit("wachtwoorden zijn niet gelijk")
    return w1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=str(HIER / "magazijn.db"), help="pad naar de SQLite-database")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("start", help="server starten")
    s.add_argument("--plc", default="opc.tcp://192.168.0.1:4840", help="OPC UA-adres van de PLC")
    s.add_argument("--plc-gebruiker")
    s.add_argument("--plc-wachtwoord")
    s.add_argument("--host", default="127.0.0.1", help="0.0.0.0 = bereikbaar op het netwerk (vereist HTTPS)")
    s.add_argument("--poort", type=int, default=8443)
    s.add_argument("--cert", help="TLS-certificaat (PEM)")
    s.add_argument("--key", help="TLS-sleutel (PEM)")
    s.add_argument("--onveilig-http", action="store_true",
                   help="HTTP zonder TLS op het netwerk toestaan (alleen voor een afgeschermd testnetwerk)")

    gb = sub.add_parser("gebruiker", help="gebruiker aanmaken")
    gb.add_argument("--naam", required=True)
    gb.add_argument("--rol", required=True, choices=list(ROL_NIVEAU))
    gb.add_argument("--klant", default="", help="klantnaam (verplicht bij rol klant)")
    gb.add_argument("--wachtwoord-stdin", action="store_true", help="wachtwoord van stdin lezen (scripts)")

    ak = sub.add_parser("api-sleutel", help="API-sleutel voor het MES maken")
    ak.add_argument("--naam", required=True)

    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("asyncua").setLevel(logging.ERROR)
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    db = Db(a.db)

    if a.cmd == "gebruiker":
        naam = a.naam.strip().lower()
        if not NAAM_RE.match(naam):
            sys.exit("naam: 3–32 tekens, kleine letters, cijfers, . _ -")
        if a.rol == "klant" and not a.klant:
            sys.exit("--klant is verplicht bij rol klant")
        if db.gebruiker_op_naam(naam):
            sys.exit("naam bestaat al")
        w = lees_wachtwoord(a.wachtwoord_stdin)
        if p := bv.wachtwoord_probleem(w):
            sys.exit(p)
        db.maak_gebruiker(naam, bv.hash_wachtwoord(w), a.rol, a.klant)
        db.audit("cli", "cli", f"gebruiker {naam} ({a.rol}) aangemaakt")
        print(f"gebruiker {naam} ({a.rol}) aangemaakt")
        return

    if a.cmd == "api-sleutel":
        sleutel = "mz_" + bv.nieuw_token()
        db.maak_api_sleutel(a.naam, bv.token_hash(sleutel))
        db.audit("cli", "cli", f"API-sleutel {a.naam} aangemaakt")
        print("API-sleutel (wordt maar één keer getoond, bewaar hem veilig):")
        print(sleutel)
        return

    ssl_ctx = None
    if a.cert and a.key:
        ssl_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ssl_ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ssl_ctx.load_cert_chain(a.cert, a.key)
    elif not is_lokaal(a.host) and not a.onveilig_http:
        sys.exit("Op het netwerk alleen met HTTPS: geef --cert en --key mee (zie README), "
                 "of gebruik --host 127.0.0.1.")
    if not db.q("SELECT 1 FROM gebruikers WHERE rol = 'beheerder' AND actief = 1"):
        log.warning("nog geen beheerder: maak er een met  python server.py gebruiker --naam beheer --rol beheerder")
    plc = Plc(a.plc, a.plc_gebruiker, a.plc_wachtwoord)
    web.run_app(maak_app(db, plc), host=a.host, port=a.poort, ssl_context=ssl_ctx, access_log=None)


if __name__ == "__main__":
    main()
