TEXTRACT ACCESS — GATE RE-CHECK ONLY. 1 task + verification. Do
NOT build Chancery Phase 3's actual extraction logic yet — this
is purely confirming real AWS Textract access now works, since
credentials were just provisioned. If this passes, Phase 3's
Tasks 2/3 (Textract-calling service + K-1 template mapping) can
be drafted as a genuine follow-on sprint next.

=== TASK 1: Real, live Textract access check ===
Using boto3 with the environment's AWS credentials (AWS_ACCESS_
KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION — do not
hardcode or print these values anywhere, including in logs),
attempt a REAL DetectDocumentText call against a trivial, valid
test image or PDF (generate one with real text, same discipline
as Chancery Phase 1's test-PDF generation). Confirm:
  - The call succeeds (no NoCredentialsError, no
    AccessDeniedException, no region error)
  - The response contains real detected text matching what was
    embedded in the test document
If it fails, report the EXACT error type and message clearly —
do not retry blindly or guess at a fix; a clear, honest failure
report is the correct outcome if something is still misconfigured.

=== VERIFICATION ===
Write verify_textractgate.py (apps/api/scripts/) — pass/fail
only, no interactive prompts, teardown-at-start and teardown-at-
end (delete any test artifacts, no real AWS resources should
persist beyond the API call itself, which has no cleanup needed
since Textract doesn't store anything server-side for this API).

Assertions:
  [Y] boto3 textract client initializes without a credentials
      error
  [Y] A real DetectDocumentText call against a generated test
      document succeeds
  [Y] The detected text matches the real text embedded in the
      test document
  [Y] Report the exact region and confirm it matches
      AWS_DEFAULT_REGION's configured value

Report each assertion explicitly, including the exact error if
anything fails. Push when 100% pass.
