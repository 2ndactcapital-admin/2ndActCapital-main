PERMS1 FIX 1 — make verify_perms1.py pass. 3 FAILs: one may be a masking
defect in the new household screen (security — first), one is teardown
blocked by an immutability rule, one is the staffroles1 regression's seeded-
grant check reacting to perms1's own change. 4 tasks. Branch perms1-work
(perms1 = ff54ef0). Decide code vs verify with evidence; never weaken an
assertion.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (from verify_perms1's run,
2026-11-04 ~12:25 ET: TOTAL 131 PASS, 3 FAIL; everything else — one source
of permissions, org-admin baseline, deal and household creation, WF/REP
fixes, test-row removal — passed; keep it):
- FAIL A (FIRST): "the household detail shows the mask, never the full
  number — 200". verify_perms1.py:995 asserts the household detail body
  contains "*******6789" and not "0042"; the stored-row checks just above
  PASSED (hash correct, full number nowhere on the row, "0042" absent from
  every column). So the detail endpoint either renders the mask in a
  different shape (e.g. "****6789" or "…6789" rather than seven asterisks)
  — VERIFY defect: assert on the stored account_number_masked value, not a
  hand-typed pattern — or leaks "0042" from somewhere other than the row
  (a label, a note, the request echo) — CODE defect: find the string in the
  body and remove it. Print the body's account fields in the detail.
- FAIL B: "teardown raised — InsufficientPrivilegeError:
  account_authority_settings rows are never deleted": the household/account
  fixture created rep7b authority settings (through the real "Add account"
  path), and that table's immutability rule refuses DELETE. VERIFY defect:
  do what verify_rep7b and _verify_cleanup do for this table (the documented
  privileged / GUC path for fixture rows by id, or archive via valid_to /
  system_to if that is the rule) — never weaken the trigger; teardown must
  end with zero fixture rows of every kind.
- FAIL C: regression verify_staffroles1.py → 135 PASS, 1 FAIL: "support
  persona's DB permissions == its seeded grant — missing []; extra
  ['manage_documents']". perms1 made the Principal = role ∪ profile ∪
  permission sets; the support-staff fixture carries the "CSA / Ops" profile
  (which grants manage_documents), so the Principal now correctly shows it.
  The staffroles1 check compared the Principal with the ROLE grant only, a
  premise perms1 changed by design. VERIFY defect in verify_staffroles1:
  compare with the union the fixture actually holds (role + profile + sets)
  — the assertion "no permission appears that was not granted somewhere"
  stays; and add the opposite check that a key granted ONLY on a profile
  that is staff-only still does not reach a member fixture. Say which
  lines.
- WSL memory is limited: do NOT run verify_perms1.py, verify_staffroles1.py
  or npm run build; --self-checks-only and a one-off request MAY be run.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts; run every command synchronously in the
foreground; no network beyond Doppler hydration; never print a secret or a
full account number. A one-off script uses only your is_test fixture rows
and removes them. Commit on perms1-work with message "perms1 fix 1: <one
line per fix, code vs verify>".

=== TASK 1: DISCOVER === A–C: cause, code or verify, evidence (A: the exact
body fields and where any "0042" comes from).
=== TASK 2: FIX === code (A if a leak) and the verifies.
=== TASK 3: PROVE === a one-off household + account through the real path:
the detail body contains the stored mask and no full number; teardown of
that fixture leaves zero rows including account_authority_settings;
verify_staffroles1 --self-checks-only → 0 FAIL.
=== TASK 4: UPDATE PROJECT STATUS === one UPDATE line under perms1.
