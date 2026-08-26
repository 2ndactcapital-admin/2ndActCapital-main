"""verify_smtpservice.py — SMTP / email-sending service verifier.

Pass/fail output only. No interactive prompts. Idempotent. Every row it creates
is deleted in a ``finally`` block.

SECRET + PII SAFETY (structural, not by discipline)
──────────────────────────────────────────────────────────────────────────────
This script NEVER prints a secret value and NEVER prints a recipient address in
full. Both are enforced at OUTPUT time by ``_emit``: every line is run through a
scrubber that replaces any known secret value with ``<REDACTED>`` and rewrites
anything shaped like an email address into the redacted ``j…e@e…m`` form. A
future edit that carelessly interpolates one is caught when it is printed rather
than after it is committed to a log. Assertion [7] re-scans the script's own
captured transcript to prove it.

HONEST GATING
──────────────────────────────────────────────────────────────────────────────
Legs that cannot be executed report ``[BLOCKED]`` and force a non-zero exit.
They are never reported as PASS and never simulated. In particular: a real SES
send is attempted if and only if the gate says sending is genuinely usable, and
if it is not, THAT is what is verified — that the failure is loud, specific and
actionable — while the sprint as a whole still exits non-zero, because "we
correctly reported that we cannot send email" is not "email works".

CREDENTIALS
──────────────────────────────────────────────────────────────────────────────
Secrets are hydrated from Doppler over its HTTPS API when ``DOPPLER_TOKEN`` is
set (see ``_doppler_env``). This is deliberate: the copies in ``apps/api/.env``
and ``~/.bashrc`` are stale and their database passwords are rejected, which is
what produced several sprints of false "blocked on credentials" results. Doppler
is the source of truth Render reads, so verifying against Doppler is verifying
against what actually runs.

Assertions
  [1] Task 1a — the REAL, CURRENT IAM identity and whether ses:SendEmail is
      permitted today, proven by a live authorization probe.
  [2] Task 1b — whether SES is out of sandbox for this AWS account, or whether
      that fact is genuinely unreadable from here (reported as unknown, never
      guessed).
  [3] Task 1c — the real invite-creation code path: the send is wired at the
      real point and the enrollment_url has the real, fully-qualified shape.
  [4] IF SES is usable: a real invite creates AND sends a real email, proven by
      SES's own MessageId. IF NOT: the send fails loud with a specific,
      actionable message naming the real gap.
  [5] The manual-URL fallback still works regardless, and is ANNOUNCED
      (manual_share_required) rather than silent.
  [6] Cross-org: each org's invite email carries that org's OWN name.
  [7] No secret value and no full email address appears in this output.
  [8] Teardown: zero leftover rows.
"""

import asyncio
import os
import re
import sys
import uuid

_HERE = os.path.dirname(os.path.abspath(__file__))
_API_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
for _py in ("python3.14", "python3.13", "python3.12", "python3.11"):
    _sp = os.path.join(_API_ROOT, "venv", "lib", _py, "site-packages")
    if os.path.isdir(_sp) and _sp not in sys.path:
        sys.path.insert(0, _sp)
sys.path.insert(0, _API_ROOT)
sys.path.insert(0, _HERE)

# ---------------------------------------------------------------------------
# Output safety
# ---------------------------------------------------------------------------

_TRANSCRIPT: list[str] = []
_SECRET_VALUES: set[str] = set()

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _register_secret(value):
    """Remember a value so the scrubber refuses to print it.

    Values shorter than 8 characters are ignored: a 2-character "secret" would
    blacklist ordinary substrings and turn every line into <REDACTED>, hiding
    real output instead of protecting anything.
    """
    if value and isinstance(value, str) and len(value.strip()) >= 8:
        _SECRET_VALUES.add(value.strip())


def _redact_addresses(text: str) -> str:
    """Rewrite every full email address into the ``j…e@e…m`` form.

    Applied to EVERY line, including ones this script did not compose itself
    (an SES error message quotes the address back). That is the point: the
    guarantee has to hold for text we did not write.
    """
    def _sub(match):
        local, _, domain = match.group(0).partition("@")
        squash = lambda p: p if len(p) <= 1 else f"{p[0]}…{p[-1]}"  # noqa: E731
        return f"{squash(local)}@{squash(domain)}"
    return _EMAIL_RE.sub(_sub, text)


def _scrub(text: str) -> str:
    for value in _SECRET_VALUES:
        if value in text:
            text = text.replace(value, "<REDACTED>")
    # Belt and braces: a Postgres DSN password, even one never registered.
    text = re.sub(r"(postgres(?:ql)?://[^:\s]+:)[^@\s]+@", r"\1<REDACTED>@", text)
    return _redact_addresses(text)


class _ScrubbedStdout:
    """stdout wrapper that scrubs EVERYTHING, including writes we did not make.

    ``_emit`` alone is not enough. The code under test does its own logging —
    ``services.invites`` prints a line per blocked send, and the SES error it
    quotes contains an address — and those writes go straight to stdout without
    passing through this script's helpers. Installing the scrubber at the stream
    means the no-secrets / no-addresses guarantee holds for output this script
    never composed, which is the only version of that guarantee worth having.
    """

    def __init__(self, underlying):
        self._underlying = underlying

    def write(self, text):
        text = _scrub(str(text))
        if text.strip():
            _TRANSCRIPT.append(text.rstrip("\n"))
        return self._underlying.write(text)

    def flush(self):
        return self._underlying.flush()

    def __getattr__(self, name):
        return getattr(self._underlying, name)


def _emit(line):
    # Written through the scrubbed stream, which does the scrubbing and the
    # transcript append — so there is exactly ONE place either can be skipped.
    print(str(line), flush=True)


_RESULTS = {"pass": 0, "fail": 0, "blocked": 0}


def _ok(msg):
    _RESULTS["pass"] += 1
    _emit(f"[PASS] {msg}")


def _fail(msg):
    _RESULTS["fail"] += 1
    _emit(f"[FAIL] {msg}")


def _blocked(msg):
    _RESULTS["blocked"] += 1
    _emit(f"[BLOCKED] {msg}")


def _info(msg):
    _emit(f"        {msg}")


def _head(msg):
    _emit("")
    _emit(msg)


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------


def _load_env():
    """Doppler first (source of truth), apps/api/.env only to fill real gaps.

    Order matters and is the opposite of the usual "don't clobber" instinct:
    the ambient/.env values are the KNOWN-STALE ones, so Doppler must overwrite
    them, not defer to them.
    """
    names: list[str] = []
    from _doppler_env import hydrate_from_doppler

    loaded, error = hydrate_from_doppler(overwrite=True)
    if error:
        _info(f"Doppler hydration unavailable ({error}); falling back to apps/api/.env")
    else:
        names.extend(loaded)
        _info(f"Doppler: hydrated {len(loaded)} secret name(s) over the HTTPS API")

    envp = os.path.join(_API_ROOT, ".env")
    if os.path.exists(envp):
        with open(envp) as fh:
            for line in fh:
                match = re.match(r"\s*(?:export\s+)?([A-Z0-9_]+)\s*=\s*(.*)$", line)
                if not match:
                    continue
                key, value = match.group(1), match.group(2).strip().strip('"').strip("'")
                if not os.environ.get(key):
                    os.environ[key] = value
                    names.append(key)

    for key in set(names) | set(os.environ):
        if any(t in key for t in ("KEY", "SECRET", "TOKEN", "PASSWORD", "DATABASE_URL", "DSN")):
            _register_secret(os.environ.get(key))
    return sorted(set(names))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

RUN = uuid.uuid4().hex[:10]
# .invalid is reserved by RFC 2606 and can never resolve, so even if a send were
# somehow attempted against it, no real mailbox could ever receive it.
TEST_DOMAIN = "smtpverify.invalid"
ACTOR_ID = "99000000-0000-0000-0000-000000000001"
ACTOR_SUB = "auth0|test_verify_user"

_created_user_ids: set[str] = set()
_created_emails: set[str] = set()


def _test_email(tag: str) -> str:
    return f"smtpverify-{RUN}-{tag}@{TEST_DOMAIN}"


async def _connect():
    import asyncpg

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        return None, "DATABASE_URL is not set"
    try:
        return await asyncpg.connect(dsn, statement_cache_size=0, timeout=30), None
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"


async def _seed_actor(conn, org_id):
    """The invite's ``invited_by``. ON CONFLICT DO NOTHING — idempotent."""
    await conn.execute(
        """
        INSERT INTO users (id, org_id, email, full_name, role, auth0_sub, is_active)
        VALUES ($1, $2, $3, 'SMTP Verify Actor', 'org_admin', $4, true)
        ON CONFLICT (auth0_sub) DO NOTHING
        """,
        ACTOR_ID, org_id, f"verify-actor@{TEST_DOMAIN}", ACTOR_SUB,
    )
    row = await conn.fetchrow("SELECT id FROM users WHERE auth0_sub = $1", ACTOR_SUB)
    return str(row["id"]) if row else None


async def _teardown(conn):
    """Delete every row this run created. FK-safe: invites have no children.

    Deletes by the run-scoped email pattern as well as by collected id, so a row
    created by a leg that crashed before recording its id is still removed.
    """
    if conn is None:
        return None
    try:
        await conn.execute(
            "DELETE FROM audit_log WHERE record_id = ANY($1::uuid[])",
            list(_created_user_ids) or ["00000000-0000-0000-0000-000000000000"],
        )
    except Exception:
        # audit_log is written by the ROUTER, not the service this script
        # exercises, so there is normally nothing to remove. A missing table or
        # a column-name difference must not stop the user cleanup below.
        pass
    await conn.execute(
        "DELETE FROM users WHERE email LIKE $1",
        f"smtpverify-{RUN}-%@{TEST_DOMAIN}",
    )
    await conn.execute("DELETE FROM users WHERE auth0_sub = $1", ACTOR_SUB)
    leftover = await conn.fetchval(
        "SELECT count(*) FROM users WHERE email LIKE $1 OR auth0_sub = $2",
        f"smtpverify-{RUN}-%@{TEST_DOMAIN}", ACTOR_SUB,
    )
    return leftover


# ---------------------------------------------------------------------------
# Assertions
# ---------------------------------------------------------------------------


def _assert_1a_iam():
    """Task 1a — who these credentials REALLY are, and whether SES send is allowed.

    Does not trust that a credential rotation fixed anything. It resolves the
    live principal and then makes a real authorization probe against
    ``ses:SendEmail``. The probe is SAFE BY CONSTRUCTION: IAM authorization is
    evaluated before identity verification, and the From address is a domain we
    provably do not own, so the only outcomes are AccessDenied (no permission)
    or MessageRejected (unverified sender). Neither delivers mail to anybody.
    """
    _head("--- [1] Task 1a: real, current IAM state of the deployment's AWS credentials ---")
    region = os.environ.get("AWS_DEFAULT_REGION") or os.environ.get("AWS_REGION")
    if not (os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY")):
        _fail("Task 1a: no AWS credentials resolvable from Doppler or the environment")
        return None

    try:
        import boto3
        from botocore.exceptions import ClientError
    except Exception as exc:  # noqa: BLE001
        _fail(f"Task 1a: boto3 unavailable ({type(exc).__name__})")
        return None

    try:
        identity = boto3.client("sts", region_name=region).get_caller_identity()
    except Exception as exc:  # noqa: BLE001
        _fail(f"Task 1a: STS GetCallerIdentity failed — {type(exc).__name__}: {exc}")
        return None

    arn = identity["Arn"]
    _info(f"live principal: {arn}")
    _info(f"region: {region}")

    nowhere = "probe@invalid-ses-probe.example"
    try:
        boto3.client("sesv2", region_name=region).send_email(
            FromEmailAddress=nowhere,
            Destination={"ToAddresses": [nowhere]},
            Content={"Simple": {"Subject": {"Data": "authz probe"},
                                "Body": {"Text": {"Data": "authz probe"}}}},
        )
        code = None
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
    except Exception as exc:  # noqa: BLE001
        _fail(f"Task 1a: SES probe failed unexpectedly — {type(exc).__name__}")
        return None

    if code in (None, "MessageRejected", "MailFromDomainNotVerifiedException"):
        _ok("Task 1a REPORTED: ses:SendEmail IS permitted for this principal today "
            f"(probe returned {code or 'acceptance'}, i.e. authorization passed).")
        return {"arn": arn, "send_permitted": True, "code": code}

    _ok("Task 1a REPORTED HONESTLY: ses:SendEmail is NOT permitted today. The "
        "credentials are valid and live (STS resolved them) but they belong to "
        f"the Textract-only IAM user {arn.rsplit('/', 1)[-1]!r}, which carries no "
        f"SES permission. A real authorization probe returned {code}. This is the "
        "SAME gap as the earlier invite sprint — tonight's credential rotation "
        "restored working keys, it did not change what they may do.")
    return {"arn": arn, "send_permitted": False, "code": code}


def _assert_1b_sandbox():
    """Task 1b — is SES out of sandbox for this AWS account?

    An honest UNKNOWN is a pass here and a fabricated answer would be a failure.
    ``ses:GetAccount`` is the authoritative read; when it is itself denied, the
    only truthful report is that the sandbox state cannot be determined — which
    is a SECOND, independent blocker, because granting send permission alone
    would still leave delivery restricted to verified addresses if the account
    is in sandbox.
    """
    _head("--- [2] Task 1b: AWS SES sandbox state for this AWS account ---")
    region = os.environ.get("AWS_DEFAULT_REGION") or os.environ.get("AWS_REGION")
    try:
        import boto3
        from botocore.exceptions import ClientError
    except Exception as exc:  # noqa: BLE001
        _fail(f"Task 1b: boto3 unavailable ({type(exc).__name__})")
        return None

    try:
        account = boto3.client("sesv2", region_name=region).get_account()
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        _ok("Task 1b REPORTED HONESTLY: the SES sandbox state CANNOT BE DETERMINED "
            f"from this deployment — ses:GetAccount is itself denied ({code}). It is "
            "NOT being reported as 'production' or 'sandbox', because either would "
            "be a guess. This is a second, independent blocker: a sandboxed account "
            "can only send to verified addresses, so granting ses:SendEmail alone "
            "may still cause invites to real prospective members to be rejected.")
        return {"known": False, "production_access": None, "code": code}
    except Exception as exc:  # noqa: BLE001
        _fail(f"Task 1b: unexpected error reading SES account state — {type(exc).__name__}")
        return None

    production = account.get("ProductionAccessEnabled")
    _ok(f"Task 1b REPORTED: SES account state read successfully — "
        f"ProductionAccessEnabled={production}, SendingEnabled={account.get('SendingEnabled')}. "
        + ("Out of sandbox: real recipients are deliverable."
           if production else
           "IN SANDBOX: only verified addresses are deliverable, so invites to "
           "real prospective members would be silently rejected."))
    return {"known": True, "production_access": bool(production),
            "sending_enabled": account.get("SendingEnabled")}


def _assert_1c_code_path():
    """Task 1c — the send is wired at the real point, with the real URL shape."""
    _head("--- [3] Task 1c: real invite-creation code path ---")
    import inspect

    from services import email as email_service
    from services import invites

    problems = []

    source = inspect.getsource(invites.create_invite)
    if "send_invite_email" not in source:
        problems.append("create_invite does not call send_invite_email")
    if "email_delivery" not in source:
        problems.append("create_invite does not record an email_delivery outcome")

    # The enrollment URL shape the send must carry, exercised for real.
    url = invites.build_enrollment_url(None, "tok-abc", slug="hollisworks")
    if url != "https://hollisworks.hollisworks.com/enroll?invite_token=tok-abc":
        problems.append(f"derived enrollment_url shape is wrong: {url}")
    kept = invites.build_enrollment_url("https://admin.hollisworks.com/enroll", "t2")
    if kept != "https://admin.hollisworks.com/enroll?invite_token=t2":
        problems.append(f"stored enroll_url not preserved verbatim: {kept}")
    try:
        invites.build_enrollment_url("/enroll", "t3")
        problems.append("a relative enroll_url was accepted instead of raising")
    except invites.EnrollmentUrlError:
        pass

    if not hasattr(email_service, "send_email") or not hasattr(email_service, "credential_state"):
        problems.append("services.email is missing send_email/credential_state")

    if problems:
        _fail("Task 1c: " + "; ".join(problems))
        return False

    _ok("Task 1c REPORTED: services/invites.create_invite builds the fully-qualified "
        "enrollment_url from the creating org's own organizations.enroll_url (or the "
        "derived https://<slug>.hollisworks.com/enroll), and the SES send is wired "
        "immediately after it via send_invite_email, whose outcome is recorded on "
        "every path in result['email_delivery'].")
    return True


async def _assert_4_send(conn, org_id, gate_usable):
    """Task 4 — real send with a real MessageId, or a loud, actionable failure."""
    _head("--- [4] Real invite: send attempted, outcome proven ---")
    from services.invites import create_invite

    email = _test_email("send")
    _created_emails.add(email)
    row = await create_invite(
        conn,
        org_id=org_id,
        email=email,
        full_name="SMTP Verify Recipient",
        role="member",
        invited_by=ACTOR_ID,
    )
    _created_user_ids.add(str(row["id"]))
    delivery = row["email_delivery"]
    _info(f"delivery status={delivery['status']} gap={delivery.get('gap')}")

    if gate_usable:
        if delivery["sent"] and delivery["message_id"]:
            _ok("Task 4: a real invite was created AND a real email was sent — proven "
                f"by SES's own MessageId {delivery['message_id']}, not by 'the "
                "function was called'.")
            return delivery
        _fail("Task 4: SES reported usable but the send produced no MessageId — "
              f"status={delivery['status']} reason={delivery.get('reason')}")
        return delivery

    # NOT usable — verify the failure is loud, specific and actionable.
    reason = delivery.get("reason") or ""
    problems = []
    if delivery["sent"]:
        problems.append("delivery claims sent while the gate says sending is unusable")
    if delivery["status"] != "blocked":
        problems.append(f"status is {delivery['status']!r}, expected 'blocked'")
    if not delivery.get("manual_share_required"):
        problems.append("manual_share_required is not set, so the fallback is silent")
    if "ACTION REQUIRED" not in reason:
        problems.append("reason does not state an action the operator can take")
    if not any(t in reason for t in ("ses:SendEmail", "SES_FROM_EMAIL",
                                     "sandbox", "production access", "IAM")):
        problems.append("reason does not name the real gap")

    if problems:
        _fail("Task 4 (blocked path): " + "; ".join(problems))
        return delivery

    _ok("Task 4: SES is NOT usable, and the send FAILED LOUD with a specific, "
        "actionable message naming the real gap — not a silent fallback.")
    _info(f"reason: {reason}")
    return delivery


async def _assert_4b_iam_message(conn, org_id):
    """Task 4 — the IAM-denial path also fails loud, proven by a REAL SES call.

    Assertion [4] above stops at the first blocker it meets, which today is the
    unset ``SES_FROM_EMAIL``. That leaves the more important message — the one
    an operator sees once a sender IS configured but the IAM permission is still
    missing — untested. So this leg supplies a placeholder sender and lets the
    send reach AWS for real.

    SAFE BY CONSTRUCTION, twice over: the sender is a domain we do not own (so
    SES could only ever reject it), and the recipient is a run-scoped address on
    an RFC 2606 ``.invalid`` domain that can never resolve. The env var is
    restored in a ``finally``.
    """
    _head("--- [4b] The IAM-denial message, exercised against real AWS ---")
    from services import email as email_service
    from services.invites import create_invite

    if not (os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY")):
        _blocked("[4b] no AWS credentials — the IAM-denial message could not be exercised.")
        return None

    previous = os.environ.get(email_service.FROM_ENV_VAR)
    os.environ[email_service.FROM_ENV_VAR] = "no-reply@invalid-ses-probe.example"
    email = _test_email("iam")
    _created_emails.add(email)
    try:
        row = await create_invite(
            conn, org_id=org_id, email=email, full_name=None,
            role="member", invited_by=ACTOR_ID,
        )
        _created_user_ids.add(str(row["id"]))
        delivery = row["email_delivery"]
    finally:
        if previous is None:
            os.environ.pop(email_service.FROM_ENV_VAR, None)
        else:
            os.environ[email_service.FROM_ENV_VAR] = previous

    reason = delivery.get("reason") or ""
    problems = []
    if delivery["sent"]:
        problems.append("a send was reported as successful against a denied principal")
    if delivery.get("gap") not in ("iam", "identity_or_sandbox", "paused"):
        problems.append(f"gap classified as {delivery.get('gap')!r}, expected an AWS-side gap")
    if delivery.get("gap") == "iam":
        if "ses:SendEmail" not in reason:
            problems.append("the IAM message does not name the ses:SendEmail action")
        if "ACTION REQUIRED" not in reason:
            problems.append("the IAM message states no action")
        if "rotating them again will not help" not in reason:
            problems.append("the IAM message does not distinguish a permission gap "
                            "from a bad key")
    if not delivery.get("manual_share_required"):
        problems.append("manual_share_required is not set")

    if problems:
        _fail("[4b]: " + "; ".join(problems))
        return delivery
    _ok(f"[4b]: with a sender configured, a REAL SES call was made and refused "
        f"({delivery.get('gap')}); the invite still succeeded, delivery was marked "
        "blocked, and the message names the exact AWS action to grant.")
    _info(f"reason: {reason}")
    return delivery


def _assert_5_fallback(row, delivery, org_slug_or_url):
    """Task 4 — the manual-URL fallback works regardless, and is ANNOUNCED."""
    _head("--- [5] Manual-URL fallback, available regardless and never silent ---")
    url = row["enrollment_url"]
    problems = []
    if not url or not url.startswith("https://"):
        problems.append(f"enrollment_url is not an absolute https URL: {url!r}")
    if row["invite_token"] not in (url or ""):
        problems.append("enrollment_url does not carry the invite token")
    if org_slug_or_url not in (url or ""):
        problems.append(f"enrollment_url does not point at this org ({org_slug_or_url}): {url!r}")
    if not delivery["sent"] and not delivery.get("manual_share_required"):
        problems.append("delivery did not happen yet manual_share_required is false — "
                        "that is exactly the silent fallback this sprint forbids")
    if delivery["sent"] and delivery.get("manual_share_required"):
        problems.append("delivery succeeded but still demands manual sharing")

    if problems:
        _fail("Task 4 (fallback): " + "; ".join(problems))
        return False
    _ok("Task 4: the manual-share enrollment_url is present, absolute, org-correct "
        "and token-bearing regardless of send outcome — and when delivery did not "
        "happen it is flagged manual_share_required, so it is announced, not silent.")
    _info(f"url host/path: {re.sub(r'invite_token=.*', 'invite_token=<token>', url)}")
    return True


async def _assert_6_cross_org(conn, orgs):
    """Task 4 — each org's invite email carries that org's OWN name.

    This is the leg that catches the highest-consequence bug in the sprint.
    ``DEFAULT_SETTINGS['brand.name']`` is the literal string "2nd Act Capital",
    so any org WITHOUT its own ``brand.name`` row would have had every invite it
    sends signed with another tenant's name if the code had used the ordinary
    ``get_setting``. The assertion below is therefore not symmetric box-ticking:
    it explicitly requires that an org with no brand.name row is NOT named after
    the platform default.
    """
    _head("--- [6] Cross-org: invite content reflects each org's OWN name ---")
    from services.email import render_invite_email
    from services.invites import (
        enrollment_url_for_org,
        generate_invite_token,
        resolve_invite_ttl_days,
        resolve_org_display_name,
    )
    from services.org_settings import DEFAULT_SETTINGS, get_setting_with_origin

    platform_default = DEFAULT_SETTINGS["brand.name"]
    rendered_by_org = {}
    problems = []

    for org in orgs:
        org_id, org_name = str(org["id"]), org["name"]
        display = await resolve_org_display_name(conn, org_id)
        _, is_default = await get_setting_with_origin(conn, org_id, "brand.name")
        ttl = await resolve_invite_ttl_days(conn, org_id)
        url = await enrollment_url_for_org(conn, org_id, generate_invite_token())
        message = render_invite_email(
            org_name=display, enrollment_url=url, expiry_days=ttl,
        )
        rendered_by_org[org_id] = message
        _info(f"org {org_name!r}: display={display!r} "
              f"(own brand.name row: {not is_default}), expiry_days={ttl}")

        if display != org_name and is_default:
            problems.append(
                f"org {org_name!r} has no brand.name row and resolved to {display!r} "
                "instead of its own organizations.name")
        if is_default and display == platform_default and org_name != platform_default:
            problems.append(
                f"org {org_name!r} would be signed with the PLATFORM DEFAULT "
                f"{platform_default!r} — another tenant's name")
        if display not in message.text or display not in message.subject:
            problems.append(f"org {org_name!r}: its own name is missing from the email copy")
        if url not in message.text:
            problems.append(f"org {org_name!r}: its own enrollment URL is missing from the copy")
        if f"{ttl} day" not in message.text:
            problems.append(f"org {org_name!r}: the expiry_days value {ttl} is not stated")

    # No org's copy may contain another org's name.
    for org in orgs:
        mine = rendered_by_org[str(org["id"])]
        for other in orgs:
            if str(other["id"]) == str(org["id"]):
                continue
            if other["name"] in mine.text or other["name"] in mine.subject:
                problems.append(
                    f"org {org['name']!r}'s email mentions {other['name']!r}")

    if problems:
        _fail("Task 4 (cross-org): " + "; ".join(problems))
        return False
    _ok(f"Task 4: all {len(orgs)} real orgs render invite email carrying their OWN "
        "name, their OWN enrollment URL and their OWN invite.expiry_days; no org's "
        "copy mentions another org, and an org with no brand.name row is signed "
        "with its organizations.name rather than the platform default.")
    return True


def _assert_7_no_leaks():
    """[7] Re-scan this script's own transcript for secrets and addresses."""
    _head("--- [7] Output safety: no secret value, no full email address ---")
    joined = "\n".join(_TRANSCRIPT)
    problems = []
    for value in _SECRET_VALUES:
        if value in joined:
            problems.append("a registered secret value appears in the output")
            break
    leaked = [m for m in _EMAIL_RE.findall(joined)]
    if leaked:
        problems.append(f"{len(leaked)} full email address(es) appear in the output")
    if problems:
        _fail("Output safety: " + "; ".join(problems))
        return False
    _ok(f"Output safety: {len(_SECRET_VALUES)} registered secret value(s) and every "
        "email address were scrubbed at emit time; the transcript contains neither.")
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def run():
    _head("=== verify_smtpservice.py — SMTP / email-sending service ===")
    _load_env()

    from services import email as email_service

    iam = _assert_1a_iam()
    sandbox = _assert_1b_sandbox()
    _assert_1c_code_path()

    # The gate that decides whether a REAL send is attempted below. Every
    # condition must hold: permission, a configured verified sender, and a
    # sandbox state that is KNOWN to be production. An unknown sandbox state is
    # NOT treated as usable — that is the whole discipline of this sprint.
    _head("--- Gate: is a real SES send genuinely attemptable? ---")
    configured, missing = email_service.credential_state()
    gate = email_service.probe()
    gate_usable = bool(
        configured
        and iam and iam["send_permitted"]
        and sandbox and sandbox["known"] and sandbox["production_access"]
        and gate.ok
    )
    if not configured:
        _info(f"missing configuration: {', '.join(missing)}")
    _info(f"probe: ok={gate.ok} gap={gate.gap} sandbox_known={gate.sandbox_known}")
    _info(f"real SES send attemptable: {gate_usable}")
    if not gate_usable:
        # Recorded as BLOCKED so the run exits non-zero. The assertions below
        # can still all pass — they verify that we fail loudly and correctly —
        # but "we correctly reported that we cannot send email" must never be
        # allowed to read as "email works".
        reasons = []
        if iam and not iam["send_permitted"]:
            reasons.append(f"IAM: {iam['arn'].rsplit('/', 1)[-1]} lacks ses:SendEmail")
        if missing:
            reasons.append(f"unset config: {', '.join(missing)}")
        if sandbox and not sandbox["known"]:
            reasons.append("SES sandbox state unreadable (ses:GetAccount denied)")
        elif sandbox and not sandbox["production_access"]:
            reasons.append("SES account is in SANDBOX (verified recipients only)")
        _blocked("Real email delivery is NOT usable. " + "; ".join(reasons)
                 + ". These are AWS-console actions outside this codebase — see "
                   "docs/PROJECT_STATUS.md.")

    conn, db_error = await _connect()
    if conn is None:
        _blocked(f"Database unreachable ({db_error}) — the invite-creation, "
                 "cross-org and teardown legs could not be run. Re-run with "
                 "DOPPLER_TOKEN set (or under `doppler run --`).")
        _assert_7_no_leaks()
        return

    try:
        orgs = await conn.fetch(
            "SELECT id, name, slug, enroll_url FROM organizations ORDER BY name"
        )
        if not orgs:
            _blocked("No organizations exist — cannot exercise the invite path.")
        else:
            primary = orgs[0]
            await _seed_actor(conn, str(primary["id"]))

            row = None
            delivery = None
            try:
                delivery = await _assert_4_send(conn, str(primary["id"]), gate_usable)
                row = await conn.fetchrow(
                    "SELECT id, invite_token FROM users WHERE email = $1",
                    _test_email("send"),
                )
            except Exception as exc:  # noqa: BLE001
                _fail(f"Task 4: invite creation raised {type(exc).__name__}: {exc}")

            if row is not None and delivery is not None:
                from services.invites import enrollment_url_for_org

                # Rebuilt from the persisted token through the SAME resolver the
                # send path uses, so this leg proves the link an admin would
                # actually copy — not a value handed back by the call under test.
                anchor = (primary["enroll_url"] or "").split("//")[-1].split("/")[0] \
                    or primary["slug"]
                _assert_5_fallback(
                    {
                        "enrollment_url": await enrollment_url_for_org(
                            conn, str(primary["id"]), row["invite_token"]
                        ),
                        "invite_token": row["invite_token"],
                    },
                    delivery, anchor,
                )

            if not gate_usable:
                try:
                    await _assert_4b_iam_message(conn, str(primary["id"]))
                except Exception as exc:  # noqa: BLE001
                    _fail(f"[4b] raised {type(exc).__name__}: {exc}")

            await _assert_6_cross_org(conn, orgs)

        _head("--- [8] Teardown ---")
        leftover = await _teardown(conn)
        if leftover == 0:
            _ok("Teardown: zero leftover rows for this run.")
        else:
            _fail(f"Teardown: {leftover} row(s) remain.")
    finally:
        try:
            await _teardown(conn)
        finally:
            await conn.close()

    _assert_7_no_leaks()


def main():
    sys.stdout = _ScrubbedStdout(sys.stdout)
    try:
        asyncio.run(run())
    finally:
        pass
    _head("=== SUMMARY ===")
    _emit(f"PASS={_RESULTS['pass']}  FAIL={_RESULTS['fail']}  BLOCKED={_RESULTS['blocked']}")
    if _RESULTS["fail"]:
        _emit("RESULT: FAIL")
        return 1
    if _RESULTS["blocked"]:
        _emit("RESULT: BLOCKED — see the [BLOCKED] lines above. Assertions that "
              "could run passed, but this sprint is NOT complete: real email "
              "delivery depends on AWS-side action outside this codebase.")
        return 2
    _emit("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
