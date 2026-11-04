from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field
from pathlib import Path
from datetime import datetime
import io
import os
import sqlite3
import tempfile
import zipfile


def resolve_data_root() -> Path:
    configured = os.getenv('HONGZHOU_DATA_DIR')
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(os.getenv('LOCALAPPDATA', str(Path.home()))) / 'HongzhouAssistant'


ROOT = resolve_data_root()
ROOT.mkdir(parents=True, exist_ok=True)
DB = ROOT / 'hongzhou.db'
BACKUPS = ROOT / 'backups'
BACKUPS.mkdir(parents=True, exist_ok=True)
app = FastAPI(title='弘洲營運助手')


def connect(database_path: Path | None = None):
    target = database_path or DB
    con = sqlite3.connect(target)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    return con


def setup_database():
    ROOT.mkdir(parents=True, exist_ok=True)
    BACKUPS.mkdir(parents=True, exist_ok=True)
    with connect() as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS prices(
                id INTEGER PRIMARY KEY,
                date TEXT NOT NULL,
                market TEXT NOT NULL,
                product TEXT NOT NULL,
                price REAL NOT NULL CHECK(price>=0),
                unit TEXT NOT NULL,
                note TEXT DEFAULT '',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        c.execute(
            """CREATE TABLE IF NOT EXISTS customers(
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                address TEXT DEFAULT '',
                phone TEXT DEFAULT '',
                note TEXT DEFAULT ''
            )"""
        )
        c.execute(
            """CREATE TABLE IF NOT EXISTS trades(
                id INTEGER PRIMARY KEY,
                date TEXT NOT NULL,
                customer_id INTEGER,
                kind TEXT NOT NULL CHECK(kind IN ('buy','sell')),
                material TEXT NOT NULL,
                kg REAL NOT NULL CHECK(kg>0),
                unit_price REAL NOT NULL CHECK(unit_price>=0),
                transport REAL NOT NULL DEFAULT 0 CHECK(transport>=0),
                note TEXT DEFAULT '',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(customer_id) REFERENCES customers(id)
            )"""
        )
        c.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT DEFAULT CURRENT_TIMESTAMP)"
        )
        c.execute(
            "INSERT OR IGNORE INTO schema_migrations(version) VALUES (1)"
        )
    return DB


setup_database()


class Price(BaseModel):
    date: str
    market: str = Field(min_length=1, max_length=80)
    product: str = Field(min_length=1, max_length=80)
    price: float = Field(ge=0)
    unit: str = Field(min_length=1, max_length=30)
    note: str = ''


class Customer(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    address: str = ''
    phone: str = ''
    note: str = ''


class Trade(BaseModel):
    date: str
    customer_id: int | None = None
    kind: str
    material: str = Field(min_length=1, max_length=80)
    kg: float = Field(gt=0)
    unit_price: float = Field(ge=0)
    transport: float = Field(default=0, ge=0)
    note: str = ''


def date_ok(d):
    try:
        datetime.strptime(d, '%Y-%m-%d')
    except ValueError as exc:
        raise HTTPException(400, '日期格式需為 YYYY-MM-DD') from exc


def backup_database(as_zip: bool = True, target_root: Path | None = None) -> Path:
    target_root = (target_root or BACKUPS).resolve()
    target_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    if as_zip:
        zip_path = target_root / f'hongzhou-transfer-{timestamp}.zip'
        temp_db = None
        try:
            with tempfile.NamedTemporaryFile(suffix='.db', dir=target_root, delete=False) as tmp:
                temp_db = Path(tmp.name)
            with connect() as src, sqlite3.connect(temp_db) as dest:
                src.backup(dest)
            with sqlite3.connect(temp_db) as check:
                if check.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise HTTPException(500, '資料庫完整性檢查失敗')
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as archive:
                archive.write(temp_db, arcname='hongzhou.db')
                archive.writestr(
                    '移機說明.txt',
                    '弘洲營運助手資料備份\n請先關閉舊電腦與新電腦上的程式。\n在新電腦解壓縮本專案，執行 restore_windows.bat，選擇這個ZIP。\n還原前程式會備份新電腦現有資料庫。\n此檔案包含公司資料，請妥善保管。\n',
                )
            return zip_path
        finally:
            if temp_db and temp_db.exists():
                try:
                    temp_db.unlink(missing_ok=True)
                except PermissionError:
                    pass
    backup_path = target_root / f'hongzhou-{timestamp}.db'
    with connect() as src, sqlite3.connect(backup_path) as dest:
        src.backup(dest)
    return backup_path


def restore_database(backup_file: str | Path, target_root: Path | None = None) -> Path:
    backup_path = Path(backup_file)
    target_root = (target_root or ROOT).resolve()
    target_root.mkdir(parents=True, exist_ok=True)
    target_db = target_root / 'hongzhou.db'

    if backup_path.suffix.lower() == '.zip':
        with zipfile.ZipFile(backup_path) as archive:
            names = archive.namelist()
            if 'hongzhou.db' not in names:
                raise ValueError('ZIP 內沒有 hongzhou.db')
            extracted = archive.read('hongzhou.db')
        with tempfile.NamedTemporaryFile(suffix='.db', dir=target_root, delete=False) as tmp:
            tmp.write(extracted)
            temp_db = Path(tmp.name)
    else:
        temp_db = backup_path

    try:
        with sqlite3.connect(temp_db) as check:
            if check.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('資料庫損毀或不是有效資料庫')
            tables = {row[0] for row in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {'prices', 'customers', 'trades'}.issubset(tables):
                raise ValueError('不是有效的弘洲資料庫')

        if target_db.exists():
            backup_target = (target_root / 'backups').resolve()
            backup_target.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
            candidate = backup_target / f'before-restore-{timestamp}.db'
            counter = 1
            while candidate.exists():
                candidate = backup_target / f'before-restore-{timestamp}-{counter}.db'
                counter += 1
            with sqlite3.connect(target_db) as src, sqlite3.connect(candidate) as dest:
                src.backup(dest)

        target_db.write_bytes(temp_db.read_bytes())
        return target_db
    finally:
        if temp_db != backup_path and temp_db.exists():
            try:
                temp_db.unlink(missing_ok=True)
            except PermissionError:
                pass


@app.get('/', response_class=HTMLResponse)
def home():
    return (Path(__file__).parent / 'index.html').read_text(encoding='utf-8')


@app.get('/api/prices')
def prices():
    with connect() as c:
        return [dict(r) for r in c.execute('SELECT * FROM prices ORDER BY date DESC,id DESC LIMIT 500')]


@app.post('/api/prices')
def add_price(p: Price):
    date_ok(p.date)
    with connect() as c:
        c.execute(
            'INSERT INTO prices(date,market,product,price,unit,note) VALUES(?,?,?,?,?,?)',
            (p.date, p.market, p.product, p.price, p.unit, p.note),
        )
    return {'ok': True}


@app.get('/api/customers')
def customers():
    with connect() as c:
        return [dict(r) for r in c.execute('SELECT * FROM customers ORDER BY id DESC')]


@app.post('/api/customers')
def add_customer(p: Customer):
    with connect() as c:
        c.execute('INSERT INTO customers(name,address,phone,note) VALUES(?,?,?,?)', (p.name, p.address, p.phone, p.note))
    return {'ok': True}


@app.get('/api/trades')
def trades():
    with connect() as c:
        return [dict(r) for r in c.execute('SELECT t.*,c.name AS customer FROM trades t LEFT JOIN customers c ON c.id=t.customer_id ORDER BY t.date DESC,t.id DESC LIMIT 1000')]


@app.post('/api/trades')
def add_trade(p: Trade):
    date_ok(p.date)
    if p.kind not in ('buy', 'sell'):
        raise HTTPException(400, '交易類型錯誤')
    with connect() as c:
        if p.customer_id is not None and not c.execute('SELECT 1 FROM customers WHERE id=?', (p.customer_id,)).fetchone():
            raise HTTPException(400, '客戶不存在')
        c.execute(
            'INSERT INTO trades(date,customer_id,kind,material,kg,unit_price,transport,note) VALUES(?,?,?,?,?,?,?,?)',
            (p.date, p.customer_id, p.kind, p.material, p.kg, p.unit_price, p.transport, p.note),
        )
    return {'ok': True}


@app.get('/api/summary')
def summary():
    with connect() as c:
        rows = c.execute('SELECT kind,material,SUM(kg) kg,SUM(kg*unit_price) total,SUM(transport) transport FROM trades GROUP BY kind,material').fetchall()
    by = {}
    for r in rows:
        x = by.setdefault(r['material'], {'material': r['material'], 'in_kg': 0, 'out_kg': 0, 'buy_total': 0, 'sell_total': 0, 'transport': 0})
        x['in_kg' if r['kind'] == 'buy' else 'out_kg'] = r['kg']
        x['buy_total' if r['kind'] == 'buy' else 'sell_total'] = r['total']
        x['transport'] += r['transport']
    for x in by.values():
        x['balance_kg'] = x['in_kg'] - x['out_kg']
    return list(by.values())


@app.post('/api/backup')
def backup():
    target = backup_database(as_zip=False)
    return {'ok': True, 'file': target.name, 'folder': str(target.parent)}


@app.post('/api/restore')
async def restore(file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith(('.zip', '.db')):
        raise HTTPException(400, '請上傳 .zip 或 .db 備份檔')
    with tempfile.NamedTemporaryFile(suffix='.' + Path(file.filename).suffix.lstrip('.'), delete=False) as tmp:
        content = await file.read()
        tmp.write(content)
        temp_file = Path(tmp.name)
    try:
        restore_database(temp_file)
        return {'ok': True, 'message': '資料還原完成'}
    finally:
        if temp_file.exists():
            temp_file.unlink(missing_ok=True)


@app.get('/api/export/{table}')
def export_csv(table: str):
    if table not in ('prices', 'customers', 'trades'):
        raise HTTPException(404)
    import csv
    out = ROOT / (table + '.csv')
    with connect() as c:
        cur = c.execute('SELECT * FROM ' + table)
        with out.open('w', newline='', encoding='utf-8-sig') as f:
            w = csv.writer(f)
            w.writerow([x[0] for x in cur.description])
            w.writerows(cur.fetchall())
    return FileResponse(out, filename=table + '.csv', media_type='text/csv')


@app.get('/api/portable-backup')
def portable_backup():
    """Generate a zip snapshot suitable for migration to another local Windows PC."""
    snapshot = io.BytesIO()
    temp_db = backup_database(as_zip=False, target_root=BACKUPS)
    try:
        with sqlite3.connect(temp_db) as check:
            if check.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise HTTPException(500, '資料庫完整性檢查失敗')
        with zipfile.ZipFile(snapshot, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.write(temp_db, arcname='hongzhou.db')
            archive.writestr(
                '移機說明.txt',
                '弘洲營運助手資料備份\n請先關閉舊電腦與新電腦上的程式。\n在新電腦解壓縮本專案，執行 restore_windows.bat，選擇這個ZIP。\n還原前程式會備份新電腦現有資料庫。\n此檔案包含公司資料，請妥善保管。\n',
            )
        snapshot.seek(0)
        name = 'hongzhou-transfer-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.zip'
        return StreamingResponse(
            snapshot,
            media_type='application/zip',
            headers={
                'Content-Disposition': f'attachment; filename="{name}"',
                'Cache-Control': 'no-store',
            },
        )
    finally:
        temp_db.unlink(missing_ok=True)
