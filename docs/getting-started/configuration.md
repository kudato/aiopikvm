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
