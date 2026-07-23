# Document classifier eval fixtures

Drop real, ground-truth document cases here to evaluate the S25 open-set
document-type classifier on real data. **No code change is needed** — the eval
harness (`apps/api/scripts/eval_document_classifier.py`) globs every `*.json` in
this directory at run time.

Each file is either one object or a list of objects:

```json
{ "text": "<full extracted document text>", "expected_category": "<doc_category code>" }
```

`expected_category` must be a `code` from `reference_data` where
`list_key = 'doc_category'` (e.g. `k1`, `will`, `trust_instrument`, `other`).

While this directory holds no fixtures, the harness falls back to a small set of
**obviously-synthetic placeholder** snippets embedded in the script and prints an
unmissable banner warning that the resulting score is **not** representative of
real-world accuracy. Once real documents land here, that fallback and banner
switch off automatically.

Do **not** commit real client documents to the repo — this directory is for
local/secure eval runs only.
