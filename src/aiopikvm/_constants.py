"""Default constants and the vocabularies they belong to."""

from typing import Literal

DEFAULT_TIMEOUT = 10.0
DEFAULT_VERIFY_SSL = False
DEFAULT_FOLLOW_REDIRECTS = False

type AuthMode = Literal["headers", "basic", "cookie"]
"""Which credential [`PiKVM`][aiopikvm.PiKVM] sends.

kvmd tries four sources in a fixed order — the ``X-KVMD-*`` headers, the
``auth_token`` cookie, HTTP Basic, then the unix socket peer — and the first
one *present* decides the request. It never falls through to the next after
a wrong password, so sending more than one credential is not a fallback: it
picks the earlier one and hides the rest.

``"headers"``
    ``X-KVMD-User`` and ``X-KVMD-Passwd``. kvmd's own web UI and this client
    have always sent these, and they are the default here.

``"basic"``
    ``Authorization: Basic``. The same credentials at the same cost — kvmd
    runs the auth plugin either way — spelled the way Redfish tooling and
    ordinary HTTP clients expect. kvmd splits the decoded pair on the first
    ``:``, so a password containing one cannot be sent this way.

``"cookie"``
    A session token, obtained by logging in once. kvmd looks it up in a
    table it holds in memory instead of calling the auth plugin, so it does
    not run PAM or read ``htpasswd`` on every request, and its log gets one
    authorization line per session rather than one per call. That is the
    mode for anything that polls.
"""

DEFAULT_AUTH: Literal["headers"] = "headers"
