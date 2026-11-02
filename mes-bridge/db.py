"""SQLite-opslag: gebruikers, sessies, API-sleutels, orders en logboek."""
from __future__ import annotations

import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS gebruikers (
    id INTEGER PRIMARY KEY,
    naam TEXT NOT NULL UNIQUE,
    wachtwoord TEXT NOT NULL,
    rol TEXT NOT NULL CHECK (rol IN ('klant', 'planner', 'beheerder')),
    klantnaam TEXT NOT NULL DEFAULT '',
    actief INTEGER NOT NULL DEFAULT 1,
    mislukt INTEGER NOT NULL DEFAULT 0,
    geblokkeerd_tot REAL NOT NULL DEFAULT 0,
    laatst_ingelogd REAL,
    aangemaakt REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessies (
    token_hash TEXT PRIMARY KEY,
    gebruiker_id INTEGER NOT NULL REFERENCES gebruikers(id) ON DELETE CASCADE,
    csrf TEXT NOT NULL,
    verloopt REAL NOT NULL,
    eind REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS api_sleutels (
    id INTEGER PRIMARY KEY,
    naam TEXT NOT NULL UNIQUE,
    sleutel_hash TEXT NOT NULL UNIQUE,
    actief INTEGER NOT NULL DEFAULT 1,
    aangemaakt REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY,
    nr TEXT NOT NULL UNIQUE,
    klant TEXT NOT NULL,
    soort INTEGER NOT NULL,
    aantal INTEGER NOT NULL,
    gepickt INTEGER NOT NULL DEFAULT 0,
    dock INTEGER NOT NULL,
    prio INTEGER NOT NULL,
    status TEXT NOT NULL,
    fout TEXT NOT NULL DEFAULT '',
    gebruiker_id INTEGER REFERENCES gebruikers(id),
    bron TEXT NOT NULL,
    aangemaakt REAL NOT NULL,
    bijgewerkt REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS orders_status ON orders(status);
CREATE TABLE IF NOT EXISTS logboek (
    id INTEGER PRIMARY KEY,
    tijd REAL NOT NULL,
    wie TEXT NOT NULL,
    actie TEXT NOT NULL,
    ip TEXT NOT NULL DEFAULT ''
);
"""

OPEN_STATUSSEN = ("nieuw", "in_plc", "wachtend", "bezig", "gepauzeerd", "annuleren")


class Db:
    def __init__(self, pad: str):
        self.c = sqlite3.connect(pad, check_same_thread=False, isolation_level=None)
        self.c.row_factory = sqlite3.Row
        self.c.execute("PRAGMA journal_mode=WAL")
        self.c.execute("PRAGMA foreign_keys=ON")
        self.c.executescript(SCHEMA)
        self.lock = threading.Lock()

    def q(self, sql: str, args=()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.c.execute(sql, args).fetchall()]

    def een(self, sql: str, args=()) -> dict | None:
        r = self.q(sql, args)
        return r[0] if r else None

    def x(self, sql: str, args=()) -> int:
        with self.lock:
            return self.c.execute(sql, args).lastrowid

    # --- gebruikers -------------------------------------------------------
    def gebruiker_op_naam(self, naam: str):
        return self.een("SELECT * FROM gebruikers WHERE naam = ?", (naam,))

    def gebruiker(self, gid: int):
        return self.een("SELECT * FROM gebruikers WHERE id = ?", (gid,))

    def gebruikers(self):
        return self.q("SELECT id, naam, rol, klantnaam, actief, laatst_ingelogd, geblokkeerd_tot, aangemaakt "
                      "FROM gebruikers ORDER BY naam")

    def maak_gebruiker(self, naam, wachtwoord_hash, rol, klantnaam="") -> int:
        return self.x("INSERT INTO gebruikers (naam, wachtwoord, rol, klantnaam, aangemaakt) VALUES (?,?,?,?,?)",
                      (naam, wachtwoord_hash, rol, klantnaam, time.time()))

    def zet_wachtwoord(self, gid, wachtwoord_hash):
        self.x("UPDATE gebruikers SET wachtwoord = ?, mislukt = 0, geblokkeerd_tot = 0 WHERE id = ?",
               (wachtwoord_hash, gid))

    def zet_actief(self, gid, actief: bool):
        self.x("UPDATE gebruikers SET actief = ? WHERE id = ?", (1 if actief else 0, gid))

    def login_mislukt(self, gid, max_pogingen=5, blokkeer_s=900):
        with self.lock:
            self.c.execute("UPDATE gebruikers SET mislukt = mislukt + 1 WHERE id = ?", (gid,))
            self.c.execute("UPDATE gebruikers SET geblokkeerd_tot = ?, mislukt = 0 WHERE id = ? AND mislukt >= ?",
                           (time.time() + blokkeer_s, gid, max_pogingen))

    def login_ok(self, gid):
        self.x("UPDATE gebruikers SET mislukt = 0, laatst_ingelogd = ? WHERE id = ?", (time.time(), gid))

    # --- sessies ----------------------------------------------------------
    def maak_sessie(self, token_hash, gid, csrf, idle_s, max_s):
        nu = time.time()
        self.x("DELETE FROM sessies WHERE eind < ? OR verloopt < ?", (nu, nu))
        self.x("INSERT INTO sessies VALUES (?,?,?,?,?)", (token_hash, gid, csrf, nu + idle_s, nu + max_s))

    def sessie(self, token_hash):
        return self.een("SELECT * FROM sessies WHERE token_hash = ? AND verloopt > ? AND eind > ?",
                        (token_hash, time.time(), time.time()))

    def verleng_sessie(self, token_hash, idle_s):
        self.x("UPDATE sessies SET verloopt = MIN(eind, ?) WHERE token_hash = ?", (time.time() + idle_s, token_hash))

    def verwijder_sessie(self, token_hash):
        self.x("DELETE FROM sessies WHERE token_hash = ?", (token_hash,))

    def verwijder_sessies_van(self, gid, behalve: str | None = None):
        self.x("DELETE FROM sessies WHERE gebruiker_id = ? AND token_hash != ?", (gid, behalve or ""))

    # --- API-sleutels -----------------------------------------------------
    def maak_api_sleutel(self, naam, sleutel_hash):
        return self.x("INSERT INTO api_sleutels (naam, sleutel_hash, aangemaakt) VALUES (?,?,?)",
                      (naam, sleutel_hash, time.time()))

    def api_sleutel(self, sleutel_hash):
        return self.een("SELECT * FROM api_sleutels WHERE sleutel_hash = ? AND actief = 1", (sleutel_hash,))

    # --- orders -----------------------------------------------------------
    def maak_order(self, o: dict, gid, bron: str) -> str:
        nu = time.time()
        with self.lock:
            nr = o.get("nr")
            if not nr:
                prefix = "W" + time.strftime("%y%m%d") + "-"
                n = self.c.execute("SELECT COUNT(*) FROM orders WHERE nr LIKE ?", (prefix + "%",)).fetchone()[0]
                nr = f"{prefix}{n + 1:04d}"
            self.c.execute(
                "INSERT INTO orders (nr, klant, soort, aantal, dock, prio, status, gebruiker_id, bron, aangemaakt, bijgewerkt)"
                " VALUES (?,?,?,?,?,?, 'nieuw', ?,?,?,?)",
                (nr, o["klant"], o["soort"], o["aantal"], o["dock"], o["prio"], gid, bron, nu, nu))
        return nr

    def order(self, nr):
        return self.een("SELECT * FROM orders WHERE nr = ?", (nr,))

    def orders(self, gebruiker_id=None, limiet=200):
        if gebruiker_id is None:
            return self.q("SELECT * FROM orders ORDER BY id DESC LIMIT ?", (limiet,))
        return self.q("SELECT * FROM orders WHERE gebruiker_id = ? ORDER BY id DESC LIMIT ?", (gebruiker_id, limiet))

    def aantal_open(self, gebruiker_id) -> int:
        ph = ",".join("?" * len(OPEN_STATUSSEN))
        return self.een(f"SELECT COUNT(*) AS n FROM orders WHERE gebruiker_id = ? AND status IN ({ph})",
                        (gebruiker_id, *OPEN_STATUSSEN))["n"]

    def orders_met_status(self, status):
        return self.q("SELECT * FROM orders WHERE status = ? ORDER BY id", (status,))

    def zet_status(self, nr, status, fout=""):
        self.x("UPDATE orders SET status = ?, fout = ?, bijgewerkt = ? WHERE nr = ?", (status, fout, time.time(), nr))

    def update_vanuit_plc(self, nr, status, gepickt):
        self.x("UPDATE orders SET status = ?, gepickt = ?, bijgewerkt = ? "
               "WHERE nr = ? AND status NOT IN ('annuleren', 'geweigerd', 'nieuw') AND (status != ? OR gepickt != ?)",
               (status, gepickt, time.time(), nr, status, gepickt))

    # --- logboek ----------------------------------------------------------
    def audit(self, ip, wie, actie):
        self.x("INSERT INTO logboek (tijd, wie, actie, ip) VALUES (?,?,?,?)", (time.time(), wie, actie, ip or ""))

    def logboek(self, limiet=200):
        return self.q("SELECT * FROM logboek ORDER BY id DESC LIMIT ?", (limiet,))
