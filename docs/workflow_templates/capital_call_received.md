# Capital call received

```json template
{
  "key": "capital_call_received",
  "title": "Capital call received",
  "category": "alternatives",
  "parameters": [
    {
      "name": "notice_days",
      "label": "Days the notice allows for funding",
      "kind": "integer",
      "min": 1,
      "max": 30,
      "default": 10,
      "required": true
    },
    {
      "name": "approver",
      "label": "Wire approver",
      "kind": "profile",
      "required": false,
      "help": "The Profile whose members approve the wire instruction."
    }
  ]
}
```

When a capital call notice arrives, match it to the member's commitment, check that the funding account covers it, draft the wire instruction before the notice's deadline, have a second person approve the instruction, and confirm funding with the general partner.

No money moves through this workflow. The wire is drafted, approved and sent by people; the workflow only lists open SPVs and tracks the steps.
