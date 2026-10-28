"""One module per provider. ``build_providers()`` is THE code registry: a
provider key (a data_providers row) → its module's Provider instance. Adding a
vendor = a new module here + one line below + a registry row + a rank entry
(docs/SECURITY_DATA_DESIGN.md, "Add a vendor")."""

from __future__ import annotations

from typing import Any, Mapping


def build_providers(*, transports: Mapping[str, Any] | None = None, environ: Mapping[str, str] | None = None) -> dict:
    """Constructing the registry makes no network call. ``transports`` injects
    a fake transport per provider key (tests use recorded fixtures)."""
    from services.security_data.providers import (adviser_entry, csv_upload, custodian, fred, issuer_file,
                                                  platform_entry, sec_nport, vendor_stub)

    t = dict(transports or {})
    return {
        "custodian": custodian.CustodianProvider(),
        "sec_nport": sec_nport.NportProvider(http=t.get("sec_nport")),
        "ssga_spdr": issuer_file.IssuerFileProvider("ssga_spdr", http=t.get("ssga_spdr")),
        "fred": fred.FredProvider(transport=t.get("fred"), environ=environ),
        "csv_upload": csv_upload.CsvUploadProvider(),
        "adviser_entry": adviser_entry.AdviserEntryProvider(),
        "platform_entry": platform_entry.PlatformEntryProvider(),
        "vendor_slot": vendor_stub.VendorStubProvider("vendor_slot", transport=t.get("vendor_slot")),
    }
