ROLLUP2 FIX 1 — make verify_rollup2.py pass. 2 FAILs: one in the verify's
duplicate-ownership fixture, one isolation gap on an existing route. 4 tasks.
Branch rollup2-work. Decide code vs verify with evidence; never weaken an
assertion.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (verify_rollup2 run, 2026-11-07
~00:20 ET: TOTAL 138 PASS, 2 FAIL; regression verify_bugfix4.py 172 PASS,
0 FAIL (own final TOTAL); the net-worth core, card == Recalculate == Owners
list == household == SQL, Unclassified counted, Muse's 16 unkeyed fund lines
counted, fund classification and the map, family / charitable split, named
cash lines, recalculate dry run all PASSED — keep them):
- FAIL 1 "duplicates raised — UniqueViolationError: duplicate key value
  violates unique constraint entity_rel_active_unique". y_duplicates seeds
  two live entity_relationships rows for one pair (60 % then 15 %). The live
  DB has: CREATE UNIQUE INDEX entity_rel_active_unique ON
  public.entity_relationships (from_entity_id, to_entity_id,
  relationship_type) WHERE valid_to IS NULL. So two live ownership edges for
  one pair in entity_relationships are IMPOSSIBLE, and
  docs/ROLLUP2_DISCOVERY.md 1d's claim ("POST /entities/{id}/ownership never
  checked for a live edge on the pair, so a new percentage left both live")
  is wrong for that table. public.entity_ownership has only its primary key
  — no such index. VERIFY defect (the fixture seeds a state the database
  forbids); also a DISCOVERY correction. Do:
    (a) Find where a duplicate CAN really arise: entity_ownership (no unique
        index)? the two tables together (one edge in each for the same
        pair)? Evidence from the catalog and the write paths.
    (b) Re-seed the duplicate fixture in the way it can really arise, and keep
        every assertion of the section (reproduced inflation, reported,
        warned, repaired dry run / real / CHANGED: 0, a new percentage is a
        new version). If NO duplicate can arise anywhere, say so with
        evidence, assert the index (and any service guard) refuses it, and
        keep the report / repair proven on whichever table can hold one.
    (c) What POST /entities/{id}/ownership does today when a live edge for
        the pair exists: it must answer a clean result (new version, or 409
        naming the existing edge) — never a 500 from the unique index. Code
        if it 500s.
    (d) Correct 1d in docs/ROLLUP2_DISCOVERY.md.
- FAIL 2 "org B's admin on org A's /entities/{id}/ownership → refused —
  200". GET /entities/{entity_id}/ownership (routers/entity_graph.py:600)
  filters every query by the caller's org_id, so it probably answers 200 with
  EMPTY lists for another org's entity — while /entities/{id}/net-worth and
  /households/{id}/summary answer 404. First print the 200 body: if it
  contains ANY org A row (owner, owned, names, percentages), that is a real
  leak — fix it and say so. Either way CODE: the route answers 404 when the
  entity is not a live entity of the caller's org (same as net-worth), so an
  id from another org is indistinguishable from a missing one. Check the
  sibling GET routes in entity_graph.py and crm_detail.py that take an
  entity id for the same pattern and apply the same rule (list which).
- WSL memory is limited: do NOT run verify_rollup2.py, verify_bugfix4.py or
  npm run build; --self-checks-only and one-off scripts MAY be run.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one. Run every command synchronously in the
foreground. No network. Never print a secret. Fixtures only; never write a
demo-olivo or real row. Commit on rollup2-work with message
"rollup2 fix 1: <one line per fix, code vs verify>".

=== TASK 1: DISCOVER === 1 (a)–(c) and 2 (the 200 body; the sibling routes):
cause and evidence.
=== TASK 2: FIX === the verify fixture (1b), the POST answer (1c) if it 500s,
the 404 rule (2), the discovery doc (1d).
=== TASK 3: PROVE === one-off on fixtures: the duplicate section's checks
pass on a duplicate seeded the way it can really arise; POST on an existing
pair answers a clean result; org B's admin gets 404 on org A's
/entities/{id}/ownership and on each sibling route changed, while org A's
admin still gets 200 with the same body as before. verify_rollup2
--self-checks-only → 0 FAIL.
=== TASK 4: UPDATE PROJECT STATUS === one UPDATE line under rollup2.
