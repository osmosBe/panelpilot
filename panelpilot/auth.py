"""Password hashes, expiring server-side sessions and bounded login throttling."""
import hashlib
import hmac
import json
import secrets
import threading
import time
from collections import OrderedDict, deque
from pathlib import Path

from .storage import write_json

SESSION_SECONDS = 8 * 60 * 60


def password_hash(password, salt):
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=32768,
                          r=8, p=1, maxmem=128*1024*1024).hex()


def make_credentials(password, username="admin"):
    salt = secrets.token_hex(16)
    return {"username": username, "salt": salt, "hash": password_hash(password, salt)}


class Auth:
    def __init__(self, path: Path, clock=time.monotonic):
        self.path, self.clock = path, clock
        self.lock = threading.RLock()
        self.sessions = OrderedDict()
        self.failures = OrderedDict()
        self.global_attempts = deque(maxlen=60)
        self.fingerprint = None
        self.refresh()

    def refresh(self):
        raw = self.path.read_bytes()
        fingerprint = hashlib.sha256(raw).hexdigest()
        if fingerprint != self.fingerprint:
            self.credentials = json.loads(raw)
            self.fingerprint = fingerprint
            self.sessions.clear()

    def verify(self, username, password):
        supplied = password_hash(password, self.credentials["salt"])
        return (hmac.compare_digest(supplied, self.credentials["hash"])
                & hmac.compare_digest(username.encode(), self.credentials["username"].encode()))

    def login(self, username, password, peer):
        with self.lock:
            self.refresh()
            now = self.clock()
            recent = self.failures.setdefault(peer, deque(maxlen=5))
            self.failures.move_to_end(peer)
            while recent and now-recent[0] >= 60:
                recent.popleft()
            while self.global_attempts and now-self.global_attempts[0] >= 60:
                self.global_attempts.popleft()
            if len(recent) >= 5 or len(self.global_attempts) >= 60:
                raise ValueError("rate_limit")
            self.global_attempts.append(now)
            if not self.verify(username, password):
                recent.append(now)
                if len(self.failures) > 1024:
                    self.failures.popitem(last=False)
                return None
            self.failures.pop(peer, None)
            token = secrets.token_urlsafe(32)
            session = {"username": username, "csrf": secrets.token_urlsafe(32), "expires": now+SESSION_SECONDS}
            self.sessions[token] = session
            while len(self.sessions) > 128:
                self.sessions.popitem(last=False)
            return token, session

    def session(self, token):
        with self.lock:
            self.refresh()
            value = self.sessions.get(token)
            if value and value["expires"] > self.clock():
                return value
            self.sessions.pop(token, None)
            return None

    def logout(self, token):
        with self.lock:
            self.sessions.pop(token, None)

    def change_password(self, current, replacement):
        with self.lock:
            self.refresh()
            if not self.verify(self.credentials["username"], current):
                return False
            write_json(self.path, make_credentials(replacement, self.credentials["username"]))
            self.refresh()
            return True
