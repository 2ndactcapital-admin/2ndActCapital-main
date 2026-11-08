# Held-away statement refresh

```json template
{
  "key": "held_away_statement_refresh",
  "title": "Held-away statement refresh",
  "category": "client_service",
  "parameters": [
    {
      "name": "approver",
      "label": "Approver",
      "kind": "profile",
      "required": false,
      "help": "The Profile whose members approve held-away drafts."
    }
  ]
}
```

Refresh a member's held-away accounts: request the latest statement, import it as a held-away draft, have a second person approve the draft, then read the updated allocation.

The import and the approval happen on the existing held-away screens; the final look-up only reads.
