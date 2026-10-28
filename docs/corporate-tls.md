# Optional corporate TLS build setup

Normal Docker builds do not need this file or a custom certificate. If a trusted organizational proxy intercepts HTTPS, provide its trusted CA chain without disabling certificate verification.

On Windows, the included helper exports **public** certificates from Python's Windows-backed default trust store to an ignored artifact:

```powershell
python scripts/export_local_ca.py
```

Create the following **local, Git-ignored** `compose.override.yaml` in the repository root:

```yaml
services:
  order:
    build:
      secrets: [build_ca]
  inventory:
    build:
      secrets: [build_ca]
secrets:
  build_ca:
    file: ./artifacts/build-ca.pem
```

Then run the normal command:

```text
docker compose up --build
```

BuildKit mounts the CA bundle only for the dependency installation layer. The Dockerfile points `SSL_CERT_FILE` at that secret during the build; the bundle is not copied into the final image. This does not install or change system certificates. Use only roots your organization has authorized. On macOS/Linux, supply the appropriate approved PEM CA bundle at the same local path instead of running the Windows helper.

For host uv, recent versions support the system trust store:

```powershell
$env:UV_SYSTEM_CERTS = "true"
uv sync --frozen --python 3.13
```

The local override and certificate bundle should stay out of Git. To return to an ordinary build environment, remove the local override and rebuild. If the bundle is absent while the override remains, Compose reports a missing secret file.
