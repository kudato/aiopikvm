# Configuration

## Constructor parameters

```python
from aiopikvm import PiKVM

kvm = PiKVM(
    "https://pikvm.local",
    user="admin",
    passwd="secret",
    totp="123456",
    verify_ssl=False,
    timeout=10.0,
)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `url` | `str` | *(required)* | PiKVM base URL |
| `user` | `str` | `"admin"` | Username for authentication |
| `passwd` | `str` | `""` | Password for authentication |
| `totp` | `str \| None` | `None` | TOTP code for two-factor auth |
| `auth` | `AuthMode` | `"headers"` | Which credential to send — see [below](#authentication-modes) |
| `session_expire` | `int` | `0` | Lifetime of a session `auth="cookie"` opens; `0` asks for unlimited |
| `verify_ssl` | `VerifyTypes` | `False` | What to trust: `bool`, a CA bundle path, or an `ssl.SSLContext` |
| `cert` | `CertTypes \| None` | `None` | Client certificate to present |
| `proxy` | `str \| None` | `None` | Proxy URL to reach the device through |
| `trust_env` | `bool` | `True` | Read proxy settings from the environment |
| `timeout` | `float` | `10.0` | Request timeout in seconds |
| `http_client` | `httpx.AsyncClient \| None` | `None` | External httpx client |

## Authentication modes

kvmd tries four credential sources in order — the `X-KVMD-*` headers, the
`auth_token` cookie, HTTP Basic, then the unix socket peer — and the first one
**present** decides the request. It never falls through after a wrong password,
so sending more than one credential is not a fallback: the earlier one wins and
the rest are never looked at. `auth` picks exactly one.

```python
# The default: X-KVMD-User and X-KVMD-Passwd on every request
async with PiKVM(url, user="admin", passwd="secret") as kvm: ...

# Authorization: Basic — what Redfish tooling and ordinary HTTP clients expect
async with PiKVM(url, user="admin", passwd="secret", auth="basic") as kvm: ...

# A session token: log in once, then send only the cookie
async with PiKVM(url, user="admin", passwd="secret", auth="cookie") as kvm: ...
```

`"headers"` and `"basic"` cost the same. kvmd runs its auth plugin — PAM, or a
read of `htpasswd` — on **every** request and writes a line to its log for each
one. `"cookie"` does not: kvmd looks the token up in a table it keeps in memory.
Measured against kvmd 4.206, ten `/api/auth/check` calls:

| Mode | `Authorized user` lines in kvmd's log |
|---|---|
| `"headers"` | 10 |
| `"cookie"` | 0 |

That is the mode for anything that polls.

`auth="cookie"` logs in by itself on the first request that needs a token, and
again if kvmd refuses the one it holds — a session expires, or another logout of
the same user drops it. It gives up after one retry, so a wrong password fails
as a wrong password rather than looping.

!!! warning
    A session opened this way outlives the client. kvmd cannot close one
    session — `logout()` ends **every** session that user has — so the tidy
    way to avoid leaving one behind is `session_expire`:

    ```python
    async with PiKVM(url, passwd="secret", auth="cookie", session_expire=3600) as kvm:
        ...
    ```

    Leave it at `0` for a long-lived client, where one session is the point.

`kvm.ws()` carries whichever credential the mode says. Under `auth="cookie"` the
token has to exist by the time the socket is **opened** — neither `ws()` nor the
handshake logs in — so make a request first, or call `login()` yourself.
Building the socket earlier is fine: the token is read when the handshake goes
out, so this works, and so does reopening the socket after kvmd replaced the
session under it.

```python
async with PiKVM(url, passwd="secret", auth="cookie") as kvm:
    socket = kvm.ws()              # no session yet
    await kvm.auth.login("admin", "secret")
    async with socket as ws:       # carries the token that login minted
        ...
```

## TOTP authentication

When TOTP is enabled on PiKVM, the code is concatenated to the password
**without a separator** — kvmd reads the last six characters of what it is sent
as the code and the rest as the password.

A code is good for one thirty-second step, and kvmd allows the neighbouring two,
so a literal one stops working about a minute after it was read:

```python
# Fine for a script that runs and exits
async with PiKVM("https://pikvm.local", passwd="secret", totp="123456") as kvm:
    ...
```

Pass `TOTP` instead — or any zero-argument callable returning a string — and the
code is worked out per request:

```python
from aiopikvm import PiKVM, TOTP

# The secret is what `kvmd-totp show` prints on the device
async with PiKVM("https://pikvm.local", passwd="secret", totp=TOTP(secret)) as kvm:
    ...   # still authenticating an hour later
```

A callable also covers the case where the secret is not yours to hold — a
hardware token, a secrets manager, another process:

```python
async with PiKVM(url, passwd="secret", totp=lambda: vault.read("pikvm/totp")) as kvm:
    ...
```

!!! note
    `TOTP` implements RFC 6238 with the parameters kvmd fixes by running
    `pyotp.TOTP(secret)` with its defaults: HMAC-SHA1, six digits, a
    thirty-second step. It is checked against the RFC's own published test
    vectors.

## Session tokens

`auth="cookie"` above manages a session for you. `kvm.auth.login()` is for the
other case: handing a session to something that should not see the password.

```python
async with PiKVM("https://pikvm.local", user="admin", passwd="secret") as kvm:
    token = await kvm.auth.login("admin", "secret", expire=3600)
    # 64 hex characters; kvmd only ever sends it in a Set-Cookie header
```

A token only authenticates a client that sends no credential headers at all —
`auth="cookie"` is one, and so is an external `httpx.AsyncClient` carrying
nothing but the cookie:

```python
import httpx

async with httpx.AsyncClient(base_url="https://pikvm.local", verify=False) as http:
    http.cookies.set("auth_token", token)

    async with PiKVM("https://pikvm.local", http_client=http) as kvm:
        await kvm.auth.check()    # authenticated by the token alone
        await kvm.auth.logout()   # see the warning below before calling this
```

!!! note
    `expire=0` asks for an unlimited session, and kvmd caps every session at the
    device-wide limit from its own config either way. An expired or logged-out
    token raises `AuthError`.

    With an external `http_client` the WebSocket is not covered by the token:
    it authenticates with the `user` and `passwd` the client was built with,
    which are the defaults when the credentials live on that client instead.
    Use [`auth="cookie"`](#authentication-modes) to have both go by session.

!!! warning
    `logout()` closes **every** session belonging to that user, not only the one
    whose token is passed — kvmd looks up the token's owner and drops all of
    them. Logging out a token your script created also signs the same account
    out of the PiKVM web UI.

## Client lifecycle

### Async context manager (recommended)

```python
async with PiKVM("https://pikvm.local", user="admin", passwd="admin") as kvm:
    await kvm.atx.get_state()
# Client is automatically closed here
```

### Explicit close

```python
kvm = PiKVM("https://pikvm.local", user="admin", passwd="admin")
await kvm.__aenter__()
try:
    await kvm.atx.get_state()
finally:
    await kvm.aclose()
```

!!! warning
    Always close the client when done. `aclose()` releases the HTTP connection and clears cached resources.

### A closed client stays closed

`aclose()` is final, the same as it is on `httpx.AsyncClient`. Afterwards every
resource, `base_url`, `cookies`, `request()` and `ws()` raise, and the client
cannot be reopened:

```python
kvm = PiKVM("https://pikvm.local", user="admin", passwd="admin")
async with kvm:
    await kvm.atx.get_state()

await kvm.atx.get_state()   # PiKVMError: this client has been closed
async with kvm:             # ConfigurationError: cannot reopen
    ...
```

Entering the same client twice is refused for the same reason — the inner
block's exit would close the connection the outer one is still using. Build a
new `PiKVM` for a new session; it is a thin object around the HTTP client.

Calling `aclose()` a second time does nothing, so a `finally` that closes an
already-closed client is safe.

## External httpx client

You can provide your own `httpx.AsyncClient` for advanced use cases (custom middleware, shared connection pools, etc.):

```python
import httpx
from aiopikvm import PiKVM

async with httpx.AsyncClient(verify=False, timeout=30.0) as http:
    async with PiKVM("https://pikvm.local", http_client=http) as kvm:
        state = await kvm.atx.get_state()
```

!!! note
    When an external client is provided, PiKVM does **not** close it on exit. The caller is responsible for managing the client's lifecycle.

    It still lets go of it, though: the `PiKVM` object is closed either way,
    and none of its resources work afterwards. The alternative is worse — a
    `PiKVM` that keeps serving requests through an `httpx.AsyncClient` its
    owner is free to have closed in the meantime.

    Its `timeout` is the one every HTTP call uses, streaming calls included:
    those lift the read timeout, since a stream has no end to wait for, and
    keep the connect, write and pool values the injected client was built
    with. The sockets are the exception — `ws()`, `media_ws()` and `webrtc()`
    do not go through httpx at all, and take their `open_timeout` and
    `close_timeout` from the `timeout` passed to `PiKVM` itself.

## Resource access

Resources are accessed as properties on the `PiKVM` instance. They are lazily initialized on first access:

```python
async with PiKVM("https://pikvm.local", user="admin", passwd="admin") as kvm:
    # Resources are created on first access
    atx = kvm.atx        # ATXResource
    hid = kvm.hid        # HIDResource
    msd = kvm.msd        # MSDResource
    gpio = kvm.gpio      # GPIOResource
    streamer = kvm.streamer  # StreamerResource
    switch = kvm.switch  # SwitchResource
    redfish = kvm.redfish    # RedfishResource
    prometheus = kvm.prometheus  # PrometheusResource
    auth = kvm.auth      # AuthResource
```

!!! warning
    Resources can only be accessed after entering the async context, and until
    the client is closed. Accessing one before `__aenter__()` or after
    `aclose()` raises `PiKVMError`, and the message says which of the two it
    was.
