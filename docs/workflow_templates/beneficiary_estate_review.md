# Beneficiary and estate document review

```json template
{
  "key": "beneficiary_estate_review",
  "title": "Beneficiary and estate document review",
  "category": "client_service",
  "parameters": [
    {
      "name": "review_month",
      "label": "Month of the annual review",
      "kind": "choice",
      "default": "January",
      "required": true,
      "choices": [
        {
          "key": "January",
          "label": "January"
        },
        {
          "key": "April",
          "label": "April"
        },
        {
          "key": "July",
          "label": "July"
        },
        {
          "key": "October",
          "label": "October"
        }
      ]
    },
    {
      "name": "adviser",
      "label": "Adviser",
      "kind": "profile",
      "required": false,
      "help": "The Profile whose members review the documents."
    }
  ]
}
```

Once a year, remind the adviser to open the beneficiary review, look up the member's ownership hierarchy, review the beneficiary designations and estate documents with the member, and record any changes the member asks for.

The look-up only reads; every change is made by a person.
