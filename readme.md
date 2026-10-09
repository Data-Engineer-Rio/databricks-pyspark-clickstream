# 1. Bối cảnh
Công ty **ABC Retail** vận hành sàn thương mại điện tử đa kênh. Dữ liệu sự kiện người dùng và giao dịch được đổ về **Data Lake** theo dạng file (JSON/Parquet) theo giờ. Bạn được giao xây dựng **Lakehouse** trên **Databricks + Delta Lake** để phục vụ phân tích near-real-time, SLA chất lượng dữ liệu, và khả năng audit/time-travel.

Bạn phải thiết kế pipeline theo mô hình Bronze → Silver → Gold, đảm bảo idempotent, xử lý late-arriving data, schema drift, và cung cấp bảng Gold cho dashboard.

# 2. Phạm vi & yêu cầu chung
- Thực hiện trên Databricks với Apache Spark và Delta Lake.
- Chỉ dùng bảng Delta và các tính năng Delta (ACID, MERGE, OPTIMIZE, ZORDER, VACUUM, Time Travel, CDF… nếu có).
- Pipeline phải chạy lặp lại nhiều lần mà **không tạo trùng dữ liệu** (idempotent).
- Bắt buộc có **kiểm soát chất lượng dữ liệu** và **ghi nhận lỗi** (quarantine/error table).
- Bắt buộc có **audit & lineage** tối thiểu: mỗi record phải truy được “đến từ batch nào, file nào, ingest lúc nào”.

# 3. Dữ liệu đầu vào (giả lập)
Bạn sẽ nhận 3 luồng dữ liệu vào **Landing/Staging Layer** (mỗi giờ 1 batch, có thể trễ):
1. clickstream_events (JSON)
2. orders (JSON/Parquet)
3. customers (CSV/JSON)

**Landing/Staging Layer** là vùng lưu dữ liệu thô đúng như hệ thống upstream gửi đến, trước khi bạn ingest vào **Bronze Layer**. Nhằm giúp:
- Tách biệt trách nhiệm: upstream chỉ “drop file”, pipeline của bạn chịu trách nhiệm chuẩn hoá/kiểm soát chất lượng.
- Cho phép **replay/reprocess** theo batch (khi lỗi pipeline, khi thay đổi logic).
- Hỗ trợ **audit**: truy vết file nào đến lúc nào, từ nguồn nào.
- Xử lý tình huống late-arriving và retry (file trùng) mà không làm phức tạp **Bronze/Silver Layer**.

## 3.1. Clickstream Events (JSON)
Mỗi record có các trường (có thể thiếu/đổi kiểu theo thời gian):
- `event_id` (string, duy nhất theo sự kiện)
- `event_time` (string ISO8601, có thể lệch timezone)
- `ingest_time` (string, có thể null)
- `user_id` (string, có thể null)
- `session_id` (string)
- `event_type` (string: view/add_to_cart/purchase/…)
- `page` (string)
- `device` (`struct` hoặc `string` tùy batch: {os, model} hoặc "ios|iphone" → `schema drift`)
- `attributes` (map<string,string>, tùy biến)

**Đặc điểm khó:**
- Trùng `event_id` có thể xuất hiện do `retry`.
- Late events: `event_time` có thể đến trễ 7 ngày.
- Schema drift ở cột `device`.

## 3.2. Orders (JSON/Parquet)
- `order_id` (string, duy nhất)
- `order_time` (timestamp hoặc string)
- `user_id` (string)
- `items` (array<struct{sku, qty, unit_price}>)
- `currency` (string)
- `discount_code` (string, có thể null)
- `shipping_address` (struct)
- `status` (string: created/paid/shipped/cancelled/returned)
- `updated_at` (timestamp)

## 3.3. Customers (CSV/JSON)
- `user_id` (string)
- `email` (string, có thể null/không hợp lệ)
- `phone` (string)
- `country` (string)
- `created_at` (timestamp)
- `updated_at` (timestamp)

# 4. Nhiệm vụ thiết kế Lakehouse (Medallion Architecture)
## 4.1. Bronze Layer (Append Only)
Tạo các bảng **Delta Table** Bronze:
- bronze_clickstream
- bronze_orders
- bronze_customers

**Yêu cầu chi tiết:**
1. Ingest theo micro-batch hoặc batch (tùy chọn), nhưng phải ghi thêm các cột metadata:
    - `ingest_ts`, `source_file`, `batch_id`, `load_date`
2. Bronze phải **append-only** (không update).
3. Xử lý schema drift:
    - Cho phép tự động mở rộng schema cho **clickstream**, nhưng phải ghi nhận schema version hoặc bảng log schema change.
4. Tạo error/quarantine tables cho từng nguồn:
    - Ghi các bản ghi không parse được, thiếu khóa chính, timestamp lỗi format,...
    - **Error table** phải lưu raw payload + reason + source_file.

## 4.2. Silver Layer (Cleansed & Conformed)
Tạo các bảng **Delta Table** Silver:
- silver_events
- silver_orders
- silver_order_items
- silver_customers

**Yêu cầu chi tiết:**

**Silver Events**
1. Chuẩn hóa:
    - `event_time` chuẩn timestamp UTC.
    - Chuẩn hóa `device` về `struct` {os, model} dù input khác kiểu.
2. Deduplicate theo `event_id` (ưu tiên record có ingest_time mới nhất).
3. Enforce constraints:
    - `event_id` not null
    - `event_time` hợp lệ
4. Phân vùng (partition) hợp lý và giải thích lựa chọn.

**Silver Orders & Items**
1. Upsert orders theo order_id + updated_at (**SCD1 / latest state**).
2. Tách items thành bảng `silver_order_items` có khóa:
    - order_id, sku
    - Bảo toàn qty/price theo từng lần update (bạn phải quyết định: overwrite hay versioning) và nêu rõ trong thiết kế.
3. Chuẩn hóa tiền tệ:
    - Nếu currency khác `USD`, quy đổi theo bảng tỷ giá giả lập `dim_fx_rates` (bạn tự tạo).
    - Lưu cả giá gốc và giá quy đổi.

**Silver Customers (SCD Type 2)**
1. Theo dõi lịch sử thay đổi với các cột:
    - `effective_from` 
    - `effective_to` 
    - `is_current`
2. Natural key: `user_id`
3. Phát hiện thay đổi dựa trên tập cột: `email`, `phone`, `country` (có thể define thêm).
4. Xử lý trường hợp:
    - record đến trễ (`updated_at` cũ hơn hiện tại)
    - bản ghi thiếu `email/phone`: đưa vào quarantine hoặc rule rõ ràng.

## 4.3. Gold Layer (Business marts)
Tạo tối thiểu 4 bảng Gold (Delta) phục vụ BI:

1. **gold_daily_funnel**

    - Theo ngày (based on `event_time`), thống kê unique users theo các bước: view → add_to_cart → purchase.
    - Phải xử lý user không có `user_id` (anonymous): quy định mapping theo `session_id` hoặc xóa bỏ.

2. **gold_revenue_daily**

    - Doanh thu theo ngày, theo `country`.
    - Tính doanh thu `net` (trừ cancelled/returned theo `status timeline`).

3. **gold_customer_360**

    - 1 dòng/user_id: thông tin khách hàng hiện tại + tổng orders + tổng revenue + lần mua gần nhất + thiết bị phổ biến nhất (từ events).
    - Phải đảm bảo join đúng với customers current record.

4. **gold_data_quality_dashboard**

    - Bảng tổng hợp số lượng bản ghi hợp lệ/lỗi theo ngày, theo source.
    - Phân loại lỗi: parse_error, missing_key, invalid_timestamp, schema_mismatch, duplicate_key, fx_rate_missing,…

# 5. Tính năng Delta Lake cần phải có
## 5.1. MERGE, Time Travel & Auditability
1. Bắt buộc dùng `MERGE INTO` cho upsert Silver Orders và Silver Customers
2. Thể hiện khả năng **Time Travel**:
    - Truy vấn gold hoặc silver tại “một thời điểm trước đó” hoặc “một version trước đó”.
3. Tạo bảng `audit_pipeline_runs` lưu:
    - `pipeline_name`
    - `batch_id`
    - `start_ts`
    - `end_ts`
    - `status`
    - `rows_read`
    - `rows_written`
    - `rows_quarantined`
    - `source_paths`
    - `notebook/job_run_id`

## 5.2. Optimization
1. Chọn ít nhất 2 bảng để:
    - `OPTIMIZE`
    - `ZORDER` BY (giải thích cột chọn)
2. Đặt lịch `VACUUM` với retention hợp lý (giải thích tradeoff).
3. Đánh giá file sizes / small files problem:
    - đưa ra chỉ số trước và sau (ví dụ: number of files, avg file size) bằng cách query metadata/log.

## 5.3. Change Data Feed
1. Nếu workspace hỗ trợ Change Data Feed: bật CDF cho ít nhất 1 bảng Silver và dùng nó để cập nhật 1 bảng Gold theo incremental.
2. Nếu không dùng CDF: thiết kế incremental dựa trên watermark (updated_at, _ingest_ts) nhưng phải chứng minh xử lý late data.