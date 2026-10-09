#!/usr/bin/env python3
"""Generate synthetic landing data for the ABC Retail Lakehouse assignment.

The generator creates hourly landing batches for:
- clickstream_events as JSON Lines
- orders as JSON Lines, Parquet, or a mixed format
- customers as CSV, JSON Lines, or a mixed format
- dim_fx_rates as CSV

The data is intentionally not purely random. It includes controlled cases for
deduplication, late-arriving records, schema drift, malformed records, SCD2
customer changes, non-USD currencies, and missing FX rates.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import shutil
from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, DefaultDict, Iterable

from faker import Faker


COUNTRIES = ["US", "VN", "JP", "DE", "FR", "SG"]
FX_RATES_TO_USD = {
    "USD": 1.0,
    "EUR": 1.08,
    "VND": 0.000039,
    "JPY": 0.0064,
}
DEVICE_STRUCTS = [
    {"os": "ios", "model": "iphone_15"},
    {"os": "android", "model": "samsung_s24"},
    {"os": "web", "model": "chrome"},
    {"os": "web", "model": "safari"},
]
DEVICE_STRINGS = [
    "ios|iphone_15",
    "android|samsung_s24",
    "web|chrome",
    "web|safari",
]
DISCOUNT_CODES = [None, None, None, "WELCOME10", "SUMMER15", "VIP20"]
ORDER_STATUS_SEQUENCES = [
    (["created", "paid", "shipped"], 0.50),
    (["created", "paid", "cancelled"], 0.16),
    (["created", "paid", "shipped", "returned"], 0.10),
    (["created", "paid"], 0.14),
    (["created"], 0.10),
]


JsonLine = dict[str, Any] | str
BatchBuckets = DefaultDict[datetime, list[JsonLine]]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate synthetic Landing/Staging data for Assignment 02."
    )
    parser.add_argument("--output", default="landing", help="Output landing directory.")
    parser.add_argument("--start-date", default="2026-05-01", help="YYYY-MM-DD.")
    parser.add_argument("--days", type=int, default=7, help="Business event horizon.")
    parser.add_argument("--seed", type=int, default=42, help="Random seed.")
    parser.add_argument("--users", type=int, default=5_000, help="Number of users.")
    parser.add_argument("--skus", type=int, default=500, help="Number of SKUs.")
    parser.add_argument(
        "--clickstream-events",
        type=int,
        default=50_000,
        help="Approximate total clickstream rows including injected duplicates/errors.",
    )
    parser.add_argument(
        "--order-updates",
        type=int,
        default=8_000,
        help="Approximate total order update rows.",
    )
    parser.add_argument(
        "--customer-versions",
        type=int,
        default=7_000,
        help="Approximate total customer version rows.",
    )
    parser.add_argument(
        "--late-max-days",
        type=int,
        default=7,
        help="Maximum artificial arrival delay for late clickstream/order/customer rows.",
    )
    parser.add_argument(
        "--orders-format",
        choices=["json", "parquet", "mixed"],
        default="mixed",
        help="Output format for orders batches.",
    )
    parser.add_argument(
        "--customers-format",
        choices=["csv", "json", "mixed"],
        default="mixed",
        help="Output format for customers batches.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Delete the output directory before generating new files.",
    )
    return parser.parse_args()


def utc_dt(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)


def parse_start_date(value: str) -> date:
    return date.fromisoformat(value)


def iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def iso_with_random_timezone(dt_utc: datetime, rng: random.Random) -> str:
    offsets = [
        timezone.utc,
        timezone(timedelta(hours=7)),
        timezone(timedelta(hours=-5)),
        timezone(timedelta(hours=1)),
        timezone(timedelta(hours=9)),
    ]
    tz = rng.choice(offsets)
    return dt_utc.astimezone(tz).isoformat()


def random_dt_between(
    rng: random.Random, start: datetime, end: datetime
) -> datetime:
    seconds = int((end - start).total_seconds())
    return start + timedelta(seconds=rng.randint(0, max(seconds, 1)))


def hour_bucket(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def add_to_bucket(buckets: BatchBuckets, load_ts: datetime, row: JsonLine) -> None:
    buckets[hour_bucket(load_ts)].append(row)


def weighted_choice(rng: random.Random, weighted_values: list[tuple[Any, float]]) -> Any:
    values = [value for value, _ in weighted_values]
    weights = [weight for _, weight in weighted_values]
    return rng.choices(values, weights=weights, k=1)[0]


def make_batch_dir(root: Path, source: str, prefix: str, load_hour: datetime) -> Path:
    batch_id = f"{prefix}_{load_hour:%Y%m%d_%H}"
    return (
        root
        / source
        / f"load_date={load_hour:%Y-%m-%d}"
        / f"hour={load_hour:%H}"
        / f"batch_id={batch_id}"
    )


def write_json_lines(path: Path, rows: Iterable[JsonLine]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            if isinstance(row, str):
                handle.write(row.rstrip("\n") + "\n")
            else:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            count += 1
    return count


def write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column) for column in columns})
    return len(rows)


def write_parquet(path: Path, rows: list[dict[str, Any]]) -> int:
    import pyarrow as pa
    import pyarrow.parquet as pq

    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(rows)
    pq.write_table(table, path)
    return len(rows)


def format_source_file(root: Path, path: Path) -> str:
    return str(path.relative_to(root))


def make_fake(seed: int) -> Faker:
    fake = Faker("en_US", use_weighting=False)
    Faker.seed(seed)
    fake.seed_instance(seed)
    return fake


def build_user_profiles(
    fake: Faker, rng: random.Random, user_count: int, start_ts: datetime
) -> dict[str, dict[str, Any]]:
    profiles: dict[str, dict[str, Any]] = {}
    for index in range(1, user_count + 1):
        user_id = f"user_{index:06d}"
        country = rng.choice(COUNTRIES)
        created_at = start_ts - timedelta(
            days=rng.randint(1, 180), seconds=rng.randint(0, 86_399)
        )
        profiles[user_id] = {
            "user_id": user_id,
            "email": f"{user_id}@example.com",
            "phone": fake.phone_number(),
            "country": country,
            "created_at": created_at,
            "updated_at": created_at,
            "address": {
                "line1": fake.street_address().replace("\n", " "),
                "city": fake.city().replace("\n", " "),
                "country": country,
            },
        }
    return profiles


def choose_device(
    rng: random.Random, load_ts: datetime, start_ts: datetime, event_days: int
) -> Any:
    elapsed_days = (load_ts - start_ts).total_seconds() / 86_400

    if rng.random() < 0.006:
        return {"unexpected": ["bad", "device", "shape"]}

    if elapsed_days < event_days / 2:
        return rng.choice(DEVICE_STRUCTS) if rng.random() < 0.88 else rng.choice(DEVICE_STRINGS)

    return rng.choice(DEVICE_STRINGS) if rng.random() < 0.88 else rng.choice(DEVICE_STRUCTS)


def clickstream_page(event_type: str, sku: str) -> str:
    if event_type == "view":
        return f"/product/{sku}"
    if event_type == "add_to_cart":
        return "/cart"
    return "/checkout/confirmation"


def clickstream_attributes(
    rng: random.Random, event_type: str, sku: str
) -> dict[str, str]:
    attributes = {
        "sku": sku,
        "campaign": rng.choice(["organic", "summer", "retargeting", "email"]),
    }
    if event_type == "purchase":
        attributes["payment_method"] = rng.choice(["card", "wallet", "cod"])
    return attributes


def generate_clickstream(
    rng: random.Random,
    user_ids: list[str],
    skus: list[str],
    start_ts: datetime,
    days: int,
    late_max_days: int,
    target_rows: int,
) -> tuple[BatchBuckets, dict[str, Any]]:
    buckets: BatchBuckets = defaultdict(list)
    end_ts = start_ts + timedelta(days=days)
    base_target = max(1, int(target_rows * 0.965))
    duplicate_target = max(1, int(target_rows * 0.018))
    parse_error_target = max(1, target_rows - base_target - duplicate_target)

    valid_events_for_duplicate: list[tuple[dict[str, Any], datetime]] = []
    generated = 0
    session_index = 1
    stats = defaultdict(int)

    while generated < base_target:
        session_id = f"sess_{session_index:010d}"
        session_index += 1
        user_id = None if rng.random() < 0.18 else rng.choice(user_ids)
        session_start = random_dt_between(rng, start_ts, end_ts - timedelta(minutes=30))
        event_sequence = ["view"]
        if rng.random() < 0.34:
            event_sequence.append("add_to_cart")
            if rng.random() < 0.32:
                event_sequence.append("purchase")
        elif rng.random() < 0.025:
            event_sequence.append("purchase")

        sku = rng.choice(skus)
        for step, event_type in enumerate(event_sequence):
            if generated >= base_target:
                break

            event_time = session_start + timedelta(minutes=step * rng.randint(1, 12))
            normal_delay = timedelta(minutes=rng.randint(0, 150))
            is_late = rng.random() < 0.045
            if is_late:
                delay = timedelta(
                    days=rng.randint(1, late_max_days),
                    minutes=rng.randint(0, 180),
                )
                stats["late_records"] += 1
            else:
                delay = normal_delay
            load_ts = event_time + delay

            event_id = f"evt_{generated + 1:010d}"
            event_time_value = iso_with_random_timezone(event_time, rng)
            if rng.random() < 0.007:
                event_time_value = rng.choice(["not-a-timestamp", "2026-99-99T99:00:00Z"])
                stats["invalid_timestamp"] += 1

            if rng.random() < 0.005:
                event_id = None
                stats["missing_key"] += 1

            record = {
                "event_id": event_id,
                "event_time": event_time_value,
                "ingest_time": None if rng.random() < 0.04 else iso_z(load_ts),
                "user_id": user_id,
                "session_id": session_id,
                "event_type": event_type,
                "page": clickstream_page(event_type, sku),
                "device": choose_device(rng, load_ts, start_ts, days),
                "attributes": clickstream_attributes(rng, event_type, sku),
            }
            add_to_bucket(buckets, load_ts, record)
            if event_id and event_time_value.startswith(("2026-", "2025-")):
                valid_events_for_duplicate.append((record, load_ts))
            if user_id is None:
                stats["anonymous_records"] += 1
            generated += 1

    for index in range(duplicate_target):
        source, original_load_ts = rng.choice(valid_events_for_duplicate)
        duplicate = deepcopy(source)
        duplicate_load_ts = original_load_ts + timedelta(
            hours=rng.randint(1, 36), minutes=rng.randint(0, 59)
        )
        duplicate["ingest_time"] = iso_z(duplicate_load_ts)
        duplicate["attributes"]["retry_marker"] = f"retry_{index:06d}"
        add_to_bucket(buckets, duplicate_load_ts, duplicate)
        stats["duplicate_event_id"] += 1

    for index in range(parse_error_target):
        bad_load_ts = random_dt_between(
            rng,
            start_ts,
            end_ts + timedelta(days=late_max_days),
        )
        bad_line = (
            '{"event_id": "evt_parse_%06d", "event_time": "2026-05-01T00:00:00Z", '
            '"device": '
        ) % index
        add_to_bucket(buckets, bad_load_ts, bad_line)
        stats["parse_error"] += 1

    stats["rows"] = sum(len(rows) for rows in buckets.values())
    stats["batches"] = len(buckets)
    return buckets, dict(stats)


def build_order_items(
    rng: random.Random, skus: list[str], allow_bad: bool = False
) -> list[dict[str, Any]]:
    item_count = rng.randint(1, 5)
    items = []
    chosen_skus = rng.sample(skus, k=min(item_count, len(skus)))
    for sku in chosen_skus:
        qty = rng.randint(1, 4)
        unit_price = round(rng.uniform(5, 500), 2)
        items.append({"sku": sku, "qty": qty, "unit_price": unit_price})

    if allow_bad and items:
        bad_item = rng.choice(items)
        if rng.random() < 0.5:
            bad_item["qty"] = rng.choice([0, -1])
        else:
            bad_item["unit_price"] = -abs(float(bad_item["unit_price"]))
    return items


def generate_orders(
    rng: random.Random,
    user_profiles: dict[str, dict[str, Any]],
    skus: list[str],
    start_ts: datetime,
    days: int,
    late_max_days: int,
    target_rows: int,
) -> tuple[BatchBuckets, dict[str, Any]]:
    buckets: BatchBuckets = defaultdict(list)
    user_ids = list(user_profiles)
    end_ts = start_ts + timedelta(days=days)
    stats = defaultdict(int)
    order_index = 1
    generated = 0

    while generated < target_rows:
        order_id = f"order_{order_index:08d}"
        order_index += 1
        user_id = rng.choice(user_ids)
        profile = user_profiles[user_id]
        order_time = random_dt_between(rng, start_ts, end_ts - timedelta(hours=8))
        currency = weighted_choice(
            rng,
            [
                ("USD", 0.58),
                ("EUR", 0.16),
                ("VND", 0.12),
                ("JPY", 0.12),
                ("GBP", 0.02),
            ],
        )
        if currency == "GBP":
            stats["fx_rate_missing_candidate"] += 1

        status_sequence = weighted_choice(rng, ORDER_STATUS_SEQUENCES)
        items = build_order_items(rng, skus)
        shipping_address = deepcopy(profile["address"])
        shipping_address["postal_code"] = f"{rng.randint(10000, 99999)}"

        for status_index, status in enumerate(status_sequence):
            if generated >= target_rows:
                break
            updated_at = order_time + timedelta(hours=status_index * rng.randint(1, 9))
            delay = timedelta(minutes=rng.randint(0, 180))
            if rng.random() < 0.03:
                delay += timedelta(days=rng.randint(1, late_max_days))
                stats["late_records"] += 1
            if status_index == 0 and len(status_sequence) > 1 and rng.random() < 0.025:
                delay += timedelta(days=rng.randint(2, late_max_days + 1))
                stats["stale_update_arrives_late"] += 1

            bad_items = rng.random() < 0.006
            record = {
                "order_id": order_id,
                "order_time": iso_with_random_timezone(order_time, rng),
                "user_id": user_id,
                "items": build_order_items(rng, skus, allow_bad=True)
                if bad_items
                else deepcopy(items),
                "currency": currency,
                "discount_code": rng.choice(DISCOUNT_CODES),
                "shipping_address": shipping_address,
                "status": status,
                "updated_at": iso_z(updated_at),
            }

            dice = rng.random()
            if dice < 0.004:
                record["order_id"] = None
                stats["missing_key"] += 1
            elif dice < 0.008:
                record["order_time"] = "not-a-timestamp"
                stats["invalid_timestamp"] += 1
            elif dice < 0.011:
                record["items"] = []
                stats["invalid_items"] += 1
            elif bad_items:
                stats["invalid_items"] += 1

            add_to_bucket(buckets, updated_at + delay, record)
            generated += 1

    parse_error_target = max(1, int(target_rows * 0.002))
    for index in range(parse_error_target):
        bad_load_ts = random_dt_between(
            rng,
            start_ts,
            end_ts + timedelta(days=late_max_days),
        )
        bad_line = (
            '{"order_id": "order_parse_%06d", "order_time": "2026-05-01T00:00:00Z", '
            '"items": ['
        ) % index
        add_to_bucket(buckets, bad_load_ts, bad_line)
        stats["parse_error"] += 1

    stats["rows"] = sum(len(rows) for rows in buckets.values())
    stats["batches"] = len(buckets)
    return buckets, dict(stats)


def mutate_customer_state(
    fake: Faker, rng: random.Random, state: dict[str, Any], user_id: str
) -> None:
    change = rng.choice(["email", "phone", "country"])
    if change == "email":
        state["email"] = f"{user_id}.{rng.randint(1, 9999):04d}@example.com"
    elif change == "phone":
        state["phone"] = fake.phone_number()
    else:
        state["country"] = rng.choice([country for country in COUNTRIES if country != state["country"]])


def customer_row(state: dict[str, Any], updated_at: datetime) -> dict[str, Any]:
    return {
        "user_id": state["user_id"],
        "email": state["email"],
        "phone": state["phone"],
        "country": state["country"],
        "created_at": iso_z(state["created_at"]),
        "updated_at": iso_z(updated_at),
    }


def generate_customers(
    fake: Faker,
    rng: random.Random,
    user_profiles: dict[str, dict[str, Any]],
    start_ts: datetime,
    days: int,
    late_max_days: int,
    target_rows: int,
) -> tuple[BatchBuckets, dict[str, Any]]:
    buckets: BatchBuckets = defaultdict(list)
    stats = defaultdict(int)
    user_ids = list(user_profiles)
    base_count = min(len(user_ids), max(1, target_rows))
    customer_ids = user_ids[:base_count]
    states = {user_id: deepcopy(user_profiles[user_id]) for user_id in customer_ids}

    generated = 0
    for user_id in customer_ids:
        state = states[user_id]
        updated_at = random_dt_between(
            rng,
            start_ts - timedelta(days=30),
            start_ts + timedelta(hours=6),
        )
        state["updated_at"] = updated_at
        load_ts = updated_at + timedelta(minutes=rng.randint(0, 240))
        add_to_bucket(buckets, load_ts, customer_row(state, updated_at))
        generated += 1
        if generated >= target_rows:
            break

    while generated < target_rows:
        user_id = rng.choice(customer_ids)
        state = states[user_id]
        previous_updated_at = state["updated_at"]
        late_stale_update = rng.random() < 0.018

        if late_stale_update:
            stale_state = deepcopy(state)
            mutate_customer_state(fake, rng, stale_state, user_id)
            updated_at = previous_updated_at - timedelta(
                days=rng.randint(1, 3), hours=rng.randint(0, 12)
            )
            load_ts = previous_updated_at + timedelta(
                days=rng.randint(1, late_max_days), minutes=rng.randint(0, 180)
            )
            row = customer_row(stale_state, updated_at)
            stats["late_stale_update"] += 1
        else:
            mutate_customer_state(fake, rng, state, user_id)
            updated_at = previous_updated_at + timedelta(
                hours=rng.randint(1, 72), minutes=rng.randint(0, 59)
            )
            state["updated_at"] = updated_at
            load_ts = updated_at + timedelta(minutes=rng.randint(0, 240))
            if rng.random() < 0.025:
                load_ts += timedelta(days=rng.randint(1, late_max_days))
                stats["late_records"] += 1
            row = customer_row(state, updated_at)

        dice = rng.random()
        if dice < 0.006:
            row["user_id"] = None
            stats["missing_key"] += 1
        elif dice < 0.014:
            row["email"] = rng.choice([None, "", "not-an-email", "abc@@example"])
            stats["invalid_email"] += 1
        elif dice < 0.020:
            row["phone"] = None
            stats["missing_phone"] += 1
        elif dice < 0.024:
            row["updated_at"] = "not-a-timestamp"
            stats["invalid_timestamp"] += 1

        add_to_bucket(buckets, load_ts, row)
        generated += 1

    stats["rows"] = sum(len(rows) for rows in buckets.values())
    stats["batches"] = len(buckets)
    return buckets, dict(stats)


def generate_fx_rates(output_root: Path, start_ts: datetime, days: int, late_max_days: int) -> int:
    rows = []
    total_days = days + late_max_days + 2
    for day_offset in range(total_days):
        rate_date = (start_ts + timedelta(days=day_offset)).date().isoformat()
        for currency, base_rate in FX_RATES_TO_USD.items():
            daily_jitter = 1 + ((day_offset % 5) - 2) * 0.002
            rows.append(
                {
                    "rate_date": rate_date,
                    "currency": currency,
                    "rate_to_usd": round(base_rate * daily_jitter, 8),
                }
            )

    path = output_root / "dim_fx_rates" / "fx_rates.csv"
    return write_csv(path, rows, ["rate_date", "currency", "rate_to_usd"])


def write_clickstream_batches(output_root: Path, buckets: BatchBuckets) -> dict[str, Any]:
    files = []
    rows_written = 0
    for load_hour in sorted(buckets):
        batch_dir = make_batch_dir(
            output_root, "clickstream_events", "click", load_hour
        )
        path = batch_dir / "events.json"
        count = write_json_lines(path, buckets[load_hour])
        files.append({"path": format_source_file(output_root, path), "rows": count})
        rows_written += count
    return {"files": files, "rows_written": rows_written}


def rows_as_dicts(rows: list[JsonLine]) -> list[dict[str, Any]]:
    return [row for row in rows if isinstance(row, dict)]


def write_order_batches(
    output_root: Path, buckets: BatchBuckets, orders_format: str
) -> dict[str, Any]:
    files = []
    rows_written = 0
    for load_hour in sorted(buckets):
        rows = buckets[load_hour]
        batch_dir = make_batch_dir(output_root, "orders", "orders", load_hour)
        contains_raw_lines = any(isinstance(row, str) for row in rows)
        selected_format = orders_format
        if orders_format == "mixed":
            selected_format = "parquet" if load_hour.hour % 2 == 0 else "json"
        if selected_format == "parquet" and contains_raw_lines:
            selected_format = "json"

        if selected_format == "parquet":
            path = batch_dir / "orders.parquet"
            count = write_parquet(path, rows_as_dicts(rows))
        else:
            path = batch_dir / "orders.json"
            count = write_json_lines(path, rows)
        files.append(
            {
                "path": format_source_file(output_root, path),
                "rows": count,
                "format": selected_format,
            }
        )
        rows_written += count
    return {"files": files, "rows_written": rows_written}


def write_customer_batches(
    output_root: Path, buckets: BatchBuckets, customers_format: str
) -> dict[str, Any]:
    files = []
    rows_written = 0
    columns = ["user_id", "email", "phone", "country", "created_at", "updated_at"]
    for load_hour in sorted(buckets):
        rows = rows_as_dicts(buckets[load_hour])
        batch_dir = make_batch_dir(output_root, "customers", "customers", load_hour)
        selected_format = customers_format
        if customers_format == "mixed":
            selected_format = "csv" if load_hour.hour % 2 == 0 else "json"

        if selected_format == "csv":
            path = batch_dir / "customers.csv"
            count = write_csv(path, rows, columns)
        else:
            path = batch_dir / "customers.json"
            count = write_json_lines(path, rows)

        files.append(
            {
                "path": format_source_file(output_root, path),
                "rows": count,
                "format": selected_format,
            }
        )
        rows_written += count
    return {"files": files, "rows_written": rows_written}


def write_manifest(
    output_root: Path,
    args: argparse.Namespace,
    source_stats: dict[str, Any],
) -> None:
    manifest = {
        "generated_at": iso_z(datetime.now(timezone.utc)),
        "parameters": {
            "start_date": args.start_date,
            "days": args.days,
            "seed": args.seed,
            "users": args.users,
            "skus": args.skus,
            "clickstream_events": args.clickstream_events,
            "order_updates": args.order_updates,
            "customer_versions": args.customer_versions,
            "late_max_days": args.late_max_days,
            "orders_format": args.orders_format,
            "customers_format": args.customers_format,
        },
        "sources": source_stats,
        "notable_cases": {
            "clickstream": [
                "duplicate event_id records with newer ingest_time",
                "late records with event_time up to late_max_days before load_date",
                "device schema drift between struct and pipe-delimited string",
                "malformed JSON lines",
                "missing event_id and invalid event_time values",
                "anonymous rows with user_id = null and session_id present",
            ],
            "orders": [
                "SCD1 order updates with created/paid/shipped/cancelled/returned states",
                "stale updates arriving after newer updates",
                "nested items arrays for silver_order_items",
                "non-USD currencies and GBP rows without FX rates",
                "malformed JSON lines, missing order_id, invalid order_time, invalid items",
            ],
            "customers": [
                "SCD2 attribute changes across email, phone, and country",
                "late stale updates older than the current version",
                "missing user_id, invalid email, missing phone, invalid updated_at",
            ],
            "fx_rates": [
                "USD, EUR, VND, and JPY rates are generated",
                "GBP is intentionally omitted to exercise fx_rate_missing quarantine",
            ],
        },
    }
    path = output_root / "_manifest.json"
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")


def validate_overwrite_target(output_root: Path) -> None:
    resolved_output = output_root.resolve()
    cwd = Path.cwd().resolve()
    home = Path.home().resolve()
    if resolved_output in {cwd, home, Path(resolved_output.anchor)}:
        raise ValueError(f"Refusing to overwrite unsafe output path: {resolved_output}")


def main() -> None:
    args = parse_args()
    output_root = Path(args.output)
    if output_root.exists() and args.overwrite:
        validate_overwrite_target(output_root)
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    rng = random.Random(args.seed)
    fake = make_fake(args.seed)
    start_day = parse_start_date(args.start_date)
    start_ts = utc_dt(start_day)
    skus = [f"sku_{index:05d}" for index in range(1, args.skus + 1)]
    user_profiles = build_user_profiles(fake, rng, args.users, start_ts)
    user_ids = list(user_profiles)

    clickstream_buckets, clickstream_stats = generate_clickstream(
        rng=rng,
        user_ids=user_ids,
        skus=skus,
        start_ts=start_ts,
        days=args.days,
        late_max_days=args.late_max_days,
        target_rows=args.clickstream_events,
    )
    order_buckets, order_stats = generate_orders(
        rng=rng,
        user_profiles=user_profiles,
        skus=skus,
        start_ts=start_ts,
        days=args.days,
        late_max_days=args.late_max_days,
        target_rows=args.order_updates,
    )
    customer_buckets, customer_stats = generate_customers(
        fake=fake,
        rng=rng,
        user_profiles=user_profiles,
        start_ts=start_ts,
        days=args.days,
        late_max_days=args.late_max_days,
        target_rows=args.customer_versions,
    )

    source_stats: dict[str, Any] = {
        "clickstream_events": clickstream_stats,
        "orders": order_stats,
        "customers": customer_stats,
    }
    source_stats["clickstream_events"].update(
        write_clickstream_batches(output_root, clickstream_buckets)
    )
    source_stats["orders"].update(
        write_order_batches(output_root, order_buckets, args.orders_format)
    )
    source_stats["customers"].update(
        write_customer_batches(output_root, customer_buckets, args.customers_format)
    )
    fx_rows = generate_fx_rates(output_root, start_ts, args.days, args.late_max_days)
    source_stats["dim_fx_rates"] = {
        "rows": fx_rows,
        "files": [{"path": "dim_fx_rates/fx_rates.csv", "rows": fx_rows}],
    }
    write_manifest(output_root, args, source_stats)

    print(json.dumps({"output": str(output_root), "sources": source_stats}, indent=2))


if __name__ == "__main__":
    main()
