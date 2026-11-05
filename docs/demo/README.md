# Demo data — index

Permanent, fictional demo data that lives in a real tenant and is shown to prospects. It is entered through the
front end, exactly as a firm's staff would onboard a client — never seeded by a script.

| Demo | Folder | Tag | Entered by | What it is |
|---|---|---|---|---|
| The Olivo family | `olivo/` | `demo-olivo` | HollisMuse, from `olivo/HOLLISMUSE_OLIVO_DATA_ENTRY_v1.md` | A post-liquidity multigenerational family: 16 people, 26 legal entities (trusts, LLCs, an LP, a foundation, a DAF), 8 outside organisations, 62 addresses incl. seasonal homes, 117 relationships, 29 ownership links, 34 accounts, 95 holdings, 19 direct assets, 7 liabilities |

## How to find it
- CRM: search any Olivo name, or filter records by the tag (`demo-olivo`).
- Portfolio → Households → `Olivo Family`.
- Every record of a demo carries a tag beginning `demo-`; that prefix is what protects it.

## The protection rule (crmdetail1)
Any record tagged `demo-*` (entities.tags), and any household whose members are all demo-tagged, is **permanent**:
- the database refuses to delete it — a BEFORE DELETE guard on the CRM tables (entities, households, memberships,
  relationships, attributes, addresses, notes, ownership, liabilities) raises for any demo row of a real org; the
  only path past it is the verify-fixture GUC `crm.demo_teardown = 'fixture'`, honoured for `is_test` orgs only;
- `apps/api/scripts/_verify_cleanup.py` (every verify teardown) refuses a delete set that touches a demo row, and the
  HollisMuse removal scripts (`perms1_remove_muse_rows.py`, `bugfix2_remove_muse_rows.py`) refuse a plan holding one;
- the app refuses to delete a demo household (409); staff may still edit demo records and correct memberships.

Throwaway test rows use the `MUSE –` name prefix and are removed by clean-up scripts. Never mix the two: a demo
record must never carry the `MUSE –` prefix, and a test row must never carry a `demo-` tag.
