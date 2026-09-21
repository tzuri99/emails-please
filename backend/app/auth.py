"""Firebase authentication.

Auth is optional by configuration, not by accident. With no
FIREBASE_PROJECT_ID set the API runs open, which is what a local run and a
demo need. Set the project id and every mutating endpoint requires a valid
Firebase ID token.

The important consequence of turning it on is not the gate itself: it is
that the reviewer's identity stops being a string the client can type and
becomes the verified subject of a signed token. A review log is only worth
keeping if the names in it can be trusted.
"""
import logging

from fastapi import Depends, Header, HTTPException

from .config import settings

log = logging.getLogger(__name__)
_initialised = False


class Principal:
    """Who is making this request."""

    def __init__(self, uid, email, verified):
        self.uid = uid
        self.email = email
        self.verified = verified        # False when auth is switched off

    @property
    def display(self):
        return self.email or self.uid

    def __repr__(self):
        return f"Principal({self.display!r}, verified={self.verified})"


ANONYMOUS = Principal(uid="anonymous", email="", verified=False)


def enabled():
    return bool(settings.firebase_project_id)


def _init():
    """Initialise the Firebase Admin SDK once.

    Credentials come from the ambient environment
    (GOOGLE_APPLICATION_CREDENTIALS, or the managed identity when running on
    Azure), so no key material is read from settings or committed.
    """
    global _initialised
    if _initialised:
        return
    import firebase_admin
    if not firebase_admin._apps:
        firebase_admin.initialize_app(
            options={"projectId": settings.firebase_project_id})
    _initialised = True


def verify_token(token):
    """Return a Principal for a valid Firebase ID token, else raise 401."""
    _init()
    from firebase_admin import auth as fb_auth
    try:
        claims = fb_auth.verify_id_token(token)
    except Exception as exc:
        log.warning("rejected token: %s", type(exc).__name__)
        raise HTTPException(401, "invalid or expired sign-in") from exc
    return Principal(uid=claims.get("uid") or claims.get("sub", ""),
                     email=claims.get("email", ""), verified=True)


async def current_user(authorization: str = Header(default="")):
    """FastAPI dependency: the caller's identity.

    When auth is disabled this yields ANONYMOUS rather than failing, so the
    same code path serves an open demo and a locked-down deployment.
    """
    if not enabled():
        return ANONYMOUS
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "sign-in required")
    return verify_token(token)


RequireUser = Depends(current_user)
