FIX verify_schedulernotify.py's SELF-CONTAMINATED DISCOVERY
CHECKS. 1 task. The three [FAIL] lines in the last run (Tasks 1a,
1b, 1c) are NOT functional defects — Tasks 2 and 3's actual real
work (35 genuine passes: cross-org isolation, idempotent orphan
sweep, correct expiring-schedule warnings) is fully correct and
must not be touched.

THE BUG: each of the three discovery checks searches
apps/api/services/workflow_scheduler.py's CURRENT content to
establish a "pre-sprint" fact, but by the time verify runs, this
SAME sprint's own Task 2/3 code already added real todo-writing
calls (the orphan sweep, the expiring-schedule warning) to that
same file — contaminating a check meant to measure the file's
state BEFORE those additions.

FIX: scope each of the three checks narrowly to the SPECIFIC gap
being asked about (a consecutive-skip alert; orphan cleanup; a
forward-looking end-of-life warning), not "does this file call a
todo writer for ANY reason." Confirm the three findings' PROSE
text (which is correct) and align the boolean check to match it.

Do not change Task 2 or Task 3's actual functional code. Re-run
verify and confirm all assertions pass, then push and merge.
