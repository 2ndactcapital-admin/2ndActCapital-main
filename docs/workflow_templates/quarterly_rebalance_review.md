# Quarterly rebalance review

```json template
{
  "key": "quarterly_rebalance_review",
  "title": "Quarterly rebalance review",
  "category": "trading_rebalancing",
  "parameters": [
    {
      "name": "drift_threshold",
      "label": "Drift threshold (percentage points)",
      "kind": "integer",
      "min": 1,
      "max": 25,
      "default": 5,
      "required": true
    },
    {
      "name": "approver",
      "label": "Approver",
      "kind": "profile",
      "required": false,
      "help": "The Profile whose members give the second-person approval."
    }
  ]
}
```

Each quarter, read every account's current allocation, check drift against the model targets, draft rebalance proposals for the accounts whose drift is past the threshold, have a second person approve the proposals, and record the decision.

Nothing here trades. Proposals are drafted and approved by people on the existing proposal screens; the workflow tracks the review.
