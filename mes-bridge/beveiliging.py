"""Wachtwoorden, tokens en het afremmen van inlogpogingen."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from collections import defaultdict, deque

_N, _R, _P = 2 ** 14, 8, 1


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def hash_wachtwoord(wachtwoord: str) -> str:
    """scrypt met willekeurige salt; formaat: scrypt$n$r$p$salt$hash."""
    salt = os.urandom(16)
    h = hashlib.scrypt(wachtwoord.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${_b64(salt)}${_b64(h)}"


def check_wachtwoord(wachtwoord: str, opgeslagen: str) -> bool:
    try:
        _, n, r, p, salt, h = opgeslagen.split("$")
        poging = hashlib.scrypt(wachtwoord.encode(), salt=base64.b64decode(salt),
                                n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(poging, base64.b64decode(h))
    except (ValueError, TypeError):
        return False


# Wordt gecontroleerd als de gebruiker niet bestaat, zodat het antwoord even lang duurt
# (anders verraadt de reactietijd welke namen bestaan).
DUMMY_HASH = hash_wachtwoord(secrets.token_hex(16))


def wachtwoord_probleem(w: str) -> str | None:
    if len(w) < 10:
        return "wachtwoord moet minimaal 10 tekens hebben"
    if len(w) > 128:
        return "wachtwoord is te lang"
    if w.lower() in {"wachtwoord1", "welkom1234", "1234567890", "magazijn123", "password12"}:
        return "kies een minder voorspelbaar wachtwoord"
    return None


def nieuw_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    """Tokens en API-sleutels worden alleen als hash opgeslagen."""
    return hashlib.sha256(token.encode()).hexdigest()


def gelijk(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


class IpLimiet:
    """Maximaal `max_pogingen` mislukte logins per IP-adres per `venster` seconden."""

    def __init__(self, max_pogingen: int = 20, venster: float = 900):
        self.max = max_pogingen
        self.venster = venster
        self.pogingen: dict[str, deque] = defaultdict(deque)

    def _schoon(self, ip: str) -> deque:
        d = self.pogingen[ip]
        grens = time.monotonic() - self.venster
        while d and d[0] < grens:
            d.popleft()
        return d

    def mag(self, ip: str) -> bool:
        return len(self._schoon(ip)) < self.max

    def mislukt(self, ip: str) -> None:
        self._schoon(ip).append(time.monotonic())
