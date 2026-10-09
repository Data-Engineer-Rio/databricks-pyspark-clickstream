# Databricks PySpark — E-Commerce Lakehouse

An end-to-end e-commerce analytics Lakehouse built with **Databricks, PySpark, Delta Lake, and the Medallion architecture**. The project processes clickstream, order, customer, and foreign-exchange data through Bronze, Silver, and Gold layers to support near-real-time reporting, data quality monitoring, and auditable historical analysis.

## Project Goals

- Build repeatable, idempotent data pipelines on Databricks using Apache Spark and Delta Lake.
- Handle production-style data challenges: schema drift, duplicate delivery, late-arriving records, malformed payloads, and changing customer attributes.
- Provide traceability from each curated record back to its source file, batch, and ingestion time.
- Deliver business-ready Delta tables for funnel, revenue, customer, and data-quality reporting.

## Architecture

```text
Landing files (JSON / CSV / Parquet)
                |
                v
Bronze: raw, append-only Delta tables + quarantine tables
                |
                v
Silver: cleansed, conformed, deduplicated Delta tables
                |
                v
Gold: business marts and data-quality dashboard tables
```

The `landing/` directory is a local staging area for generated or downloaded input files. It is intentionally excluded from Git because it may contain large, regenerated datasets.

## Data Sources

| Source | Formats | Key challenges |
| --- | --- | --- |
| Clickstream events | JSON | Duplicate events, late delivery, and `device` schema drift |
| Orders | JSON / Parquet | Order updates, nested items, and multi-currency values |
| Customers | CSV / JSON | Invalid contact details and slowly changing attributes |
| FX rates | CSV / Delta | Currency conversion for financial reporting |

### Clickstream events

Each event includes identifiers, event and ingestion timestamps, user/session context, event type, page, device details, and flexible attributes. Device data may arrive either as a struct or a delimited string, requiring schema normalization.

### Orders

Orders contain an order identifier, customer identifier, update timestamp, status, currency, nested line items, discount information, and shipping details. Line items are exploded into a separate Silver table for product-level analysis.

### Customers

Customer records include a natural key (`user_id`) and profile attributes such as email, phone, and country. Attribute changes are preserved using Slowly Changing Dimension Type 2 (SCD2) logic.

## Lakehouse Layers

### Bronze — Raw and Append-Only

Bronze tables preserve incoming data with minimal transformation:

- `bronze_clickstream`
- `bronze_orders`
- `bronze_customers`

Every ingested record includes `ingest_ts`, `source_file`, `batch_id`, and `load_date`. Invalid records are routed to source-specific quarantine tables with the raw payload, failure reason, and source file. Clickstream schema changes are captured through schema evolution and schema-change logging.

### Silver — Cleansed and Conformed

- `silver_events`: Converts event time to UTC, normalizes device fields, validates required fields, and deduplicates by `event_id`, keeping the latest ingestion record.
- `silver_orders`: Maintains the latest order state through `MERGE INTO` using `order_id` and `updated_at`.
- `silver_order_items`: Explodes order items to the `order_id` + `sku` grain and keeps original as well as USD-converted amounts.
- `silver_customers`: Maintains full customer history with SCD2 columns: `effective_from`, `effective_to`, and `is_current`.

Late records, missing business keys, invalid timestamps, invalid contact information, and missing FX rates are handled explicitly through validation rules and quarantine paths.

### Gold — Business Marts

| Table | Business purpose |
| --- | --- |
| `gold_daily_funnel` | Daily distinct-user conversion across view → add-to-cart → purchase events |
| `gold_revenue_daily` | Daily net revenue by country, excluding cancelled or returned orders according to status history |
| `gold_customer_360` | Current customer profile, order and revenue aggregates, latest purchase, and most-used device |
| `gold_data_quality_dashboard` | Daily valid/error record counts by source and error category |

Anonymous clickstream behavior is handled with a documented session-based identity rule or is excluded from user-level funnel metrics, depending on the metric definition.

## Delta Lake Capabilities

The pipeline is designed to demonstrate core Delta Lake functionality:

- **ACID transactions** and Delta tables throughout all layers.
- **MERGE INTO** for SCD1 order upserts and SCD2 customer history management.
- **Time Travel** for querying previous versions of Silver or Gold data.
- **Change Data Feed (CDF)** for incremental Gold updates when enabled in the workspace; watermark-based processing is the fallback.
- **OPTIMIZE** and **ZORDER** for selected high-volume query paths.
- **VACUUM** with an explicitly justified retention policy that balances storage cost against time-travel and recovery needs.

## Auditability and Data Quality

The `audit_pipeline_runs` table captures operational lineage for every pipeline run:

- `pipeline_name`, `batch_id`, `start_ts`, `end_ts`, and `status`
- `rows_read`, `rows_written`, and `rows_quarantined`
- `source_paths`
- Databricks notebook or job run identifier

The data-quality dashboard groups rejected records by categories such as `parse_error`, `missing_key`, `invalid_timestamp`, `schema_mismatch`, `duplicate_key`, and `fx_rate_missing`.

## Repository Structure

```text
.
├── notebooks/     # Databricks notebooks: setup, Bronze/Silver/Gold, validation, and Delta demos
├── scripts/       # Local utilities for generating and uploading landing data
├── docs/          # Assignment guide, runbook, and upload documentation
├── landing/       # Local input data; ignored by Git
├── requirements.txt
└── readme.md
```

## Notebook Flow

Run the notebooks in numerical order:

1. `00_setup_environment` through `04_validate_foundation`: environment, control tables, quarantine tables, and foundational validation.
2. `05_bronze_common` through `09_validate_bronze`: raw ingestion and Bronze-layer validation.
3. `10_silver_events` through `14_validate_silver`: event, order, item, and customer transformations.
4. `15_gold_daily_funnel` through `19_validate_gold`: Gold marts and business-layer validation.
5. `20_incremental_funnel_cdf` through `24_workflow_readiness`: incremental processing, time travel, maintenance, end-to-end testing, and workflow readiness.

## Local Setup

Create a virtual environment and install the local helper dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

To create mock landing data, run:

```bash
python scripts/generate_mock_landing_data.py
```

For Azure landing-zone upload instructions, see [docs/upload_landing_to_azure.md](docs/upload_landing_to_azure.md). For the operating sequence and validation guidance, see [docs/pipeline_runbook.md](docs/pipeline_runbook.md).

## Technology Stack

- Databricks Workflows and Notebooks
- Apache Spark / PySpark
- Delta Lake
- Python
- Azure Data Lake Storage integration
