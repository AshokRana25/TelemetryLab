# Validation record

Validated on **28 October 2026 (Asia/Calcutta)** in Windows with Docker Desktop's Linux engine. These are observed results, not projected results.

| Check | Actual result |
| --- | --- |
| Compose configuration | `docker compose config --quiet` passed. All seven services built and reached healthy status. |
| Python lint and formatting | Ruff check and format check passed against project source. |
| Application/telemetry tests | **19 passed**, using the committed lockfile and Python 3.13.9 inside a temporary Linux container. |
| Browser-logic tests | **3 passed** with Node's test runner. Verified exactly 12 requests, maximum concurrency three, double-click suppression, reset, and disabled demo behavior. |
| Browser JavaScript syntax | `node --check services/order/static/app.js` passed. |
| Live successful checkout | HTTP 201; Tempo returned both services, the shared trace ID, and Inventory's SERVER span parented to Order's HTTP CLIENT span. |
| Live slow checkout | HTTP 201; `inventory.check` contained the configured delay; the duration histogram increased. |
| Live failed checkout | HTTP 502 from Order; Inventory failure trace contained ERROR status and an exception event. Error and failure metrics increased. |
| Live correlated logs | Loki returned logs from both services filtered by each scenario's actual trace ID, with span metadata. |
| Live recovery | A subsequent normal checkout succeeded with a distributed trace and correlated logs. |
| Error-ratio correction | Known status counters are initialized to zero. The final smoke test observed an error ratio greater than zero and at most one after an injected failure. Grafana's percent axis is explicitly 0–100%. |
| Dashboard queries | Every provisioned PromQL target returned finite live data through Grafana's datasource proxy. The logs query returned real application records. |
| Visual inspection | Checkout layout and the Grafana dashboard were observed in Edge using the native computer-use integration. |
| Saved screenshots | **Not captured to repository files.** Computer Use was stopped with the physical Escape key; UI automation was stopped. Instructions are in `screenshots.md`. |
| GitHub Actions | The local validation above does not establish a hosted CI result. Check the [Actions history](https://github.com/AshokRana25/TelemetryLab/actions) for the result of each published commit. |

The last full smoke command was `python scripts/smoke.py` against the running localhost stack. It also has a container command documented in the README. Local raw build and smoke logs are under ignored `artifacts/`; they are diagnostic output, not required project inputs.

## Environment-specific setup

Docker Desktop initially was stopped and was started for validation. Corporate TLS interception caused package certificate failures in the Linux build. Public trusted certificates were exported from the Windows trust store into ignored `artifacts/build-ca.pem` and supplied using a Docker BuildKit secret. No private keys were exported and TLS verification stayed enabled.

A local **ignored** `compose.override.yaml` attaches that build secret to Order and Inventory. It remains in this workspace so the default startup command works on this machine. It is not part of the portable repository, and normal networks need no override. See `corporate-tls.md` for a reproducible opt-in setup.

Windows Python wheel downloads were interrupted, so host-side Python dependency installation was **not** completed. Python lint and tests were instead run with the exact locked dependencies in Linux, matching the runtime image. The temporary test container was removed after verification.

## Remaining scope limits

The browser controls' logic is covered by tests; a complete graphical click-through, mobile viewport inspection, and saved trace/log screenshots remain unverified. API-level storage, propagation, correlation, and dashboard query behavior were verified live. No macOS/ARM execution or production load/HA validation was performed. Resource guidance in the README is an estimate, and no benchmark claims are made.
