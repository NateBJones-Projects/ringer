# Ringer model bakeoff starter kit

## What it is

This kit is the runnable sample for the [Chinese-model bakeoff
guide](https://unlock-ai.natebjones.com/guides/chinese-model-bakeoff). It pairs a
five-claim research task with local source documents, a deterministic validator,
two fixtures, a model-only Ringer manifest, and a score sheet.

The validator checks completeness, output structure, allowed statuses, and
literal source evidence. It does not decide whether each research verdict is
intellectually correct. Do that in the blind review described in the guide.

## Files

```text
ringer-bakeoff-kit/
├── check_result.py
├── score-sheet.csv
├── swarm.model-only.example.json
├── README.md
├── fixtures/
│   ├── bad-result.json
│   └── good-result.json
└── source-packet/
    ├── claims.json
    └── sources/
        ├── security-note.md
        └── travel-policy.md
```

Everything runs locally. The validator uses Python 3.11 or newer, imports only
the standard library, and makes no network calls.

## About score-sheet.csv — do not re-enter what Ringer already tracks

Eight of the fourteen columns are already computed for you on the **Models view
in Ringside** (`./ringer.py hud`, then switch to Models), built from your own
attempt history:

`exact_model_id` · `lab` · `harness` · `provider_or_plan` ·
`first_try_acceptance` · `acceptance_after_retry` · `total_duration` ·
`reported_tokens`

Copy those across, or just read them there. The six that are genuinely yours to
fill are the ones Ringer cannot see:

`case_type` · `ringer_commit` · `human_repair_minutes` · `provider_charge` ·
`tool_and_infrastructure_cost` · `failure_type`

That split is the point of the sheet. Ringer knows what happened; it does not
know what it cost you. The money and the minutes are what turn a pass rate into
a business decision.

## Prove the checker

Go to the included kit and run the known-good fixture:

```bash
cd '/ABSOLUTE/PATH/TO/ringer-bakeoff-kit'

python3 check_result.py \
  fixtures/good-result.json \
  --claims source-packet/claims.json \
  --sources source-packet/sources
```

Expected result:

```text
PASS: every requested claim is present, the schema is valid, and all cited evidence appears in an allowed source
```

Now run the known-bad fixture:

```bash
python3 check_result.py \
  fixtures/bad-result.json \
  --claims source-packet/claims.json \
  --sources source-packet/sources
```

The second command must exit nonzero and name the invented quotation, missing
source, invalid status, and omitted claim.

## Use it for your work

Copy `swarm.model-only.example.json` to `swarm.json`. Replace every
`/ABSOLUTE/PATH/TO/ringer-bakeoff-kit`, both model-ID placeholders, and the dated
work directory. Keep the assignment, sources, reasoning variant, timeout, and
check identical for both model cells.

To replace the sample task, edit `source-packet/claims.json` and the Markdown
files under `source-packet/sources/`. Keep stable claim IDs. Each result row must
contain `claim_id`, `status`, `source`, `quote`, and `explanation`. Run the
validator directly against your own result before running Ringer:

```bash
python3 check_result.py \
  /ABSOLUTE/PATH/TO/your-result.json \
  --claims /ABSOLUTE/PATH/TO/your-claims.json \
  --sources /ABSOLUTE/PATH/TO/your-sources
```

Add known-good and known-bad fixtures for any new output rule. A checker that
accepts a bad fixture or rejects a good fixture is not ready to grade a model.
