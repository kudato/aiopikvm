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
| `verify_ssl` | `bool \| str \| Path \| ssl.SSLContext` | `False` | Verify TLS: a flag, a CA bundle, or a context |
| `proxy` | `str \| None` | `None` | Proxy URL for requests and the WebSocket |
| `trust_env` | `bool` | `True` | Read proxy settings from the environment |
| `timeout` | `float` | `10.0` | Request timeout in seconds |
| `follow_redirects` | `bool` | `False` | Follow redirects instead of raising `RedirectError` |
| `http_client` | `httpx.AsyncClient \| None` | `None` | External httpx client |

## TLS

PiKVM ships a self-signed certificate, which is why `verify_ssl` is off by
default. A hardened device has a real one, and `verify_ssl` takes whatever it
was issued against.

```python
import ssl
from aiopikvm import PiKVM

# A public CA — the default trust store already has it
PiKVM("https://kvm.example.com", verify_ssl=True)

# A private CA: the path to its bundle, a PEM file or a c_rehash'd directory
PiKVM("https://kvm.example.com", verify_ssl="/etc/ssl/certs/internal-ca.pem")

# Anything else, a client certificate included
context = ssl.create_default_context(cafile="/etc/ssl/certs/internal-ca.pem")
context.load_cert_chain("/etc/ssl/client.pem", "/etc/ssl/client.key")
PiKVM("https://kvm.example.com", verify_ssl=context)
```

A path is read into an `ssl.SSLContext` when the client is built, so a *file*
that is missing or holds no certificate raises `ConfigurationError` there
rather than at the first request. A *directory* is not read: OpenSSL walks a
hashed store one certificate at a time, so an empty or unhashed one is only
found out about during the handshake. Either way the result is one context for
the whole client — `kvm.ws()` verifies against the same certificates the
requests do, unless an external `http_client` was supplied, which brings its
own TLS settings for the requests while the socket keeps using these.

!!! note
    `websockets` accepts no path at all, and httpx only accepts one under a
    `DeprecationWarning` it added in 0.28. Reading the bundle here is what
    keeps the two from being configured differently.

!!! warning "`verify_ssl=True` is the one setting the two do not share"
    It is passed on as it came, and each library then builds its own context.
    httpx verifies against certifi's roots, or against `SSL_CERT_FILE` and
    `SSL_CERT_DIR` when the environment names them and `trust_env` is on;
    `websockets` asks `ssl.create_default_context()`, which verifies against
    the system store, or against those same two variables whenever they are
    set — `trust_env` is httpx's idea, and OpenSSL has never heard of it.

    So the two agree on a machine that sets them and is trusted, and part
    company on one that sets neither, and again on one that sets them with
    `trust_env=False`. On a device whose CA is in one store and not the other,
    one half of the client connects and the other does not. Pass the bundle or
    a context instead, and both verify against the same certificates.

## Proxies

`proxy` covers both protocols: the requests and the WebSocket go through it.

```python
async with PiKVM("https://pikvm.local", proxy="http://proxy.local:3128") as kvm:
    await kvm.atx.get_state()
```

The URL is checked when the client is built, against what **both** libraries
accept, because a setting that only one of them takes would configure the
requests and break the socket. `websockets` is the stricter of the two, so it
sets the bar: a URL with no host, or carrying a path, a query, a fragment or a
username without a password is refused here, even though httpx would take it
and quietly work around it — sending the lone username as `username:`, in that
last case. So is a port outside 0–65535, which httpx accepts and only fails on
when it connects.

Three more are refused because the libraries would not use them alike:

- a `socks5://` proxy with no usable port. Left blank, or written as `:0`,
  httpcore fills in 1080 and `websockets` fills in 80, and the one setting
  reaches two different proxies without either of them saying so;
- a host that is not ASCII and the two spell differently. httpx encodes it by
  IDNA 2008 and `websockets` by IDNA 2003, which agree on `münchen.de` and part
  company over `faß.de` — `xn--fa-hia.de` for the requests, `fass.de` for the
  socket — while `☃.net` httpx refuses to encode at all and `websockets`
  encodes happily. Write such a host in its punycode form and both read it
  alike;
- credentials the two would send differently. httpx percent-decodes the user
  information and `websockets` sends it as written, so
  `http://user:p%40ss@proxy.local:3128` authenticates the requests as `p@ss`
  and collects a 407 on the socket.

The last two are decided by asking both libraries what they would aim at, so
only an actual disagreement is refused: an ordinary
`http://user:pass@proxy.local:3128` goes through untouched.

### What the environment sets is left to the libraries

Without a `proxy`, both of them read the environment — `HTTPS_PROXY`,
`WSS_PROXY`, `NO_PROXY` and the rest — which is what `trust_env=True` leaves
them doing. Pass `trust_env=False` to connect directly whatever the
environment says; in httpx that also switches off `SSL_CERT_FILE` and
`SSL_CERT_DIR`, which it reads only when `verify_ssl=True`.

Those variables are **not** held to the bar above, and the one-proxy-for-both
promise does not extend to them. Deciding which variable each library reads
for a given URL, and whether `NO_PROXY` exempts the host, would mean
reproducing two sets of rules that disagree with each other:

| | httpx | `websockets` |
|---|---|---|
| `NO_PROXY=pikvm.local:8443` for `https://pikvm.local:8443` | bypasses | bypasses |
| `NO_PROXY=.example.com` for `https://example.com` | uses the proxy | bypasses |
| `WS_PROXY` | never read | read for a `ws://` socket |
| `HTTP_PROXY` behind an `https://` device | mounted anyway | not read |

A guess at that lands wrong in both directions — refusing a setting that was
working, or missing one that was not — so no guess is made. What the client
does promise is that neither library's complaint escapes `PiKVMError`: a port
outside 0–65535 arrives from httpx as an `OverflowError` inside an
`ExceptionGroup` and becomes a `ConnectError` naming the variable, and from
`websockets` as a bare `ValueError` that becomes a `WebSocketError`.

A `socks5://` proxy also needs a package neither library depends on: `socksio`
for the requests, `python-socks` for the socket. Without them the client raises
`ConfigurationError` and names the missing one.

!!! note
    `socks5://` still resolves the device's name in two places: httpcore sends
    the name to the proxy, and `websockets` resolves it on this machine before
    connecting. On a name only the proxy's DNS knows, the requests work and the
    socket does not. `socks5h://` is the spelling both resolve remotely.

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
