from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse, RedirectResponse
from pydantic import BaseModel, Field, ConfigDict
from contextlib import contextmanager, asynccontextmanager
from threading import RLock
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse
from pathlib import Path
from datetime import datetime
import io
import os
import sqlite3
import tempfile
import zipfile
import market
import market_sources
import metal_engine
import metal_analysis
import market_detail
from fastapi.staticfiles import StaticFiles


def resolve_data_root() -> Path:
    configured = os.getenv('HONGZHOU_DATA_DIR')
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(os.getenv('LOCALAPPDATA', str(Path.home()))) / 'HongzhouAssistant'


ROOT = resolve_data_root()
DB = ROOT / 'hongzhou.db'
BACKUPS = ROOT / 'backups'
DB_LOCK = RLock()
MAX_BACKUP_BYTES = 256 * 1024 * 1024


@asynccontextmanager
async def lifespan(application):
    setup_database()
    stop, worker = market_sources.start_scheduler()
    try:
        yield
    finally:
        stop.set()
        if worker:
            import asyncio
            await asyncio.to_thread(worker.join, 25)


app = FastAPI(title='弘洲營運助手', lifespan=lifespan)
app.include_router(market.router)
app.include_router(metal_engine.router)
app.include_router(metal_analysis.router)
app.include_router(market_detail.router)
app.mount('/assets', StaticFiles(directory=Path(__file__).parent / 'assets'), name='assets')
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['localhost', '127.0.0.1', '[::1]', 'testserver'])


@app.get('/api/health')
def health():
    with connect() as c:
        c.execute('SELECT id FROM market_quotes LIMIT 1').fetchone()
        version = c.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0]
    return {'app':'hongzhou-metal-market','ui_contract':'market-detail-3','database':'ok','schema_version':version,'data_directory':str(ROOT.resolve())}


@app.get('/market.html', include_in_schema=False)
@app.get('/index.html', include_in_schema=False)
def canonical_home():
    return RedirectResponse('/')


@app.middleware('http')
async def protect_local_requests(request, call_next):
    from urllib.parse import urlsplit
    origin = request.headers.get('origin')
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        if request.headers.get('sec-fetch-site') == 'cross-site' or (origin and urlsplit(origin).netloc != request.headers.get('host')):
            return JSONResponse({'detail': '僅接受同來源操作'}, status_code=403)
    response = await call_next(request)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


@contextmanager
def connect(database_path: Path | None = None):
    with DB_LOCK:
        con = sqlite3.connect(database_path or DB, timeout=15)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA foreign_keys=ON')
        try:
            with con:
                yield con
        finally:
            con.close()


def setup_database():
    ROOT.mkdir(parents=True, exist_ok=True)
    BACKUPS.mkdir(parents=True, exist_ok=True)
    # Snapshot existing databases before the first additive market migration.
    if DB.exists():
        with connect() as c:
            has_market = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_quotes'").fetchone()
            has_versions = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'").fetchone()
            version = c.execute('SELECT COALESCE(MAX(version),0) FROM schema_migrations').fetchone()[0] if has_versions else 0
        if not has_market or version < 4:
            backup_database(as_zip=False)
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
        market.migrate(c)
        market_sources.migrate(c)
        metal_engine.migrate(c)
    return DB


class InputModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, allow_inf_nan=False)


class Price(InputModel):
    date: str
    market: str = Field(min_length=1, max_length=80)
    product: str = Field(min_length=1, max_length=80)
    price: float = Field(ge=0)
    unit: str = Field(min_length=1, max_length=30)
    note: str = ''


class Customer(InputModel):
    name: str = Field(min_length=1, max_length=150)
    address: str = ''
    phone: str = ''
    note: str = ''


class Trade(InputModel):
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
            with connect() as src, connect(temp_db) as dest:
                src.backup(dest)
            with connect(temp_db) as check:
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
    with connect() as src, connect(backup_path) as dest:
        src.backup(dest)
    return backup_path


def validate_database(path: Path):
    required = {
        'prices': {'id', 'date', 'market', 'product', 'price', 'unit', 'note', 'created_at'},
        'customers': {'id', 'name', 'address', 'phone', 'note'},
        'trades': {'id', 'date', 'customer_id', 'kind', 'material', 'kg', 'unit_price', 'transport', 'note', 'created_at'},
    }
    with connect(path) as c:
        if c.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('資料庫完整性檢查失敗')
        for table, columns in required.items():
            if not columns.issubset({r['name'] for r in c.execute(f'PRAGMA table_info({table})')}):
                raise ValueError('備份缺少必要欄位')
        if c.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('備份客戶關聯無效')
        if c.execute("SELECT 1 FROM sqlite_master WHERE name='schema_migrations'").fetchone():
            version = c.execute('SELECT COALESCE(MAX(version),0) FROM schema_migrations').fetchone()[0]
            if version > 4:
                raise ValueError('備份版本較新，請先升級程式')
            if version >= 2:
                columns = {r['name'] for r in c.execute('PRAGMA table_info(market_quotes)')}
                if not set(market.CSV_FIELDS + ['id', 'updated_at', 'method']).issubset(columns):
                    raise ValueError('行情資料表欄位不完整')
                for row in c.execute('SELECT * FROM market_quotes'):
                    market.Quote(**{name: row[name] for name in market.CSV_FIELDS})
            if version >= 3:
                columns = {r['name'] for r in c.execute('PRAGMA table_info(market_quotes)')}
                if not {'publication_known','source_updated_at'}.issubset(columns):
                    raise ValueError('自動來源欄位不完整')
                c.execute('SELECT instrument,provider,checked_at,success_at,latest_date,source_updated_at,error,imported,conflicts FROM market_source_status LIMIT 0')
                row = c.execute("SELECT value FROM market_settings WHERE name='application'").fetchone()
                if row:
                    values = market_sources.Settings.model_validate_json(row[0])
                    if not set(values.compare_markets).issubset(values.tracked_markets):
                        raise ValueError('比較設定與追蹤市場不符')
            if version >= 4:
                c.execute('SELECT id,name,provider,currency,unit,frequency,specification,source_url,license,notes FROM metal_series LIMIT 0')
                c.execute('SELECT provider,checked_at,success_at,latest_period,error,imported,source_url,source_updated FROM metal_updates LIMIT 0')
                for row in c.execute('SELECT series_id,period,value,retrieved_at,source_updated,notes FROM metal_observations'):
                    metal_engine.numeric(row['value'])
                    datetime.strptime(row['period'],'%Y-%m')
                metal_engine.validate(c)
                market_detail.validate(c)
                row=c.execute("SELECT value FROM market_settings WHERE name='metals'").fetchone()
                if row:metal_engine.MetalSettings.model_validate_json(row[0])
        for table, model in [('prices', Price), ('customers', Customer), ('trades', Trade)]:
            for row in c.execute(f'SELECT * FROM {table}'):
                model(**dict(row))
                if table != 'customers':
                    date_ok(row['date'])
                if table == 'trades' and row['kind'] not in ('buy', 'sell'):
                    raise ValueError('備份交易類型無效')


def restore_database(backup_file: str | Path, target_root: Path | None = None) -> Path:
    # Same lock order as source updates: finish any in-flight download before restore.
    with market_sources.UPDATE_LOCK, metal_engine.LOCK:
        with DB_LOCK:
            return _restore_database(backup_file, target_root)


def _restore_database(backup_file: str | Path, target_root: Path | None = None) -> Path:
    backup_path = Path(backup_file)
    if not backup_path.is_file() or backup_path.stat().st_size > MAX_BACKUP_BYTES:
        raise ValueError('備份不存在或超過 256 MB')
    target_root = (target_root or ROOT).resolve()
    target_root.mkdir(parents=True, exist_ok=True)
    target_db = target_root / 'hongzhou.db'

    if backup_path.suffix.lower() == '.zip':
        with zipfile.ZipFile(backup_path) as archive:
            names = archive.namelist()
            if names.count('hongzhou.db') != 1:
                raise ValueError('ZIP 內沒有 hongzhou.db')
            if archive.getinfo('hongzhou.db').file_size > MAX_BACKUP_BYTES:
                raise ValueError('解壓後資料庫超過 256 MB')
            extracted = archive.read('hongzhou.db')
        with tempfile.NamedTemporaryFile(suffix='.db', dir=target_root, delete=False) as tmp:
            tmp.write(extracted)
            temp_db = Path(tmp.name)
    else:
        temp_db = backup_path

    try:
        validate_database(temp_db)

        if target_db.exists():
            backup_target = (target_root / 'backups').resolve()
            backup_target.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
            candidate = backup_target / f'before-restore-{timestamp}.db'
            counter = 1
            while candidate.exists():
                candidate = backup_target / f'before-restore-{timestamp}-{counter}.db'
                counter += 1
            with connect(target_db) as src, connect(candidate) as dest:
                src.backup(dest)
            validate_database(candidate)

        with connect(temp_db) as src, connect(target_db) as dest:
            src.backup(dest)
        if target_db.resolve() == DB.resolve():
            setup_database()
        return target_db
    finally:
        if temp_db != backup_path and temp_db.exists():
            try:
                temp_db.unlink(missing_ok=True)
            except PermissionError:
                pass


@app.get('/', response_class=HTMLResponse)
def home():
    folder = Path(__file__).parent
    html=(folder / 'market.html').read_text(encoding='utf-8')
    html=html.replace('<!--settings-panel-->',(folder / 'templates' / 'settings.html').read_text(encoding='utf-8'))
    html=html.replace('<!--decision-panel-->',(folder / 'templates' / 'decision.html').read_text(encoding='utf-8').replace('<!--steel-updates-->',(folder / 'templates' / 'steel-updates.html').read_text(encoding='utf-8')))
    metals=(folder / 'templates' / 'metals.html').read_text(encoding='utf-8')
    metals=metals.replace('<form id="metalSettings"',(folder / 'templates' / 'metal-controls.html').read_text(encoding='utf-8')+'<form id="metalSettings"')
    return html.replace('<!--metals-panels-->',metals+(folder / 'templates' / 'detail.html').read_text(encoding='utf-8'))


@app.get('/legacy', response_class=HTMLResponse)
def legacy():
    return (Path(__file__).parent / 'index.html').read_text(encoding='utf-8')


@app.get('/market-guide')
def market_guide():
    return FileResponse(Path(__file__).parent / 'docs' / 'MARKET_SOURCES.md', media_type='text/plain; charset=utf-8')


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
    temp_file = None
    try:
        with tempfile.NamedTemporaryFile(suffix=Path(file.filename).suffix, delete=False) as tmp:
            temp_file = Path(tmp.name)
            size = 0
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_BACKUP_BYTES:
                    raise HTTPException(413, '備份超過 256 MB')
                tmp.write(chunk)
        restore_database(temp_file)
        return {'ok': True, 'message': '資料還原完成'}
    except (ValueError, sqlite3.Error, zipfile.BadZipFile) as exc:
        raise HTTPException(400, '備份無效或版本不支援，原資料未變更') from exc
    finally:
        await file.close()
        if temp_file and temp_file.exists():
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
        with connect(temp_db) as check:
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
