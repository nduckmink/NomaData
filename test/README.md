# Test infra — hạ tầng DB giả lập của bên vận hành sẵn

Dựng lại hai DB server của SCP để NomaData có nguồn dữ liệu thật mà đấu vào.

Đây là một **compose project độc lập** (`scp-source`), không phải một phần của
NomaData: project name riêng, network riêng, volume riêng. NomaData nhìn nó qua
cổng host, y như nhìn DB của khách hàng chạy ở một chỗ nào đó bên ngoài — không
share network, không service nào của app depend vào, Cube cũng không đụng tới.

Bật/tắt hai bên hoàn toàn tách rời: `docker compose down` ở gốc repo không hề
làm sập hạ tầng này, và ngược lại.

## Hai DB server

| | DB 1 | DB 2 |
|---|---|---|
| Engine | MySQL 8.0 | SQL Server 2022 |
| Database | `stg_scp_app` | `stg_scp_2` |
| Host port | `localhost:3307` | `localhost:1434` |
| User | `scp` / `scp` | `sa` / `Scp_Str0ng!Pass` |
| Root | `root` / `scp_root` | — |
| Container | `scp-mysql` | `scp-mssql` |
| Nguồn | `test/data/stg_scp_app/` — 127 file dump HeidiSQL, 37 MB | `test/data/stg_scp_2.bak` — backup MTF, 294 MB |

Cổng lệch 1 so với mặc định (3307 thay vì 3306, 1434 thay vì 1433) để không đụng
DB cài sẵn trên máy.

Hai nguồn data này **khác nhau**, không phải hai bản của cùng một database.

## Bật

```bash
cd test/infra
docker compose up -d          # cả hai DB
docker compose up -d mysql    # chỉ MySQL
docker compose up -d mssql    # chỉ SQL Server
```

Lần đầu chậm, hai bên chậm vì hai lý do khác nhau:

- **MySQL** replay 127 file dump trước khi mở cổng — vài phút.
- **SQL Server** phải pull image ~1.5 GB, rồi restore 294 MB.

Healthcheck của cả hai đều có `start_period` dài nên cứ để nó chạy. Theo dõi bằng:

```bash
docker compose ps          # đợi cột STATUS chuyển sang (healthy)
docker compose logs -f
```

Healthcheck của `mssql` chỉ báo healthy khi database `stg_scp_2` đã ONLINE, chứ
không phải khi `sqlservr` vừa lên — nên `(healthy)` ở đây nghĩa là restore đã xong.

## Mở shell SQL để gõ tay (chỉ để xem/debug)

Phần này **không phải** cách NomaData kết nối — nó dành cho bạn, khi muốn ngó
nhanh xem trong DB có gì mà không cần cài client nào lên máy.

Cách hoạt động: `docker compose exec` nhảy vào bên trong container rồi chạy
client SQL có sẵn ở đó. Kết nối xảy ra ngay trong container, `localhost` ở đây
là chính container đó — không đi qua mạng, không dùng cổng 3307/1434.

**MySQL** — giống nhau ở mọi shell:

```
docker compose exec mysql mysql -uscp -pscp stg_scp_app
#                        ^^^^^ ^^^^^
#                        │     └── lệnh client mysql chạy bên trong
#                        └── tên service trong docker-compose.yml
```

**SQL Server** — lệnh khác nhau tuỳ shell, copy đúng cái của shell bạn đang dùng:

```powershell
# PowerShell / CMD
docker compose exec mssql /opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -P 'Scp_Str0ng!Pass' -C -d stg_scp_2
```

```bash
# Git Bash — bắt buộc có tiền tố MSYS_NO_PATHCONV=1
MSYS_NO_PATHCONV=1 docker compose exec mssql /opt/mssql-tools18/bin/sqlcmd \
  -S localhost -U sa -P 'Scp_Str0ng!Pass' -C -d stg_scp_2
```

Gõ xong thì thoát bằng `exit` (MySQL) hoặc `QUIT` (sqlcmd).

Ba chi tiết dễ vướng ở lệnh SQL Server:

- **Đừng dán bản Git Bash vào PowerShell.** PowerShell không có cú pháp đặt biến
  môi trường ngay trước lệnh (`VAR=x lệnh`) — đó là cú pháp của bash — nên nó sẽ
  coi `MSYS_NO_PATHCONV=1` là tên lệnh và báo
  `The term 'MSYS_NO_PATHCONV=1' is not recognized`. Bỏ tiền tố đi là chạy.
- **`MSYS_NO_PATHCONV=1` chỉ cần cho Git Bash.** Git Bash tự dịch mọi tham số
  trông giống đường dẫn Unix sang đường dẫn Windows, nên
  `/opt/mssql-tools18/bin/sqlcmd` bị biến thành
  `C:/Program Files/Git/opt/mssql-tools18/bin/sqlcmd` và báo
  `no such file or directory`. Biến đó tắt hành vi dịch.
- **`-C`** là trust self-signed certificate, thiếu nó sqlcmd sẽ bỏ kết nối.

Muốn dùng GUI (HeidiSQL, DBeaver, TablePlus, SSMS) thì bỏ qua mục này, dùng
host/port/credentials ở mục ngay dưới.

## URI để NomaData kết nối

Nguyên tắc: NomaData **luôn vào qua địa chỉ mạng và cổng host**, không bao giờ
qua tên service của Docker. Hai compose project không chung network, và đó là
chủ ý — DB của khách hàng thì NomaData phải đi qua mạng mới tới, không có đặc
quyền nội bộ nào. Cổng nội bộ 3306/1433 xem như không tồn tại; từ ngoài chỉ có
**3307** và **1434**.

Địa chỉ thay đổi theo chỗ NomaData đang chạy:

| NomaData chạy ở | Host trong URI | Đã kiểm |
|---|---|---|
| Trên máy này (`pnpm api:dev`) | `127.0.0.1` | ✓ |
| Trong Docker (`pnpm up`) | `host.docker.internal` → `192.168.65.254` | ✓ |
| Máy khác cùng mạng LAN | `192.168.1.52` (IP Wi-Fi của máy này) | ✓ |

Cả hai cổng đều bind `0.0.0.0`, nên máy khác trong mạng kết nối được ngay mà
không cần chỉnh gì thêm.

**DB 1 — MySQL / `stg_scp_app`**

```
# SQLAlchemy (đổi <HOST> theo bảng trên)
mysql+pymysql://scp:scp@<HOST>:3307/stg_scp_app?charset=utf8mb4

# JDBC
jdbc:mysql://<HOST>:3307/stg_scp_app?user=scp&password=scp&useUnicode=true&characterEncoding=utf8

# CLI
mysql -h <HOST> -P 3307 -uscp -pscp stg_scp_app
```

**DB 2 — SQL Server / `stg_scp_2`**

```
# SQLAlchemy + pyodbc
mssql+pyodbc://sa:Scp_Str0ng!Pass@<HOST>:1434/stg_scp_2?driver=ODBC+Driver+18+for+SQL+Server&TrustServerCertificate=yes

# SQLAlchemy + pymssql (không cần cài ODBC driver)
mssql+pymssql://sa:Scp_Str0ng!Pass@<HOST>:1434/stg_scp_2

# JDBC
jdbc:sqlserver://<HOST>:1434;databaseName=stg_scp_2;user=sa;password=Scp_Str0ng!Pass;encrypt=true;trustServerCertificate=true
```

SQL Server 2022 bật mã hoá mặc định nhưng dùng self-signed cert, nên **bắt buộc**
có `TrustServerCertificate=yes` / `trustServerCertificate=true`, nếu không sẽ
lỗi bắt tay SSL.

GUI client (HeidiSQL, DBeaver, TablePlus, SSMS) dùng cùng host/port/credentials ở trên.

### Cho NomaData vào thật qua internet

Hiện hai DB mới mở tới mức **LAN**. Muốn đúng nghĩa "vào qua internet" thì cần
thêm một lớp nữa, chọn một trong hai:

- **Tunnel** (nhanh, không đụng router): `cloudflared tunnel --url tcp://localhost:3307`,
  hoặc `ngrok tcp 3307`. Mỗi DB một tunnel, và URI đổi thành host/port do tunnel cấp.
- **Port forward trên router** tới `192.168.1.52:3307` và `:1434`, kèm mở
  Windows Firewall.

Trước khi làm, cân nhắc: credentials hiện tại (`scp/scp`, `sa/Scp_Str0ng!Pass`)
là loại dùng cho máy local, và bộ data này là dump staging thật có thông tin
doanh nghiệp, lao động, hợp đồng. Mở thẳng ra internet là đưa nguyên khối đó cho
bất kỳ ai quét cổng. Nếu chỉ cần NomaData "đi qua mạng để tới" thì ba trường hợp
ở bảng trên đã đủ giống thật rồi — cùng một đường code, chỉ khác cái hostname.

## Kiểm tra data

MySQL — phải ra **124 bảng + 3 routine** = đúng 127 file dump:

```
docker compose exec -T mysql mysql -uscp -pscp stg_scp_app -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='stg_scp_app'; SELECT COUNT(*) FROM information_schema.routines WHERE routine_schema='stg_scp_app';"
```

SQL Server — phải ra **196 bảng**:

```powershell
# PowerShell / CMD
docker compose exec -T mssql /opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -P 'Scp_Str0ng!Pass' -C -Q "SELECT COUNT(*) FROM stg_scp_2.sys.tables;"
```

```bash
# Git Bash
MSYS_NO_PATHCONV=1 docker compose exec -T mssql /opt/mssql-tools18/bin/sqlcmd \
  -S localhost -U sa -P 'Scp_Str0ng!Pass' -C -Q \
  "SELECT COUNT(*) FROM stg_scp_2.sys.tables;"
```

Số dòng để đối chiếu:

- `stg_scp_app` (MySQL): `enterprises` 2417, `transactions` 2466, `category_banks` 82
- `stg_scp_2` (SQL Server): `docs` 80757, `transaction_docs` 56838, `sign_coordinate` 17677

## Tắt / nạp lại

```bash
cd test/infra
docker compose stop       # tạm dừng, container còn đó
docker compose down       # gỡ container, GIỮ data — lần bật sau lên ngay
docker compose down -v    # xoá luôn volume — lần sau nạp + restore lại từ đầu
```

Data nằm trong volume `scp-source_mysqldata` và `scp-source_mssqldata`.

Cả hai DB chỉ nạp data **một lần**: MySQL replay dump khi volume rỗng, còn
`restore.sh` bỏ qua khi thấy `stg_scp_2` đã tồn tại. Nên sửa file trong
`test/data/` rồi `up` lại sẽ không có tác dụng gì — muốn nạp lại phải `down -v`.

## Vài chỗ dễ vướng

- **Restore SQL Server bắt buộc phải có `MOVE`.** File `.bak` backup từ SQL Server
  chạy trên Windows nên đường dẫn file gốc là `C:\...`, không tồn tại trong
  container Linux. `mssql/restore.sh` đọc `RESTORE FILELISTONLY` để lấy tên file
  logic rồi sinh mệnh đề `MOVE` tương ứng — với bộ backup này ra hai file:
  `scp` (data) → `/var/opt/mssql/data/scp.mdf` và `scp_log` (log) → `scp_log.ldf`.
  Script parse kết quả bằng bash chứ
  không dùng `INSERT ... EXEC` trong T-SQL, vì số cột của `FILELISTONLY` đổi theo
  phiên bản SQL Server và `INSERT ... EXEC` đòi khớp cột tuyệt đối — rất dễ vỡ
  khi đổi image.
- **Tiếng Việt hiện thành `C�ng ty`** khi query trong Git Bash: đó là codepage
  của terminal, data vẫn đúng utf8mb4 (kiểm bằng `SELECT HEX(name)` — `Công` ra
  `43 C3B4 6E 67`). Xem bằng GUI client là bình thường.
- **Password của `sa` không đổi tuỳ tiện được.** SQL Server bắt buộc password
  mạnh; đặt yếu thì container chết ngay lúc boot với lỗi trong log.
- **`test/data/` chưa được gitignore.** Một lệnh `git add .` là kéo file `.bak`
  294 MB vào repo, gỡ ra rất mệt.
- **Lần bật `mssql` đầu tiên có thể treo vài phút ở bước `Creating`.** Docker
  Desktop phải thiết lập chia sẻ file `.bak` 294 MB lần đầu. Cứ để yên, hoặc
  Ctrl-C rồi `up -d mssql` lại — lần hai lên ngay.
- **Bộ dump MySQL nạp sạch**: không view, không trigger, không mệnh đề `DEFINER`,
  chỉ 3 stored procedure, và mỗi file tự tắt `FOREIGN_KEY_CHECKS` ở đầu nên thứ
  tự nạp theo alphabet không gây lỗi khoá ngoại.

## File trong `test/infra/`

| File | Vai trò |
|---|---|
| `docker-compose.yml` | Định nghĩa hai DB server, project `scp-source` |
| `mssql/restore.sh` | Entrypoint của `scp-mssql`: boot `sqlservr` rồi restore `.bak` một lần |

Vài lựa chọn trong compose, để sau khỏi phải đoán:

- `mysql:8.0` khớp đúng server sinh ra dump (8.0.44), collation
  `utf8mb4_unicode_520_ci` đúng như dump khai báo.
- `--log-bin-trust-function-creators=1` là bảo hiểm cho stored routine khi binlog bật.
- `mssql` chạy `user: root` để entrypoint ghi được file data khi restore.
- `restart: unless-stopped` để hạ tầng "bên thứ ba" tự lên lại sau khi khởi động
  máy, giống một DB đang vận hành thật.
