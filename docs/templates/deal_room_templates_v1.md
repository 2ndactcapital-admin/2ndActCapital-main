# Deal Room — standard agreement templates (DRAFT v1)

DRAFT for counsel review before outside publishers go live. Generalised from the
MedEquity / Caerus / Invicta Non-Disclosure and Non-Circumvention Agreement
(Aug 21, 2026) and the Caerus Placement joinder (Oct 21, 2026). Not legal advice.

Fields in {{double_braces}} are filled from the deal's settings (publisher) or
the CRM (recipient). Fields marked [optional] drop their sentence when empty.

---

## TEMPLATE 1 — NON-DISCLOSURE AND NON-CIRCUMVENTION AGREEMENT

key: platform_nda · version: 1

This Non-Disclosure and Non-Circumvention Agreement (the "Agreement") is made as
of {{signature_date}} between {{publisher_legal_name}}, a {{publisher_state}}
{{publisher_entity_type}} with an office at {{publisher_address}} (the
"Discloser"), and {{recipient_name}}{{recipient_entity_clause}} (the
"Recipient").

1. Purpose. The Discloser intends to share information about a possible
   investment opportunity referred to on the platform as "{{deal_name}}" (the
   "Opportunity") so the Recipient can evaluate it (the "Purpose").

2. Confidential Information. "Confidential Information" means all information
   the Discloser, or anyone on its behalf, provides about the Opportunity, the
   Discloser, any sponsor or target company, in any form and whether or not
   marked confidential, together with any analyses or notes the Recipient
   prepares that contain or reflect it. It includes the existence of the
   Opportunity and of discussions about it. It excludes information that (a) is
   or becomes public other than through the Recipient's breach, (b) the
   Recipient already lawfully had, (c) the Recipient receives from a third party
   free to disclose it, or (d) the Recipient develops independently.

3. Use and disclosure. The Recipient will use Confidential Information only for
   the Purpose and will keep it confidential. The Recipient may share it only
   with its officers, directors, employees, and legal, tax and financial
   advisors who need it for the Purpose and are bound to keep it confidential
   ("Representatives"). The Recipient is responsible for any breach by its
   Representatives.

4. Required disclosure. If the law requires disclosure, the Recipient will,
   where lawful, give the Discloser prompt notice and disclose only what is
   required.

5. Non-circumvention. [optional: include when {{non_circumvention}} is on]
   For {{non_circumvention_months}} months from the date of this Agreement, the
   Recipient will not, without the Discloser's written consent, contact or deal
   directly with any sponsor, target company or other party introduced through
   the Opportunity in order to pursue the Opportunity or a substantially similar
   transaction, other than through the Discloser.

6. No obligation. Nothing in this Agreement obliges either party to proceed
   with any transaction. No such obligation exists unless set out in a separate
   signed written agreement.

7. Return or destruction. On the Discloser's written request, the Recipient will
   promptly return or destroy Confidential Information in its possession, except
   copies it must keep by law, regulation or a bona fide document retention
   policy, and routine back-ups, all of which stay subject to this Agreement.

8. Remedies. Money damages may not be an adequate remedy for a breach. The
   Discloser may seek specific performance or an injunction, without posting a
   bond, in addition to any other remedy.

9. Term. This Agreement lasts {{term_months}} months from its date. The
   confidentiality obligations in sections 2, 3 and 7 survive for
   {{survival_months}} months after it ends.

10. Governing law. This Agreement is governed by the laws of the State of
    {{governing_law_state}}, and the parties submit to the courts located in
    {{venue}}.

11. General. This is the entire agreement on its subject. It may be signed
    electronically, and an electronic signature has the same effect as a
    handwritten one. If any provision is unenforceable, the rest remains in
    effect.

Signed electronically by {{recipient_signatory_name}}{{recipient_signatory_title_clause}}
for {{recipient_name}} on {{signature_date}}.

Fields:
- publisher_legal_name, publisher_state, publisher_entity_type, publisher_address — deal settings (from the publisher's CRM record)
- deal_name — the deal
- non_circumvention (on/off, default on), non_circumvention_months (default 24)
- term_months (default 24), survival_months (default 24)
- governing_law_state (default Delaware), venue (default "Delaware")
- recipient_name, recipient_entity_clause (", a {{state}} {{entity_type}}" when investing through an entity), recipient_signatory_name, recipient_signatory_title_clause — CRM and the signer
- signature_date — set at signing

---

## TEMPLATE 2 — JOINDER TO CONFIDENTIALITY AGREEMENT

key: platform_joinder · version: 1

{{signature_date}}

{{recipient_name}}
{{recipient_address}}

{{publisher_legal_name}} ("Advisor") has entered into a confidentiality
agreement dated {{underlying_nda_date}} (the "Underlying NDA") with
{{underlying_counterparty}} (the "Counterparty"), protecting confidential
information about a possible transaction referred to on the platform as
"{{deal_name}}" (the "Transaction"). {{recipient_name}} (the "Recipient") has
asked to receive that information to evaluate the Transaction and agrees to this
joinder (the "Agreement") as a condition of receiving it. A copy of the
Underlying NDA is attached as Exhibit A. [optional: when
{{underlying_nda_attached}} is off, replace the last sentence with "The
Recipient acknowledges the Underlying NDA's terms have been made available to
it."]

1. The Recipient will comply with the terms of the Underlying NDA, and the
   restrictions, covenants and agreements that bind Advisor under it, as if the
   Recipient were an original signatory, and will indemnify Advisor against any
   loss, cost or liability Advisor incurs because of the Recipient's breach.
   This Agreement binds the Recipient to the Underlying NDA on the same basis as
   Advisor and lets Advisor enforce it against the Recipient.

2. The Recipient may share information with its officers, directors, employees
   and advisors who need it to evaluate the Transaction ("Representatives") and
   is responsible for any breach of the Underlying NDA by them.

3. Discussions create no obligation on Advisor, the Counterparty or the
   Recipient to proceed with the Transaction or to let the Recipient
   participate. No such obligation exists unless set out in a separate signed
   written agreement.

4. Money damages may not be an adequate remedy for a breach. Advisor may seek
   specific performance or an injunction, without posting a bond, in addition to
   any other remedy.

5. On written request from Advisor or the Counterparty, the Recipient will
   promptly return or destroy the information in its possession, except copies
   it must keep by law, regulation, professional standards or a bona fide
   document retention policy, and routine back-ups, all of which stay subject to
   this Agreement.

6. This Agreement is governed by the laws of the State of
   {{governing_law_state}}, and any action relating to it will be brought only
   in the courts located in {{venue}}.

7. This Agreement is effective from its date until the Underlying NDA expires,
   and its termination does not release the Recipient from claims arising
   before then. It is the entire agreement on its subject, may be signed
   electronically, and an electronic signature has the same effect as a
   handwritten one. If any provision is unenforceable, the rest remains in
   effect.

Accepted and agreed, signed electronically by {{recipient_signatory_name}}{{recipient_signatory_title_clause}}
for {{recipient_name}} on {{signature_date}}.

Exhibit A — Underlying NDA (attached by the publisher as a PDF in the deal's
agreements)

Fields:
- publisher_legal_name — deal settings (the advisor / placement agent)
- underlying_counterparty, underlying_nda_date — deal settings
- underlying_nda_attached (on/off; on requires the publisher to upload the NDA PDF as Exhibit A)
- deal_name — the deal
- governing_law_state (default Delaware), venue (default "Delaware")
- recipient_name, recipient_address, recipient_signatory_name, recipient_signatory_title_clause — CRM and the signer
- signature_date — set at signing
