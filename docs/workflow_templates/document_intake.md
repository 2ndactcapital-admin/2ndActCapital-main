# Document intake

```json template
{
  "key": "document_intake",
  "title": "Document intake",
  "category": "documents",
  "parameters": [
    {
      "name": "reviewer",
      "label": "Document reviewer",
      "kind": "profile",
      "required": false,
      "help": "The Profile whose members confirm extracted fields."
    }
  ]
}
```

When a document arrives, upload it, classify it in Chancery (or confirm the classification Chancery proposed), check the open review tasks, have a reviewer confirm the extracted fields, and file the document to the member's record.

Chancery's own pipeline runs separately; this workflow tracks the people's steps around it.
