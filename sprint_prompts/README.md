# sprint_prompts/

Save each sprint's Part 3 prompt here, named to declare its
risk tier explicitly:

    sprint23.structural.md   -> schema/logic changes, ledger,
                                 money, anything with real
                                 blast radius. Verify passes ->
                                 pushed to the feature branch,
                                 held for YOUR manual review
                                 before merging to main.

    sprint24.lowrisk.md      -> UI fixes, reference data, docs,
                                 cosmetic/isolated changes.
                                 Verify passes -> auto-merged
                                 to main, no review needed.

Run with:

    ./scripts/run_sprint.sh sprint23.structural
    ./scripts/run_sprint.sh sprint24.lowrisk

You choose the tier. When in doubt, use .structural — it costs
you one manual review, not a production incident.

Logs land in sprint_prompts/logs/, including a running
decision_log.jsonl (timestamp, sprint, risk tier, cost, duration,
turns, result) — this is the seed of the future TaskRouter (S27)
decision log, so it's worth keeping around from day one.
