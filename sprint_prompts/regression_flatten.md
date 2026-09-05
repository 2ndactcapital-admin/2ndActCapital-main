verify_udf01b.py, verify_udf01c.py, and verify_udf02a.py each call their
predecessor's full script as a regression gate, but the predecessors ALSO
call theirs — so verify_udf01a runs 4x, verify_udf01b runs 3x, verify_udf01c
runs 2x in a single verify_udf02a execution. Fix: each script calls only its
DIRECT predecessor's main() ONCE. verify_udf01c must NOT let verify_udf01b's
own internal call to verify_udf01a run again — call verify_udf01a and
verify_udf01b each exactly once, flat, not nested.

Read verify_udf01b.py, verify_udf01c.py, verify_udf02a.py in full. Refactor
the regression-gate sections so each earlier script's main() is invoked
exactly once per run, regardless of depth. Then run all four
(01a, 01b, 01c, 02a) yourself via doppler run, in order, synchronously.
Paste full literal stdout of all four. Confirm all still pass at their
known baselines (119/0/0, 104/0/2, 93/0/2, and 02a per its last fix).
Do not end turn without real captured output.

Do not commit or push.
