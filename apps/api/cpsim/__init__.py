"""cpsim — the COUNTERPARTY SIMULATOR (cpsim01). SIMULATED counterparties only.

A semi-independent package that plays the other side of Hollisworks (custodians and banks) so the app can be tested and
demoed end to end. Design: docs/cpsim/COUNTERPARTY_SIMULATOR_DESIGN_v1.md; notes: docs/cpsim/CPSIM01_NOTES.md.

Boundaries (scripts/cpsim_boundary_check.py enforces them):
  * mounted at /cpsim on the existing API (main.py ``app.mount``) — its own FastAPI sub-app, its own auth (its fake
    ``cpsim_`` keys, never a Hollisworks session);
  * its own database access: role ``cpsim_app`` entered with ``SET LOCAL ROLE`` inside every transaction (cpsim/db.py);
    it reads the security master + prices and writes ONLY ``cpsim.*``;
  * imports nothing from ``services/`` except the two pure shared helpers ``services.us_business_days`` and
    ``services.bitemporal`` — so it can be lifted into its own service by copying those two files.

Every simulator response carries ``X-Counterparty-Simulated: true``.
"""

SIMULATED_HEADER = "X-Counterparty-Simulated"
SIMULATED_VALUE = "true"
MOUNT_PATH = "/cpsim"
