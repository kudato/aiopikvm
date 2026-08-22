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

PiKVM devices typically use **self-signed SSL certificates**. By default, aiopikvm disables SSL verification (`verify_ssl=False`).

If your PiKVM has a valid certificate, you can enable verification:

```python
from aiopikvm import PiKVM

async with PiKVM("https://pikvm.local", verify_ssl=True) as kvm:
    ...
```

## Verify installation

```python
import aiopikvm
print(aiopikvm.__version__)
```
