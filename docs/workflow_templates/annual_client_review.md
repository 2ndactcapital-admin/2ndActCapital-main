# Annual client review

```json template
{
  "key": "annual_client_review",
  "title": "Annual client review",
  "category": "reviews_compliance",
  "parameters": [
    {
      "name": "lead_days",
      "label": "Start preparing this many days before the meeting",
      "kind": "integer",
      "min": 7,
      "max": 60,
      "default": 21,
      "required": true
    },
    {
      "name": "adviser",
      "label": "Adviser",
      "kind": "profile",
      "required": false,
      "help": "The Profile whose members hold the review meeting."
    }
  ]
}
```

Once a year, ahead of each member's review meeting: gather the member's current allocation and holdings, prepare the portfolio X-ray and draft the review pack, hold the adviser meeting, draft the meeting note and file it.

The two look-ups only read. The meeting-note step is a reference step — in a workflow run it records the step; the adviser writes and files the note.
