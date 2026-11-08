# Tax-loss harvesting sweep

```json template
{
  "key": "tax_loss_harvesting_sweep",
  "title": "Tax-loss harvesting sweep",
  "category": "tax",
  "parameters": [
    {
      "name": "loss_threshold",
      "label": "Minimum unrealized loss to consider (USD)",
      "kind": "integer",
      "min": 100,
      "max": 1000000,
      "default": 1000,
      "required": true
    },
    {
      "name": "approver",
      "label": "Approver",
      "kind": "profile",
      "required": false,
      "help": "The Profile whose members approve harvest proposals."
    }
  ]
}
```

Periodically sweep taxable accounts for lots with unrealized losses over a threshold, check the wash-sale windows and replacement securities, draft harvest proposals, and have an approver approve or decline them.

Nothing here trades: the sweep, the checks and the proposals are done by people; a harvest only happens through the existing approval and execution screens.
