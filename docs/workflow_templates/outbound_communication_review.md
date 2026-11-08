# Compliance review of outbound communication

```json template
{
  "key": "outbound_communication_review",
  "title": "Compliance review of outbound communication",
  "category": "reviews_compliance",
  "parameters": [
    {
      "name": "compliance",
      "label": "Compliance reviewer",
      "kind": "profile",
      "required": false,
      "help": "The Profile whose members review outbound communication."
    }
  ]
}
```

Before any marketing or client communication goes out: draft it, prepare it for review, have compliance approve it or return it with comments, revise and resubmit when it was returned, then send the approved version and archive it.

The drafting step is a reference step — in a run it records the step; the author writes the draft. Nothing is sent by the workflow.
