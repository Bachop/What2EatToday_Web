"""Passwords and opaque, revocable login credentials."""

import hashlib
import hmac
import secrets
from http.cookies import SimpleCookie, CookieError

ITERATIONS = 600_000
COOKIE = 'w2e_session'


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def hash_password(password):
    salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt), ITERATIONS)
    return f'{ITERATIONS}${salt}${key.hex()}'


def verify_password(password, encoded):
    rounds, salt, expected = encoded.split('$')
    actual = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt), int(rounds))
    return hmac.compare_digest(actual.hex(), expected)


def read_cookie(environ):
    cookie = SimpleCookie()
    try:
        cookie.load(environ.get('HTTP_COOKIE', ''))
        return cookie[COOKIE].value if COOKIE in cookie else ''
    except CookieError:
        return ''


def make_session(db, now, *, user_id=None, guest_scope=None, remember=False, secure=False):
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    lifetime = 30 * 86400 if remember else 12 * 3600
    if guest_scope is not None:
        lifetime = 365 * 86400
    db.execute('INSERT INTO sessions VALUES(?,?,?,?,?)',
               (digest(token), csrf, now + lifetime, user_id, guest_scope))
    cookie = f'{COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax'
    if remember or guest_scope is not None:
        cookie += f'; Max-Age={lifetime}'
    if secure:
        cookie += '; Secure'
    return db.execute('SELECT * FROM sessions WHERE token_hash=?', (digest(token),)).fetchone(), cookie
