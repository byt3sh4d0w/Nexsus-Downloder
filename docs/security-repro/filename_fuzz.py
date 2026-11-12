"""Feeds hostile names to safe_filename() and checks none leaves the target folder. Run from the repo root."""
import os,sys,tempfile
sys.path.insert(0,os.getcwd())
from core.security import safe_filename, validate_download_url
cases=["../../evil.exe","..\\..\\evil.exe","/etc/passwd","C:\\Windows\\x.dll","..","...","CON","con .txt","NUL.txt","COM1","LPT9.log","a:b.txt","file.txt:stream","evil\u202etxt.exe","a\x00b","  ","a"*500,"résumé.pdf","名前.zip","x;calc.exe;.txt","$(calc).txt","trailing. .","COM¹","CONIN$"]
d=tempfile.mkdtemp(); base=os.path.realpath(d); bad=0
for c in cases:
    n=safe_filename(c); p=os.path.realpath(os.path.join(d,n))
    esc = os.path.dirname(p)!=base or any(ch in n for ch in '/\\:')
    bad+=esc; print(repr(c)[:40].ljust(42),'->',repr(n)[:60], 'ESCAPES' if esc else 'ok')
print("escapes:",bad)
for u in ["https://u:p@host/x","ftp://h/x","file:///etc/passwd","https://h/a b","javascript:alert(1)","http://127.0.0.1/x","http://[::1]/x","https:///x","HTTPS://Ex.com/a"]:
    print(u.ljust(32),validate_download_url(u))
