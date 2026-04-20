"""
dashboard/realtime_app.py — WebSocket 기반 실시간 모니터링 대시보드

Flask-SocketIO를 사용하여 서버에서 클라이언트로
실시간 데이터를 push한다. (polling 불필요)

실행:
    python dashboard/realtime_app.py
    → http://localhost:5001
"""

from __future__ import annotations

import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from flask import Flask, render_template_string
from flask_socketio import SocketIO, emit

sys.path.insert(0, str(Path(__file__).parent.parent))

from dashboard.db_reader import (
    get_ai_judge_log,
    get_daily_pnl,
    get_orders,
    get_summary_stats,
    get_ticker_stats,
)

app    = Flask(__name__)
app.config["SECRET_KEY"] = "kiwoom-ai-trader-secret"
sio    = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# ── HTML 템플릿 (인라인) ──────────────────────
HTML = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI 주식 실시간 대시보드</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<script src="https://cdn.socket.io/4.7.2/socket.io.min.js"></script>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#f5f4f0;--surface:#fff;--border:#e0dfd8;--border2:#d3d1c7;
  --text:#2c2c2a;--text2:#5f5e5a;--text3:#888780;
  --green:#27500A;--green-bg:#EAF3DE;--red:#A32D2D;--red-bg:#FCEBEB;
  --blue:#185FA5;--blue-bg:#E6F1FB;--amber:#854F0B;--amber-bg:#FAEEDA;
  --purple:#3C3489;
}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
     background:var(--bg);color:var(--text);font-size:14px}

.header{background:var(--surface);border-bottom:1px solid var(--border);
        padding:0 24px;height:56px;display:flex;align-items:center;
        justify-content:space-between;position:sticky;top:0;z-index:100;
        box-shadow:0 1px 4px rgba(0,0,0,.05)}
.logo{font-size:16px;font-weight:500}
.header-right{display:flex;align-items:center;gap:16px}
.ws-badge{display:flex;align-items:center;gap:6px;font-size:12px;color:var(--text3)}
.ws-dot{width:8px;height:8px;border-radius:50%;background:#d4d2ca;transition:.3s}
.ws-dot.connected{background:#639922;box-shadow:0 0 0 3px #EAF3DE}
.clock{font-size:12px;color:var(--text3);font-variant-numeric:tabular-nums}

.container{max-width:1400px;margin:0 auto;padding:20px 24px}

/* KPI */
.kpi-grid{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:20px}
.kpi{background:var(--surface);border:1px solid var(--border);border-radius:12px;
     padding:16px 18px;transition:box-shadow .2s}
.kpi:hover{box-shadow:0 2px 8px rgba(0,0,0,.08)}
.kpi-label{font-size:10px;color:var(--text3);text-transform:uppercase;
           letter-spacing:.6px;margin-bottom:8px;font-weight:500}
.kpi-val{font-size:24px;font-weight:500;color:var(--text);line-height:1.2}
.kpi-sub{font-size:11px;color:var(--text3);margin-top:4px}
.pos{color:var(--green)} .neg{color:var(--red)}

/* 레이아웃 */
.row{display:grid;gap:14px;margin-bottom:14px}
.row-2{grid-template-columns:1fr 1fr}
.row-3{grid-template-columns:2fr 1fr 1fr}
.card{background:var(--surface);border:1px solid var(--border);
      border-radius:12px;padding:18px 20px}
.card-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:14px}
.card-title{font-size:13px;font-weight:500;display:flex;align-items:center;gap:8px}
.dot{width:7px;height:7px;border-radius:50%}
.dt-green{background:#639922} .dt-blue{background:#378ADD}
.dt-amber{background:#EF9F27} .dt-purple{background:#7F77DD}
.card-badge{font-size:10px;padding:2px 8px;border-radius:99px;font-weight:500}
.live-badge{background:#EAF3DE;color:var(--green)}
.update-time{font-size:10px;color:var(--text3)}

/* 스크롤 테이블 */
.tbl-wrap{overflow-y:auto;max-height:320px}
table{width:100%;border-collapse:collapse}
th{font-size:10px;font-weight:500;color:var(--text3);padding:7px 10px;
   text-align:left;border-bottom:1px solid var(--border);
   position:sticky;top:0;background:var(--surface);z-index:1}
td{font-size:11px;padding:8px 10px;border-bottom:1px solid var(--border2);color:var(--text2)}
tr:hover td{background:#faf9f6}
.badge{display:inline-flex;align-items:center;padding:1px 6px;
       border-radius:99px;font-size:10px;font-weight:500}
.b-buy{background:var(--green-bg);color:var(--green)}
.b-sell{background:var(--red-bg);color:var(--red)}
.b-filled{background:var(--blue-bg);color:var(--blue)}
.b-blocked{background:var(--amber-bg);color:var(--amber)}

/* AI 로그 */
.ai-row{display:flex;align-items:center;gap:10px;padding:9px 0;
        border-bottom:1px solid var(--border2)}
.ai-row:last-child{border-bottom:none}

/* 알림 피드 */
.feed-wrap{overflow-y:auto;max-height:260px}
.feed-item{display:flex;gap:10px;padding:8px 0;border-bottom:1px solid var(--border2);
           animation:fadeIn .4s ease}
@keyframes fadeIn{from{opacity:0;transform:translateY(-6px)}to{opacity:1;transform:none}}
.feed-time{font-size:10px;color:var(--text3);white-space:nowrap;padding-top:2px}
.feed-body{font-size:12px;color:var(--text2);flex:1}
.feed-ticker{font-weight:500;color:var(--text)}

/* 반응형 */
@media(max-width:1100px){.row-3{grid-template-columns:1fr 1fr}}
@media(max-width:800px){
  .kpi-grid{grid-template-columns:repeat(2,1fr)}
  .row-2,.row-3{grid-template-columns:1fr}
}
</style>
</head>
<body>

<div class="header">
  <div class="logo">🤖 AI 주식 실시간 모니터링</div>
  <div class="header-right">
    <div class="ws-badge">
      <div class="ws-dot" id="ws-dot"></div>
      <span id="ws-status">연결 중...</span>
    </div>
    <span class="clock" id="clock">--:--:--</span>
  </div>
</div>

<div class="container">

  <!-- KPI -->
  <div class="kpi-grid">
    <div class="kpi"><div class="kpi-label">실현 손익</div>
      <div class="kpi-val" id="k-pnl">—</div>
      <div class="kpi-sub">누적</div></div>
    <div class="kpi"><div class="kpi-label">총 주문</div>
      <div class="kpi-val" id="k-total">—</div>
      <div class="kpi-sub" id="k-today">오늘 — 건</div></div>
    <div class="kpi"><div class="kpi-label">체결</div>
      <div class="kpi-val" id="k-filled">—</div>
      <div class="kpi-sub" id="k-blocked">차단 — 건</div></div>
    <div class="kpi"><div class="kpi-label">매수 금액</div>
      <div class="kpi-val" id="k-buy">—</div>
      <div class="kpi-sub" id="k-sell">매도 —</div></div>
    <div class="kpi"><div class="kpi-label">업데이트</div>
      <div class="kpi-val" id="k-upd" style="font-size:15px">—</div>
      <div class="kpi-sub">자동 5초 갱신</div></div>
  </div>

  <!-- 차트 행 -->
  <div class="row row-2">
    <div class="card">
      <div class="card-head">
        <div class="card-title"><span class="dot dt-blue"></span>일별 손익</div>
        <span class="badge live-badge">LIVE</span>
      </div>
      <canvas id="pnl-chart" height="140"></canvas>
    </div>
    <div class="card">
      <div class="card-head">
        <div class="card-title"><span class="dot dt-green"></span>종목별 거래량</div>
        <span class="update-time" id="ticker-upd">—</span>
      </div>
      <canvas id="ticker-chart" height="140"></canvas>
    </div>
  </div>

  <!-- 주문 + AI + 피드 -->
  <div class="row row-3">
    <div class="card">
      <div class="card-head">
        <div class="card-title"><span class="dot dt-amber"></span>최근 주문</div>
        <span class="badge live-badge">실시간</span>
      </div>
      <div class="tbl-wrap">
        <table><thead><tr>
          <th>시각</th><th>종목</th><th>구분</th><th>수량</th><th>가격</th><th>상태</th>
        </tr></thead>
        <tbody id="orders-body"></tbody></table>
      </div>
    </div>
    <div class="card">
      <div class="card-head">
        <div class="card-title"><span class="dot dt-purple"></span>AI 판단</div>
      </div>
      <div id="ai-panel"></div>
    </div>
    <div class="card">
      <div class="card-head">
        <div class="card-title"><span class="dot dt-green"></span>실시간 피드</div>
        <span class="badge live-badge">PUSH</span>
      </div>
      <div class="feed-wrap" id="feed"></div>
    </div>
  </div>

</div>

<script>
// ── 소켓 연결 ──
const socket = io();
const dot    = document.getElementById('ws-dot');
const status = document.getElementById('ws-status');

socket.on('connect', () => {
  dot.className = 'ws-dot connected';
  status.textContent = '실시간 연결됨';
  addFeed('🟢', '시스템', 'WebSocket 연결 성공');
});
socket.on('disconnect', () => {
  dot.className = 'ws-dot';
  status.textContent = '연결 끊김';
  addFeed('🔴', '시스템', 'WebSocket 연결 해제');
});

// ── 시계 ──
setInterval(() => {
  document.getElementById('clock').textContent = new Date().toLocaleTimeString('ko-KR');
}, 1000);

// ── 차트 객체 ──
let pnlChart = null, tickerChart = null;

// ── KPI 수신 ──
socket.on('summary', d => {
  const pnl = d.realized_pnl;
  const el  = document.getElementById('k-pnl');
  el.textContent = (pnl>=0?'+':'')+pnl.toLocaleString()+'원';
  el.className   = 'kpi-val '+(pnl>=0?'pos':'neg');
  document.getElementById('k-total').textContent   = d.total_orders.toLocaleString();
  document.getElementById('k-today').textContent   = '오늘 '+d.today_count+'건';
  document.getElementById('k-filled').textContent  = d.filled_orders.toLocaleString();
  document.getElementById('k-blocked').textContent = '차단 '+d.blocked_orders+'건';
  document.getElementById('k-buy').textContent     = (d.buy_amount/10000).toFixed(0)+'만원';
  document.getElementById('k-sell').textContent    = '매도 '+(d.sell_amount/10000).toFixed(0)+'만원';
  document.getElementById('k-upd').textContent     = new Date().toLocaleTimeString('ko-KR');
});

// ── 손익 차트 ──
socket.on('pnl_data', data => {
  const labels = data.map(d=>d.date);
  const vals   = data.map(d=>d.pnl);
  const colors = vals.map(v=>v>=0?'#27500A':'#A32D2D');
  const bgcol  = vals.map(v=>v>=0?'#EAF3DE':'#FCEBEB');
  if(pnlChart) pnlChart.destroy();
  pnlChart = new Chart(document.getElementById('pnl-chart').getContext('2d'),{
    type:'bar',
    data:{labels, datasets:[{data:vals,backgroundColor:bgcol,
          borderColor:colors,borderWidth:1.5,borderRadius:4}]},
    options:{responsive:true,
      plugins:{legend:{display:false},
        tooltip:{callbacks:{label:c=>(c.parsed.y>=0?'+':'')+c.parsed.y.toLocaleString()+'원'}}},
      scales:{
        x:{grid:{display:false},ticks:{font:{size:9},maxTicksLimit:10}},
        y:{grid:{color:'#f1efe8'},ticks:{font:{size:9},callback:v=>(v/10000).toFixed(0)+'만'}}
      }
    }
  });
});

// ── 종목 차트 ──
const nameMap = {'005930':'삼성전자','000660':'SK하이닉스','035420':'NAVER',
                 '051910':'LG화학','006400':'삼성SDI'};
socket.on('ticker_stats', data => {
  const labels = data.map(d=>nameMap[d.ticker]||d.ticker);
  const vals   = data.map(d=>d.total);
  document.getElementById('ticker-upd').textContent = new Date().toLocaleTimeString('ko-KR');
  if(tickerChart) tickerChart.destroy();
  tickerChart = new Chart(document.getElementById('ticker-chart').getContext('2d'),{
    type:'bar',
    data:{labels, datasets:[{data:vals,
      backgroundColor:['#534AB7','#1D9E75','#D85A30','#D4537E','#BA7517'],
      borderRadius:6,borderWidth:0}]},
    options:{indexAxis:'y',responsive:true,
      plugins:{legend:{display:false},
        tooltip:{callbacks:{label:c=>c.parsed.x+'건'}}},
      scales:{
        x:{grid:{color:'#f1efe8'},ticks:{font:{size:9}}},
        y:{grid:{display:false},ticks:{font:{size:11}}}
      }
    }
  });
});

// ── 주문 테이블 ──
socket.on('orders', data => {
  const tbody = document.getElementById('orders-body');
  const rows  = data.slice(0,25).map(o => {
    const t = new Date(o.timestamp);
    const tm= `${t.getMonth()+1}/${t.getDate()} ${t.getHours()}:${String(t.getMinutes()).padStart(2,'0')}`;
    const tb= o.order_type==='BUY'
      ? '<span class="badge b-buy">매수</span>'
      : '<span class="badge b-sell">매도</span>';
    const sb= o.status?.includes('FILLED')
      ? '<span class="badge b-filled">체결</span>'
      : '<span class="badge b-blocked">차단</span>';
    return `<tr><td>${tm}</td><td><b>${o.ticker}</b></td><td>${tb}</td>
            <td>${o.qty}주</td><td>${(o.price||0).toLocaleString()}</td><td>${sb}</td></tr>`;
  }).join('');
  tbody.innerHTML = rows || '<tr><td colspan="6" style="text-align:center;color:var(--text3);padding:20px">데이터 없음</td></tr>';
});

// ── AI 패널 ──
const iconMap  = {BUY:'🟢',SELL:'🔴',HOLD:'🟡'};
const colMap   = {BUY:'var(--green)',SELL:'var(--red)',HOLD:'var(--text3)'};
socket.on('ai_log', data => {
  const html = data.map(d=>`
    <div class="ai-row">
      <span style="font-size:15px">${iconMap[d.action]||'⚪'}</span>
      <div style="flex:1">
        <div style="display:flex;gap:8px;align-items:center">
          <span style="font-weight:500;font-size:12px">${d.ticker}</span>
          <span style="font-size:10px;font-weight:500;color:${colMap[d.action]}">${d.action}</span>
        </div>
        <div style="font-size:11px;color:var(--text2)">${d.reason||''}</div>
      </div>
      <span style="font-size:11px;font-weight:500;color:${d.confidence>=70?'var(--green)':'var(--text3)'}">${d.confidence}점</span>
    </div>`).join('');
  document.getElementById('ai-panel').innerHTML = html || '<p style="color:var(--text3);font-size:12px;padding:8px">로그 없음</p>';
});

// ── 실시간 이벤트 피드 ──
socket.on('new_event', evt => { addFeed(evt.icon, evt.ticker, evt.message); });

function addFeed(icon, ticker, msg) {
  const feed = document.getElementById('feed');
  const now  = new Date().toLocaleTimeString('ko-KR');
  const item = document.createElement('div');
  item.className = 'feed-item';
  item.innerHTML = `
    <span class="feed-time">${now}</span>
    <div class="feed-body"><span class="feed-ticker">${icon} ${ticker}</span> ${msg}</div>`;
  feed.prepend(item);
  while(feed.children.length > 50) feed.removeChild(feed.lastChild);
}
</script>
</body>
</html>"""


# ── WebSocket 이벤트 ──────────────────────────

@sio.on("connect")
def on_connect():
    """클라이언트 연결 시 초기 데이터 전송"""
    emit("summary",      get_summary_stats())
    emit("pnl_data",     get_daily_pnl())
    emit("ticker_stats", get_ticker_stats())
    emit("orders",       get_orders(limit=50))
    emit("ai_log",       get_ai_judge_log())


# ── 백그라운드 브로드캐스터 ───────────────────

def _broadcast_loop():
    """5초마다 모든 클라이언트에 최신 데이터를 push한다."""
    while True:
        time.sleep(5)
        try:
            sio.emit("summary",      get_summary_stats())
            sio.emit("pnl_data",     get_daily_pnl())
            sio.emit("ticker_stats", get_ticker_stats())
            sio.emit("orders",       get_orders(limit=50))
            sio.emit("ai_log",       get_ai_judge_log())
        except Exception:
            pass


# ── 라우트 ────────────────────────────────────

@app.route("/")
def index():
    return render_template_string(HTML)


@app.route("/api/health")
def health():
    from flask import jsonify
    return jsonify({"status": "ok", "mode": "websocket",
                    "time": datetime.now().isoformat()})


# ── 진입점 ────────────────────────────────────

if __name__ == "__main__":
    t = threading.Thread(target=_broadcast_loop, daemon=True)
    t.start()
    print("\n" + "="*55)
    print("  🤖 AI 주식 실시간 대시보드 (WebSocket)")
    print("  http://0.0.0.0:5001")
    print("  외부 접속: http://192.168.45.201:5001")
    print("  5초마다 전체 데이터 자동 push")
    print("="*55 + "\n")

    # eventlet 또는 gevent 사용 권장 (외부 접속 안정성)
    try:
        import eventlet
        eventlet.monkey_patch()
        sio.run(app, host="0.0.0.0", port=5001, debug=False)
    except ImportError:
        # eventlet 없으면 기본 모드
        sio.run(app, host="0.0.0.0", port=5001, debug=False, allow_unsafe_werkzeug=True)
