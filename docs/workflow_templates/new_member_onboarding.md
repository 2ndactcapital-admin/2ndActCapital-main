# New member onboarding checklist

```json template
{
  "key": "new_member_onboarding",
  "title": "New member onboarding checklist",
  "category": "onboarding",
  "parameters": [
    {
      "name": "kyc_reviewer",
      "label": "KYC reviewer",
      "kind": "profile",
      "required": false,
      "help": "The Profile whose members review KYC / AML; leave empty to assign later."
    },
    {
      "name": "welcome_days",
      "label": "Send the welcome pack within (days)",
      "kind": "integer",
      "min": 1,
      "max": 30,
      "default": 5,
      "required": true
    }
  ]
}
```

When a new member joins, collect their onboarding documents (identity, entity documents, investment profile), have a KYC and AML reviewer clear them, look up the member's ownership hierarchy so every owning entity is on file, complete the account-opening tasks (forms and signatures), and send the welcome pack within the agreed number of days.

Every step except the hierarchy look-up is done by a person. The look-up only reads; it changes nothing.
