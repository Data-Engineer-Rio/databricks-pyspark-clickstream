# ABC Retail Pipeline Runbook

This runbook tracks the implementation order for the Databricks Lakehouse
pipeline described in `readme.md`.

## Compliance Contract

The authoritative requirement-to-delivery checklist is
`docs/readme_compliance_matrix.md`. A planned notebook is not considered
complete until its acceptance evidence is captured in Databricks.

## Current Phase: Bronze

Import or open the `.ipynb` files in `notebooks/` on Databricks, then run them
in this order.

| Order | Notebook | Purpose |
|---:|---|---|
| 1 | `00_setup_environment.ipynb` | Create the catalog and required schemas |
| 2 | `01_create_control_tables.ipynb` | Create idempotency, audit, watermark, and schema log tables |
| 3 | `02_create_quarantine_tables.ipynb` | Create source-specific error tables |
| 4 | `03_load_fx_rates.ipynb` | Load and merge the simulated FX rates |
| 5 | `04_validate_foundation.ipynb` | Verify all foundation tables are Delta tables |

## Required Parameters

Each notebook exposes Databricks widgets where needed:

| Parameter | Example | Meaning |
|---|---|---|
| `catalog` | `abc_retail` | Unity Catalog catalog used by the pipeline |
| `landing_root` | `/Volumes/abc_retail/dev/landing` | Staging Volume whose direct children are the landing sources |
| `checkpoint_root` | `/Volumes/abc_retail/dev/landing/_checkpoints` | Auto Loader schema and stream checkpoints |
| `job_run_id` | `manual` | Databricks workflow run identifier used for audit |

The configured landing root must contain:

```text
clickstream_events/
orders/
customers/
dim_fx_rates/fx_rates.csv
```

Source-file lineage is read from Unity Catalog's `_metadata.file_path` hidden
column. Pipeline notebooks must not use `input_file_name()`.

## Foundation Verification

After the notebooks complete:

```sql
SELECT * FROM abc_retail.control.audit_pipeline_runs
ORDER BY start_ts DESC;

SELECT * FROM abc_retail.control.data_quality_results
ORDER BY recorded_ts DESC;

SELECT * FROM abc_retail.silver.dim_fx_rates
ORDER BY rate_date, currency;

DESCRIBE DETAIL abc_retail.control.processed_files;
DESCRIBE DETAIL abc_retail.quarantine.clickstream_errors;
```

Expected results:

- All created tables use Delta format.
- `silver.dim_fx_rates` contains 64 unique `(rate_date, currency)` rows for
  the current generated dataset.
- Running `03_load_fx_rates.ipynb` again does not create duplicate FX keys and its
  audit row reports `rows_written = 0` when no rates changed.

## Bronze Execution

After Foundation passes, run:

| Order | Notebook | Purpose |
|---:|---|---|
| 1 | `05_bronze_common.ipynb` | Shared helpers; normally loaded automatically with `%run` |
| 2 | `06_bronze_clickstream.ipynb` | Auto Loader JSON ingestion, schema evolution, schema log, and quarantine |
| 3 | `07_bronze_orders.ipynb` | Mixed JSON/Parquet order ingestion |
| 4 | `08_bronze_customers.ipynb` | Mixed CSV/JSON customer ingestion |
| 5 | `09_validate_bronze.ipynb` | Append-only, lineage, Delta-format, and count reconciliation checks |

The Bronze ingestion notebooks create the `bronze` schema if it is missing,
but Foundation notebooks `01` and `02` must still run first because Bronze
ingestion writes to the control and quarantine tables they create.

`07_bronze_orders.ipynb` reads Parquet files independently before converting
them to raw JSON. This handles batches where a null-only column is physically
stored as Parquet `INT32` while other batches store the same column as
`STRING`.

Run `06`, `07`, and `08` a second time before `09`. The second execution must
report no new files or write zero additional business records.

With Auto Loader `addNewColumns`, discovery of a brand-new clickstream column
can stop the current query after updating its schema location. Rerun
`06_bronze_clickstream.ipynb`; the new column will then be appended to Bronze
and logged in `control.schema_change_log`.

## Silver Execution

After `09_validate_bronze.ipynb` passes, run:

| Order | Notebook | Purpose |
|---:|---|---|
| 1 | `10_silver_events.ipynb` | UTC/device normalization, quarantine, newest-event deduplication, and CDF |
| 2 | `11_silver_orders.ipynb` | Orders SCD1 `MERGE`, FX conversion, validation, and status history |
| 3 | `12_silver_order_items.ipynb` | Latest-state item synchronization with original/USD prices |
| 4 | `13_silver_customers_scd2.ipynb` | Late-aware customer SCD2 timeline rebuild using `MERGE` |
| 5 | `14_validate_silver.ipynb` | Key, constraint, interval, Delta-format, and MERGE-evidence checks |

The Silver notebooks rebuild deterministic state from append-only Bronze and
then merge only genuine state changes. Running `10`-`13` again must not create
duplicate business keys or rewrite unchanged state solely because the
pipeline run ID changed.

## Gold Execution

After `14_validate_silver.ipynb` passes, run:

| Order | Notebook | Purpose |
|---:|---|---|
| 1 | `15_gold_daily_funnel.ipynb` | Ordered daily view → add-to-cart → purchase funnel using `user_id` or `anonymous:<session_id>` |
| 2 | `16_gold_revenue_daily.ipynb` | Daily/country net revenue from paid and reversal status-change dates |
| 3 | `17_gold_customer_360.ipynb` | One row per current customer with orders, net revenue, latest purchase, and most common device |
| 4 | `18_gold_data_quality_dashboard.ipynb` | Valid and classified error counts from persisted Bronze, quarantine, and duplicate metrics |
| 5 | `19_validate_gold.ipynb` | Delta, key, reconciliation, required-category, lineage, and MERGE checks |

The four Gold marts are deterministic full-snapshot synchronizations implemented
with Delta `MERGE`. Their business rows are idempotent across reruns because
unchanged `record_hash` values are not updated, and stale keys are deleted.
Each Gold notebook refreshes direct aggregate-to-source mappings in
`control.record_lineage` and writes a final audit record.

Important business decisions:

- Funnel steps must occur in order for the same identity on the same
  `event_date`. Anonymous identities use `anonymous:<session_id>`.
- Revenue is recognized on the status-change date: `paid` adds revenue and
  `cancelled`/`returned` subtract it. Country comes from
  `shipping_address.country` carried into order status history.
- Customer 360 starts from `silver_customers.is_current = true`; customers
  without orders or events remain present with zero/null metrics.
- The DQ dashboard reads persisted business/error records instead of summing
  every pipeline execution, so rerunning a notebook does not inflate counts.

## Delta Features Execution

After `19_validate_gold.ipynb` passes, run:

| Order | Notebook | Purpose |
|---:|---|---|
| 1 | `20_incremental_funnel_cdf.ipynb` | Read unprocessed `silver_events` CDF versions and recompute only affected funnel dates |
| 2 | `21_time_travel_demo.ipynb` | Query and persist evidence from previous Silver and Gold Delta versions |
| 3 | `22_optimize_vacuum_metrics.ipynb` | OPTIMIZE/ZORDER two Silver tables, capture file metrics, and dry-run or execute VACUUM |

Run `20` once to initialize its CDF version watermark. After future executions
of `10_silver_events.ipynb`, rerun `20`; it derives affected dates from CDF
preimages/postimages, so late events update their historical `event_date`.
Running `20` again without a new Silver version writes zero business rows.

Run `21` before any real VACUUM execution. It persists current/previous version
and row-count evidence in `control.time_travel_evidence`.

Notebook `22` defaults to `vacuum_mode=DRY_RUN`. It persists before/after
`num_files`, `size_in_bytes`, and average active file size in
`control.delta_optimization_metrics`. Schedule it weekly with
`vacuum_mode=EXECUTE` only after required Time Travel and CDF evidence has been
captured. The 720-hour retention preserves 30 days of debugging, replay, and
historical-query capacity while eventually reclaiming obsolete files.

## Evidence and Workflow Execution

After notebooks through `22` pass:

1. Rerun `06_bronze_clickstream.ipynb`, `07_bronze_orders.ipynb`, and
   `08_bronze_customers.ipynb` with the same landing files. Their successful
   audit rows must report `rows_written = 0`.
2. Run `23_end_to_end_tests.ipynb`. It persists every required acceptance test
   as PASS/FAIL in `control.end_to_end_test_results` and raises when evidence is
   missing or a business invariant fails.
3. Run `24_workflow_readiness.ipynb`. It persists and validates hourly, weekly,
   and manual task graphs in `control.workflow_task_plan`.

The hourly plan runs Bronze sources in parallel, applies their dependent Silver
pipelines, updates the funnel through CDF, refreshes the remaining Gold marts,
and finishes with Gold validation. The weekly maintenance plan captures Time
Travel evidence before executing OPTIMIZE/ZORDER and 720-hour VACUUM. Keep
deployed schedules paused until the interactive end-to-end evidence run passes.

## Full Implementation Roadmap

| Phase | Planned notebooks | Required result |
|---|---|---|
| Foundation | `00`-`04` | Delta schemas, controls, quarantine, FX rates, foundation validation |
| Bronze | `05`-`09` | Append-only, idempotent Bronze with lineage, schema log, and reconciliation |
| Silver | `10`-`14` | Cleansed/deduplicated Events, Orders SCD1, status history, items, Customers SCD2 |
| Gold | `15`-`19` | Four required Delta business marts and Gold validation |
| Delta features | `20`-`22` | CDF incremental Gold, Time Travel, Optimize/Z-Order, Vacuum, file metrics |
| Evidence | `23`-`24` | Proof of idempotency, late data, schema drift, quality, audit, and scheduled workflow |
