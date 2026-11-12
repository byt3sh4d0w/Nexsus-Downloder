"""Repro for SEC-01: HTTPS link redirected to plain HTTP. Needs openssl. Run from the repo root: python docs/security-repro/https_downgrade.py

Before the fix the HTTP target logs HEAD+GET; after the fix it logs nothing."""
import os,sys,ssl,threading,http.server,socket
import subprocess, tempfile
T=tempfile.mkdtemp(prefix="nexus_tls_")
def sh(*a): subprocess.run(a, cwd=T, check=True, capture_output=True)
sh("openssl","req","-x509","-newkey","rsa:2048","-nodes","-keyout","ca.key","-out","ca.pem","-days","2","-subj","/CN=TestCA","-addext","basicConstraints=critical,CA:TRUE","-addext","keyUsage=critical,keyCertSign,cRLSign")
sh("openssl","req","-newkey","rsa:2048","-nodes","-keyout","srv.key","-out","srv.csr","-subj","/CN=localhost")
open(T+"/ext.cnf","w").write("subjectAltName=DNS:localhost\nbasicConstraints=CA:FALSE\nkeyUsage=digitalSignature,keyEncipherment\n")
sh("openssl","x509","-req","-in","srv.csr","-CA","ca.pem","-CAkey","ca.key","-CAcreateserial","-out","srv.pem","-days","2","-extfile","ext.cnf")
os.environ["SSL_CERT_FILE"]=T+"/ca.pem"
sys.path.insert(0,os.getcwd())
seen=[]
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self,*a):pass
    def do_GET(self):
        if self.server.kind=="http":
            seen.append((self.command,self.path,dict(self.headers))); self.send_response(200); self.send_header("Content-Length","3"); self.end_headers(); self.wfile.write(b"abc")
        else:
            self.send_response(302); self.send_header("Location",f"http://localhost:{HP}/secret?token=SIGNED123"); self.end_headers()
    do_HEAD=do_GET
def serve(kind,ctx=None):
    s=http.server.ThreadingHTTPServer(("127.0.0.1",0),H); s.kind=kind
    if ctx: s.socket=ctx.wrap_socket(s.socket,server_side=True)
    threading.Thread(target=s.serve_forever,daemon=True).start(); return s.server_address[1]
HP=serve("http")
ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ctx.load_cert_chain(T+"/srv.pem",T+"/srv.key")
SP=serve("https",ctx)
from core.downloader import Downloader
d=Downloader(f"https://localhost:{SP}/start?token=SIGNED123","/tmp/x.bin")
try:
    print("result:",d._probe_once(None))
except Exception as e:
    print("raised:",type(e).__name__,str(e)[:120])
print("plain-HTTP requests that reached the redirect target BEFORE refusal:",[(m,p) for m,p,h in seen])
# SSRF-style: redirect to loopback is followed
d2=Downloader(f"http://localhost:{HP}/x","/tmp/y.bin")
print("loopback target allowed:",d2._probe_once(None)[:3])
