#!/usr/bin/env python3
"""Recursively upload the local landing directory to Azure Blob Storage.

The uploader preserves all paths relative to the source directory. For example:

  landing/customers/load_date=2026-05-01/hour=00/customers.csv

is uploaded as:

  <container>/landing/customers/load_date=2026-05-01/hour=00/customers.csv

Authentication order:
1. AZURE_STORAGE_CONNECTION_STRING environment variable.
2. --sas-token or AZURE_STORAGE_SAS_TOKEN environment variable.
3. DefaultAzureCredential, including interactive browser authentication.
"""

from __future__ import annotations

import argparse
import mimetypes
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any


@dataclass(frozen=True)
class UploadItem:
    local_path: Path
    blob_name: str
    size: int


@dataclass(frozen=True)
class UploadResult:
    item: UploadItem
    status: str
    message: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Upload a local directory recursively to Azure Blob Storage."
    )
    parser.add_argument(
        "--source",
        default="landing",
        help="Local source directory. Default: landing",
    )
    parser.add_argument(
        "--account-name",
        help=(
            "Azure Storage account name. Required unless "
            "AZURE_STORAGE_CONNECTION_STRING is set."
        ),
    )
    parser.add_argument(
        "--container",
        required=True,
        help="Destination blob container or ADLS Gen2 filesystem name.",
    )
    parser.add_argument(
        "--prefix",
        default="landing",
        help="Destination prefix inside the container. Default: landing",
    )
    parser.add_argument(
        "--sas-token",
        help=(
            "Storage/container SAS token. Prefer AZURE_STORAGE_SAS_TOKEN "
            "instead of passing a secret in shell history."
        ),
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=16,
        help="Number of files uploaded concurrently. Default: 16",
    )
    parser.add_argument(
        "--file-concurrency",
        type=int,
        default=2,
        help="Concurrent chunks used for each file. Default: 2",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite blobs that already exist. Default: skip existing blobs.",
    )
    parser.add_argument(
        "--create-container",
        action="store_true",
        help="Create the container if it does not exist.",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="After upload, verify that every expected blob exists with the same size.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned uploads without connecting to Azure.",
    )
    parser.add_argument(
        "--show-all",
        action="store_true",
        help="Print every planned/uploaded blob instead of periodic progress.",
    )
    return parser.parse_args()


def normalize_prefix(prefix: str) -> str:
    return prefix.strip().strip("/")


def build_upload_items(source: Path, prefix: str) -> list[UploadItem]:
    if not source.exists():
        raise FileNotFoundError(f"Source directory does not exist: {source}")
    if not source.is_dir():
        raise NotADirectoryError(f"Source is not a directory: {source}")

    normalized_prefix = normalize_prefix(prefix)
    items = []
    for local_path in sorted(path for path in source.rglob("*") if path.is_file()):
        relative = PurePosixPath(local_path.relative_to(source).as_posix())
        blob_name = (
            str(PurePosixPath(normalized_prefix) / relative)
            if normalized_prefix
            else str(relative)
        )
        items.append(
            UploadItem(
                local_path=local_path,
                blob_name=blob_name,
                size=local_path.stat().st_size,
            )
        )
    return items


def human_size(size: int) -> str:
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.2f} {unit}"
        value /= 1024
    return f"{value:.2f} TiB"


def create_container_client(args: argparse.Namespace) -> tuple[Any, Any]:
    try:
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobServiceClient
    except ImportError as exc:
        raise RuntimeError(
            "Azure SDK is not installed. Run: "
            "myenv/bin/pip install -r requirements.txt"
        ) from exc

    connection_string = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    sas_token = args.sas_token or os.environ.get("AZURE_STORAGE_SAS_TOKEN")

    if connection_string:
        service_client = BlobServiceClient.from_connection_string(connection_string)
        auth_method = "AZURE_STORAGE_CONNECTION_STRING"
    else:
        if not args.account_name:
            raise ValueError(
                "--account-name is required when AZURE_STORAGE_CONNECTION_STRING "
                "is not set."
            )
        account_url = f"https://{args.account_name}.blob.core.windows.net"
        if sas_token:
            service_client = BlobServiceClient(
                account_url=account_url,
                credential=sas_token.lstrip("?"),
            )
            auth_method = "SAS token"
        else:
            credential = DefaultAzureCredential(
                exclude_interactive_browser_credential=False
            )
            service_client = BlobServiceClient(
                account_url=account_url,
                credential=credential,
            )
            auth_method = "DefaultAzureCredential"

    return service_client.get_container_client(args.container), auth_method


def upload_one(
    container_client: Any,
    item: UploadItem,
    overwrite: bool,
    file_concurrency: int,
) -> UploadResult:
    try:
        from azure.core.exceptions import ResourceExistsError
        from azure.storage.blob import ContentSettings
    except ImportError as exc:
        raise RuntimeError("Azure SDK is not installed.") from exc

    content_type, content_encoding = mimetypes.guess_type(item.local_path.name)
    content_settings = ContentSettings(
        content_type=content_type or "application/octet-stream",
        content_encoding=content_encoding,
    )
    blob_client = container_client.get_blob_client(item.blob_name)

    try:
        with item.local_path.open("rb") as handle:
            blob_client.upload_blob(
                handle,
                overwrite=overwrite,
                content_settings=content_settings,
                max_concurrency=file_concurrency,
                metadata={"source_relative_path": item.blob_name},
            )
        return UploadResult(item=item, status="uploaded")
    except ResourceExistsError:
        return UploadResult(item=item, status="skipped", message="already exists")
    except Exception as exc:  # Azure SDK exposes several provider-specific errors.
        return UploadResult(item=item, status="failed", message=str(exc))


def upload_all(
    container_client: Any,
    items: list[UploadItem],
    args: argparse.Namespace,
) -> list[UploadResult]:
    results = []
    completed = 0
    lock = threading.Lock()
    total = len(items)

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(
                upload_one,
                container_client,
                item,
                args.overwrite,
                args.file_concurrency,
            ): item
            for item in items
        }

        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            with lock:
                completed += 1
                if args.show_all or result.status == "failed" or completed % 100 == 0:
                    print(
                        f"[{completed}/{total}] {result.status.upper():8} "
                        f"{result.item.blob_name}"
                        + (f" | {result.message}" if result.message else "")
                    )

    return results


def verify_upload(container_client: Any, items: list[UploadItem], prefix: str) -> int:
    normalized_prefix = normalize_prefix(prefix)
    name_starts_with = f"{normalized_prefix}/" if normalized_prefix else None
    remote_sizes = {
        blob.name: blob.size
        for blob in container_client.list_blobs(name_starts_with=name_starts_with)
    }

    failures = []
    for item in items:
        remote_size = remote_sizes.get(item.blob_name)
        if remote_size is None:
            failures.append(f"missing: {item.blob_name}")
        elif remote_size != item.size:
            failures.append(
                f"size mismatch: {item.blob_name} local={item.size} remote={remote_size}"
            )

    if failures:
        print(f"Verification failed for {len(failures)} file(s):", file=sys.stderr)
        for message in failures[:30]:
            print(f"  {message}", file=sys.stderr)
        if len(failures) > 30:
            print(f"  ... and {len(failures) - 30} more", file=sys.stderr)
        return len(failures)

    print(f"Verification passed: {len(items)} blob(s) exist with matching sizes.")
    return 0


def print_plan(items: list[UploadItem], args: argparse.Namespace) -> None:
    total_size = sum(item.size for item in items)
    print(f"Source:      {Path(args.source).resolve()}")
    print(f"Container:   {args.container}")
    print(f"Blob prefix: {normalize_prefix(args.prefix) or '<container root>'}")
    print(f"Files:       {len(items)}")
    print(f"Total size:  {human_size(total_size)}")
    print(f"Overwrite:   {args.overwrite}")

    preview = items if args.show_all else items[:20]
    for item in preview:
        print(f"  {item.local_path} -> {item.blob_name} ({human_size(item.size)})")
    if len(items) > len(preview):
        print(f"  ... and {len(items) - len(preview)} more file(s)")


def main() -> int:
    args = parse_args()
    if args.workers < 1 or args.file_concurrency < 1:
        raise ValueError("--workers and --file-concurrency must be positive.")

    source = Path(args.source)
    items = build_upload_items(source, args.prefix)
    if not items:
        raise ValueError(f"No files found under source directory: {source}")

    print_plan(items, args)
    if args.dry_run:
        print("Dry run complete. No Azure connection was made.")
        return 0

    container_client, auth_method = create_container_client(args)
    print(f"Authentication: {auth_method}")

    if args.create_container:
        from azure.core.exceptions import ResourceExistsError

        try:
            container_client.create_container()
            print(f"Created container: {args.container}")
        except ResourceExistsError:
            print(f"Container already exists: {args.container}")

    results = upload_all(container_client, items, args)
    status_counts = {
        status: sum(result.status == status for result in results)
        for status in ("uploaded", "skipped", "failed")
    }
    print(
        "Upload summary: "
        f"uploaded={status_counts['uploaded']}, "
        f"skipped={status_counts['skipped']}, "
        f"failed={status_counts['failed']}"
    )

    if status_counts["failed"]:
        return 1
    if args.verify:
        return 1 if verify_upload(container_client, items, args.prefix) else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
