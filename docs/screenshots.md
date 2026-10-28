# Authentic screenshots

**Missing captures:** checkout, dashboard, slow-trace, and correlated-log image files. The stack ran successfully and the checkout/dashboard were visually inspected, but Computer Use was stopped with Escape before screenshots were saved. No fabricated captures are included. Full check results are recorded in [validation.md](validation.md).

With Compose healthy, open the lab at 1440×1000 or a similar desktop viewport:

1. Place a healthy order and capture `docs/images/checkout.png`, including its real response and trace ID.
2. Run healthy, slow, and failure bursts; wait for two scrapes. Open the Grafana dashboard, choose the last 15 minutes, and capture `docs/images/dashboard.png` with actual latency and error data.
3. Follow a slow request's trace link. Expand Order and Inventory until `inventory.check` is visible with its actual duration. Capture `docs/images/slow-trace.png`.
4. Open the matching log stream and expand a line to show `trace_id`, `span_id`, and the internal Tempo link. Capture `docs/images/correlated-logs.png`.

Record the scenario and date with any committed image. Avoid private browser tabs, other applications, or unrelated data. Screenshots are illustrative evidence from one run; they are not performance benchmarks. The automated smoke test is the repeatable verification.
