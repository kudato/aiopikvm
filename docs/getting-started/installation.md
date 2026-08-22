# Installation

## Install from PyPI

=== "pip"

    ```bash
    pip install aiopikvm
    ```

=== "uv"

    ```bash
    uv add aiopikvm
    ```

=== "poetry"

    ```bash
    poetry add aiopikvm
    ```

## Dependencies

aiopikvm automatically installs the following dependencies:

| Package | Purpose |
|---------|---------|
| [httpx](https://www.python-httpx.org/) | Async HTTP client |
| [pydantic](https://docs.pydantic.dev/) v2 | Response models |
| [websockets](https://websockets.readthedocs.io/) | WebSocket client |

## Python version

aiopikvm requires **Python 3.13** or later.

## PiKVM version

aiopikvm targets **kvmd 4.206** or later. That is the version behind every
capture in the test suite, and the only one this client is checked against.

Nothing here inspects the device's version or refuses an older one. Calls
whose endpoints have not changed keep working; the rest fail the way kvmd
fails, and not all of those failures say anything — an event carrying a flag
an older kvmd does not read can be dropped inside its handler with no answer
of any kind, so the call looks like it landed. Upgrading the device is the
supported answer.
[`PiKVMWebSocket.version`][aiopikvm.PiKVMWebSocket.version] reports what a
device is actually running, once its first `loop` event has arrived.

## SSL certificates

PiKVM devices typically use **self-signed SSL certificates**. By default,
aiopikvm disables SSL verification (`verify_ssl=False`) — an untouched device
would be unreachable otherwise.

`verify_ssl` takes four things:

```python
from aiopikvm import PiKVM

# Nothing is verified — the default
async with PiKVM("https://pikvm.local", verify_ssl=False) as kvm: ...

# The system trust store, for a device with a publicly-signed certificate
async with PiKVM("https://pikvm.local", verify_ssl=True) as kvm: ...

# A CA bundle: the usual answer for a device re-issued a certificate from a
# private CA, and the only thing trusted when it is given
async with PiKVM("https://pikvm.local", verify_ssl="/etc/ssl/my-ca.pem") as kvm: ...

# A context you built yourself, for anything the three above cannot say
import ssl
context = ssl.create_default_context(cafile="/etc/ssl/my-ca.pem")
context.minimum_version = ssl.TLSVersion.TLSv1_3
async with PiKVM("https://pikvm.local", verify_ssl=context) as kvm: ...
```

A client certificate goes in `cert` — a combined PEM, a `(cert, key)` pair, or
`(cert, key, password)`:

```python
async with PiKVM(url, verify_ssl="/etc/ssl/my-ca.pem", cert=("/c.pem", "/c.key")) as kvm:
    ...
```

`cert` cannot be combined with a ready-made `ssl.SSLContext`: load it in with
`load_cert_chain()` and pass the context. Doing it for you would mean editing an
object you own.

Whatever you configure applies to the WebSocket too — the context is built once
and both halves of the client use it, so a socket cannot end up trusting more
than the REST calls do.

## Proxies

```python
# Reach the device through a proxy
async with PiKVM("https://pikvm.local", proxy="http://proxy.local:3128") as kvm: ...

# Ignore HTTPS_PROXY and the rest of the environment
async with PiKVM("https://pikvm.local", trust_env=False) as kvm: ...
```

Both apply to the WebSocket as well.

## Verify installation

```python
import aiopikvm
print(aiopikvm.__version__)
```
