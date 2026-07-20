import sqlite3
from pathlib import Path
def backup_sqlite(source,destination):
 target=Path(destination);target.parent.mkdir(parents=True,exist_ok=True)
 with sqlite3.connect(str(source)) as src,sqlite3.connect(str(target)) as dst:src.backup(dst)
 with sqlite3.connect(str(target)) as check:
  if check.execute("PRAGMA integrity_check").fetchone()[0]!="ok":raise ValueError("backup verification failed")
 return target
def verify_sqlite(path):
 with sqlite3.connect(str(path)) as db:return db.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
