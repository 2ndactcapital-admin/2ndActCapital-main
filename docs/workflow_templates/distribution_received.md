# Distribution received

```json template
{
  "key": "distribution_received",
  "title": "Distribution received",
  "category": "cash_transfers",
  "parameters": [
    {
      "name": "notify_channel",
      "label": "How members are told",
      "kind": "choice",
      "default": "a portal message",
      "required": true,
      "choices": [
        {
          "key": "a portal message",
          "label": "Portal message"
        },
        {
          "key": "email",
          "label": "Email"
        },
        {
          "key": "a phone call",
          "label": "Phone call"
        }
      ]
    }
  ]
}
```

When an SPV distribution is received, list the open SPVs, propose the carry waterfall for the realization, record the distribution and allocate it to the members, and tell the members.

The carry proposal is the one automated step that writes, and it needs approval: the run stops there until a second person approves it, and even then it only records a DRAFT carry run — it never posts and moves no money. Recording and allocating the distribution is done by a person.
