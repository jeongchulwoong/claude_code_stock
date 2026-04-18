#!/usr/bin/env bash
# scripts/setup.sh — 초기 환경 설정 스크립트
# 실행: bash scripts/setup.sh

set -e

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║   AI 자동매매 시스템 — 환경 초기 설정        ║"
echo "╚══════════════════════════════════════════════╝"
echo ""

# 1. Python 버전 확인
PY=$(python3 --version 2>&1)
echo "✅ Python: $PY"

# 2. pip 업그레이드
echo "📦 pip 업그레이드..."
python3 -m pip install --upgrade pip --quiet

# 3. 패키지 설치
echo "📦 패키지 설치..."
python3 -m pip install -r requirements.txt --quiet

# 4. .env 파일 생성
if [ ! -f ".env" ]; then
  cp .env.example .env
  echo "📝 .env 파일 생성됨 — API 키를 입력해주세요"
else
  echo "✅ .env 파일 존재"
fi

# 5. DB 초기화
echo "🗄️  DB 초기화..."
python3 -c "
import sys; sys.path.insert(0,'.')
from core.db_manager import init_db
init_db()
print('DB 초기화 완료')
"

# 6. 디렉토리 생성
mkdir -p db logs reports
echo "✅ 디렉토리 생성: db/ logs/ reports/"

# 7. 설치 검증
echo ""
echo "🧪 핵심 모듈 검증..."
python3 -c "
import sys; sys.path.insert(0,'.')
from config import PAPER_TRADING, RISK_CONFIG
from core.kiwoom_api import get_kiwoom_api
from core.risk_manager import RiskManager
from core.news_analyzer import StockNewsService
kw = get_kiwoom_api(paper_trading=True); kw.login()
rm = RiskManager()
print('  ✅ 핵심 모듈 로드 성공')
print(f'  📄 모드: {\"페이퍼 트레이딩\" if PAPER_TRADING else \"실거래\"}')
print(f'  🛡️  손절선: {RISK_CONFIG[\"stop_loss_pct\"]:.0%}')
"

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║   설치 완료!                                 ║"
echo "║                                              ║"
echo "║   페이퍼 트레이딩 시작:                      ║"
echo "║     python main_v2.py                        ║"
echo "║                                              ║"
echo "║   대시보드:                                  ║"
echo "║     python dashboard/realtime_app.py          ║"
echo "║     → http://localhost:5001/advanced          ║"
echo "║                                              ║"
echo "║   통합 테스트:                               ║"
echo "║     python tests/integration_test.py          ║"
echo "╚══════════════════════════════════════════════╝"
echo ""
