"""Offline Windows restore utility. Run only after shutting down the app."""
from pathlib import Path
import os, sqlite3, sys, zipfile, shutil
from datetime import datetime
from tkinter import Tk, filedialog

root=Path(os.environ.get('LOCALAPPDATA',str(Path.home()))) / 'HongzhouAssistant'
root.mkdir(parents=True,exist_ok=True)
db=root/'hongzhou.db'
Tk().withdraw()
path=filedialog.askopenfilename(title='選擇弘洲營運助手移機備份ZIP',filetypes=[('ZIP備份','*.zip')])
if not path:
    print('已取消。');sys.exit(0)
try:
    with zipfile.ZipFile(path) as z:
        if z.namelist().count('hongzhou.db') != 1: raise ValueError('ZIP內沒有唯一的 hongzhou.db')
        info=z.getinfo('hongzhou.db')
        if info.file_size > 1024*1024*1024: raise ValueError('備份超過1GB，請人工檢查')
        data=z.read(info)
    import tempfile
    with tempfile.NamedTemporaryFile(dir=root,suffix='.db',delete=False) as f:
        f.write(data);tmp=Path(f.name)
    try:
        with sqlite3.connect(tmp) as c:
            if c.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise ValueError('資料庫損毀')
            names={r[0] for r in c.execute('SELECT name FROM sqlite_master WHERE type="table"')}
            if not {'prices','customers','trades'}.issubset(names): raise ValueError('不是有效的弘洲資料庫')
        print('還原將取代目前電腦的弘洲資料。請先確認APP已關閉。')
        if input('輸入 RESTORE 確認還原：').strip()!='RESTORE':
            print('已取消。');sys.exit(0)
        if db.exists():
            folder=root/'backups';folder.mkdir(exist_ok=True)
            old=folder/('before-restore-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'.db')
            with sqlite3.connect(db) as src, sqlite3.connect(old) as dest: src.backup(dest)
            print('原有資料已備份：',old)
        os.replace(tmp,db)
        print('還原完成：',db)
    finally:
        tmp.unlink(missing_ok=True)
except Exception as e:
    print('還原失敗：',e);sys.exit(1)
