- Database vs Postgresql: 
    + Index là gì? Tại sao index lại truy vấn nhanh hơn? Điểm xấu nếu đánh index 1 cách bừa bãi 
    + JOIN: Inner JOIN, Outer JOIN, Cross JOIN 
    + Union vs Union All 
    + Tổ chức sắp xếp dữ liệu: Page/Block => Phân mãnh dữ liệu thì ra sao? 
    + Materialized View: Cache View => Refresh lại thì như thế nào? 
    + Partition: Chia theo row, chia theo column 
        => Partition Pruning: tối ưu việc truy vấn, không cần scan toàn bộ partition mà biết rõ là dữ liệu nằm trong partition A => Vào lấy dữ liệu ra luôn.
        => Nếu không đảm bảo Pruning: Chia lại partition cho phù hợp (hoặc chia nhỏ thêm nữa Multi Partition) 
    + MVCC - Multi Version Concurrency Control: xmin, xmax, ctid
    + VACCUM - Dọn dữ liệu cũ/thừa vẫn tồn tại trong các table 
    + Phân biệt VACUUM vs VACUUM FULL


- PySpark vs Databrick: 
    + Kiến trúc của PySpark 
    + Databrick = Lakehouse => Lakehouse vs Datawarehouse vs Datalake? (Có trong slide)
    + Bản chất của việc xử lý dữ liệu lớn => MPP (Massive Process Parallel) => Data Skew, Data Shuffle
    + RDD => Resilient Distributed Data => Cơ chế Resillient
    + DataFrame API: Filter, Sort, Aggregation, Join (Broadcast JOIN => Giảm thiểu Repartitioning) 
    + Delta Lake: ACID, Time Traversal, ... => Delta Table => Trong Databrick thì sẽ có Delta Live Table
    + Thiết lập kết nối Data Lake vs Databrick: Access Key, SAS, Service Principal 
    + Tất cả các cấu hình connection thường phải để Init Script (Cluster) => Cluster Scope 
    + Ngoài ra những key nhạy cảm: ClientId, ClientSecret, TenantId => Azure KeyVault/AWS KMS/AWS SecretManager 
    + Để móc được key config trong KeyVault 
        => SecretScope (Databrick) => Truy cập thì về trang chủ Databrick rồi điền thêm "#secretScope" vào cuối URL 
        => Viết notebook thông qua serviceScope
    + Metastore: Hive Metastore vs Unity Catalog 
    + Medallion Architect: Bronze -> Sliver -> Gold 
        + Data Source => Landing/Staging Layer => Bronze Layer => Silver Layer => Gold Layer 
    + Dữ liệu: 
        + Dữ liệu có thể sai: sai định dạng/datatype/business 
        + Dữ liệu tăng dần theo thời gian: Nếu Full Load => Lần nào chạy pipeline cũng xử lý lại từ đầu => Incremental Load (Watermark): Lưu vết thời gian xử lý 
    + Dữ liệu có thể không đồng nhất về schema: sinh ra version cho schema 
    + Dữ liệu có thể bị đến trễ (bài toán streaming)