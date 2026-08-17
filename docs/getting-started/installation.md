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

## SSL certificates

PiKVM devices typically use **self-signed SSL certificates**. By default, aiopikvm disables SSL verification (`verify_ssl=False`).

If your PiKVM has a valid certificate, you can enable verification:

```python
from aiopikvm import PiKVM

async with PiKVM("https://pikvm.local", verify_ssl=True) as kvm:
    ...
```

A certificate from a private CA needs that CA's bundle, and anything beyond
that — a client certificate, a pinned context — is configured with an
`ssl.SSLContext`. See [Configuration](configuration.md#tls).

## Verify installation

```python
import aiopikvm
print(aiopikvm.__version__)
```
