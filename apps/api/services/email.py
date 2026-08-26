"""Transactional email — the single AWS SES choke point.

WHY THIS MODULE EXISTS
──────────────────────────────────────────────────────────────────────────────
Before this sprint there was NO email-sending code anywhere in the API. No SES,
SMTP, SendGrid, Postmark or Resend client existed in ``services/`` or
``routers/``. That blocked an already-shipped feature: ``POST /admin/invites``
mints a real invite and hands the admin an ``enrollment_url`` to share BY HAND,
because there was no way to actually mail it.

This module is that missing transport. It is deliberately shaped like
``services.textract`` (one shared client, one error taxonomy) and like
``services.portfolio_altruist`` (an explicit :func:`credential_state` gate and a
:func:`probe` that makes ONE real authenticated call), because both patterns are
already established here and both exist to stop exactly the failure mode this
integration is most exposed to: reporting "configured" when it is not.

THE STANDING RULE OF THIS FILE — NEVER FAIL SILENTLY
──────────────────────────────────────────────────────────────────────────────
A send that cannot happen raises :class:`EmailBlocked` carrying a specific,
actionable reason naming the real gap (which IAM principal, which action, which
AWS-side change fixes it). It never returns a falsy "oh well" that a caller can
mistake for success, and it never degrades to "just return the URL" on its own.
The invite path DOES still hand back a shareable URL — but only after recording
an explicit, visible ``email_delivery`` status saying delivery did not happen
and why. A silent fallback and an announced fallback are different things and
only the second one is honest.

LIVE STATE AS OF THIS SPRINT (2026-08-26) — measured, not assumed
──────────────────────────────────────────────────────────────────────────────
The deployment's AWS credentials are valid and live (``sts:GetCallerIdentity``
succeeds) but resolve to ``arn:aws:iam::…:user/Texttrac-Ripasso`` — the SAME
Textract-only principal as before. A real authorization probe on the send action
returns::

    AccessDeniedException: User '…:user/Texttrac-Ripasso' is not authorized to
    perform 'ses:SendEmail'

and ``ses:GetAccount`` / ``ses:GetAccountSendingEnabled`` /
``sesv2:ListEmailIdentities`` are ALL denied too, so the account's sandbox state
cannot even be READ from here. Those are two independent blockers: granting
``ses:SendEmail`` alone would still restrict delivery to verified addresses if
the account is in sandbox. Both are AWS-console actions outside this codebase.
See docs/PROJECT_STATUS.md for the named action items.

Credentials + region come from the standard AWS env chain, exactly as
``services.textract`` proved. This module NEVER prints, logs or hardcodes a
secret, and never logs a recipient address in full (see :func:`redact_email`).
boto3 is synchronous — async callers MUST invoke via ``run_in_threadpool``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from html import escape

# ── Configuration ──────────────────────────────────────────────────────────

#: The sender address SES will be asked to send FROM. Must be an identity that
#: is verified in this AWS account (an address or a domain), or every send is
#: rejected regardless of IAM. Deliberately a separate var from the AWS creds:
#: "we have AWS access" and "we have a verified sender" are different facts and
#: an operator needs to know which one is missing.
FROM_ENV_VAR = "SES_FROM_EMAIL"
FROM_NAME_ENV_VAR = "SES_FROM_NAME"

#: Optional SES configuration set (bounce/complaint event routing). Not required.
CONFIG_SET_ENV_VAR = "SES_CONFIGURATION_SET"

#: AWS region is satisfied by EITHER var — the alias is why this is a tuple of
#: alternatives rather than a flat required-var list like Altruist's.
_REGION_ENV_VARS = ("AWS_DEFAULT_REGION", "AWS_REGION")

#: Every variable that must resolve for a send to be even attempted.
EMAIL_ENV_VARS: tuple[str, ...] = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    _REGION_ENV_VARS[0],
    FROM_ENV_VAR,
)

#: Error codes meaning "the caller is not ALLOWED to do this" — an IAM gap the
#: operator fixes in the AWS console, never something a retry will resolve.
_IAM_CODES = frozenset({
    "AccessDenied", "AccessDeniedException", "UnauthorizedException",
    "AuthFailure", "InvalidClientTokenId", "UnrecognizedClientException",
    "SignatureDoesNotMatch", "InvalidSignatureException",
    "IncompleteSignatureException", "MissingAuthenticationToken",
    "InvalidAccessKeyId", "ExpiredToken", "ExpiredTokenException",
})

#: Error codes meaning "allowed, but SES refused this particular message" —
#: overwhelmingly the sandbox / unverified-identity case.
_IDENTITY_CODES = frozenset({
    "MessageRejected", "MailFromDomainNotVerifiedException",
    "NotFoundException", "BadRequestException",
})

#: Error codes meaning the whole account's sending is switched off.
_PAUSED_CODES = frozenset({
    "AccountSendingPausedException", "SendingPausedException",
})


class EmailBlocked(RuntimeError):
    """Email cannot be sent. Carries the specific, actionable reason.

    A distinct exception type rather than a bool, for the same reason
    :class:`services.portfolio_altruist.AltruistBlocked` is one: a caller cannot
    ignore it by accident, and the reason travels with the failure instead of
    being reconstructed by whatever catches it.

    ``gap`` is the machine-readable classification — ``"credentials"``,
    ``"iam"``, ``"identity_or_sandbox"``, ``"paused"``, ``"transport"`` or
    ``"unknown"`` — so a caller can branch without regex-ing the prose.
    """

    def __init__(self, message: str, *, gap: str = "unknown", error_code: str | None = None):
        super().__init__(message)
        self.gap = gap
        self.error_code = error_code


@dataclass(frozen=True)
class EmailGate:
    """The result of checking whether SES can be used at all.

    ``attempted`` separates the two blocked cases, which are NOT the same
    finding — "no credentials, nothing tried" is a provisioning gap; "real call
    made, refused" is a permissions or account-state problem. Collapsing them
    loses the only fact an operator needs to know who to go and ask.

    ``production_access`` is deliberately TRI-STATE. ``True``/``False`` mean the
    account's sandbox status was actually READ from SES. ``None`` means it could
    not be determined — which is the live situation here, because this principal
    is denied ``ses:GetAccount``. Reporting an unknown as ``False`` would be a
    guess and reporting it as ``True`` would be a dangerous one, so it stays
    None and :attr:`sandbox_known` says so out loud.
    """

    ok: bool
    attempted: bool
    reason: str
    gap: str = "unknown"
    missing_vars: tuple[str, ...] = ()
    error_code: str | None = None
    production_access: bool | None = None
    detail: str | None = None

    @property
    def sandbox_known(self) -> bool:
        """True only when SES actually told us the account's sandbox state."""
        return self.production_access is not None


@dataclass(frozen=True)
class SendResult:
    """A real, confirmed SES acceptance — never constructed on a failure path.

    ``message_id`` is SES's OWN identifier for the accepted message. It is the
    only proof that a send happened; "the function was called" is not proof, and
    this dataclass exists so a caller cannot conflate the two. There is no
    ``sent: bool`` field on purpose — the existence of a ``SendResult`` IS the
    success signal, so there is no falsy instance to mistake for one.
    """

    message_id: str
    recipient_redacted: str
    from_address: str
    subject: str


def redact_email(address: str | None) -> str:
    """A loggable form of an address that is not the address.

    ``joe@example.com`` → ``j…e@e…m``. Enough to correlate two log lines about
    the same recipient, not enough to be a leaked contact record. Every log and
    every verification line in this sprint uses this — a real member's address
    is never printed in full.
    """
    value = (address or "").strip()
    if not value:
        return "<empty>"
    if "@" not in value:
        return f"{value[0]}…{len(value)}c"
    local, _, domain = value.partition("@")
    def _squash(part: str) -> str:
        if len(part) <= 1:
            return part or "?"
        return f"{part[0]}…{part[-1]}"
    return f"{_squash(local)}@{_squash(domain)}"


def _region() -> str | None:
    for var in _REGION_ENV_VARS:
        value = (os.environ.get(var) or "").strip()
        if value:
            return value
    return None


def _usable_secret(name: str) -> bool:
    """A credential value is usable only if present and whitespace-free.

    Whitespace is never valid in an AWS access key id or secret, so a
    placeholder like ``"changeme\\n"`` counts as ABSENT. Same rule, and same
    reasoning, as ``services.textract._usable_secret``: it skips a doomed call
    that could only ever return IncompleteSignatureException, and it stops a
    placeholder from reading as "configured".
    """
    value = os.environ.get(name) or ""
    return bool(value) and not any(c.isspace() for c in value)


def credential_state() -> tuple[bool, tuple[str, ...]]:
    """``(all_present, missing_var_names)`` — reads the environment, nothing else.

    The Altruist signature, deliberately. ``AWS_DEFAULT_REGION`` is reported as
    the missing name when neither it nor its ``AWS_REGION`` alias is set, since
    that is the one an operator should set.
    """
    missing: list[str] = []
    for var in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        if not _usable_secret(var):
            missing.append(var)
    if not _region():
        missing.append(_REGION_ENV_VARS[0])
    if not (os.environ.get(FROM_ENV_VAR) or "").strip():
        missing.append(FROM_ENV_VAR)
    return (not missing), tuple(missing)


def email_configured() -> bool:
    """Cheap pre-check: are all the variables a send needs present?

    Presence, NOT connectivity — the distinction that cost four sprints of false
    green during the Doppler migration, where ``if VAR:`` was read as "this
    works". A True here means "worth attempting", never "will succeed". Use
    :func:`probe` when the question is whether it actually works.
    """
    ok, _ = credential_state()
    return ok


def from_address() -> str:
    """The configured sender, formatted with a display name when one is set."""
    address = (os.environ.get(FROM_ENV_VAR) or "").strip()
    name = (os.environ.get(FROM_NAME_ENV_VAR) or "").strip()
    if name and address:
        # Quote the display name so a comma in it cannot split the header.
        safe = name.replace('"', "")
        return f'"{safe}" <{address}>'
    return address


def _client(api: str = "sesv2"):
    """Construct a boto3 SES client (region/creds from the standard env chain)."""
    import boto3  # local import: boto3 is only needed on the send path

    return boto3.client(api, region_name=_region())


def _classify(code: str, message: str, *, principal_hint: str = "") -> tuple[str, str]:
    """Map an SES error code to ``(gap, actionable_message)``.

    The message is written for the person who has to FIX it: it names the AWS
    action that was refused and the console change that unblocks it. "Email
    failed" is not actionable; "this IAM user lacks ses:SendEmail, attach a
    policy granting it" is.
    """
    detail = (message or "").strip()
    if code in _IAM_CODES:
        return "iam", (
            f"AWS SES refused the send with {code}: {detail} "
            "ACTION REQUIRED (AWS console, outside this codebase): attach an IAM "
            "policy granting 'ses:SendEmail' (and 'ses:SendRawEmail') to the "
            f"principal these credentials belong to{principal_hint}. The "
            "credentials themselves are valid — this is a missing permission, "
            "not a bad key, so rotating them again will not help."
        )
    if code in _PAUSED_CODES:
        return "paused", (
            f"AWS SES has PAUSED sending for this account ({code}): {detail} "
            "ACTION REQUIRED (AWS console): resolve the reputation/review issue "
            "in the SES dashboard; no code change will re-enable sending."
        )
    if code in _IDENTITY_CODES:
        return "identity_or_sandbox", (
            f"AWS SES accepted the request but rejected the message ({code}): "
            f"{detail} ACTION REQUIRED (AWS console): this is either an "
            f"unverified sender identity (verify {os.environ.get(FROM_ENV_VAR) or FROM_ENV_VAR} "
            "or its domain in SES) or SES sandbox mode, which can only deliver "
            "to verified addresses — request production access for this account "
            "to send to real prospective members."
        )
    return "unknown", (
        f"AWS SES returned an unexpected error ({code}): {detail}"
    )


def probe(*, timeout: float | None = None) -> EmailGate:
    """Check credentials and, if present, make ONE real call to SES.

    Never raises: a probe exists to REPORT the state, and a probe that throws
    forces every caller to re-implement the reporting. The call it makes is
    ``sesv2:GetAccount`` — the lightest request that is still authoritative
    about BOTH questions this sprint has to answer honestly (is sending
    permitted at all, and is the account out of sandbox). When that read is
    itself denied, the probe says the sandbox state is UNKNOWN rather than
    inventing one, and reports the IAM gap it did prove.
    """
    present, missing = credential_state()
    if not present:
        return EmailGate(
            ok=False,
            attempted=False,
            gap="credentials",
            reason=(
                "Email sending is not configured. Missing environment "
                f"variable(s): {', '.join(missing)}. No call was attempted — "
                "there is nothing to authenticate or send with."
            ),
            missing_vars=missing,
        )

    try:
        from botocore.exceptions import BotoCoreError, ClientError
    except Exception as exc:  # pragma: no cover — botocore ships with boto3
        return EmailGate(ok=False, attempted=False, gap="transport",
                         reason=f"botocore import failed: {exc}")

    try:
        client = _client("sesv2")
    except Exception as exc:  # noqa: BLE001 — client construction failure = blocked
        return EmailGate(ok=False, attempted=False, gap="transport",
                         reason=f"SES client init failed: {type(exc).__name__}: {exc}")

    try:
        account = client.get_account()
    except ClientError as exc:
        err = exc.response.get("Error", {})
        code = err.get("Code", "")
        gap, message = _classify(code, err.get("Message", ""))
        if gap == "iam":
            message = (
                f"AWS SES refused the account read with {code}: {err.get('Message','')} "
                "ACTION REQUIRED (AWS console): grant this principal "
                "'ses:GetAccount' AND 'ses:SendEmail'. Because the account read "
                "is denied, the SES SANDBOX STATE CANNOT BE DETERMINED from "
                "here — granting send permission alone may still leave delivery "
                "restricted to verified addresses."
            )
        return EmailGate(ok=False, attempted=True, gap=gap, reason=message,
                         error_code=code, detail=err.get("Message"))
    except BotoCoreError as exc:
        return EmailGate(
            ok=False, attempted=True, gap="transport",
            reason=("SES credentials are present but the real call to "
                    f"sesv2:GetAccount failed at the transport layer: "
                    f"{type(exc).__name__}: {exc}"),
            detail=str(exc),
        )

    production = account.get("ProductionAccessEnabled")
    sending = account.get("SendingEnabled")
    if sending is False:
        return EmailGate(
            ok=False, attempted=True, gap="paused",
            production_access=production,
            reason=("SES reports SendingEnabled=false for this account. ACTION "
                    "REQUIRED (AWS console): re-enable sending in the SES "
                    "dashboard."),
        )
    if production is False:
        return EmailGate(
            ok=False, attempted=True, gap="identity_or_sandbox",
            production_access=False,
            reason=("SES is in SANDBOX mode for this account "
                    "(ProductionAccessEnabled=false). Delivery is restricted to "
                    "verified addresses, so invites to real prospective members "
                    "would be rejected. ACTION REQUIRED (AWS console): request "
                    "production access for SES in this region."),
        )
    return EmailGate(
        ok=True, attempted=True, gap="none", production_access=production,
        reason=(f"SES reachable and out of sandbox (ProductionAccessEnabled="
                f"{production}); sender {redact_email(os.environ.get(FROM_ENV_VAR))}."),
    )


def send_email(
    *,
    to_address: str,
    subject: str,
    text_body: str,
    html_body: str | None = None,
    reply_to: str | None = None,
) -> SendResult:
    """Send ONE real email through AWS SES. Returns SES's own message id.

    Raises :class:`EmailBlocked` — never returns a falsy value — when the send
    cannot happen, with a reason naming the real, actionable gap. This is the
    "fail loud" contract: there is no code path here that swallows a failure and
    lets a caller believe mail went out.

    Synchronous (boto3). Async callers MUST wrap this in ``run_in_threadpool``.
    """
    recipient = (to_address or "").strip()
    if not recipient or "@" not in recipient:
        raise EmailBlocked(
            f"Refusing to send to an invalid recipient address {redact_email(recipient)}.",
            gap="recipient",
        )

    present, missing = credential_state()
    if not present:
        raise EmailBlocked(
            "Email sending is not configured, so no message was sent. Missing "
            f"environment variable(s): {', '.join(missing)}. ACTION REQUIRED: "
            f"set them (SES credentials + region + a verified {FROM_ENV_VAR}) in "
            "Doppler and redeploy.",
            gap="credentials",
        )

    try:
        from botocore.exceptions import (
            BotoCoreError,
            ClientError,
            NoCredentialsError,
            NoRegionError,
        )
    except Exception as exc:  # pragma: no cover
        raise EmailBlocked(f"botocore import failed: {exc}", gap="transport") from exc

    try:
        client = _client("sesv2")
    except Exception as exc:  # noqa: BLE001
        raise EmailBlocked(
            f"SES client init failed: {type(exc).__name__}: {exc}", gap="transport"
        ) from exc

    body: dict = {"Text": {"Data": text_body, "Charset": "UTF-8"}}
    if html_body:
        body["Html"] = {"Data": html_body, "Charset": "UTF-8"}

    kwargs: dict = {
        "FromEmailAddress": from_address(),
        "Destination": {"ToAddresses": [recipient]},
        "Content": {"Simple": {
            "Subject": {"Data": subject, "Charset": "UTF-8"},
            "Body": body,
        }},
    }
    if reply_to:
        kwargs["ReplyToAddresses"] = [reply_to]
    config_set = (os.environ.get(CONFIG_SET_ENV_VAR) or "").strip()
    if config_set:
        kwargs["ConfigurationSetName"] = config_set

    try:
        response = client.send_email(**kwargs)
    except (NoCredentialsError, NoRegionError) as exc:
        raise EmailBlocked(
            f"SES could not authenticate: {type(exc).__name__}: {exc}",
            gap="credentials",
        ) from exc
    except ClientError as exc:
        err = exc.response.get("Error", {})
        code = err.get("Code", "")
        gap, message = _classify(code, err.get("Message", ""))
        raise EmailBlocked(message, gap=gap, error_code=code) from exc
    except BotoCoreError as exc:
        raise EmailBlocked(
            f"SES send failed at the transport layer: {type(exc).__name__}: {exc}",
            gap="transport",
        ) from exc

    message_id = response.get("MessageId")
    if not message_id:
        # SES always returns a MessageId on acceptance. No id means we cannot
        # prove the message was accepted, and an unprovable send is a failure.
        raise EmailBlocked(
            "SES returned no MessageId, so acceptance cannot be confirmed.",
            gap="unknown",
        )
    return SendResult(
        message_id=message_id,
        recipient_redacted=redact_email(recipient),
        from_address=kwargs["FromEmailAddress"],
        subject=subject,
    )


# ── Templates ──────────────────────────────────────────────────────────────
# Minimal and real. No design-system work: an invite has exactly three jobs —
# say who is inviting you, give you the link, and tell you when it stops
# working. Everything the copy says is derived from real data (the ORG'S OWN
# name, that org's enrollment URL, that org's configured invite.expiry_days), so
# a Hollisworks invite can never carry 2nd Act's name or link.


@dataclass(frozen=True)
class RenderedEmail:
    """A rendered message, ready to hand to :func:`send_email`."""

    subject: str
    text: str
    html: str
    #: Kept so callers/verification can assert the org's own values landed in
    #: the copy without re-parsing the rendered body.
    org_name: str = ""
    enrollment_url: str = ""
    expiry_days: int = 0
    fields: dict = field(default_factory=dict)


def _expiry_phrase(expiry_days: int, expires_at=None) -> str:
    """"in 7 days (on 2 September 2026)" — a duration AND a real date when known.

    The duration alone ages badly in an inbox (an email read three days later
    still claims "7 days"), and the date alone hides the urgency. When the
    caller has the DB-computed ``invite_expires_at`` we state both; the date is
    always the database's, never re-derived from the app server's clock.
    """
    unit = "day" if expiry_days == 1 else "days"
    phrase = f"in {expiry_days} {unit}"
    if expires_at is not None:
        try:
            phrase += f" (on {expires_at.strftime('%-d %B %Y')})"
        except (AttributeError, ValueError):
            # A non-datetime, or a platform without %-d. The duration alone is
            # still correct, so degrade the detail rather than the message.
            pass
    return phrase


def render_invite_email(
    *,
    org_name: str,
    enrollment_url: str,
    expiry_days: int,
    expires_at=None,
    full_name: str | None = None,
) -> RenderedEmail:
    """Render the invite email for ONE org. Pure — no environment, no database.

    ``org_name`` is the INVITING org's own name and is used everywhere the copy
    names the firm; it is a required argument rather than a lookup precisely so
    that no default can quietly substitute another tenant's brand.

    Voice follows the project's copy rules: quiet, precise, no hype, no emoji,
    "member"/"membership" rather than "user"/"unlock". It makes no data-handling
    or privacy promise — that language is on hold until the ZDR contract is
    signed (see CLAUDE.md).
    """
    name = (org_name or "").strip()
    if not name:
        raise ValueError(
            "render_invite_email requires the inviting org's own name — refusing "
            "to send an invite that does not say who it is from."
        )
    url = (enrollment_url or "").strip()
    if not url:
        raise ValueError("render_invite_email requires an enrollment_url")

    greeting = f"Hello {full_name.strip()}," if (full_name or "").strip() else "Hello,"
    expiry = _expiry_phrase(expiry_days, expires_at)
    subject = f"Your invitation to {name}"

    text = "\n".join([
        greeting,
        "",
        f"You have been invited to join {name}.",
        "",
        "Use the link below to set up your membership account:",
        url,
        "",
        f"This invitation expires {expiry}.",
        "",
        "If you were not expecting this invitation, no action is needed.",
        "",
        f"— {name}",
    ])

    safe_name = escape(name)
    safe_url = escape(url, quote=True)
    safe_greeting = escape(greeting)
    safe_expiry = escape(expiry)
    html = (
        '<!DOCTYPE html><html><body style="font-family:Georgia,serif;'
        'font-size:17px;line-height:1.6;color:#0F172A;background:#FAF9F6;'
        'padding:32px;">'
        f"<p>{safe_greeting}</p>"
        f"<p>You have been invited to join {safe_name}.</p>"
        "<p>Use the link below to set up your membership account:</p>"
        f'<p><a href="{safe_url}" style="color:#1B2B4B;">{safe_url}</a></p>'
        f"<p>This invitation expires {safe_expiry}.</p>"
        "<p>If you were not expecting this invitation, no action is needed.</p>"
        f'<p style="color:#64748B;">&mdash; {safe_name}</p>'
        "</body></html>"
    )

    return RenderedEmail(
        subject=subject,
        text=text,
        html=html,
        org_name=name,
        enrollment_url=url,
        expiry_days=expiry_days,
        fields={"greeting": greeting, "expiry_phrase": expiry},
    )
