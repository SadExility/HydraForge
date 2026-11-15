#!/usr/bin/env python3
"""
Hydra Ghost - compila no GitHub Actions e baixa o .jar (interface web mobile).
So usa a biblioteca padrao do Python. Funciona no Termux / Pydroid / PC.

Uso:  python build_on_github_web.py
      -> abre http://127.0.0.1:8765 no navegador (abra manualmente se nao abrir)
Token: GitHub > Settings > Developer settings > Tokens (classic)
       com os escopos:  repo  e  workflow
"""
import base64, io, json, os, threading, time, webbrowser, zipfile
import urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

API = "https://api.github.com"
PORT = 8765
DEST_DIR = None  # definido pelo app Android (start_background)

WORKFLOW = """name: Build
on: workflow_dispatch
jobs:
  build:
    runs-on: ubuntu-22.04
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-java@v4
        with:
          distribution: temurin
          java-version: 8
      - name: Instalar Gradle 2.14.1
        run: |
          wget -q https://services.gradle.org/distributions/gradle-2.14.1-bin.zip
          unzip -q gradle-2.14.1-bin.zip -d $HOME
      - name: Compilar
        env:
          GRADLE_OPTS: -Xmx3G
        run: |
          $HOME/gradle-2.14.1/bin/gradle setupDecompWorkspace --no-daemon
          $HOME/gradle-2.14.1/bin/gradle build --no-daemon
      - uses: actions/upload-artifact@v4
        with:
          name: hydraghost-jar
          path: build/libs/*.jar
"""

# ---------------------------------------------------------------- estado
STATE = {"status": "idle", "logs": [], "step": 0, "total": 0,
         "jar_name": "", "jar_data": None, "saved": ""}
LOCK = threading.Lock()


def log(msg):
    with LOCK:
        STATE["logs"].append(msg)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


# ---------------------------------------------------------------- build
class Builder:
    def __init__(self, token, repo_name, zip_bytes, destination):
        self.token, self.repo_name, self.zip_bytes = token, repo_name, zip_bytes
        self.destination = destination

    def req(self, method, url, data=None):
        headers = {"Accept": "application/vnd.github+json",
                   "User-Agent": "hg-builder",
                   "Authorization": "Bearer " + self.token}
        body = json.dumps(data).encode() if data is not None else None
        r = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(r, timeout=120) as resp:
                c = resp.read()
                return json.loads(c) if c else {}
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"HTTP {e.code} em {method} {url}\n{e.read().decode(errors='ignore')}")

    def download_artifact(self, url):
        opener = urllib.request.build_opener(NoRedirect)
        r = urllib.request.Request(url, headers={
            "Authorization": "Bearer " + self.token, "User-Agent": "hg-builder"})
        try:
            opener.open(r)
        except urllib.error.HTTPError as e:
            loc = e.headers.get("Location")
            if not loc:
                raise
            with urllib.request.urlopen(loc, timeout=300) as resp:
                return resp.read()
        raise RuntimeError("No redirect received when downloading the artifact")

    def load_project(self):
        files = {}
        with zipfile.ZipFile(io.BytesIO(self.zip_bytes)) as z:
            names = [n for n in z.namelist() if not n.endswith("/")]
            first = names[0].split("/")[0]
            top = first + "/" if all(n.startswith(first + "/") for n in names) else ""
            for n in names:
                files[n[len(top):]] = z.read(n)
        if "build.gradle" in files:
            files["build.gradle"] = files["build.gradle"].replace(
                b"https://maven.minecraftforge.net/",
                b"https://files.minecraftforge.net/maven")
        files[".github/workflows/build.yml"] = WORKFLOW.encode()
        files[".gitignore"] = b".gradle/\nbuild/\nrun/\n.idea/\n"
        return files

    def run(self):
        user = self.req("GET", API + "/user")["login"]
        log(f"Signed in as {user}")
        repo = f"{user}/{self.repo_name}"
        try:
            self.req("POST", API + "/user/repos", {
                "name": self.repo_name, "private": True,
                "description": "Hydra Ghost build", "auto_init": True})
            log(f"Private repository created: {repo}")
            time.sleep(3)
        except RuntimeError as e:
            if "already exists" in str(e):
                log(f"Repository already exists, updating: {repo}")
            else:
                raise

        files = self.load_project()
        with LOCK:
            STATE["status"], STATE["total"], STATE["step"] = "uploading", len(files), 0
        for i, (path, content) in enumerate(sorted(files.items()), 1):
            url = f"{API}/repos/{repo}/contents/{path}"
            payload = {"message": f"add {path}",
                       "content": base64.b64encode(content).decode()}
            try:
                payload["sha"] = self.req("GET", url)["sha"]
            except RuntimeError:
                pass
            self.req("PUT", url, payload)
            with LOCK:
                STATE["step"] = i
            log(f"[{i}/{len(files)}] {path}")

        with LOCK:
            STATE["status"] = "building"
        log("Starting build...")
        started = time.time()
        for _ in range(10):
            try:
                self.req("POST", f"{API}/repos/{repo}/actions/workflows/build.yml/dispatches",
                         {"ref": "main"})
                break
            except RuntimeError:
                time.sleep(5)
        else:
            raise RuntimeError("Nao consegui iniciar o workflow (token precisa do escopo 'workflow').")

        time.sleep(8)
        run = None
        while True:
            runs = self.req("GET", f"{API}/repos/{repo}/actions/runs?event=workflow_dispatch&per_page=5")
            cand = [r for r in runs["workflow_runs"]
                    if time.mktime(time.strptime(r["created_at"], "%Y-%m-%dT%H:%M:%SZ")) - time.timezone >= started - 60]
            if cand:
                run = cand[0]
                log(f"Status: {run['status']} - {int(time.time() - started)} s")
                if run["status"] == "completed":
                    break
            time.sleep(15)

        if run["conclusion"] != "success":
            raise RuntimeError(f"Build failed ({run['conclusion']}). Logs: {run['html_url']}")

        arts = self.req("GET", f"{API}/repos/{repo}/actions/runs/{run['id']}/artifacts")["artifacts"]
        art = next(a for a in arts if a["name"] == "hydraghost-jar")
        data = self.download_artifact(art["archive_download_url"])

        dest = self.destination
        if not os.path.isdir(dest):
            raise RuntimeError("The selected output folder does not exist or is not accessible: " + dest)
        os.makedirs(dest, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for n in z.namelist():
                if n.endswith(".jar"):
                    jar = z.read(n)
                    name = os.path.basename(n)
                    out = os.path.join(dest, name)
                    with open(out, "wb") as f:
                        f.write(jar)
                    log(f"JAR saved to: {out}")
                    with LOCK:
                        STATE["jar_name"], STATE["jar_data"], STATE["saved"] = name, jar, out
                    return
        raise RuntimeError("No .jar file was found in the artifact.")


def worker(token, repo, zip_bytes, destination):
    try:
        Builder(token, repo, zip_bytes, destination).run()
        with LOCK:
            STATE["status"] = "done"
    except Exception as e:
        log("ERROR: " + str(e))
        with LOCK:
            STATE["status"] = "error"


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json", extra=None):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/":
            return self.send(200, PAGE, "text/html; charset=utf-8")
        if self.path == "/logo":
            try:
                with open(os.path.join(os.path.dirname(__file__), "hydraforge_logo.png"), "rb") as f:
                    return self.send(200, f.read(), "image/png", {"Cache-Control": "public, max-age=86400"})
            except OSError:
                return self.send(404, "logo not found", "text/plain")
        if self.path.startswith("/status"):
            since = int(self.path.split("since=")[-1]) if "since=" in self.path else 0
            with LOCK:
                out = {"status": STATE["status"], "logs": STATE["logs"][since:],
                       "next": len(STATE["logs"]), "step": STATE["step"],
                       "total": STATE["total"], "jar_name": STATE["jar_name"],
                       "saved": STATE["saved"]}
            return self.send(200, json.dumps(out))
        if self.path == "/jar":
            with LOCK:
                data, name = STATE["jar_data"], STATE["jar_name"]
            if not data:
                return self.send(404, "sem jar", "text/plain")
            return self.send(200, data, "application/java-archive",
                             {"Content-Disposition": f'attachment; filename="{name}"'})
        self.send(404, "not found", "text/plain")

    def do_POST(self):
        if self.path != "/start":
            return self.send(404, "not found", "text/plain")
        with LOCK:
            if STATE["status"] in ("uploading", "building", "starting"):
                return self.send(409, json.dumps({"error": "A build is already running."}))
        token = self.headers.get("X-Token", "").strip()
        repo = unquote(self.headers.get("X-Repo", "")).strip()
        destination = unquote(self.headers.get("X-Destination", "")).strip()
        size = int(self.headers.get("Content-Length", 0))
        zip_bytes = self.rfile.read(size)
        if not token or not repo or not destination or not zip_bytes:
            return self.send(400, json.dumps({"error": "GitHub token, repository, output folder, and ZIP file are required."}))
        try:
            zipfile.ZipFile(io.BytesIO(zip_bytes)).testzip()
        except zipfile.BadZipFile:
            return self.send(400, json.dumps({"error": "O arquivo nao e um ZIP valido."}))
        with LOCK:
            STATE.update(status="starting", logs=[], step=0, total=0,
                         jar_name="", jar_data=None, saved="")
        threading.Thread(target=worker, args=(token, repo, zip_bytes, destination), daemon=True).start()
        self.send(200, json.dumps({"ok": True}))


# ---------------------------------------------------------------- pagina
PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#101810">
<title>HydraForge | Minecraft Build Center</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Press+Start+2P&family=Roboto+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root{--bg:#101810;--panel:#19251b;--line:#354b35;--text:#e9f3e5;--muted:#9caf98;--green:#75c94b;--green2:#a1e66b;--gold:#e4bd55;--red:#e56a62}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;min-height:100vh;color:var(--text);background-color:var(--bg);background-image:linear-gradient(#ffffff08 1px,transparent 1px),linear-gradient(90deg,#ffffff08 1px,transparent 1px);background-size:24px 24px;font:14px/1.6 "Roboto Mono",monospace;padding:max(18px,env(safe-area-inset-top)) 14px max(28px,env(safe-area-inset-bottom))}
main{width:100%;max-width:560px;margin:0 auto;display:grid;gap:14px}.pixel{font-family:"Press Start 2P",monospace;text-transform:uppercase}
header{display:flex;align-items:center;gap:13px;padding:8px 3px 14px}
.logo{width:58px;height:58px;object-fit:cover;border:2px solid #48e52b;border-radius:7px;box-shadow:0 0 18px #39e52b55}
h1{font:12px/1.8 "Press Start 2P",monospace;margin:0;color:#b9f28e}header p{font-size:10px;color:var(--muted);margin:4px 0 0}
.card{background:linear-gradient(180deg,#1d2a20,#172219);border:2px solid var(--line);border-radius:7px;box-shadow:0 5px 0 #080e09,inset 0 1px #ffffff0b;padding:16px;display:grid;gap:14px}
.card-title{font:10px/1.8 "Press Start 2P",monospace;color:var(--green2);margin:0}
label{display:block;color:#c1d0bc;font-size:11px;font-weight:700;margin-bottom:7px}
input[type=text],input[type=password]{width:100%;height:48px;padding:0 13px;border:2px solid #405541;border-radius:5px;background:#0f1911;color:var(--text);font:12px "Roboto Mono",monospace;outline:none}
input:focus{border-color:var(--green);box-shadow:0 0 0 2px #75c94b25}.field{position:relative}
.eye{position:absolute;right:5px;bottom:5px;width:40px;height:38px;border:0;background:transparent;color:var(--muted);font-size:18px}
.hint{font-size:10px;color:var(--muted);margin:-7px 0 0}
.drop{display:block;text-align:center;padding:20px 12px;border:2px dashed #587052;border-radius:5px;background:#111d13;cursor:pointer;transition:.15s}
.drop:hover,.drop.on{border-color:var(--green);background:#75c94b12}.drop .ico{display:block;font-size:27px;margin-bottom:7px}.drop b{display:block;font-size:12px;color:#d9edcf}.drop span:last-child{font-size:10px;color:var(--muted)}
input[type=file]{display:none}
button.main{width:100%;min-height:52px;border:0;border-bottom:5px solid #37672b;border-radius:5px;background:linear-gradient(#83d454,#5eaa39);color:#10200d;font:10px "Press Start 2P",monospace;cursor:pointer;padding:12px}
button.main:active{transform:translateY(2px);border-bottom-width:3px}button.main:disabled{opacity:.45;cursor:not-allowed}
.token-link{display:block;text-align:center;padding:12px;border:1px solid #52734b;border-radius:5px;color:#b9f28e;text-decoration:none;font-size:10px;background:#75c94b10}
.token-link:active{background:#75c94b25}
.brand-inside{display:flex;align-items:center;gap:11px;border-bottom:1px solid #354b35;padding-bottom:12px}.brand-inside img{width:43px;height:43px;object-fit:cover;border:1px solid #48e52b;border-radius:5px}.brand-inside strong{font:9px "Press Start 2P",monospace;color:#b9f28e}.brand-inside small{display:block;color:var(--muted);font-size:9px;margin-top:6px}
.progress-head{display:flex;align-items:center;justify-content:space-between;gap:10px}.pill{font-size:9px;padding:4px 8px;border:1px solid #52694b;border-radius:4px;color:var(--gold);background:#e4bd5510}
.steps{display:grid;grid-template-columns:repeat(3,1fr);gap:7px}.steps div{text-align:center;font-size:9px;padding:9px 3px;border:1px solid #354735;background:#111b13;color:#71816d;border-radius:4px}
.steps .act{border-color:var(--gold);color:var(--gold);background:#e4bd5510}.steps .done{border-color:var(--green);color:var(--green2);background:#75c94b12}
.statusbox{background:#101a12;border:1px solid #3b503a;border-radius:5px;padding:12px;display:grid;gap:8px}.statusline{display:flex;justify-content:space-between;gap:10px;align-items:center}.status-label{font-size:9px;color:var(--muted)}.status-value{font-size:10px;font-weight:700;color:#dcebd5;text-align:right;overflow-wrap:anywhere}
.dot{display:inline-block;width:8px;height:8px;background:var(--gold);margin-right:7px}.dot.live{background:var(--green);animation:blink 1s infinite}.dot.bad{background:var(--red)}@keyframes blink{50%{opacity:.25}}
.metrics{display:grid;grid-template-columns:1fr 1fr;gap:8px}.metric{background:#223124;border:1px solid #405541;border-radius:4px;padding:10px}.metric small{display:block;color:var(--muted);font-size:9px;margin-bottom:5px}.metric strong{font:11px "Press Start 2P",monospace;color:#c6f59e}
.bar{height:13px;border:2px solid #405541;background:#0d160e;overflow:hidden;border-radius:3px;padding:2px}.bar i{display:block;height:100%;width:0;background:repeating-linear-gradient(90deg,#75c94b 0 9px,#5eaa39 9px 12px);transition:width .3s}.bar.ind i{width:32%;animation:slide 1.1s infinite ease-in-out}@keyframes slide{0%{margin-left:-32%}100%{margin-left:100%}}
.msg{font-size:11px;color:#c5d4c0;margin:0;overflow-wrap:anywhere}.msg.err{color:var(--red)}.msg.ok{color:var(--green2)}
a.dl{display:none;text-align:center;text-decoration:none;padding:15px 8px;border-radius:5px;border-bottom:4px solid #37672b;background:#83d454;color:#10200d;font:9px "Press Start 2P",monospace}
details summary{cursor:pointer;color:var(--green2);font-size:10px}pre{margin:10px 0 0;background:#0b120c;color:#b8d6ad;border:1px solid #30432f;border-radius:4px;padding:12px;font:10px/1.6 "Roboto Mono",monospace;max-height:230px;overflow:auto;white-space:pre-wrap;word-break:break-word}
.footer{text-align:center;color:#65765f;font-size:9px;padding:3px}.footer img{width:30px;height:30px;object-fit:cover;vertical-align:middle;margin-right:7px;border-radius:4px}
@media(max-width:380px){h1{font-size:10px}.card{padding:13px}.metric strong{font-size:9px}}
</style>
</head>
<body><main>
<header><img class="logo" src="/logo" alt="HydraForge pickaxe logo"><div><h1>HydraForge</h1><p>Minecraft 1.8.9 · Build Center</p></div></header>
<section class="card">
 <div class="brand-inside"><img src="/logo" alt="HydraForge logo"><div><strong>HYDRAFORGE</strong><small>Compile your Minecraft project</small></div></div>
 <h2 class="card-title">Build Configuration</h2>
 <div class="field"><label for="tk">GITHUB CLASSIC TOKEN</label><input id="tk" type="password" autocomplete="off" placeholder="ghp_..."><button class="eye" id="eye" type="button" aria-label="Show token">◉</button></div>
 <p class="hint">Required token scopes: repo and workflow. Your token is only used for this build session.</p>
 <a class="token-link" href="https://github.com/settings/tokens/new" target="_blank" rel="noopener">↗ CREATE A CLASSIC TOKEN ON GITHUB</a>
 <div><label for="rp">REPOSITORY NAME</label><input id="rp" type="text" value="hydraforge" autocapitalize="none" autocorrect="off" spellcheck="false"></div>
 <div><label for="dest">OUTPUT FOLDER PATH</label><input id="dest" type="text" value="" placeholder="Enter an existing folder path" autocapitalize="none" autocorrect="off" spellcheck="false"></div>
 <p class="hint">No folder is preselected. Enter the full path of an existing folder where the JAR should be saved.</p>
 <div><label>PROJECT ARCHIVE</label><label class="drop" id="drop" for="zp"><span class="ico">📦</span><b id="zn">Choose project ZIP</b><span id="zs">Tap to browse for your project archive</span></label><input id="zp" type="file" accept=".zip,application/zip"></div>
 <button class="main" id="go">▶ START BUILD</button>
</section>
<section class="card" id="prog" style="display:none">
 <div class="brand-inside"><img src="/logo" alt="HydraForge logo"><div><strong>HYDRAFORGE BUILD</strong><small>Live compilation monitor</small></div></div>
 <div class="progress-head"><h2 class="card-title">Build Monitor</h2><span class="pill" id="phase">WAITING</span></div>
 <div class="steps"><div id="s1">01 · UPLOAD</div><div id="s2">02 · BUILD</div><div id="s3">03 · DOWNLOAD</div></div>
 <div class="statusbox"><div class="statusline"><span class="status-label">CURRENT STATUS</span><span class="status-value" id="statusText"><i class="dot"></i>Starting...</span></div><div class="statusline"><span class="status-label">CURRENT ACTIVITY</span><span class="status-value" id="activity">Preparing environment</span></div></div>
 <div class="metrics"><div class="metric"><small>⏱ ELAPSED TIME</small><strong id="elapsed">00:00:00</strong></div><div class="metric"><small>📁 FILES UPLOADED</small><strong id="count">0 / 0</strong></div></div>
 <div class="bar" id="bar"><i></i></div><p class="msg" id="msg">Waiting...</p>
 <a class="dl" id="dl" href="/jar">⬇ DOWNLOAD JAR FILE</a>
 <details><summary>▸ VIEW BUILD LOG</summary><pre id="log"></pre></details>
</section>
<div class="footer"><img src="/logo" alt="">HYDRAFORGE · MINECRAFT BUILD TOOLS</div>
</main>
<script>
const $=id=>document.getElementById(id);let since=0,timer=null,startedAt=0,clock=null;
$('eye').onclick=()=>{const t=$('tk');t.type=t.type==='password'?'text':'password'};
function setFile(f){if(!f)return;$('zn').textContent=f.name;$('zs').textContent=(f.size/1048576).toFixed(2)+' MB'}
$('zp').onchange=e=>setFile(e.target.files[0]);const d=$('drop');
['dragover','dragenter'].forEach(ev=>d.addEventListener(ev,e=>{e.preventDefault();d.classList.add('on')}));
['dragleave','drop'].forEach(ev=>d.addEventListener(ev,e=>{e.preventDefault();d.classList.remove('on')}));
d.addEventListener('drop',e=>{const f=e.dataTransfer.files[0];if(f){$('zp').files=e.dataTransfer.files;setFile(f)}});
function say(t,c){const m=$('msg');m.textContent=t;m.className='msg '+(c||'')}
function steps(a,dn){['s1','s2','s3'].forEach((id,i)=>{$(id).className=i<dn?'done':(i===a?'act':'')})}
function formatTime(seconds){const h=Math.floor(seconds/3600),m=Math.floor(seconds%3600/60),s=seconds%60;return [h,m,s].map(v=>String(v).padStart(2,'0')).join(':')}
function startClock(){startedAt=Date.now();$('elapsed').textContent='00:00:00';clearInterval(clock);clock=setInterval(()=>$('elapsed').textContent=formatTime(Math.floor((Date.now()-startedAt)/1000)),1000)}
function setStatus(text,type){$('statusText').innerHTML='<i class="dot '+(type||'')+'"></i>'+text}
function activityFromLogs(logs){if(!logs.length)return;const last=logs[logs.length-1];$('activity').textContent=last.length>58?last.slice(0,55)+'...':last}
$('go').onclick=async()=>{
 const tk=$('tk').value.trim(),rp=$('rp').value.trim(),dest=$('dest').value.trim(),f=$('zp').files[0];
 if(!tk)return alert('Enter your GitHub classic token.');
 if(!rp)return alert('Enter a repository name.');
 if(!dest)return alert('Enter an output folder path.');
 if(!f)return alert('Choose your project ZIP file.');
 $('go').disabled=true;$('prog').style.display='grid';$('dl').style.display='none';$('log').textContent='';since=0;steps(0,0);
 $('bar').classList.remove('ind');$('bar').firstElementChild.style.width='0';$('phase').textContent='STARTING';$('count').textContent='0 / 0';
 $('activity').textContent='Uploading project archive';setStatus('Connecting...','live');say('Uploading project ZIP...');startClock();
 try{
  const r=await fetch('/start',{method:'POST',body:f,headers:{'X-Token':tk,'X-Repo':encodeURIComponent(rp),'X-Destination':encodeURIComponent(dest)}});
  const j=await r.json();if(!r.ok){say(j.error||'Build error','err');setStatus('Could not start','bad');$('go').disabled=false;clearInterval(clock);return}
 }catch(e){say('Could not connect to the local server.','err');setStatus('Server unavailable','bad');$('go').disabled=false;clearInterval(clock);return}
 clearInterval(timer);timer=setInterval(poll,2000);poll();
};
async function poll(){
 let s;try{s=await (await fetch('/status?since='+since)).json()}catch(e){return}
 since=s.next;if(s.logs.length){const l=$('log');l.textContent+=s.logs.join('\n')+'\n';l.scrollTop=l.scrollHeight;activityFromLogs(s.logs)}
 $('count').textContent=(s.step||0)+' / '+(s.total||0);const bar=$('bar'),fill=bar.firstElementChild;
 if(s.status==='starting'){$('phase').textContent='CONNECTING';steps(0,0);setStatus('Connecting to GitHub','live');$('activity').textContent='Preparing repository';bar.classList.add('ind');say('Authenticating and preparing the build environment...')}
 else if(s.status==='uploading'){$('phase').textContent='UPLOAD';steps(0,0);setStatus('Uploading project files','live');bar.classList.remove('ind');fill.style.width=(s.total?s.step/s.total*100:0)+'%';say('Uploading project files to GitHub...')}
 else if(s.status==='building'){$('phase').textContent='BUILDING';steps(1,1);setStatus('Compilation in progress','live');bar.classList.add('ind');say('Gradle is compiling your project on GitHub Actions. This may take a few minutes...')}
 else if(s.status==='done'){clearInterval(timer);clearInterval(clock);$('elapsed').textContent=formatTime(Math.floor((Date.now()-startedAt)/1000));$('phase').textContent='COMPLETE';steps(3,3);bar.classList.remove('ind');fill.style.width='100%';setStatus('Build completed','live');$('activity').textContent='Generated: '+s.jar_name;say('Success! Saved to: '+s.saved,'ok');$('dl').style.display='block';$('go').disabled=false;$('dl').textContent='⬇ DOWNLOAD '+s.jar_name}
 else if(s.status==='error'){clearInterval(timer);clearInterval(clock);$('phase').textContent='FAILED';bar.classList.remove('ind');setStatus('Build failed','bad');$('activity').textContent='Check the build log for details';say('The build failed. Open the log below for details.','err');$('go').disabled=false;document.querySelector('details').open=true}
}
</script></body></html>
"""


_SERVER = None


def start_background(dest_dir=None):
    """Usado pelo app Android: sobe o servidor em segundo plano e devolve a porta."""
    global _SERVER, DEST_DIR
    if _SERVER is None:
        for port in (PORT, 0):
            try:
                _SERVER = ThreadingHTTPServer(("127.0.0.1", port), Handler)
                break
            except OSError:
                continue
        threading.Thread(target=_SERVER.serve_forever, daemon=True).start()
    return _SERVER.server_address[1]


def main():
    server = None
    for port in (PORT, 0):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            continue
    url = f"http://127.0.0.1:{server.server_address[1]}"
    print("Abra no navegador:", url)
    print("(Ctrl+C para encerrar)")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nEncerrado.")


if __name__ == "__main__":
    main()
