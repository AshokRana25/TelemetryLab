"""Export public Windows trust roots for a local, ignored Docker build secret. No private keys."""

import ssl
from pathlib import Path

destination = Path("artifacts/build-ca.pem")
destination.parent.mkdir(exist_ok=True)
certificates = ssl.create_default_context().get_ca_certs(binary_form=True)
destination.write_text(
    "".join(ssl.DER_cert_to_PEM_cert(cert) for cert in certificates), encoding="ascii"
)
print(f"Exported {len(certificates)} public trust certificates to {destination}")
