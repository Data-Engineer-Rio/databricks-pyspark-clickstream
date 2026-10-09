# Upload Landing Data To Azure Storage

Script `scripts/upload_landing_to_azure.py` uploads the complete local
`landing/` directory recursively while preserving paths such as:

```text
landing/customers/load_date=2026-05-01/hour=00/batch_id=.../customers.csv
```

## 1. Install Dependencies

```bash
myenv/bin/pip install -r requirements.txt
```

## 2. Preview The Upload

Replace `abc-retail` with the destination container name:

```bash
myenv/bin/python scripts/upload_landing_to_azure.py \
  --source landing \
  --account-name <storage-account> \
  --container abc-retail \
  --prefix landing \
  --dry-run
```

The destination paths will have this form:

```text
abc-retail/landing/clickstream_events/...
abc-retail/landing/orders/...
abc-retail/landing/customers/...
abc-retail/landing/dim_fx_rates/...
```

## 3. Authenticate

### Option A: SAS Token

Create a container SAS with create, write, list, and read permissions, then set
it as an environment variable:

```bash
export AZURE_STORAGE_SAS_TOKEN='<sas-token-without-leading-question-mark>'
```

### Option B: Azure Identity

Run the uploader without a SAS token. `DefaultAzureCredential` will use an
available Azure identity and can open an interactive browser login.

The identity needs at least the `Storage Blob Data Contributor` role.

### Option C: Connection String

```bash
export AZURE_STORAGE_CONNECTION_STRING='<connection-string>'
```

Do not commit SAS tokens or connection strings to the repository.

## 4. Upload And Verify

```bash
myenv/bin/python scripts/upload_landing_to_azure.py \
  --source landing \
  --account-name <storage-account> \
  --container abc-retail \
  --prefix landing \
  --workers 16 \
  --verify
```

By default, blobs that already exist are skipped. To replace them:

```bash
myenv/bin/python scripts/upload_landing_to_azure.py \
  --source landing \
  --account-name <storage-account> \
  --container abc-retail \
  --prefix landing \
  --workers 16 \
  --overwrite \
  --verify
```

## 5. External Volume Location

After upload, the ADLS Gen2 path used to create an external volume is:

```text
abfss://abc-retail@<storage-account>.dfs.core.windows.net/landing
```
