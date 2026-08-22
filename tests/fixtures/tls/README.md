# TLS test material

A self-signed certificate and its key, generated once for the tests in
`tests/test_tls.py`:

```bash
openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout client.key -out client.crt \
  -days 36500 -subj "/CN=aiopikvm-test"
```

Unlike everything in `../data`, this is **not** captured from a device and is
not a contract fixture. It exists because `ssl.create_default_context(cafile=…)`
and `SSLContext.load_cert_chain()` need real PEM files to say anything, and the
suite has no certificate library to make one at runtime.

It is a throwaway. It authenticates nothing, was never presented to anything,
and the key is public by construction — it is in this repository. Never point a
device at it.
