"""Payment adapters (CASH07, stage A) — the provider-neutral interface a custodian's payment API would implement,
beside the trade routers (services/execution_routers.py: the same shape — name, capabilities, send, status — and the
same "not connected" discipline). The MANUAL adapter is the default route; cpsim04 adds ONE connected implementation —
for a SIMULATED custodian only (:class:`AltruistSimulatorPaymentAdapter`, key ``altruist_simulator``).

    ManualPaymentAdapter   "sends" by rendering the printable PAYMENT SHEET (services.execution_letter's variant) for
                           the operator to give the custodian; the instruction moves to SENT only when the operator
                           confirms the channel they used (services.payments.mark_sent). Always available.
    NotConnectedPaymentAdapter
                           every other custodian. Choosing it (an org that "enabled" a direct adapter for a custodian
                           no code connects) refuses EVERY call with the reason. No client code, no socket, no HTTP.

REGISTRY (:func:`adapter_for`): the source account → its custodian key (services.custody.registry.trade_custodian_key,
the key custodian_capabilities uses — cpsim04: ``altruist_simulator`` for an account crosswalked to a SIMULATED
Altruist-compatible custodian, set by services.payments.load_account) → the org's enabled direct adapter for that
custodian (org setting ``cash.payment_adapters``: a list of custodian keys) → else MANUAL. An enabled custodian with
no connected implementation resolves to NotConnected — refused with its reason, never silently re-routed, so a person
sees why. The simulator adapter is chosen only when the caller's D4 route (services.payment_custodian.simulator_route)
holds; otherwise the source routes MANUAL with that reason (``route_reason``).

THE CUSTODY CLIFF. Money movement never gets an agent, and nothing here sends money by itself: a connected adapter
(none exists) would still send only what a PERSON approved (services.payments: the S30 floor), on a person's action.

Adding a real adapter — the checklist (also docs/cash/CASH_PAYMENTS.md):
  1. Counsel on custody / standing letters of authorisation; the custodian's written API terms.
  2. A subclass of :class:`PaymentAdapter` in its own module: send (the custodian's transfer request, idempotent on the
     instruction id), status (the custodian's status → acknowledged / settled / rejected / returned).
  3. Credentials in Doppler only, read at call time, never logged; full account numbers never leave the custodian.
  4. ``connected = True`` only after a certification run against the custodian's sandbox, recorded with evidence; the
     custodian's ``payment_status_feed`` capability recorded 'yes' with that evidence.
  5. The verify proves manual is still offered and still the default.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

MANUAL = "manual"
ORG_SETTING = "cash.payment_adapters"
SIMULATOR_KEY = "altruist_simulator"       # cpsim04 — the SIMULATED Altruist-compatible custodian's payment API
CUSTODIAN_API = "custodian_api"           # cpsim04 — recorded ONLY by "Send to custodian" (never "Confirm sent")
CHANNEL_LABELS = {
    "custodian_portal": "Custodian portal (uploaded / keyed by the operator)", "email": "Email to the custodian",
    "secure_message": "Custodian secure message", "fax": "Fax", "phone": "Phone (recorded line)",
    "in_person": "In person", "other": "Other", CUSTODIAN_API: "Custodian API (simulated)",
}
#: the channels a PERSON may confirm with "Confirm sent" (the manual route) — never the custodian API
MANUAL_CHANNELS = tuple(k for k in CHANNEL_LABELS if k != CUSTODIAN_API)
NOT_CONNECTED_REASON = ("no payment adapter is connected for {custodian} — none is contracted (cash07 builds the "
                        "interface only: no client code, no network). Use the manual route: download the payment sheet, "
                        "send it to the custodian and confirm the channel")


class PaymentAdapterNotConnected(RuntimeError):
    """The adapter exists only as an interface (no contract, no connection)."""


@dataclass
class SendResult:
    accepted: bool
    adapter: str
    reference: str | None = None
    document: bytes | None = None          # the manual adapter's sheet (never stored)
    file_name: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class StatusResult:
    known: bool
    status: str | None = None              # acknowledged / settled / rejected / returned (a connected adapter only)
    note: str | None = None
    detail: dict | None = None             # cpsim04 — the custodian's full status record (events, dates, codes)


class PaymentAdapter(ABC):
    key: str = ""
    label: str = ""
    connected: bool = False
    #: the capability keys the adapter itself provides (custodian_capabilities keys)
    capabilities: tuple[str, ...] = ()

    @abstractmethod
    async def send(self, instruction: dict, **kw) -> SendResult: ...

    @abstractmethod
    async def status(self, reference: str | None) -> StatusResult: ...


class ManualPaymentAdapter(PaymentAdapter):
    """Renders the payment sheet. ``send`` returns the PDF; the caller (services.payments.sheet) logs the rendering,
    and the instruction is SENT only when the operator confirms the channel (services.payments.mark_sent)."""

    key = MANUAL
    label = "Manual — print the payment sheet and send it to the custodian"
    connected = True
    capabilities = ("payment_sheet",)
    #: CPSIM04 FIX 1 — why the simulated custodian's route was NOT taken (D4's first missing condition), when D4 was
    #: evaluated; None for a payment D4 never applied to. The page shows the same reason (payment_custodian.panel).
    route_reason: str | None = None

    async def send(self, instruction: dict, *, org_name: str = "", prepared_by: str = "",
                   disclosure: str | None = None, **kw) -> SendResult:
        from services.execution_letter import build_payment_sheet

        pdf = build_payment_sheet(instruction, org_name=org_name, prepared_by=prepared_by, disclosure=disclosure)
        return SendResult(accepted=True, adapter=self.key, reference=None, document=pdf,
                          file_name=f"payment-sheet-{str(instruction['id'])[:8]}.pdf",
                          notes=["the operator sends the sheet and confirms the channel; nothing was transmitted"])

    async def status(self, reference: str | None) -> StatusResult:
        return StatusResult(known=False, note="the manual route has no status feed — confirm acknowledged / settled "
                                              "by hand from the custodian's confirmation")


class NotConnectedPaymentAdapter(PaymentAdapter):
    """The placeholder for any custodian's own payment API: every call refuses, with the reason."""

    connected = False

    def __init__(self, custodian_key: str):
        self.key = custodian_key
        self.label = f"{custodian_key} payment API — not connected"
        self.reason = NOT_CONNECTED_REASON.format(custodian=custodian_key or "this custodian")

    async def send(self, instruction: dict, **kw) -> SendResult:
        raise PaymentAdapterNotConnected(self.reason)

    async def status(self, reference: str | None) -> StatusResult:
        raise PaymentAdapterNotConnected(self.reason)


class AltruistSimulatorPaymentAdapter(PaymentAdapter):
    """cpsim04 D5 — the SIMULATED Altruist-compatible custodian's transfers API (docs/cpsim/ALTRUIST_CONTRACT.md §9,
    every field ASSUMED), through services/altruist_transfers.py. The checklist above, for a simulator: (1) no real
    custodian, so no API terms — simulated only, never real Altruist; (2) send is idempotent on the instruction id
    (``client_transfer_id``); (3) the access token comes from the org's simulator connection (Doppler-held secrets
    only); (4) ``connected`` because it is certified against itself by the conformance ``transfers`` group, and the
    capability rows for ``altruist_simulator`` are written only by the operator's loader; (5) manual stays offered and
    the default. Reached ONLY through services.payment_custodian's person actions — never by itself.

    ``send`` / ``status`` take a SESSION (services.altruist_transfers.transfer_session) read by the caller, which then
    released its database connection — no transaction is held open across the HTTP call."""

    key = SIMULATOR_KEY
    label = "Simulated custodian — transfers API (SIMULATED)"
    connected = True
    capabilities = ("ach", "wire", "journal", "payment_status_feed")

    def __init__(self, route: dict | None = None):
        self.route = route or {}

    async def send(self, instruction: dict, *, session: dict | None = None, body: dict | None = None,
                   transport=None, **kw) -> SendResult:
        from services import altruist_transfers as AT

        if session is None or body is None:
            raise PaymentAdapterNotConnected("the simulated custodian is reached only through Send to custodian")
        http_status, transfer = await AT.submit_with_session(session, transfer=body, transport=transport)
        res = SendResult(accepted=bool(transfer.get("transfer_id")), adapter=self.key,
                         reference=transfer.get("transfer_id"),
                         notes=[f"HTTP {http_status}", f"custodian status {transfer.get('status')}"])
        res.transfer = transfer          # the custodian's record (the caller checks it is this instruction's)
        res.http_status = http_status
        return res

    async def status(self, reference: str | None, *, session: dict | None = None, transport=None) -> StatusResult:
        from services import altruist_transfers as AT

        if not reference or session is None:
            return StatusResult(known=False, note="no custodian transfer to read")
        t = await AT.fetch_with_session(session, transfer_id=reference, transport=transport)
        st = t.get("status")
        return StatusResult(known=bool(t), status=st if st in ("acknowledged", "settled", "rejected", "returned")
                            else None, note=t.get("reject_reason") or t.get("return_reason"), detail=t)


#: connected implementations by custodian key — cpsim04: the SIMULATED custodian only (the manual adapter is the route,
#: not a custodian's; no real custodian's payment API is connected)
_CONNECTED: dict[str, type[PaymentAdapter]] = {SIMULATOR_KEY: AltruistSimulatorPaymentAdapter}


def manual() -> ManualPaymentAdapter:
    return ManualPaymentAdapter()


def registry() -> dict[str, dict]:
    """What exists: the manual route (connected) and, for any custodian, a not-connected placeholder."""
    return {MANUAL: {"key": MANUAL, "label": ManualPaymentAdapter.label, "connected": True, "reason": None},
            **{k: {"key": k, "label": f"{k} payment API", "connected": True, "reason": None} for k in _CONNECTED}}


def custodian_key(account: dict) -> str:
    """The payment custodian key: ``altruist_simulator`` when services.payments resolved the account to a SIMULATED
    custodian (``payment_custodian``), else exactly the trade key as before."""
    from services.custody.registry import trade_custodian_key

    return account.get("payment_custodian") or trade_custodian_key(account.get("custodian_code"))


def enabled_from_setting(value) -> set[str]:
    if isinstance(value, list):
        return {str(v).strip().lower() for v in value if str(v).strip()}
    if isinstance(value, str) and value.strip():
        return {x.strip().lower() for x in value.split(",") if x.strip()}
    return set()


def adapter_for(account: dict, enabled: set[str] | None, route: dict | None = None) -> PaymentAdapter:
    """account → custodian → the org's enabled adapter for it → else manual. An enabled custodian with no connected
    implementation is NotConnected (refuses with the reason) — never a silent fallback. cpsim04: the SIMULATED
    custodian's adapter only when ``route`` (services.payment_custodian.simulator_route) holds; otherwise manual, with
    the route's reason on ``route_reason`` (today's behaviour, said why)."""
    key = custodian_key(account)
    if key == SIMULATOR_KEY:
        if key in (enabled or set()) and route is not None and route.get("ok"):
            return AltruistSimulatorPaymentAdapter(route)
        m = manual()
        m.route_reason = (route or {}).get("reason") or ("the simulated custodian's payment adapter is not enabled for "
                                                          "this organization")
        return m
    if key and key != MANUAL and key in (enabled or set()):
        impl = _CONNECTED.get(key)
        return impl() if impl is not None else NotConnectedPaymentAdapter(key)
    m = manual()
    if route is not None and not route.get("ok"):
        m.route_reason = route.get("reason")    # CPSIM04 FIX 1: manual, with the D4 reason (e.g. real_crosswalk)
    return m


async def enabled_for_org(conn, org_id) -> set[str]:
    from services.org_settings import get_setting

    return enabled_from_setting(await get_setting(conn, str(org_id), ORG_SETTING))
