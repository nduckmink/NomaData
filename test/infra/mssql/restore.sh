#!/bin/bash
# Entrypoint cho scp-mssql: boot sqlservr, rồi restore stg_scp_2.bak một lần.
#
# .bak là backup từ SQL Server chạy trên Windows nên đường dẫn file gốc là dạng
# C:\... — không tồn tại trong container Linux. Vì vậy bắt buộc phải RESTORE kèm
# MOVE cho từng file logic. Tên file logic thì chỉ .bak mới biết, nên script đọc
# RESTORE FILELISTONLY trước rồi sinh mệnh đề MOVE từ kết quả đó.
#
# Cố ý parse FILELISTONLY bằng bash thay vì INSERT ... EXEC trong T-SQL: số cột
# của FILELISTONLY đổi theo phiên bản SQL Server, INSERT ... EXEC đòi khớp cột
# tuyệt đối nên rất dễ vỡ khi đổi image.

set -uo pipefail

BAK=/backup/stg_scp_2.bak
DB=stg_scp_2
DATADIR=/var/opt/mssql/data

# Đường dẫn sqlcmd và chuyện TLS khác nhau giữa các đời image mssql:
# tools18 bắt buộc -C (trust self-signed cert), tools cũ thì không có cờ này.
if [ -x /opt/mssql-tools18/bin/sqlcmd ]; then
  SQLCMD_BIN=/opt/mssql-tools18/bin/sqlcmd
  SQLCMD_TLS=-C
elif [ -x /opt/mssql-tools/bin/sqlcmd ]; then
  SQLCMD_BIN=/opt/mssql-tools/bin/sqlcmd
  SQLCMD_TLS=
else
  SQLCMD_BIN=
  SQLCMD_TLS=
fi

sq() {
  "$SQLCMD_BIN" -S localhost -U sa -P "$MSSQL_SA_PASSWORD" $SQLCMD_TLS -b "$@"
}

log() { echo "[restore] $*"; }

do_restore() {
  if [ -z "$SQLCMD_BIN" ]; then
    log "KHÔNG tìm thấy sqlcmd trong image — bỏ qua restore."
    log "Restore thủ công bằng: docker exec -it scp-mssql /bin/bash"
    return 0
  fi

  log "chờ sqlservr sẵn sàng..."
  local ready=0 i
  for i in $(seq 1 120); do
    if sq -Q "SELECT 1" >/dev/null 2>&1; then ready=1; break; fi
    sleep 5
  done
  if [ "$ready" -ne 1 ]; then
    log "sqlservr không lên sau 10 phút — bỏ qua restore."
    return 0
  fi

  # Volume giữ lại giữa các lần bật, nên chỉ restore khi DB chưa có.
  if sq -h -1 -W -Q "SET NOCOUNT ON; SELECT COUNT(*) FROM sys.databases WHERE name = '$DB'" 2>/dev/null | grep -qx '1'; then
    log "database [$DB] đã có sẵn — bỏ qua restore."
    return 0
  fi

  if [ ! -f "$BAK" ]; then
    log "không thấy $BAK — bỏ qua restore."
    return 0
  fi

  log "đọc danh sách file logic trong .bak..."
  local filelist
  filelist=$(sq -h -1 -W -s'|' -Q "SET NOCOUNT ON; RESTORE FILELISTONLY FROM DISK = N'$BAK'" 2>&1)
  if [ $? -ne 0 ] || [ -z "$filelist" ]; then
    log "RESTORE FILELISTONLY lỗi:"
    echo "$filelist"
    return 0
  fi

  local moves="" logical type safe ext
  while IFS='|' read -r logical _physical type _rest; do
    # Bỏ dòng rỗng và dòng thông báo kiểu "(2 rows affected)".
    [ -z "$logical" ] && continue
    case "$logical" in \(*) continue ;; esac

    # Tên logic có thể chứa dấu cách / ký tự lạ — làm sạch để đặt tên file.
    safe=$(printf '%s' "$logical" | tr -c 'A-Za-z0-9_.-' '_')
    if [ "$type" = "L" ]; then ext=ldf; else ext=mdf; fi
    moves="$moves, MOVE N'$logical' TO N'$DATADIR/${safe}.${ext}'"
    log "  $logical ($type) -> $DATADIR/${safe}.${ext}"
  done <<< "$filelist"

  if [ -z "$moves" ]; then
    log "không parse được file logic nào — bỏ qua restore."
    return 0
  fi

  log "bắt đầu RESTORE DATABASE [$DB] (294 MB, mất vài phút)..."
  if sq -Q "RESTORE DATABASE [$DB] FROM DISK = N'$BAK' WITH FILE = 1$moves, RECOVERY, REPLACE, STATS = 10"; then
    log "restore XONG — [$DB] sẵn sàng."
  else
    log "RESTORE lỗi (xem log phía trên)."
  fi
}

/opt/mssql/bin/sqlservr &
SQLPID=$!

do_restore &

wait "$SQLPID"
