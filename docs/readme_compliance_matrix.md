# README Compliance Matrix

This document is the delivery contract for the assignment in `readme.md`.
An item is complete only after its implementation notebook and acceptance
evidence both exist.

Status values:

- `IMPLEMENTED`: notebook exists; Databricks execution evidence is still needed.
- `PLANNED`: explicitly covered by the roadmap but not implemented yet.
- `EVIDENCE REQUIRED`: implementation may exist, but the required Databricks
  execution result has not been captured.

## Fixed Design Decisions

| Topic | Decision |
|---|---|
| Processing mode | Hourly incremental micro-batch |
| Persisted tables | All Bronze, Silver, Gold, quarantine, control, and reference tables use Delta |
| Staging root | `/Volumes/abc_retail/dev/landing` |
| Unity Catalog file lineage | Read source paths from `_metadata.file_path`; do not use unsupported `input_file_name()` |
| Pipeline retry | Successfully processed files are skipped using `control.processed_files` |
| Record lineage | Bronze/Silver keep direct metadata; aggregate Gold lineage is written to `control.record_lineage` |
| Data quality metrics | Valid/error/duplicate counts are written to `control.data_quality_results` |
| Business duplicate | Preserved in append-only Bronze; resolved in Silver |
| Orders Parquet schema drift | Read each Parquet file independently, convert to raw JSON, then union the stable Bronze envelope |
| Clickstream schema evolution | Auto Loader `addNewColumns`, rescued data for type conflicts, and schema hash logging |
| Silver Events partition | Partition by `event_date`; common dashboard filters and late-event repairs operate by event date |
| Anonymous funnel identity | `anonymous:<session_id>` |
| Funnel meaning | Ordered funnel: view before add-to-cart before purchase within the same identity and event date |
| Order items | SCD1 overwrite by `(order_id, sku)`; Bronze preserves every source version |
| Order status timeline | Additional `silver_order_status_history` table preserves status changes |
| Revenue country | `shipping_address.country` from the order event |
| Revenue timeline | Paid adds revenue; cancelled/returned subtract revenue on the status-change date |
| Missing FX | Quarantine as `fx_rate_missing`; do not publish USD revenue until a rate exists |
| Customer contact rule | Null email allowed; malformed non-null email and null phone are quarantined |
| Customer late data | Rebuild the complete timeline for affected users and synchronize it with `MERGE INTO` |
| Incremental Gold | CDF on at least `silver_events`; changed event dates are recomputed and merged into `gold_daily_funnel` |
| Vacuum retention | 30 days, scheduled weekly, after time-travel evidence is captured |

## General Requirements

| ID | README | Requirement | Delivery and acceptance evidence | Status |
|---|---|---|---|---|
| G01 | 2, 7-8 | Databricks, Spark, Delta Lake; persisted tables are Delta | `04_validate_foundation`, `14_validate_silver`, and `19_validate_gold` assert table format is Delta | IMPLEMENTED / EVIDENCE REQUIRED |
| G02 | 4, 9 | Pipeline is idempotent | `processed_files` plus deterministic Delta transactions and Silver/Gold `MERGE`; `23` persists rerun and unique-key evidence | IMPLEMENTED / EVIDENCE REQUIRED |
| G03 | 4, 10 | Data quality and quarantine | Per-source quarantine tables plus DQ validation and dashboard | IMPLEMENTED / EVIDENCE REQUIRED |
| G04 | 4, 11 | Record-level audit and lineage | Bronze/Silver/error records carry direct metadata; Gold records link through `control.record_lineage` to source file, batch, and ingest timestamp | IMPLEMENTED / EVIDENCE REQUIRED |
| G05 | 2, 4 | Near-real-time and late-arriving handling | `24` defines hourly dependency plan; `20` recomputes CDF-affected business dates including late dates | IMPLEMENTED / EVIDENCE REQUIRED |
| G06 | 19-23 | Replay/reprocess and file retry | Failed files can retry; successful identical files are skipped; Silver can be rebuilt from append-only Bronze | IMPLEMENTED / EVIDENCE REQUIRED |

## Bronze Requirements

| ID | README | Requirement | Delivery and acceptance evidence | Status |
|---|---|---|---|---|
| B01 | 63-66 | Create three Bronze Delta tables | `06_bronze_clickstream`, `07_bronze_orders`, `08_bronze_customers`; format validated | IMPLEMENTED / EVIDENCE REQUIRED |
| B02 | 69-70 | Add `ingest_ts`, `source_file`, `batch_id`, `load_date` | Schema assertions and non-null checks in `09_validate_bronze` | IMPLEMENTED / EVIDENCE REQUIRED |
| B03 | 71 | Bronze is append-only | Bronze notebooks only append; table property documents append-only intent; no Bronze `UPDATE`, `DELETE`, or `MERGE` | IMPLEMENTED / EVIDENCE REQUIRED |
| B04 | 72-73 | Clickstream automatic schema expansion and schema change logging | Auto Loader `schemaEvolutionMode=addNewColumns`; each observed schema hash is written to `schema_change_log` | IMPLEMENTED / EVIDENCE REQUIRED |
| B05 | 74-76 | Error table for every source | `02_create_quarantine_tables` creates all required source tables | IMPLEMENTED |
| B06 | 75-76 | Capture parse, missing-key, and invalid-timestamp errors with raw payload, reason, and source file | Bronze/Silver validation writes required fields; reconciliation test checks error categories | IMPLEMENTED / EVIDENCE REQUIRED |
| B07 | 15-17 | Read clickstream JSON, orders JSON/Parquet, customers CSV/JSON | Source-format tests in `09_validate_bronze` | IMPLEMENTED / EVIDENCE REQUIRED |
| B08 | 9, 23 | Same file retry does not duplicate Bronze | `processed_files`, Auto Loader checkpoint, and deterministic Delta transactions; rerun test proves unchanged Bronze counts | IMPLEMENTED / EVIDENCE REQUIRED |

## Silver Events Requirements

| ID | README | Requirement | Delivery and acceptance evidence | Status |
|---|---|---|---|---|
| E01 | 79-83 | Create `silver_events` Delta table | `10_silver_events`; format validation | IMPLEMENTED / EVIDENCE REQUIRED |
| E02 | 88-89 | Convert `event_time` to UTC timestamp | Session timezone UTC plus parsed timestamp assertions | IMPLEMENTED / EVIDENCE REQUIRED |
| E03 | 90 | Normalize device to `{os, model}` | Struct/string normalization; invalid shapes become `schema_mismatch` | IMPLEMENTED / EVIDENCE REQUIRED |
| E04 | 91 | Deduplicate by `event_id`, newest `ingest_time` wins | Deterministic window order by `ingest_time`, `ingest_ts`, `source_file`; cross-batch `MERGE` | IMPLEMENTED / EVIDENCE REQUIRED |
| E05 | 92-94 | Enforce non-null event key and valid timestamp | Invalid rows quarantined; Silver checks return zero violations | IMPLEMENTED / EVIDENCE REQUIRED |
| E06 | 95 | Partition appropriately and explain | Partition by `event_date`; explanation and partition-pruning query captured | IMPLEMENTED / EVIDENCE REQUIRED |
| E07 | 38-40 | Handle duplicate IDs, seven-day late events, and device drift | Silver full-source MERGE handles the cases; dedicated E2E scenarios verify all three | IMPLEMENTED / EVIDENCE REQUIRED |

## Silver Orders and Items Requirements

| ID | README | Requirement | Delivery and acceptance evidence | Status |
|---|---|---|---|---|
| O01 | 81-82, 98 | Create orders/items and upsert latest state by `order_id + updated_at` | `11_silver_orders` uses `MERGE INTO`; stale-update test proves latest state is preserved | IMPLEMENTED / EVIDENCE REQUIRED |
| O02 | 99-101 | Explode items with `(order_id, sku)` key and document overwrite/versioning | `12_silver_order_items`; SCD1 overwrite decision documented and uniqueness asserted | IMPLEMENTED / EVIDENCE REQUIRED |
| O03 | 102-104 | Convert non-USD using `dim_fx_rates` and keep original/USD values | `03_load_fx_rates`, orders/items columns, FX formula checks | IMPLEMENTED / EVIDENCE REQUIRED |
| O04 | 128 | Preserve status timeline for net revenue | `silver_order_status_history` stores unique `(order_id, status, updated_at)` events | IMPLEMENTED / EVIDENCE REQUIRED |
| O05 | 75, 138 | Quarantine invalid orders and missing FX | Required error categories appear in quarantine and DQ dashboard | IMPLEMENTED / EVIDENCE REQUIRED |
| O06 | 142 | Silver Orders must use `MERGE INTO` | Notebook contains `MERGE INTO`; Delta history shows MERGE operation | IMPLEMENTED / EVIDENCE REQUIRED |

## Silver Customers Requirements

| ID | README | Requirement | Delivery and acceptance evidence | Status |
|---|---|---|---|---|
| C01 | 83, 106-112 | Create customer SCD2 with natural key and required history columns | `13_silver_customers_scd2`; schema and current-record assertions | IMPLEMENTED / EVIDENCE REQUIRED |
| C02 | 112 | Detect changes using email, phone, country | Stable attribute hash; unchanged-record test does not create a new version | IMPLEMENTED / EVIDENCE REQUIRED |
| C03 | 113-115 | Handle late records and missing contacts | Full deterministic timeline rebuild; explicit email/phone rule; late-record test | IMPLEMENTED / EVIDENCE REQUIRED |
| C04 | 142 | Silver Customers must use `MERGE INTO` | Staged SCD2 synchronization uses `MERGE INTO`; Delta history proves MERGE | IMPLEMENTED / EVIDENCE REQUIRED |
| C05 | 106-115 | SCD2 timeline integrity | Exactly one current row per user; no overlapping effective intervals | IMPLEMENTED / EVIDENCE REQUIRED |

## Gold Requirements

| ID | README | Requirement | Delivery and acceptance evidence | Status |
|---|---|---|---|---|
| D01 | 117-123 | `gold_daily_funnel`, daily unique users, anonymous policy | `15_gold_daily_funnel` enforces ordered steps and `anonymous:<session_id>`; `19` checks key and monotonic counts | IMPLEMENTED / EVIDENCE REQUIRED |
| D02 | 125-128 | `gold_revenue_daily`, by day/country, net status timeline | `16_gold_revenue_daily` records paid positive and cancelled/returned negative on status-change dates; `19` reconciles to history | IMPLEMENTED / EVIDENCE REQUIRED |
| D03 | 130-133 | `gold_customer_360`, one current row per user with all requested metrics | `17_gold_customer_360` starts from current customers and adds all requested metrics; `19` checks one-row/current join | IMPLEMENTED / EVIDENCE REQUIRED |
| D04 | 135-138 | `gold_data_quality_dashboard`, valid/error counts and categories | `18_gold_data_quality_dashboard` aggregates persisted Bronze/quarantine records and duplicate metrics; `19` checks required categories | IMPLEMENTED / EVIDENCE REQUIRED |
| D05 | 118 | At least four Gold Delta tables | `19_validate_gold` checks all four tables, Delta format, MERGE history, and lineage | IMPLEMENTED / EVIDENCE REQUIRED |

## Delta Lake Feature Requirements

| ID | README | Requirement | Delivery and acceptance evidence | Status |
|---|---|---|---|---|
| F01 | 142 | `MERGE INTO` for Silver Orders and Customers | Covered by O06 and C04; Delta history evidence required | IMPLEMENTED / EVIDENCE REQUIRED |
| F02 | 143-144 | Demonstrate Time Travel | `21_time_travel_demo` queries previous versions and persists version/count evidence | IMPLEMENTED / EVIDENCE REQUIRED |
| F03 | 145-155 | Create `audit_pipeline_runs` with all required fields | `01` creates the table; operational notebooks write final records; `23` checks audit completeness | IMPLEMENTED / EVIDENCE REQUIRED |
| F04 | 158-160 | `OPTIMIZE` and `ZORDER` at least two tables with explanation | `22_optimize_vacuum_metrics` optimizes `silver_events` and `silver_orders` and stores column rationale | IMPLEMENTED / EVIDENCE REQUIRED |
| F05 | 161 | Schedule `VACUUM` and explain retention tradeoff | `22` implements 30-day VACUUM; `24` persists a weekly Time Travel → maintenance task plan | IMPLEMENTED / EVIDENCE REQUIRED |
| F06 | 162-163 | Measure small files before and after | `22` persists `numFiles`, `sizeInBytes`, average size, and table versions before/after | IMPLEMENTED / EVIDENCE REQUIRED |
| F07 | 165-167 | Use CDF for one Silver-to-Gold incremental flow, or prove watermark fallback | `20_incremental_funnel_cdf` reads Silver CDF by version watermark and merges affected funnel dates | IMPLEMENTED / EVIDENCE REQUIRED |

## Mandatory End-to-End Evidence

The assignment is not complete until `23_end_to_end_tests.ipynb` captures all
of the following:

| Test | Required result |
|---|---|
| Same-file rerun | Bronze, Silver, and Gold business results do not duplicate |
| Business duplicate event | Bronze preserves both inputs; Silver keeps the newest `ingest_time` |
| Seven-day late event | Correct historical funnel date is updated |
| Device schema drift | Struct and string normalize; incompatible shapes quarantine |
| Stale order update | Older `updated_at` cannot overwrite latest Silver state |
| Order status timeline | Paid adds and cancelled/returned subtract net revenue |
| Missing FX rate | Record is classified as `fx_rate_missing` and excluded from USD Gold revenue |
| Late customer update | Correct SCD2 interval is inserted without overlap |
| Missing/invalid customer contact | Explicit rule is applied and visible in quarantine/DQ |
| Time Travel | Previous Silver or Gold version is queryable before Vacuum retention expires |
| CDF incremental update | At least one Gold table updates from Silver CDF |
| Optimization | Before/after file metrics are captured for at least two tables |
| Audit completeness | Each operational pipeline run has final status and required metrics |
