# Ledger Migration

The current schema version is `2`.

Run:

```bash
python scripts/rebuild_clean_feedback_metrics.py
```

The script loads the existing ledger, preserves all entries, marks legacy tennis
spreads/totals as `legacy_invalid=true` and `feedback_eligible=false`, then writes a
JSON report in `data/reports/`.

Rollback is data-preserving: restore the previous ledger backup or remove migration
marker fields from affected entries. Do not delete historical entries without an
explicit backup and review.

Production migrations must run against the authoritative database backend. A local
JSON file on Render is only a diagnostic mirror.
