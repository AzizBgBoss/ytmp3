"""
auth.py
- Users stored in a JSON file: [{"username": "peter", "password": "<sha256hex>"}]
- On login: generate a random token, store in server-side dict with username + expiry
- Token saved in a cookie: session_token
- No registration - accounts are created manually in the JSON file

Usage in your Flask app:
    from auth import init_auth, login_required, login_route, logout_route
    init_auth(app, users_file='users.json', cookie_name='session_token')
    app.add_url_rule('/login',  'login',  login_route,  methods=['GET','POST'])
    app.add_url_rule('/logout', 'logout', logout_route, methods=['GET','POST'])

Then decorate any route with @login_required.
"""

import os, json, hashlib, secrets, time
from functools import wraps
from flask import request, redirect, url_for, make_response, render_template_string

# ── state (set by init_auth) ──────────────────────────────────────────────────

_USERS_FILE  = 'users.json'
_COOKIE_NAME = 'session_token'
_TOKEN_TTL   = 30 * 24 * 3600   # 30 days
_sessions    = {}               # token -> {username, expires}

# ── helpers ───────────────────────────────────────────────────────────────────

def _load_users():
    if not os.path.exists(_USERS_FILE):
        return {}
    with open(_USERS_FILE, 'r') as f:
        users = json.load(f)
    return {u['username']: u['password'] for u in users}

def _sha256(s):
    return hashlib.sha256(s.encode()).hexdigest()

def _valid_token(token):
    if not token or token not in _sessions:
        return False
    s = _sessions[token]
    if time.time() > s['expires']:
        del _sessions[token]
        return False
    return True

def _purge_expired():
    now = time.time()
    dead = [t for t, s in _sessions.items() if now > s['expires']]
    for t in dead:
        del _sessions[t]

# ── public API ────────────────────────────────────────────────────────────────

def init_auth(app, users_file='users.json', cookie_name='session_token',
              token_ttl=7*24*3600):
    global _USERS_FILE, _COOKIE_NAME, _TOKEN_TTL
    _USERS_FILE  = users_file
    _COOKIE_NAME = cookie_name
    _TOKEN_TTL   = token_ttl

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.cookies.get(_COOKIE_NAME)
        if not _valid_token(token):
            return redirect('/login?next=' + request.path)
        return f(*args, **kwargs)
    return decorated

# ── login template ────────────────────────────────────────────────────────────

_LOGIN_TMPL = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ title }} - login</title>
<style>
* { -webkit-box-sizing: border-box; box-sizing: border-box; margin: 0; padding: 0; }
body { background: #0a0a0f; color: #c8d8c8; font-family: monospace; font-size: 14px;
       display: -webkit-box; display: -webkit-flex; display: flex;
       -webkit-box-align: center; -webkit-align-items: center; align-items: center;
       -webkit-box-pack: center; -webkit-justify-content: center; justify-content: center;
       min-height: 100vh; padding: 20px; }
.box { width: 100%; max-width: 320px; }
h1 { font-size: 1.6rem; color: #00ff88; margin-bottom: 4px; }
h1 span { color: #ff6b35; }
.sub { font-size: 11px; color: #4a5a4a; letter-spacing: 3px; text-transform: uppercase; margin-bottom: 24px; }
.field { margin-bottom: 12px; }
label { display: block; font-size: 11px; color: #4a5a4a; text-transform: uppercase;
        letter-spacing: 2px; margin-bottom: 5px; }
input[type=text], input[type=password] {
  width: 100%; background: #0f0f1a; border: 1px solid #1a1a2e;
  color: #c8d8c8; font-family: monospace; font-size: 13px; padding: 9px 10px; outline: none; }
input[type=text]:focus, input[type=password]:focus { border-color: #00ff88; }
.btn { width: 100%; background: transparent; border: 1px solid #00ff88; color: #00ff88;
       font-family: monospace; font-size: 13px; padding: 9px; cursor: pointer;
       text-transform: uppercase; margin-top: 4px; }
.err { color: #ff6b35; font-size: 12px; margin-bottom: 12px;
       border: 1px solid #ff6b35; padding: 7px 10px; }
</style>
</head>
<body>
<div class="box">
  <h1>{{ h1 }}<span>{{ h1s }}</span></h1>
  <div class="sub">{{ subtitle }}</div>
  {% if error %}<div class="err">{{ error }}</div>{% endif %}
  <form method="POST" action="/login?next={{ next }}">
    <div class="field">
      <label for="u">username</label>
      <input type="text" name="username" id="u" autocomplete="off" autocorrect="off" autocapitalize="off">
    </div>
    <div class="field">
      <label for="p">password</label>
      <input type="password" name="password" id="p">
    </div>
    <button class="btn" type="submit">LOGIN</button>
  </form>
</div>
</body>
</html>
"""

# set by init_auth via keyword or directly
_TITLE    = 'app'
_H1       = 'APP'
_H1S      = ''
_SUBTITLE = 'login to continue'

def set_login_theme(title, h1, h1s, subtitle):
    global _TITLE, _H1, _H1S, _SUBTITLE
    _TITLE = title; _H1 = h1; _H1S = h1s; _SUBTITLE = subtitle

def login_route():
    next_url = request.args.get('next', '/')
    error    = None

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        users    = _load_users()
        _purge_expired()

        if username in users and users[username] == _sha256(password):
            token = secrets.token_hex(32)
            _sessions[token] = {
                'username': username,
                'expires':  time.time() + _TOKEN_TTL,
            }
            resp = make_response(redirect(next_url or '/'))
            resp.set_cookie(_COOKIE_NAME, token,
                            max_age=_TOKEN_TTL, httponly=True, samesite='Lax')
            return resp
        else:
            error = 'invalid username or password'

    html = render_template_string(_LOGIN_TMPL,
        title=_TITLE, h1=_H1, h1s=_H1S, subtitle=_SUBTITLE,
        error=error, next=next_url)
    return html

def logout_route():
    token = request.cookies.get(_COOKIE_NAME)
    if token and token in _sessions:
        del _sessions[token]
    resp = make_response(redirect('/login'))
    resp.delete_cookie(_COOKIE_NAME)
    return resp
