"""手机上打开的那一页。

单文件、无外部依赖(手机可能连不上外网,也不该为了一个内网页面去 CDN
取东西)。三件事:登录记住名字与口令、长轮询收消息、发消息。

**断线重连**在客户端这一半:请求失败或超时都按游标 `cursor` 重来,
退避 1→5 秒,页面回到前台立刻重连,断线期间的消息由服务端按游标补齐。
"""

PAGE_HTML = """<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>AI 群聊</title>
<style>
  :root { color-scheme: dark; --bg:#121212; --panel:#1e1e1e; --line:#2f2f2f;
          --text:#e0e0e0; --muted:#9e9e9e; --me:#ffd75f; --notice:#ff9e64; }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--text);
         font:16px/1.5 -apple-system,system-ui,"PingFang SC",sans-serif;
         display:flex; flex-direction:column; height:100dvh; }
  header { padding:10px 14px; background:var(--panel); border-bottom:1px solid var(--line);
           display:flex; align-items:center; gap:8px; }
  header b { font-size:15px; } header small { color:var(--muted); margin-left:auto; }
  #log { flex:1; overflow-y:auto; padding:12px 14px; -webkit-overflow-scrolling:touch; }
  .row { margin-bottom:10px; word-break:break-word; }
  .who { color:#5fd7ff; font-weight:600; }
  .who.me { color:var(--me); }
  .notice { color:var(--notice); font-size:14px; }
  .ts { color:var(--muted); font-size:12px; margin-left:6px; }
  form { display:flex; gap:8px; padding:10px; background:var(--panel);
         border-top:1px solid var(--line);
         padding-bottom:calc(10px + env(safe-area-inset-bottom)); }
  input, button { font:inherit; border-radius:10px; border:1px solid var(--line); }
  input { padding:10px 12px; background:#0d0d0d; color:var(--text); }
  form input { flex:1; }
  button { padding:10px 16px; background:#005f87; color:#fff; border:none; }
  #setup { position:fixed; inset:0; background:var(--bg); display:none;
           flex-direction:column; justify-content:center; gap:12px; padding:24px; }
  #setup.show { display:flex; }
  #state { font-size:12px; color:var(--muted); }
</style>
</head>
<body>
<header><b>AI 群聊</b><small id="state">连接中…</small></header>
<div id="log"></div>
<form id="send">
  <input id="text" placeholder="@claude 帮我看看…" autocomplete="off">
  <button>发送</button>
</form>

<div id="setup">
  <b>先填个名字和口令</b>
  <input id="name" placeholder="你的名字,比如 小明">
  <input id="token" placeholder="访问口令(启动网关时打印在终端里)">
  <button id="save">进群</button>
</div>

<script>
const $ = (id) => document.getElementById(id);
const store = window.localStorage;
let cursor = 0, backoff = 1000, stopped = false;

function need_setup() {
  return !store.getItem("name") || !store.getItem("token");
}
function show_setup() {
  $("setup").classList.add("show");
  $("name").value = store.getItem("name") || "";
  const from_url = new URLSearchParams(location.search).get("token");
  $("token").value = store.getItem("token") || from_url || "";
}
$("save").onclick = () => {
  const name = $("name").value.trim(), token = $("token").value.trim();
  if (!name || !token) return;
  store.setItem("name", name); store.setItem("token", token);
  $("setup").classList.remove("show");
  poll();
};

function append(m) {
  const row = document.createElement("div");
  row.className = "row" + (m.kind === "notice" ? " notice" : "");
  const mine = m.author === "im:" + store.getItem("name");
  row.innerHTML = '<span class="who' + (mine ? " me" : "") + '"></span>'
                + '<span class="body"></span><span class="ts"></span>';
  row.querySelector(".who").textContent = m.author.replace(/^im:/, "") + ": ";
  row.querySelector(".body").textContent = m.text;
  row.querySelector(".ts").textContent = m.ts;
  $("log").appendChild(row);
  $("log").scrollTop = $("log").scrollHeight;
}

async function poll() {
  if (stopped || need_setup()) return show_setup();
  try {
    const token = encodeURIComponent(store.getItem("token"));
    const res = await fetch(`/api/messages?since=${cursor}&token=${token}`);
    if (res.status === 401) { $("state").textContent = "口令不对"; return show_setup(); }
    const data = await res.json();
    (data.messages || []).forEach(append);
    cursor = data.cursor ?? cursor;      // 断线重连按游标续传,不丢消息
    $("state").textContent = "已连接";
    backoff = 1000;
    poll();
  } catch (err) {
    $("state").textContent = "断开,重连中…";
    setTimeout(poll, backoff);
    backoff = Math.min(backoff * 2, 5000);
  }
}

$("send").onsubmit = async (event) => {
  event.preventDefault();
  const text = $("text").value.trim();
  if (!text) return;
  $("text").value = "";
  try {
    await fetch("/api/send", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({user: store.getItem("name"), text, token: store.getItem("token")}),
    });
  } catch (err) {
    $("state").textContent = "发送失败,检查网络";
  }
};

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) { backoff = 1000; }   // 回到前台立刻重连
});

if (need_setup()) show_setup(); else poll();
</script>
</body>
</html>
"""
