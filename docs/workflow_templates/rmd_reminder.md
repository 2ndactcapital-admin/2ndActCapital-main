# Required minimum distribution reminder

```json template
{
  "key": "rmd_reminder",
  "title": "Required minimum distribution reminder",
  "category": "tax",
  "parameters": [
    {
      "name": "rmd_age",
      "label": "RMD age",
      "kind": "integer",
      "min": 70,
      "max": 80,
      "default": 73,
      "required": true
    },
    {
      "name": "reminder_month",
      "label": "Month the reminders go out",
      "kind": "choice",
      "default": "October",
      "required": true,
      "choices": [
        {
          "key": "September",
          "label": "September"
        },
        {
          "key": "October",
          "label": "October"
        },
        {
          "key": "November",
          "label": "November"
        }
      ]
    }
  ]
}
```

Each year, list the members at or past the required-minimum-distribution age who hold tax-deferred accounts, read their balances, estimate each member's required minimum distribution, send the reminder so the member confirms the distribution instructions, and record the confirmation.

The estimate is made by a person; the workflow only reads balances. No distribution is made by the workflow.
