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
| `verify_ssl` | `bool` | `False` | Verify SSL certificates |
| `timeout` | `float` | `10.0` | Request timeout in seconds |
| `http_client` | `httpx.AsyncClient \| None` | `None` | External httpx client |

## TOTP authentication

When TOTP is enabled on PiKVM, the code is concatenated to the password **without a separator**:

```python
# Password "secret" + TOTP code "123456" → sent as "secret123456"
async with PiKVM("https://pikvm.local", passwd="secret", totp="123456") as kvm:
    ...
```

## Session tokens

By default every request carries the `X-KVMD-User` and `X-KVMD-Passwd` headers,
and nothing else is needed. `kvm.auth.login()` exists for the other case: handing
a session to something that should not see the password.

```python
async with PiKVM("https://pikvm.local", user="admin", passwd="secret") as kvm:
    token = await kvm.auth.login("admin", "secret", expire=3600)
    # 64 hex characters; kvmd only ever sends it in a Set-Cookie header
```

kvmd tries four credential sources in order — the `X-KVMD-*` headers, the
`auth_token` cookie, HTTP Basic, then the unix socket peer — and the first one
**present** decides the request. A non-empty `X-KVMD-User` with a bad password is
refused outright rather than retried against the cookie, so a token only
authenticates a client that sends no credential headers at all:

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

    `kvm.ws()` is not covered by the token: the WebSocket authenticates with the
    `user` and `passwd` the client was built with, which are the defaults when
    the credentials live on an external `http_client`. Tracked in
    [#63](https://github.com/kudato/aiopikvm/issues/63).

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
    Resources can only be accessed after entering the async context. Accessing a resource before `__aenter__()` raises `PiKVMError`.
