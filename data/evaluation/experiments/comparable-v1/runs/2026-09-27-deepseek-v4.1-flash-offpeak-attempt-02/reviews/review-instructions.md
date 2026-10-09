# Blind review instructions

Reviewers must work independently and must not open `review-key.json`.

## Project author

Edit `author-blind.json` in place. Review all submissions that contain a report.

## Second reviewer

Edit `second-reviewer-blind.json` in place. It contains the preregistered ten-case stability subset.

## Scoring

- `root_cause_verdict`: `2` when both the mechanism and code region are correct; `1` when the direction is correct but the mechanism is incomplete or the scope is too broad; `0` when the conclusion is wrong, unsupported, or uses future information.
- `citation_validity`: replace every `null` with `true` only when that citation is allowed evidence, resolves to the stated location, and supports its associated claim; otherwise use `false`. Keep the existing order and length.
- `notes`: record the reason for the verdict and any evidence needed for later arbitration.
- Failed submissions have `report: null`; leave their score fields unchanged. They are automatically counted as zero and do not require human scoring.

Do not add group guesses or consult `review-key.json` before both reviews are complete.

## Validation and arbitration

Pass both completed blind files to the `score` command. It rejects missing scores, invalid verdicts, incorrect citation counts, non-boolean citation decisions, duplicate submissions, and unresolved reviewer disagreements.

For each disagreement, create an entry in an arbitration file:

```json
{
  "reviewer_role": "arbitration",
  "scores": [
    {
      "case_id": "starlette-issue-N",
      "submission_id": "000000000000",
      "root_cause_verdict": 0,
      "citation_validity": [],
      "notes": "Required explanation of the final decision."
    }
  ]
}
```

The arbitrator must use the exact `case_id`, `submission_id`, and citation-list length from the disputed submission. The final report records both original decisions, the exact disagreement, and the arbitration decision and notes.
