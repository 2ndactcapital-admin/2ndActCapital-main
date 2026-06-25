# 2nd Act Capital — Reference Data

## Fixed UUIDs

### Org
2nd Act Capital: 00000000-0000-0000-0000-000000000001

### Test User (verify scripts)
ID: 99000000-0000-0000-0000-000000000001
auth0_sub: auth0|test_verify_user

### Roles
super_admin:          00000000-0000-0000-0000-000000000010
admin:                00000000-0000-0000-0000-000000000011
advisor:              00000000-0000-0000-0000-000000000012
member:               00000000-0000-0000-0000-000000000013
next_gen:             00000000-0000-0000-0000-000000000014
member_manager:       00000000-0000-0000-0000-000000000015
investment_staff:     00000000-0000-0000-0000-000000000016
support_staff:        00000000-0000-0000-0000-000000000017
compliance_jr:        00000000-0000-0000-0000-000000000018
compliance_sr:        00000000-0000-0000-0000-000000000019
fund_finance:         00000000-0000-0000-0000-000000000020
ir_member_relations:  00000000-0000-0000-0000-000000000021
investment_committee: 00000000-0000-0000-0000-000000000022

### Seed Entities
Hargrove Family Trust: 10000000-0000-0000-0000-000000000001
Hargrove Capital LLC:  10000000-0000-0000-0000-000000000002
James Hargrove:        10000000-0000-0000-0000-000000000003
Stonegate REIT I:      10000000-0000-0000-0000-000000000004
Meridian Foundation:   10000000-0000-0000-0000-000000000005

## Entity Type Enum Values
individual, trust, llc, lp, gp,
s_corp, c_corp, corp_uk, corp_eu,
corp_cayman, corp_luxembourg, corp_other_intl,
family_office, household, foundation, other

## Role Hierarchy (highest to lowest)
super_admin > admin > investment_committee >
investment_staff > compliance_sr > advisor >
compliance_jr > member_manager >
ir_member_relations > fund_finance >
support_staff > member > next_gen

## Verify Script Teardown Order
Delete in this FK-safe order:
  1. investment_stage_history
  2. member_investments
  3. deal_ai_summaries
  4. deal_scores
  5. deal_votes
  6. deal_interest
  7. compliance_override_requests
  8. UPDATE deal_documents SET reviewed_by=NULL
  9. deal_documents
  10. deals
  11. member_target_allocations
  12. entity_ownership
  13. entities
  14. users (last)

## Sprint History
- Sprint 0: Infrastructure
- Sprint 1: Turborepo monorepo + app shell
- Sprint 2: Entity/CRM core
- Sprint 2b: CRM schema enhancement
- Sprint 3: Auth0 audience + Investment Profile
- Sprint 5: Marketplace
- Sprint 6: Asset taxonomy
- Sprint 6b: UX fixes
- Sprint 7: Document workflow + AI + deal stages
- Sprint 8: Asset class visualizations

## Config Table Categories
asset_taxonomy    → taxonomy tree (SC/MC/Sub)
deal_scoring      → 6 scoring dimensions
deal_stages       → deal pipeline stages
investment_stages → member investment stages
document_statuses → document workflow statuses
roles_config      → operational role labels
