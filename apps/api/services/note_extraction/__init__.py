"""noteextractb1 — the note-term extraction engine.

Cheapest and most exact first: rules -> EdgarTools -> trimmed text -> two
independent readers (Model 1, Model 2) -> compare in code -> Jev on
disagreements only -> escalation -> needs_review. Every value any source
produces is stored as a reading (portfolio.note_term_readings); resolved values
go to STAGING (portfolio.note_extraction_staging / _staged_fields). Nothing in
this package writes to portfolio.securities_global or
portfolio.securities_global_note_terms — B2 promotes.

Modules:
    schema      registry-driven field spec (the registry is the ONLY source), list members, ranges,
                JSON schema, Pydantic model, normalisation
    documents   load a stored filing (R2, gzip or identity) and map quotes to raw-HTML offsets
    rules       label-anchored rules for labeled fields, ranges, the hypothetical table
    label_dictionary  the per-bank label dictionary, GENERATED from an inventory run
    derive      tenor, max principal loss, decision B's unstated estimated-value unit
    checks      self-checks that send a note to needs_review with a reason
    edgartools_reader   EdgarTools on the STORED HTML with the network blocked
    trim        heading-rule trimming + the recall measure
    proxy       the ONE HTTP chokepoint for ensemble calls through the LiteLLM proxy
    readers     prompt + structured output + provenance for Model 1 / Model 2 / escalation
    jev         one Jev call per note carrying every disputed question
    compare     agreement / verification / candidate building
    spend       the per-run hard spending cap and the dry-run planner
    participants  distribution-participant reference data + matching
    store       readings / runs / staging writes (platform_scope)
    cascade     the per-note pipeline
    gold        the gold set (human-only writes) and the sampler
    metrics     the evaluation harness's pure metric functions
"""
