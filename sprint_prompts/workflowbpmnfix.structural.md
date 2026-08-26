WORKFLOW BPMN GENERATION — XML PARSE FAILURE. 4 tasks +
verification. Real, confirmed live error:

generation failed validation after one retry: BPMN did not
parse / derive: expected '>', line 84, column 13

Reproducing input — workflow name: "birthdays", description:
"check the crm for any birthdays today and send an pre-formatted
email to those who have a birthday today. run daily"

HYPOTHESIS, NOT CONFIRMED — investigate, do not assume: this
error signature (expected '>' at a specific position) commonly
indicates unescaped special characters (&, <, ', ") from user-
supplied text embedded directly into a BPMN XML attribute without
escaping. Find the REAL cause before fixing it.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue
immediately. If uncertain, continue.

=== TASK 1: DISCOVER — reproduce and find the real cause ===
  1a. Reproduce the EXACT failure using the exact name/description
      above, against the real generation code path (Workflow
      Manager Wave 2's NL-to-BPMN generator). Capture the actual
      generated XML BEFORE it fails validation, and identify
      exactly what is at line 84, column 13.
  1b. Confirm whether user-supplied text (name, description, or
      any AI-derived task label built from them) is embedded into
      BPMN XML attributes WITHOUT proper XML escaping anywhere in
      the generation path. Report the exact function/line.
  1c. Test whether the SPECIFIC text in this reproduction case
      contains a character that would break unescaped XML (this
      description contains an apostrophe-free but check for any
      real trigger character present).

=== TASK 2: FIX ===
Ensure every piece of user-supplied or AI-derived text embedded
into BPMN XML is properly escaped (standard XML entity escaping:
&amp; &lt; &gt; &quot; &apos;) at the point of insertion — not
just for this one case, but for every text field the generator
embeds.

=== TASK 3: REAL PROOF ===
  - The EXACT original failing input (name "birthdays", the full
    description given) now generates successfully.
  - A NEW test case with a deliberately adversarial description
    containing &, <, >, and an apostrophe all together succeeds
    and produces valid, parseable XML.
  - A previously-working, simple workflow generation is
    unaffected (regression check).

=== VERIFICATION: apps/api/scripts/verify_workflowbpmnfix.py ===
Pass/fail only.

Assertions:
  [Y] Report Task 1's three findings explicitly, including the
      EXACT real cause (not the hypothesis restated as fact)
  [Y] The original failing input now generates valid, parseable
      BPMN XML
  [Y] An adversarial input with &, <, >, and ' all present
      succeeds
  [Y] A previously-working simple workflow is unaffected
  [Y] Teardown: zero leftover rows
