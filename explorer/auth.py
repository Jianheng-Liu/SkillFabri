"""Sign-in with Google.

The browser gets an ID token from Google Identity Services and posts it here; this module
verifies the signature and audience against Google's public keys, then issues its own signed
cookie. That is the whole exchange — no redirect callback to register, no server-side session
table, and Google's token is never stored.

Chosen over the authorization-code flow because we need identity, not access: there is no
Google API to call on the user's behalf, so the extra round trips and a client secret would
buy nothing.

Two settings, both optional. Without SF_GOOGLE_CLIENT_ID sign-in reports itself unavailable
and the rest of the site is untouched, which is also how a local run behaves by default.
SF_SECRET_KEY signs the cookie; if it is unset a random one is generated per process, so
sessions simply do not survive a restart.
"""
import os, secrets
from flask import request
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

COOKIE = "sf_session"
MAX_AGE = 60 * 60 * 24 * 30          # thirty days

CLIENT_ID = os.environ.get("SF_GOOGLE_CLIENT_ID", "").strip()
GH_ID     = os.environ.get("SF_GITHUB_CLIENT_ID", "").strip()
GH_SECRET = os.environ.get("SF_GITHUB_CLIENT_SECRET", "").strip()
_SECRET = os.environ.get("SF_SECRET_KEY", "").strip() or secrets.token_urlsafe(32)
_signer = URLSafeTimedSerializer(_SECRET, salt="sf-session")


def providers() -> dict:
    return {"google": bool(CLIENT_ID), "github": bool(GH_ID and GH_SECRET)}


def enabled() -> bool:
    return any(providers().values())


def verify_google(credential: str):
    """Return the token's claims, or None if it is not a valid token for this app.

    google-auth checks the signature against Google's rotating public keys, the expiry, and
    that the audience is our client id — the last of which is what stops a token minted for
    some other site from being replayed here.
    """
    if not credential or not CLIENT_ID:
        return None
    try:
        from google.oauth2 import id_token
        from google.auth.transport import requests as grequests
        info = id_token.verify_oauth2_token(credential, grequests.Request(), CLIENT_ID)
    except Exception:
        return None
    if info.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
        return None
    if not info.get("sub"):
        return None
    return info


def issue(resp, sub: str):
    resp.set_cookie(COOKIE, _signer.dumps(sub), max_age=MAX_AGE,
                    httponly=True,           # unreadable from JS, so XSS cannot lift the session
                    samesite="Lax",          # survives a top-level navigation back to the site
                    secure=request.is_secure,
                    path="/")
    return resp


def clear(resp):
    resp.delete_cookie(COOKIE, path="/")
    return resp


def current_sub():
    """The signed-in user's Google subject id, or None."""
    raw = request.cookies.get(COOKIE)
    if not raw:
        return None
    try:
        return _signer.loads(raw, max_age=MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None


# ---------- GitHub ----------
# GitHub has no equivalent of an ID token, so this is the authorization-code flow: send the
# browser to GitHub, take back a one-time code, and exchange it server-side for an access token
# that we use once to read the profile and then drop. That exchange needs a client secret, which
# is why GitHub takes two settings where Google takes one.
GH_AUTH = "https://github.com/login/oauth/authorize"
GH_TOKEN = "https://github.com/login/oauth/access_token"
GH_API = "https://api.github.com"
_state = URLSafeTimedSerializer(_SECRET, salt="sf-oauth-state")
STATE_COOKIE = "sf_oauth_state"


def gh_start_url(redirect_uri: str):
    """The URL to send the browser to, plus the state to store in a cookie.

    `state` is a signed random value echoed back by GitHub and checked on return. Without it
    an attacker could feed their own code to our callback and sign the victim into the
    attacker's account.
    """
    nonce = secrets.token_urlsafe(16)
    from urllib.parse import urlencode
    url = GH_AUTH + "?" + urlencode({
        "client_id": GH_ID, "redirect_uri": redirect_uri,
        "scope": "read:user user:email", "state": nonce, "allow_signup": "true"})
    return url, _state.dumps(nonce)


def gh_check_state(cookie_val: str, returned: str) -> bool:
    if not cookie_val or not returned:
        return False
    try:
        return _state.loads(cookie_val, max_age=600) == returned
    except (BadSignature, SignatureExpired):
        return False


def gh_exchange(code: str, redirect_uri: str):
    """Swap the code for a profile. Returns (sub, email, name, avatar) or None."""
    if not (code and GH_ID and GH_SECRET):
        return None
    import requests
    try:
        tok = requests.post(GH_TOKEN, timeout=10,
                            headers={"Accept": "application/json"},
                            data={"client_id": GH_ID, "client_secret": GH_SECRET,
                                  "code": code, "redirect_uri": redirect_uri}).json()
        access = tok.get("access_token")
        if not access:
            return None
        h = {"Authorization": "Bearer " + access, "Accept": "application/vnd.github+json"}
        u = requests.get(GH_API + "/user", headers=h, timeout=10).json()
        if not u.get("id"):
            return None
        email = u.get("email")
        if not email:
            # the profile email is null unless the user made it public; the verified primary
            # from this endpoint is the one to trust
            try:
                for e in requests.get(GH_API + "/user/emails", headers=h, timeout=10).json():
                    if e.get("primary") and e.get("verified"):
                        email = e.get("email"); break
            except Exception:
                pass
        return (str(u["id"]), email, u.get("name") or u.get("login"), u.get("avatar_url"))
    except Exception:
        return None


# ---------- connecting a local instance to this one ----------
# A local install should not need its own Google project and GitHub app just to have an account.
# It borrows this deployment's: the browser is sent here to sign in, and comes back to a
# loopback URL carrying a short-lived code that the local process exchanges for a token. That
# is the flow `gh auth login` and `fly auth login` use, for the same reason.
_code = URLSafeTimedSerializer(_SECRET, salt="sf-cli-code")
CODE_MAX_AGE = 300          # five minutes between the redirect and the exchange


def cli_code(sub: str) -> str:
    return _code.dumps(sub)


def cli_read_code(code: str):
    try:
        return _code.loads(code, max_age=CODE_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None


def loopback_ok(cb: str) -> bool:
    """Only ever redirect a code to the machine the user is sitting at.

    This is the one control that matters in the whole flow. `cb` arrives as a query parameter,
    so without it anyone could send a victim to /auth/cli/start?cb=https://theirs.example and
    collect a code good for that victim's account.
    """
    from urllib.parse import urlparse
    try:
        u = urlparse(cb)
    except Exception:
        return False
    return (u.scheme == "http"
            and u.hostname in ("127.0.0.1", "localhost", "::1")
            and bool(u.port)
            and not u.query and not u.fragment)
