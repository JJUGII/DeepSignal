/* ══════════════════════════════════════════════
   DeepSignal Web UI — Single-Page App
   ══════════════════════════════════════════════ */

// ── 세션 관리 ─────────────────────────────────
let _sessionToken = localStorage.getItem('ds_session') || null;

// ── 코인 한글명 캐시 ────────────────────────────
let _coinNames = {};   // { "KRW-BTC": { korean_name: "비트코인", english_name: "Bitcoin" }, ... }
let _coinNamesFetched = false;

async function loadCoinNames() {
  if (_coinNamesFetched) return;
  try {
    const data = await GET('/api/coin-names');
    if (data && typeof data === 'object') {
      _coinNames = data;
      _coinNamesFetched = true;
    }
  } catch (_e) { /* 실패 시 영문명 사용 */ }
}

/** market("KRW-BTC") → "비트코인 BTC" 형식 */
function coinDisplayName(market) {
  const info = _coinNames[market];
  const symbol = (market || '').replace('KRW-', '').replace('-KRW', '');
  if (!info || !info.korean_name) return symbol;
  return `${info.korean_name} ${symbol}`;
}

/** market("KRW-BTC") → "비트코인" (한글명만) */
function coinKoreanName(market) {
  return (_coinNames[market] || {}).korean_name || '';
}

// ── API 헬퍼 ─────────────────────────────────
async function api(method, path, body) {
  const headers = { 'Content-Type': 'application/json' };
  if (_sessionToken) headers['X-Session-Token'] = _sessionToken;
  const opts = { method, headers };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(path, opts);
  if (res.status === 401) {
    // 세션 만료 → 재인증
    _sessionToken = null;
    localStorage.removeItem('ds_session');
    initTelegramAuth();
    throw new Error('unauthorized');
  }
  return res.json();
}
const GET  = (p)    => api('GET', p);
const POST = (p, b) => api('POST', p, b);

// ── 토스트 ───────────────────────────────────
function toast(msg, type = 'info', ms = 4000) {
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = msg;
  document.getElementById('toast-container').appendChild(el);
  setTimeout(() => el.remove(), ms);
}

function toastLoading(msg) {
  const el = document.createElement('div');
  el.className = 'toast loading';
  el.innerHTML = `<span class="toast-spinner"></span>${msg}`;
  document.getElementById('toast-container').appendChild(el);
  return {
    update(newMsg, type = 'success', ms = 8000) {
      el.className = `toast ${type}`;
      el.textContent = newMsg;
      setTimeout(() => el.remove(), ms);
    },
    remove() { el.remove(); }
  };
}

// ── 유틸 ─────────────────────────────────────
function escHtml(s) {
  return (s || '').replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
function fmt_krw(v) {
  if (v == null) return '-';
  const str = Number(v).toLocaleString('ko-KR', { minimumFractionDigits: 0, maximumFractionDigits: 2 });
  const dot = str.indexOf('.');
  if (dot === -1) return str + '원';
  return str.slice(0, dot) + `<span class="krw-dec">${str.slice(dot)}</span>원`;
}
function fmt_pct(v)   { if (v == null) return '-'; const n = Number(v); return (n >= 0 ? '+' : '') + n.toFixed(2) + '%'; }
function fmt_bps(v)   { if (v == null) return '-'; return Number(v).toFixed(1) + 'bps'; }
function fmt_time(v)  { if (!v) return '-'; try { return new Date(v).toLocaleString('ko-KR'); } catch { return v; } }
function fmt_uptime(sec) {
  if (!sec) return '-';
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = Math.floor(sec % 60);
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}
function pct_class(v) { return v > 0 ? 'text-up' : v < 0 ? 'text-down' : ''; }

function score_bar(val, max) {
  if (val == null) return '<span class="text-muted">-</span>';
  const pct = Math.min(100, Math.max(0, ((Number(val) + max) / (max * 2)) * 100));
  const cls = val > 20 ? 'bar-positive' : val < -20 ? 'bar-negative' : 'bar-neutral';
  return `<div class="score-bar-wrap">
    <div class="score-bar ${cls}" style="width:${pct.toFixed(0)}%"></div>
    <span class="score-bar-label">${Number(val).toFixed(1)}</span>
  </div>`;
}

// ── 도움말 팝업 ───────────────────────────────
const HELP = {
  scoreboard: {
    title: 'GSQS 실시간 스코어보드',
    text: 'GSQS(Global Signal Quality Score)는 시장 신호의 품질을 -100~+100 점수로 나타냅니다. 트렌드·거래량·호가·체결 등 여러 지표를 합산해 진입 타이밍을 평가합니다. +20 이상이면 매수 신호, -20 이하면 약세 신호로 봅니다.'
  },
  trend: {
    title: '트렌드 점수',
    text: '이동평균선(MA)·MACD 등을 이용해 가격의 방향성과 강도를 측정한 점수입니다. 양수면 상승 추세, 음수면 하락 추세를 의미합니다.'
  },
  volume: {
    title: '거래량 점수',
    text: '현재 거래량이 평소 대비 얼마나 높은지를 나타냅니다. 거래량이 급증하면 큰 움직임의 전조일 수 있습니다.'
  },
  orderbook: {
    title: '호가 점수',
    text: '매수 호가와 매도 호가의 잔량 불균형(OBI, Order Book Imbalance)을 측정합니다. 매수 잔량이 많을수록 높은 점수를 받습니다.'
  },
  tradeflow: {
    title: '체결 흐름 점수',
    text: '최근 체결된 거래에서 매수자가 시장가로 산 비율(Buy Flow Ratio)을 측정합니다. 공격적 매수가 많을수록 높은 점수입니다.'
  },
  futures: {
    title: '선물 점수',
    text: '선물 시장의 미결제약정(Open Interest) 변화와 자금조달비율(Funding Rate)을 반영합니다. 선물 시장의 레버리지 방향을 보여줍니다.'
  },
  risk: {
    title: '리스크 점수',
    text: '시장 변동성(ATR)과 현재 포지션 위험도를 종합한 점수입니다. 리스크가 낮을수록 높은 점수를 받습니다.'
  },
  market: {
    title: '시장 점수',
    text: '비트코인 도미넌스·공포탐욕지수 등 전체 암호화폐 시장 분위기를 반영한 점수입니다.'
  },
  signal_history: {
    title: '신호 이력',
    text: '과거에 발생한 매매 신호 목록입니다. 각 신호가 발생한 뒤 5분 후 실제로 가격이 오르거나 내렸는지 결과(WIN/LOSE)가 기록됩니다.'
  },
  win_rate: {
    title: '5분 후 승률',
    text: '신호 발생 후 5분 뒤 수익(양의 수익률)으로 끝난 비율입니다. 예: 60% = 10번 중 6번 예측이 맞았음을 의미합니다.'
  },
  auto_threshold: {
    title: '자동 임계값',
    text: '승률 기반으로 자동 계산된 신호 진입 기준 점수입니다. 승률이 낮으면 임계값을 높여 더 확실한 신호에만 진입하도록 조정됩니다.'
  },
  weight_optimizer: {
    title: 'GSQS 가중치 자동 최적화',
    text: '200개 이상의 신호 데이터가 쌓이면 AI가 트렌드·거래량 등 각 요소의 중요도(가중치)를 자동으로 재조정합니다. 실제 수익에 가장 잘 맞는 조합을 찾아 적용합니다.'
  },
  macro_status: {
    title: '매크로 상태',
    text: '비트코인·이더리움 등 주요 자산의 전반적인 시장 분위기를 나타냅니다. BULLISH(강세), BEARISH(약세), NEUTRAL(중립)로 표시됩니다.'
  },
  sync_ratio: {
    title: '동시 급변동',
    text: '여러 코인이 동시에 급격히 변동하는 비율입니다. 이 수치가 높으면 특정 코인의 움직임이 독립적이지 않고 시장 전체의 흐름에 묻혀 있는 상태입니다.'
  },
  correlation: {
    title: '상관계수',
    text: '두 자산의 가격이 얼마나 같은 방향으로 움직이는지를 나타내는 지표입니다. 1에 가까울수록 함께 움직이고, -1에 가까울수록 반대로 움직입니다. 서로 다른 코인에 분산 투자할 때 중요합니다.'
  },
  position_sizing: {
    title: '포지션 사이징',
    text: '한 번의 거래에 자본을 얼마나 사용할지 결정하는 방법입니다. ATR(변동성)·신호 강도·승률 등을 고려해 자동 계산됩니다.'
  },
  atr: {
    title: 'ATR (평균 실질 범위)',
    text: 'Average True Range의 약자로, 최근 일정 기간 동안 가격이 얼마나 크게 움직였는지를 나타내는 변동성 지표입니다. ATR이 클수록 변동성이 높아 거래 위험도 커집니다.'
  },
  tp_sl: {
    title: 'TP / SL',
    text: 'TP(Take Profit)는 목표 수익 가격, SL(Stop Loss)은 손실 한도 가격입니다. 예: TP +1.5%, SL -0.8%이면 1.5% 오르면 익절, 0.8% 내리면 손절합니다.'
  },
  score_factor: {
    title: '스코어 팩터',
    text: 'GSQS 점수를 포지션 크기에 반영하는 배수입니다. 신호 품질이 높을수록 더 많은 자본을 투입하도록 조정합니다.'
  },
  regime: {
    title: '레짐 (시장 국면)',
    text: '현재 시장이 추세 장세(Trending)인지 횡보 장세(Ranging)인지 판단한 값입니다. 각 국면에 맞는 전략을 선택하는 데 사용됩니다.'
  },
  slippage: {
    title: '체결오차란?',
    text: '주문을 넣을 때 시스템이 지정한 가격과, 거래소에서 실제로 체결된 가격의 차이입니다.\n\n예: 주문가 1,000원 → 실제 체결가 1,001원 → 체결오차 +0.1% (100bps 기준 1bps)\n\n0에 가까울수록 유리하고, 수치가 클수록 불리한 조건에서 체결된 것입니다. 거래가 적은 코인일수록 오차가 커집니다.'
  },
  reconcile: {
    title: '리콘사일 (계좌 대사)',
    text: '시스템이 기록한 포지션과 거래소 실제 계좌 잔고를 비교·대조하는 작업입니다. 불일치가 발견되면 자동으로 정정합니다. 회계의 "장부 대사"와 같은 개념입니다.'
  },
  universe: {
    title: '코인 유니버스',
    text: '시스템이 모니터링 대상으로 선정한 코인 목록입니다. 거래량·시가총액·유동성 기준을 통과한 코인만 포함됩니다.'
  },
  quality_gates: {
    title: '품질 게이트',
    text: '신호를 실제 거래로 전환하기 전에 통과해야 하는 여러 안전 조건들입니다. 승률 기준·변동성 한도·레버리지 제한 등이 포함됩니다. 모든 게이트를 통과한 신호만 실행됩니다.'
  },
  bps: {
    title: 'bps (베이시스 포인트)',
    text: '금융에서 사용하는 아주 작은 단위입니다. 1bps = 0.01%입니다. 예: 수익률 50bps = 0.5%. 수수료나 슬리피지처럼 작은 비용을 정밀하게 표현할 때 씁니다.'
  },
  kgsqs_total: {
    title: 'K-GSQS 총점 (0~100)',
    text: '6개 서브스코어(추세·거래량·호가·모멘텀·시장·리스크)를 가중 합산한 국내주식 실시간 신호 품질 점수입니다. 72점 이상 알림, 82점 이상 자동매수 후보, 88점 이상 강한 매수입니다.'
  },
  kgsqs_trend: {
    title: '추세 서브스코어 (가중치 20%)',
    text: 'MA5/MA20 정배열(가격>MA5>MA20), VWAP 위 여부, 5분 수익률 방향을 종합한 추세 점수입니다. MA 정배열이고 VWAP 위에서 거래되면 높은 점수를 받습니다.'
  },
  kgsqs_volume: {
    title: '거래량 서브스코어 (가중치 20%)',
    text: '현재 거래량 / 최근 5분 평균(거래량 배수)과 매수비율을 평가합니다. 거래량이 평균 2배 이상이고 매수비율 60% 이상이면 강한 신호입니다.'
  },
  kgsqs_orderbook: {
    title: '호가 서브스코어 (가중치 20%)',
    text: '총 매수잔량/매도잔량 비율(bid/ask ratio)과 스프레드를 평가합니다. 매수 대기가 많고 스프레드가 좁을수록 높은 점수입니다.'
  },
  kgsqs_momentum: {
    title: '모멘텀 서브스코어 (가중치 20%)',
    text: '1분/5분/15분 수익률 방향 정합과 체결강도(KIS 기준 100=보합, 100이상=매수우위)를 평가합니다. 여러 타임프레임이 일관되게 상승하면 높은 점수입니다.'
  },
  kgsqs_market: {
    title: '시장 상대강도 서브스코어 (가중치 10%)',
    text: '종목 수익률과 KOSPI 지수 수익률의 차이(알파)를 평가합니다. KOSPI 대비 수익률이 높을수록 시장에서 주목받는 종목임을 의미합니다.'
  },
  kgsqs_risk: {
    title: '리스크 게이트 서브스코어 (가중치 10%)',
    text: 'ATR(14) 기반 변동성과 당일 갭을 평가합니다. 거래정지·상한가·하한가·관리종목은 0점으로 자동 차단됩니다. 변동성이 적당하고 갭이 작을수록 안전합니다.'
  },
  kgsqs_action: {
    title: 'K-GSQS 신호 종류',
    text: 'STRONG_BUY(강한매수, 88점↑) / BUY(매수후보, 82점↑) / NOTIFY(알림, 72점↑) / HOLD(관망, 72점 미만) / SKIP(하드블록). 국내주식 거래비용 0.63%를 반영하여 암호화폐보다 임계값이 높습니다.'
  },
  kgsqs_threshold: {
    title: 'K-GSQS 임계값 설정',
    text: '국내주식 거래비용(매수 0.015%+세금 0.2%+증권사 수수료)은 약 0.63%입니다. 암호화폐(~0.1%)보다 훨씬 높아 동일 임계값을 적용하면 수익이 나지 않습니다. K-GSQS는 이를 반영해 72/82/88pt의 높은 기준을 사용합니다.'
  },
  kgsqs_stream: {
    title: 'K-GSQS 실시간 스트림',
    text: 'kis-stream 파이프라인이 실행 중일 때 KIS WebSocket으로 실시간 체결/호가 데이터를 수신하여 매 1분봉 완성 시 채점합니다. 장 운영시간(09:05~15:15 KST)에만 데이터가 흐릅니다.'
  },
  kgsqs_strength: {
    title: '체결강도',
    text: 'KIS가 계산하는 매수/매도 강도 지표입니다. 100 = 보합(매수=매도), 100 초과 = 매수 우위, 100 미만 = 매도 우위. 130 이상이면 매수세가 강한 상태입니다.'
  },
  kgsqs_bid_ask: {
    title: '호가 잔량 비율 (Bid/Ask Ratio)',
    text: '총 매수호가 잔량 / 총 매도호가 잔량입니다. 1.0 = 균형, 1.5 이상 = 매수 대기가 많음(상승 압력). 단, 허수 호가가 있을 수 있으므로 다른 지표와 함께 해석해야 합니다.'
  },
  kgsqs_signals: {
    title: 'K-GSQS 신호 이력',
    text: 'K-GSQS가 72pt 이상 신호를 발생시킨 이력입니다. 1분/3분/5분/15분 후 수익률을 사후 기록하여 전략 성과를 추적합니다. outcome_complete=true인 신호만 승률 계산에 반영됩니다.'
  },
  kgsqs_winrate: {
    title: '시간대별 승률',
    text: '신호 발생 후 N분 뒤의 수익률이 양수인 비율입니다. 5분 승률 55% 이상이면 전략이 유효한 수준입니다. 샘플 수가 적을 때는 통계적 신뢰도가 낮습니다.'
  },
  kstock_positions: {
    title: 'KIS 계좌 & 보유 포지션',
    text: '마지막으로 조회된 KIS 계좌 스냅샷입니다. 현금, 총 평가자산, 보유 종목별 평균단가·현재가·손익을 보여줍니다. 스냅샷 시간이 2시간 이상 지났으면 주황색으로 표시됩니다.'
  },
  kstock_universe: {
    title: '감시 종목 유니버스',
    text: 'kis-stream 파이프라인이 실시간으로 모니터링 중인 종목 목록입니다. 각 종목의 마지막 체결가와 1분봉 수(데이터 누적량)를 표시합니다. 장 외 시간에는 "장 마감" 상태로 표시됩니다.'
  },
  kgsqs_weights: {
    title: 'K-GSQS 가중치 구성',
    text: 'K-GSQS 총점 = 6개 서브스코어 × 가중치의 합입니다. 추세·거래량·호가·모멘텀 각 20%, 시장·리스크 각 10%. 암호화폐 GSQS(7개 서브)와 달리 선물 서브스코어가 없어 국내주식 특성에 맞게 조정되었습니다.'
  },
  kstock_kospi: {
    title: 'KOSPI 지수 연동',
    text: 'KIS WebSocket H0UPCNT0으로 KOSPI 지수(0001)를 실시간 수신합니다. 개별 종목의 수익률에서 KOSPI 수익률을 차감한 값(알파)이 시장 서브스코어에 반영됩니다. 알파 > 0이면 시장 대비 강세입니다.'
  },

  // ── 해외주식 전용 ─────────────────────────────
  os_scoreboard: {
    title: '해외주식 K-GSQS 스코어보드',
    text: '미국 주식·ETF를 대상으로 실시간 체결 데이터를 분석한 K-GSQS 점수 현황입니다. 미국 정규장(22:30~05:00 KST, 주말 제외) 중에 KIS WebSocket에서 실시간 tick 데이터를 수신해 1분봉 완성 시마다 점수를 갱신합니다. NVDA·AAPL·TSLA 등 34종목을 동시에 모니터링합니다.'
  },
  os_stream: {
    title: '해외주식 파이프라인 상태',
    text: '🟢 실시간 수신 중 = kis_overseas_stream 프로세스 실행 중이며 미국 장 시간에 데이터가 수신 중입니다.\n🟡 장 시작 대기 중 = 프로세스는 실행 중이지만 미국 정규장 시간(22:30~05:00 KST)이 아닙니다.\n🔴 파이프라인 미연결 = 백그라운드 프로세스가 동작하지 않아 실시간 데이터가 없습니다.\n\n파이프라인 재시작: 러너 제어 페이지 또는 시스템 관리자에게 문의하세요.'
  },
  os_threshold_notify: {
    title: '알림 임계값 (해외주식)',
    text: 'K-GSQS 점수가 이 값 이상이면 텔레그램 알림이 발송됩니다. 기본값 72점. 예: 74.5점 신호 발생 → 텔레그램으로 종목·점수·분석 결과가 즉시 전송됩니다.'
  },
  os_threshold_auto: {
    title: '자동 체결 임계값 (해외주식)',
    text: 'K-GSQS 점수가 이 값 이상이면 사람 승인 없이 자동으로 주문이 실행될 수 있습니다. 기본값 82점. 높은 점수일수록 신호 신뢰도가 높습니다. 자동실행은 설정(환경설정)에서 켜고 끌 수 있습니다.'
  },
  os_signal_count: {
    title: '누적 신호 수',
    text: '파이프라인이 시작된 이후 K-GSQS 알림 임계값(72pt) 이상의 신호가 발생한 총 횟수입니다. 프로세스를 재시작하면 초기화됩니다.'
  },
  os_signals: {
    title: '해외주식 신호 이력 & 승률',
    text: '미국 주식·ETF에서 K-GSQS 임계값 이상의 신호가 발생했던 이력입니다. 신호 발생 후 5분 뒤 실제 가격이 올랐으면 WIN(승), 내렸으면 LOSE(패)로 기록됩니다. 승률이 55% 이상이면 전략이 통계적으로 유효한 수준입니다.'
  },
  os_universe: {
    title: '해외주식 감시 유니버스',
    text: 'kis_overseas_stream 파이프라인이 실시간으로 모니터링 중인 미국 주식·ETF 목록입니다. NVDA·AAPL·MSFT·TSLA·META 등 개별 주식과 SPY·QQQ·TQQQ 등 ETF 포함 총 34종목을 감시합니다. 각 종목의 마지막 체결가·거래량·1분봉 누적 수를 표시합니다.'
  },
  os_market_hours: {
    title: '미국 정규장 운영 시간',
    text: '미국 동부시간(ET) 오전 9:30 ~ 오후 4:00\n한국시간(KST) 기준: 22:30 ~ 05:00 (다음날 새벽)\n주말(토·일) 및 미국 공휴일에는 장이 열리지 않습니다.\n\n파이프라인은 장 시간 중에만 실시간 데이터를 수신하며, 장 마감 후에는 데이터 흐름이 없습니다.'
  },
  os_data_freshness: {
    title: '데이터 신선도',
    text: '마지막으로 수신된 1분봉이 얼마나 오래되었는지 나타냅니다.\n• 🟢 5분 이내: 거의 실시간 데이터\n• 일반: 5~30분 — 최근 데이터지만 약간 지연\n• 회색 30분↑: 데이터가 오래됨 (장 마감이거나 연결 문제)\n\n장 운영 시간 외에는 데이터가 들어오지 않아 항상 오래된 상태로 표시됩니다.'
  },
  kstock_slippage: {
    title: '국내주식 체결오차',
    text: '지정가 주문가 대비 실제 체결 평균가의 차이를 bps(베이시스포인트, 0.01%) 단위로 기록합니다.\n\n• 0~5 bps: 우수 — 거의 완벽하게 지정가 체결\n• 5~10 bps: 양호 — 정상 범위\n• 10 bps↑: 주의 — 유동성이 낮거나 변동성이 높은 상황\n\n거래내역 탭에서 국내주식 탭을 조회할 때마다 자동으로 데이터가 누적됩니다.\n1bps = 0.01% = 만분의 1'
  },
  os_slippage: {
    title: '해외주식 체결오차',
    text: '해외주식 지정가 주문가(ft_ord_unpr3) 대비 실제 체결 평균가(ft_ccld_unpr3)의 차이를 bps 단위로 기록합니다.\n\n• 0~5 bps: 우수 (미국 대형주는 보통 1~3 bps 수준)\n• 5~15 bps: 양호 — 시장 개장 직후/마감 직전에는 다소 높아질 수 있음\n• 15 bps↑: 주의 — 유동성 부족 또는 큰 변동성\n\n거래내역 탭 해외주식 조회 시 자동 기록됩니다. 1bps = 0.01%'
  },

  // ── 대시보드 ─────────────────────────────────
  holding_pnl: {
    title: '보유 포지션 & 수익률',
    text: '현재 보유 중인 자산 목록입니다.\n• 평균단가: 내가 매수했던 평균 가격\n• 현재가: 지금 시장에서 거래되는 가격\n• 평가금액: 보유 수량 × 현재가 (지금 팔면 받을 금액 추산)\n• 수익률(%): (현재가 - 평균단가) / 평균단가 × 100\n\n수익률이 양수(초록)이면 수익 중, 음수(빨강)이면 손실 중입니다.'
  },
  last_plan: {
    title: '마지막 플랜',
    text: 'AI가 마지막으로 분석해서 생성한 매매 계획(Plan)입니다.\n• 기술적 점수: 가격·거래량·차트 패턴 분석 점수\n• 거시 점수: 시장 전반의 분위기(공포탐욕지수 등) 점수\n• 최종 점수: 두 점수를 합산해 진입 여부를 결정합니다\n\n[BUY] = 매수 플랜, [SELL] = 매도 플랜. 승인 배너가 나타나면 승인하거나 거부할 수 있습니다.'
  },
  runner_status: {
    title: '러너 상태',
    text: '백그라운드에서 자동 매매를 실행하는 crypto-auto-runner 프로세스의 상태입니다.\n🟢 실행 중: 자동 분석·매매가 진행되고 있습니다\n⏸ 일시정지: 프로세스는 실행 중이지만 매매는 중단된 상태\n🔴 중지됨: 자동 매매가 완전히 꺼진 상태\n\n러너 제어 페이지에서 시작·중지·일시정지할 수 있습니다.'
  },

  // ── 거래내역 ──────────────────────────────────
  trade_settlement: {
    title: '정산금액이란?',
    text: '실제 내 계좌에 입금되거나 출금된 최종 금액입니다.\n• 매수: 지불한 금액 + 수수료 (총 지출)\n• 매도: 받은 금액 - 수수료 (실제 수령액)\n\n예: 코인 100,000원 매도, 수수료 50원 → 정산금액 99,950원'
  },
  trade_fee: {
    title: '거래 수수료',
    text: '거래소에 지불하는 서비스 이용료입니다.\n• 업비트(코인): 약 0.05%\n• KIS(국내주식): 약 0.015% + 증권거래세 0.20% = 합계 약 0.22%\n• KIS(해외주식): 약 0.25% (USD 기준)\n\n수수료가 발생하므로 매수 직후에는 수익률이 약간 마이너스인 것이 정상입니다.'
  },
  trade_period_filter: {
    title: '기간 필터',
    text: '거래내역을 조회할 기간을 선택합니다.\n• 1주일: 최근 7일\n• 1개월: 최근 30일\n• 3개월: 최근 90일\n\n코인은 활성 거래소(Upbit/Bithumb) API에서, 국내주식·해외주식은 KIS Open API에서 실제 체결 이력을 불러옵니다.'
  },
  trade_type_filter: {
    title: '거래 유형 필터',
    text: '조회할 거래 종류를 선택합니다.\n• 전체: 매수·매도 모두 표시\n• 매수: 자산을 구매한 거래만\n• 매도: 자산을 판매한 거래만'
  }
};

// ── 품질 게이트 메타데이터 ─────────────────────
const GATE_META = {
  min_final_score: {
    kr: '최종 점수 기준',
    desc: 'final_score가 이 값 이상이어야 주문이 생성됩니다. 기준 미달이어도 다른 조건으로 통과할 수 있습니다.',
  },
  gate_mode: {
    kr: '게이트 모드',
    desc: 'ml_primary = AI(LightGBM) 모델이 1차 판단. 모델 미학습 시 rules(규칙 기반)으로 대체됩니다.',
  },
  validation: {
    kr: '데이터 유효성',
    desc: '피처 데이터에 결측치·이상값이 없는지 확인합니다. ok = 이상 없음.',
  },
  liquidity: {
    kr: '유동성 검사',
    desc: '24h 거래대금이 최소 거래량 비율(min_volume_ratio) 이상인지 확인합니다. 거래량이 너무 적은 코인은 진입·청산이 어렵습니다.',
  },
  concentration: {
    kr: '집중도 검사',
    desc: '동일 코인을 이미 너무 많이 보유하고 있지 않은지 확인합니다. 포트폴리오 편중 방지.',
  },
  ml_gate: {
    kr: 'AI 게이트',
    desc: 'LightGBM 모델의 진입 예측 결과입니다. skipped = 학습 데이터 부족으로 AI 판단을 건너뜁니다.',
  },
  ensemble_mode: {
    kr: '앙상블 모드',
    desc: 'lgbm_only = LightGBM 단독 사용. 여러 모델을 함께 쓰면 ensemble로 표시됩니다.',
  },
  execution_quality: {
    kr: '체결 품질',
    desc: '호가 스프레드와 예상 슬리피지가 허용 범위 내인지 확인합니다. pass = 정상.',
  },
  execution_quality_rr: {
    kr: '기대 손익비 (R:R)',
    desc: '수익 기댓값 ÷ 손실 기댓값. 1.0 이상이면 기댓값이 양수. 예: 1.44 = 수익이 손실보다 44% 더 기대됨.',
  },
};

// 게이트별 통과 여부 판별
function _gateStatus(key, v) {
  const s = String(v);
  if (s === 'ok' || s === 'pass' || v === true) return 'ok';
  if (key === 'execution_quality_rr') return (parseFloat(s) >= 1.0) ? 'ok' : 'fail';
  // 정보성 값 (pass/fail이 아닌 설정값·모드 표시)
  if (key === 'min_final_score' || key === 'gate_mode' || key === 'ml_gate' || key === 'ensemble_mode') return 'info';
  return 'fail';
}

let _helpPopup = null;

function helpBtn(key) {
  return `<button class="help-btn" onclick="showHelp(event,'${key}')">?</button>`;
}

function _showHelpPopup(e, title, text) {
  if (_helpPopup) { _helpPopup.remove(); _helpPopup = null; }
  const pop = document.createElement('div');
  pop.className = 'help-popup';
  pop.innerHTML = `
    <div class="help-popup-title">${escHtml(title)}</div>
    <div class="help-popup-text">${escHtml(text)}</div>
    <button class="help-popup-close" onclick="closeHelp()">✕</button>`;
  document.body.appendChild(pop);
  _helpPopup = pop;
  const btn = e.currentTarget;
  const r = btn.getBoundingClientRect();
  let top = r.bottom + 6, left = r.left;
  if (left + 284 > window.innerWidth) left = window.innerWidth - 290;
  if (top + 160 > window.innerHeight) top = r.top - 160;
  pop.style.top = top + 'px';
  pop.style.left = left + 'px';
}

function showHelp(e, key) {
  e.stopPropagation();
  const info = HELP[key];
  if (!info) return;
  _showHelpPopup(e, info.title, info.text);
}

function showHelpFromEl(e) {
  e.stopPropagation();
  const btn = e.currentTarget;
  const title = btn.dataset.helpTitle || '설명';
  const text  = btn.dataset.helpText  || '';
  _showHelpPopup(e, title, text);
}

function closeHelp() {
  if (_helpPopup) { _helpPopup.remove(); _helpPopup = null; }
}

document.addEventListener('click', (e) => {
  if (_helpPopup && !_helpPopup.contains(e.target)) closeHelp();
});

// ── 마크다운 렌더러 ───────────────────────────
function md2html(text) {
  if (!text) return '';
  const lines = text.split('\n');
  let html = '';
  let inCode = false;
  let inList = false;

  for (const line of lines) {
    if (line.startsWith('```')) {
      if (inList) { html += '</ul>'; inList = false; }
      inCode = !inCode;
      html += inCode ? '<pre class="md-pre"><code>' : '</code></pre>';
      continue;
    }
    if (inCode) { html += escHtml(line) + '\n'; continue; }

    let p = escHtml(line)
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/`(.+?)`/g, '<code class="md-inline">$1</code>');

    if (/^### /.test(line)) {
      if (inList) { html += '</ul>'; inList = false; }
      html += `<h3 class="md-h3">${p.slice(4)}</h3>`;
    } else if (/^## /.test(line)) {
      if (inList) { html += '</ul>'; inList = false; }
      html += `<h2 class="md-h2">${p.slice(3)}</h2>`;
    } else if (/^# /.test(line)) {
      if (inList) { html += '</ul>'; inList = false; }
      html += `<h1 class="md-h1">${p.slice(2)}</h1>`;
    } else if (/^[|\-]{2,}/.test(line) && line.includes('|')) {
      if (inList) { html += '</ul>'; inList = false; }
      // table row — basic support
      const cells = line.split('|').filter((_, i, a) => i > 0 && i < a.length - 1);
      const isHeader = cells.some(c => c.trim() === '' || /^[-:]+$/.test(c.trim()));
      if (!isHeader) {
        html += '<tr>' + cells.map(c => `<td>${c.trim()}</td>`).join('') + '</tr>';
      }
    } else if (/^[-•] /.test(line)) {
      if (!inList) { html += '<ul class="md-ul">'; inList = true; }
      html += `<li>${p.slice(2)}</li>`;
    } else if (/^---+$/.test(line)) {
      if (inList) { html += '</ul>'; inList = false; }
      html += '<hr class="md-hr">';
    } else if (line.trim() === '') {
      if (inList) { html += '</ul>'; inList = false; }
      html += '<br>';
    } else {
      if (inList) { html += '</ul>'; inList = false; }
      html += `<p class="md-p">${p}</p>`;
    }
  }
  if (inList) html += '</ul>';
  if (inCode) html += '</code></pre>';
  return html;
}

// ══════════════════════════════════════════════
// Telegram WebApp 인증 & 모바일 UI
// ══════════════════════════════════════════════
const _tg = window.Telegram?.WebApp;
const _isTelegramWebApp = !!((_tg && _tg.initData && _tg.initData.length > 0));

async function initTelegramAuth() {
  if (_isTelegramWebApp) {
    // Telegram WebApp 환경 — 일반 웹페이지와 동일한 레이아웃 사용
    _tg.ready();
    _tg.expand();
    // 아래로 스와이프 시 앱이 닫히는(최소화되는) 동작 차단 (Bot API 7.7+)
    try { if (_tg.disableVerticalSwipes) _tg.disableVerticalSwipes(); } catch (_e) {}
    document.body.classList.add('tg-webapp');
    applyTelegramTheme();

    // ── 먼저 서버 인증 설정 확인 ──
    let requireAuth = true;
    try {
      const cfgRes = await fetch('/auth/config');
      const cfgData = await cfgRes.json();
      requireAuth = cfgData.require_auth !== false;
    } catch (_e) { /* 설정 조회 실패 시 인증 진행 */ }

    // require_auth=false이면 인증 생략
    if (!requireAuth) {
      hideAuthOverlay();
      return;
    }

    // 기존 세션 유효성 확인
    const stored = localStorage.getItem('ds_session');
    if (stored) {
      try {
        const r = await fetch('/auth/status', {
          headers: { 'X-Session-Token': stored }
        });
        const data = await r.json();
        if (data.ok) {
          _sessionToken = stored;
          hideAuthOverlay();
          return;
        }
      } catch (_e) { /* 네트워크 오류 → 재인증 */ }
    }

    // initData로 신규 인증
    showAuthOverlay('인증 중...');
    try {
      const r = await fetch('/auth/telegram', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ init_data: _tg.initData }),
      });
      const data = await r.json();
      if (data.ok && data.token) {
        _sessionToken = data.token;
        localStorage.setItem('ds_session', data.token);
        hideAuthOverlay();
      } else {
        showAuthError(data.error || '인증 실패');
      }
    } catch (_e) {
      showAuthError('서버 연결 실패 — 잠시 후 다시 시도해주세요.');
    }
  } else {
    // 일반 브라우저 (로컬 or 인증 불필요)
    hideAuthOverlay();
  }
}

function showAuthOverlay(msg) {
  const el      = document.getElementById('auth-overlay');
  const msgEl   = document.getElementById('auth-message');
  const spinner = document.getElementById('auth-spinner');
  const error   = document.getElementById('auth-error');
  if (el)      el.classList.remove('hidden');
  if (msgEl)   msgEl.textContent = msg;
  if (spinner) spinner.style.display = 'block';
  if (error)   error.classList.add('hidden');
}

function showAuthError(msg) {
  const msgEl   = document.getElementById('auth-message');
  const spinner = document.getElementById('auth-spinner');
  const error   = document.getElementById('auth-error');
  if (msgEl)   msgEl.textContent = '';
  if (spinner) spinner.style.display = 'none';
  if (error)   { error.classList.remove('hidden'); error.textContent = msg; }
}

function hideAuthOverlay() {
  const el = document.getElementById('auth-overlay');
  if (el) el.classList.add('hidden');
}

function applyTelegramTheme() {
  // Telegram 테마 변수로 덮어쓰지 않음 — 바이낸스 다크 테마 유지
  // 대신 Telegram 앱 UI(헤더·배경)를 다크 색상으로 강제 설정
  try {
    if (_tg?.setBackgroundColor) _tg.setBackgroundColor('#0B0E11');
    if (_tg?.setHeaderColor)     _tg.setHeaderColor('#0B0E11');
    // Telegram Bot API 7.0+ bottomBarColor
    if (_tg?.setBottomBarColor)  _tg.setBottomBarColor('#1E2026');
  } catch (_e) { /* API 미지원 버전 무시 */ }
}

function setupMobileNav() {
  // 하단 탭바 표시
  const bottomNav = document.getElementById('bottom-nav');
  if (bottomNav) bottomNav.classList.remove('hidden');
  document.body.classList.add('has-bottom-nav');
  // 상단 Nav 숨김 (Telegram 모바일에서는 하단 탭바만 사용)
  const topnav = document.getElementById('topnav');
  if (topnav) topnav.style.display = 'none';
  // 콘텐츠 top 패딩 제거 (상단 Nav 없으므로)
  const pageContent = document.querySelector('.page-content');
  if (pageContent) pageContent.style.paddingTop = '0';

  // 탭 클릭 핸들러
  document.querySelectorAll('.bnav-item').forEach(el => {
    el.addEventListener('click', () => {
      const page = el.dataset.page;
      navigate(page);
    });
  });
}

function syncBottomNav(page) {
  document.querySelectorAll('.bnav-item').forEach(el => {
    el.classList.toggle('active', el.dataset.page === page);
  });
}

// ── 라우터 ───────────────────────────────────
const PAGES = ['dashboard', 'runner', 'settings', 'logs', 'analysis', 'trades', 'charts', 'reports'];
let currentPage = 'dashboard';

function navigate(page) {
  if (!PAGES.includes(page)) page = 'dashboard';
  currentPage = page;
  PAGES.forEach(p => {
    document.getElementById(`page-${p}`).classList.toggle('hidden', p !== page);
  });
  // 구버전 사이드바 링크
  document.querySelectorAll('.nav-item').forEach(el => {
    el.classList.toggle('active', el.dataset.page === page);
  });
  // 신버전 상단 Nav 링크
  document.querySelectorAll('.topnav-link').forEach(el => {
    el.classList.toggle('active', el.dataset.page === page);
  });
  // 상단 Nav 설정 버튼
  const settingsBtn = document.querySelector('.topnav-settings-btn');
  if (settingsBtn) settingsBtn.classList.toggle('active', page === 'settings');
  // 모바일 드로어 링크
  document.querySelectorAll('.drawer-link').forEach(el => {
    el.classList.toggle('active', el.dataset.page === page);
  });
  // 드로어 닫기
  const drawer = document.getElementById('topnav-drawer');
  const burger = document.getElementById('topnav-hamburger');
  if (drawer) drawer.classList.remove('open');
  if (burger) burger.classList.remove('open');
  syncBottomNav(page);
  if (page === 'dashboard') renderDashboard();
  if (page === 'runner')    renderRunner();
  if (page === 'settings') {
    activeSettingsTab = 'upbit';
    renderSettings();
  }
  if (page === 'logs')      renderLogs();
  if (page === 'analysis')  renderAnalysis();
  if (page === 'trades')    renderTrades();
  if (page === 'charts')    renderCharts();
  if (page === 'reports')   renderReports();
  window.location.hash = page;
}

// 구버전 사이드바 링크 핸들러
document.querySelectorAll('.nav-item').forEach(el => {
  el.addEventListener('click', e => { e.preventDefault(); navigate(el.dataset.page); });
});
// 신버전 상단 Nav 링크 핸들러
document.querySelectorAll('.topnav-link, .drawer-link, .topnav-brand, .topnav-settings-btn').forEach(el => {
  el.addEventListener('click', e => {
    e.preventDefault();
    const page = el.dataset.page;
    if (page) navigate(page);
  });
});
// 햄버거 토글
const _hamburger = document.getElementById('topnav-hamburger');
const _drawer    = document.getElementById('topnav-drawer');
if (_hamburger && _drawer) {
  _hamburger.addEventListener('click', () => {
    _hamburger.classList.toggle('open');
    _drawer.classList.toggle('open');
  });
}

// ══════════════════════════════════════════════
// 대시보드
// ══════════════════════════════════════════════
let dashInterval = null;
let dashAnalyzing = false;

// ── 수익률 기간별 섹션 ──────────────────────────

let _returnsPeriod = '1m';
let _returnsCache  = {};
// 현재 보유분 평가손익(미실현) — 기간 무관, status에서 세팅
let _holdingPnl = { coin: {pct: null, count: 0}, stock: {pct: null, count: 0} };

/** 실현손익 카드 — 메인=청산 거래 실현 수익률, 하단=현재 보유 평가(미실현) */
function _returnsCardHtml(label, data, holdInfo) {
  const pct = data.avg_return_pct ?? 0;
  const krw = data.total_realized_krw ?? 0;
  const cnt = data.trade_count ?? 0;
  const wr  = data.win_rate ?? 0;
  const cls = pct > 0.05 ? 'up' : pct < -0.05 ? 'down' : 'flat';
  const sign = pct > 0 ? '+' : '';
  const krwSign = krw > 0 ? '+' : '';

  // 보유 평가(미실현) 행
  let holdRow = '';
  if (holdInfo) {
    const hp = holdInfo.pct;
    const hc = holdInfo.count;
    if (hc > 0 && hp != null) {
      const hcls = hp > 0.05 ? 'text-up' : hp < -0.05 ? 'text-down' : '';
      holdRow = `<div class="returns-hold-row">
        <span class="returns-hold-key">보유 평가</span>
        <span class="returns-hold-val ${hcls}">${hp > 0 ? '+' : ''}${hp.toFixed(2)}% · ${hc}종목</span>
      </div>`;
    } else {
      holdRow = `<div class="returns-hold-row">
        <span class="returns-hold-key">보유 평가</span>
        <span class="returns-hold-val">— (0종목)</span>
      </div>`;
    }
  }

  return `<div class="returns-card">
    <div class="returns-card-label">${label}</div>
    <div class="returns-card-pct ${cls}">${cnt === 0 ? '—' : sign + pct.toFixed(2) + '%'}</div>
    <div class="returns-card-krw">${cnt === 0 ? '청산 없음' : krwSign + Math.round(krw).toLocaleString() + '원 · ' + cnt + '건' + (cnt > 0 ? ' 승률' + wr.toFixed(0) + '%' : '')}</div>
    ${holdRow}
  </div>`;
}

/** 수익률 카드 4개 통합 렌더 (중복 제거용) */
function _renderReturnsCards(data) {
  return [
    _returnsCardHtml('코인 실현손익', data.crypto, _holdingPnl.coin),
    _returnsCardHtml('주식 실현손익', data.stock,  _holdingPnl.stock),
    _returnsCardHtml('합산 실현손익', data.combined, null),
    `<div class="returns-card">
      <div class="returns-card-label">청산 건수</div>
      <div class="returns-card-pct flat" style="font-size:28px">${data.combined.trade_count ?? 0}</div>
      <div class="returns-card-krw">코인 ${data.crypto.trade_count}건 · 주식 ${data.stock.trade_count}건</div>
      <div class="returns-hold-row"><span class="returns-hold-key">승 / 패</span><span class="returns-hold-val">${data.combined.win_count ?? 0} / ${(data.combined.trade_count ?? 0) - (data.combined.win_count ?? 0)}</span></div>
    </div>`,
  ].join('');
}

function _returnsDateLabel(data) {
  // 날짜 범위 표시 — "6/1", "5/26 ~ 6/1" 형식
  const from = data.date_from || '';
  const to   = data.date_to   || '';
  if (!from) return '';
  const fmtDate = s => {
    const d = new Date(s + 'T00:00:00');
    return `${d.getMonth()+1}/${d.getDate()}`;
  };
  const periodDesc = {
    '1d':  '오늘 매도 완료 기준',
    '1w':  '최근 7일 매도 완료 기준',
    '1m':  '최근 30일 매도 완료 기준',
    'all': '전체 기간 매도 완료 기준',
  };
  const isSameDay = from === to;
  const dateStr = isSameDay ? fmtDate(from) : `${fmtDate(from)} ~ ${fmtDate(to)}`;
  const desc = periodDesc[_returnsPeriod] || '';
  return `<span class="returns-date-range">${dateStr}</span><span class="returns-period-desc">${desc}</span>`;
}

function buildReturnsSection(data) {
  const periods = [
    { key: '1d', label: '일간' },
    { key: '1w', label: '주간' },
    { key: '1m', label: '월간' },
    { key: 'all', label: '전체' },
  ];
  const tabs = periods.map(p =>
    `<button class="returns-tab ${p.key === _returnsPeriod ? 'active' : ''}"
       onclick="switchReturnsPeriod('${p.key}')">${p.label}</button>`
  ).join('');
  const dateLabel = _returnsDateLabel(data);
  return `<div class="returns-section" id="returns-section">
    <div class="returns-section-header">
      <div style="display:flex;flex-direction:column;gap:2px">
        <span class="returns-section-title">수익률 분석 <span class="returns-title-hint">실현 = 매도 청산 거래</span></span>
        <div class="returns-date-info">${dateLabel}</div>
      </div>
      <div class="returns-tabs">${tabs}</div>
    </div>
    <div class="returns-cards" id="returns-cards">${_renderReturnsCards(data)}</div>
  </div>`;
}

async function switchReturnsPeriod(period) {
  _returnsPeriod = period;
  // 탭 active 상태 즉시 변경
  document.querySelectorAll('.returns-tab').forEach(btn => {
    btn.classList.toggle('active', btn.textContent === {
      '1d':'일간','1w':'주간','1m':'월간','all':'전체'
    }[period]);
  });
  const updateDateInfo = (data) => {
    const el = document.querySelector('.returns-date-info');
    if (el) el.innerHTML = _returnsDateLabel(data);
  };
  // 캐시 확인 (30초 TTL)
  const cached = _returnsCache[period];
  if (cached && Date.now() - cached.ts < 30000) {
    document.getElementById('returns-cards').innerHTML = _renderReturnsCards(cached.data);
    updateDateInfo(cached.data);
    return;
  }
  try {
    const data = await GET(`/api/stats/returns?period=${period}`);
    _returnsCache[period] = { ts: Date.now(), data };
    document.getElementById('returns-cards').innerHTML = _renderReturnsCards(data);
    updateDateInfo(data);
  } catch(e) { console.warn('returns load fail:', e); }
}

// ── NEXORA 스타일 헬퍼 ──────────────────────────

// ④ 거래소 배지
function exchBadge(name) {
  const map = {
    upbit:   '<span class="exchange-badge upbit">Upbit</span>',
    bithumb: '<span class="exchange-badge bithumb">Bithumb</span>',
    binance: '<span class="exchange-badge binance">Binance</span>',
    kis:     '<span class="exchange-badge kis">KIS</span>',
    bybit:   '<span class="exchange-badge binance">Bybit</span>',
  };
  return map[(name || '').toLowerCase()] || '';
}

function cryptoExchangesFromStatus(d) {
  if (d && d.crypto_exchanges) return d.crypto_exchanges;
  return {
    upbit: {
      broker: 'upbit',
      connected: true,
      demo: false,
      trading_supported: true,
      holdings: d.holdings || [],
      balance: {
        available: Number(d.balance_krw || 0),
        total: Number(d.balance_krw_total ?? d.balance_krw ?? 0),
      },
      error: null,
    },
    bithumb: {
      broker: 'bithumb',
      connected: false,
      demo: true,
      trading_supported: true,
      holdings: [],
      balance: { available: 0, total: 0 },
      error: null,
    },
  };
}

function cryptoCashTotal(exchanges) {
  return Object.values(exchanges || {}).reduce(
    (sum, ex) => sum + Number(ex.balance?.total ?? ex.balance?.available ?? 0),
    0
  );
}

function buildCryptoAssetCard(ex, runner, activeBroker) {
  const broker = (ex.broker || 'upbit').toLowerCase();
  const isActive = (activeBroker || 'upbit').toLowerCase() === broker;
  const holdings = ex.holdings || [];
  const krwTotal = Number(ex.balance?.total ?? ex.balance?.available ?? 0);
  const coinVal = holdings.reduce((s, h) => s + (h.valuation_krw || 0), 0);
  const cryptoPnl = holdings.reduce((s, h) => s + (h.pnl_pct || 0), 0);
  const runnerRows = isActive ? `
    <div class="status-card-row">
      <span class="sc-key">오늘 매수</span><span class="sc-val">${fmt_krw(runner.buy_krw_today || 0)}</span>
    </div>
    <div class="status-card-row">
      <span class="sc-key">오늘 매도</span><span class="sc-val">${fmt_krw(runner.sell_krw_today || 0)}</span>
    </div>
    <div class="status-card-divider"></div>` : `
    <div class="status-card-row">
      <span class="sc-key">자동매매</span><span class="sc-val" style="color:var(--text-muted)">비활성 거래소</span>
    </div>
    <div class="status-card-divider"></div>`;
  const statusNote = ex.demo
    ? `<div class="status-card-row"><span class="sc-key" style="color:var(--text-muted)">상태</span><span class="sc-val" style="font-size:11px">API 키 미설정<br><span style="color:var(--text-muted)">설정 → 코인 (거래소) → ${broker === 'bithumb' ? 'bithumb' : 'upbit'}</span></span></div>`
    : ex.error
      ? `<div class="status-card-row"><span class="sc-key" style="color:var(--danger)">오류</span><span class="sc-val" style="font-size:11px">${escHtml(ex.error)}</span></div>`
      : '';
  return `
    <div class="status-card info">
      <div class="status-card-label">코인 자산 ${exchBadge(broker)}</div>
      <div class="status-card-value">${fmt_krw(Math.round(coinVal + krwTotal))}</div>
      <div class="status-card-row">
        <span class="sc-key">KRW 잔고</span><span class="sc-val">${fmt_krw(krwTotal)}</span>
      </div>
      ${statusNote}
      ${runnerRows}
      <div class="status-card-row">
        <span class="sc-key">보유 코인</span>
        <span class="sc-val ${holdings.length > 0 ? 'text-up' : ''}">
          ${holdings.length}개${holdings.length ? ` &nbsp;${gradPct(cryptoPnl / holdings.length)}` : ''}
        </span>
      </div>
    </div>`;
}

function buildCryptoHoldingRows(holdings) {
  return holdings.map(h => {
    const symbol = (h.market || '').replace('KRW-', '');
    const korName = coinKoreanName(h.market);
    return `<tr>
      <td>
        <strong class="symbol-link" onclick="openSymbolDetail('${escHtml(h.market)}','${escHtml(korName || '')}')">${symbol}</strong>
        ${korName ? `<br><span style="font-size:11px;color:var(--text-muted)">${korName}</span>` : ''}
      </td>
      <td>${h.quantity}</td>
      <td>${fmt_krw(h.avg_buy_price)}</td>
      <td>${fmt_krw(h.current_price)}</td>
      <td>${fmt_krw(h.valuation_krw)}</td>
      <td>${gradPct(h.pnl_pct)}</td>
    </tr>`;
  }).join('');
}

// ⑤ 그라디언트 수익률 텍스트
function gradPct(pct) {
  if (pct == null) return '—';
  const cls = pct > 0.05 ? 'grad-text-up' : pct < -0.05 ? 'grad-text-down' : pct_class(pct);
  return `<span class="${cls}">${fmt_pct(pct)}</span>`;
}

// ── 종목 상세 모달 + 캔들차트 ──────────────────
function buildCandleChart(candles, { w = 460, h = 180 } = {}) {
  if (!candles || candles.length < 2) return '<div class="text-muted" style="padding:20px;text-align:center">차트 데이터 없음</div>';
  const highs = candles.map(c => c.high), lows = candles.map(c => c.low);
  const max = Math.max(...highs), min = Math.min(...lows);
  const range = max - min || 1;
  const n = candles.length;
  const cw = w / n;             // 캔들 폭
  const bodyW = Math.max(1.5, cw * 0.6);
  const y = v => h - ((v - min) / range) * (h - 10) - 5;
  const bars = candles.map((c, i) => {
    const cx = i * cw + cw / 2;
    const up = c.close >= c.open;
    const col = up ? 'var(--up)' : 'var(--down)';
    const yO = y(c.open), yC = y(c.close);
    const top = Math.min(yO, yC), bh = Math.max(1, Math.abs(yC - yO));
    return `<line x1="${cx.toFixed(1)}" y1="${y(c.high).toFixed(1)}" x2="${cx.toFixed(1)}" y2="${y(c.low).toFixed(1)}" stroke="${col}" stroke-width="1"/>
      <rect x="${(cx - bodyW/2).toFixed(1)}" y="${top.toFixed(1)}" width="${bodyW.toFixed(1)}" height="${bh.toFixed(1)}" fill="${col}"/>`;
  }).join('');
  return `<svg viewBox="0 0 ${w} ${h}" width="100%" height="${h}" preserveAspectRatio="none" style="display:block">${bars}</svg>`;
}

// 호가창 시각화 — 매수/매도벽 깊이 막대
function buildOrderbook(ob) {
  const bids = ob.bids || [], asks = ob.asks || [];
  if (!bids.length && !asks.length) return '';
  const maxSize = Math.max(...bids.map(b=>b.size), ...asks.map(a=>a.size), 0.0001);
  const askRows = asks.slice().reverse().map(a => `<div class="ob-row">
    <span class="ob-price ask">${fmt_krw(a.price)}</span>
    <div class="ob-bar-wrap"><div class="ob-bar ask" style="width:${(a.size/maxSize*100).toFixed(0)}%"></div><span class="ob-size">${a.size.toFixed(3)}</span></div>
  </div>`).join('');
  const bidRows = bids.map(b => `<div class="ob-row">
    <span class="ob-price bid">${fmt_krw(b.price)}</span>
    <div class="ob-bar-wrap"><div class="ob-bar bid" style="width:${(b.size/maxSize*100).toFixed(0)}%"></div><span class="ob-size">${b.size.toFixed(3)}</span></div>
  </div>`).join('');
  const ratio = ob.bid_ask_ratio || 0;
  const ratioCls = ratio >= 1 ? 'text-up' : 'text-down';
  return `<div class="ob-section">
    <div class="ob-title">호가창 <span class="${ratioCls}" style="font-size:11px">매수/매도 ${ratio}x</span></div>
    <div class="ob-asks">${askRows}</div>
    <div class="ob-mid">매수벽 ${(ob.bid_total||0).toFixed(2)} ↔ 매도벽 ${(ob.ask_total||0).toFixed(2)}</div>
    <div class="ob-bids">${bidRows}</div>
  </div>`;
}

async function openSymbolDetail(market, korName) {
  const symbol = (market || '').replace('KRW-', '');
  let modal = document.getElementById('symbol-detail-modal');
  if (!modal) {
    modal = document.createElement('div');
    modal.id = 'symbol-detail-modal';
    modal.className = 'sd-modal';
    document.body.appendChild(modal);
  }
  modal.innerHTML = `<div class="sd-backdrop" onclick="closeSymbolDetail()"></div>
    <div class="sd-panel">
      <div class="sd-header">
        <div><strong style="font-size:16px">${escHtml(symbol)}</strong> <span class="text-muted">${escHtml(korName||'')}</span></div>
        <button class="sd-close" onclick="closeSymbolDetail()">✕</button>
      </div>
      <div class="sd-body" id="sd-body"><div class="text-muted" style="padding:30px;text-align:center">차트 로딩 중...</div></div>
    </div>`;
  modal.classList.add('open');
  try {
    const [cd, sig, ob] = await Promise.all([
      GET(`/api/candles?market=${encodeURIComponent(market)}&count=30`).catch(() => ({ candles: [] })),
      GET('/api/scalping/scores?limit=50').catch(() => ({ scores: [] })),
      GET(`/api/orderbook?market=${encodeURIComponent(market)}&levels=8`).catch(() => ({ bids: [], asks: [] })),
    ]);
    const candles = cd.candles || [];
    const last = candles[candles.length - 1] || {};
    const first = candles[0] || {};
    const chg = first.close ? ((last.close - first.close) / first.close * 100) : 0;
    // 해당 종목 현재 스코어
    const sc = (sig.scores || []).find(s => (s.symbol||'').replace('USDT','') === symbol);
    const body = document.getElementById('sd-body');
    if (body) body.innerHTML = `
      <div class="sd-price-row">
        <span class="sd-price">${fmt_krw(last.close)}</span>
        <span class="${chg>=0?'text-up':'text-down'}">${chg>=0?'+':''}${chg.toFixed(2)}% <span class="text-muted" style="font-size:11px">(30일)</span></span>
      </div>
      ${buildCandleChart(candles)}
      <div class="sd-stats">
        <div class="sd-stat"><span class="sd-stat-k">30일 고가</span><span>${fmt_krw(Math.max(...candles.map(c=>c.high)))}</span></div>
        <div class="sd-stat"><span class="sd-stat-k">30일 저가</span><span>${fmt_krw(Math.min(...candles.map(c=>c.low)))}</span></div>
        ${sc ? `<div class="sd-stat"><span class="sd-stat-k">GSQS 점수</span><span class="${sc.score>=70?'text-up':''}">${sc.score} (${sc.decision||''})</span></div>` : ''}
      </div>
      ${buildOrderbook(ob)}`;
  } catch (e) {
    const body = document.getElementById('sd-body');
    if (body) body.innerHTML = `<div class="text-danger" style="padding:20px">로드 실패: ${e.message}</div>`;
  }
}

function closeSymbolDetail() {
  const m = document.getElementById('symbol-detail-modal');
  if (m) m.classList.remove('open');
}

// 미체결 주문 섹션 — 3개 시장(코인·국내·해외) 대기중 주문 통합 조회·취소
const _MKT_LABEL = { crypto: '코인', stock: '국내주식', overseas: '해외주식' };
const _MKT_COLOR = { crypto: '#f59e0b', stock: '#22c55e', overseas: '#3b82f6' };

function buildOpenOrders(data) {
  const items = (data && data.items) || [];
  window._openOrders = items;  // 취소 시 참조
  if (!items.length) {
    // 대기 주문이 없어도 위치를 항상 보여줌 (어디서 보는지 명확하게)
    return `<div class="section-box" style="border-left:3px solid var(--border)">
      <div class="section-title">⏳ 대기중 주문 0건 <span style="font-size:11px;color:var(--text-muted)">· 지정가 미체결 (전 시장)</span></div>
      <div class="text-muted" style="font-size:12px;padding:6px 2px">현재 대기중인(미체결) 주문이 없습니다. 지정가 주문이 들어가면 여기에 표시되고 취소할 수 있어요.</div>
    </div>`;
  }
  const rows = items.map((o, i) => {
    const unit = o.price_unit || '₩';
    const priceStr = (o.price != null)
      ? (unit === '$' ? `$${Number(o.price).toLocaleString(undefined,{maximumFractionDigits:2})}` : fmt_krw(o.price))
      : '-';
    const amtStr = (o.amount_krw != null) ? fmt_krw(o.amount_krw) : '-';
    const usdExtra = (o.amount_usd != null) ? ` <span style="color:var(--text-muted);font-size:10px">($${Number(o.amount_usd).toLocaleString()})</span>` : '';
    const mlabel = _MKT_LABEL[o.market_type] || o.market_type;
    const mcolor = _MKT_COLOR[o.market_type] || 'var(--text-muted)';
    const krName = o.market_type === 'crypto' && coinKoreanName(o.symbol) ? `<span style="font-size:10px;color:var(--text-muted)">${coinKoreanName(o.symbol)}</span>` : '';
    const cancelBtn = o.cancellable
      ? `<button class="btn-cancel-order" onclick="cancelOpenOrder(${i})">취소</button>`
      : '<span style="font-size:10px;color:var(--text-muted)">-</span>';
    return `<tr>
      <td><span style="font-size:10px;font-weight:700;color:${mcolor}">${mlabel}</span></td>
      <td><strong>${escHtml(o.name || o.symbol || '')}</strong> ${krName}</td>
      <td><span class="side-badge ${o.side==='매수'?'buy':'sell'}">${o.side}</span></td>
      <td style="text-align:right">${priceStr}</td>
      <td style="text-align:right">${o.remaining}</td>
      <td style="text-align:right">${amtStr}${usdExtra}</td>
      <td style="text-align:center">${cancelBtn}</td>
    </tr>`;
  }).join('');
  return `<div class="section-box" style="border-left:3px solid var(--warning)">
    <div class="section-title">⏳ 대기중 주문 ${items.length}건 <span style="font-size:11px;color:var(--text-muted)">· 지정가 미체결 (전 시장)</span></div>
    <div class="table-wrap"><table>
      <thead><tr><th>시장</th><th>종목</th><th>구분</th><th style="text-align:right">지정가</th><th style="text-align:right">잔량</th><th style="text-align:right">주문액</th><th style="text-align:center">취소</th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div>
  </div>`;
}

// 주문 실패/취소 이력 — 왜 안 샀는지 사유와 함께 (토스트가 사라져도 남음)
function buildOrderFailures(data) {
  const items = (data && data.items) || [];
  if (!items.length) return '';
  const stageLabel = { gate: '호가벽', quality: '체결품질', unfilled: '미체결취소', cancel: '취소', error: '오류', ml_winprob: 'ML승률', news_risk: '뉴스악재' };
  const rows = items.slice(0, 5).map(o => {
    const t = (o.ts || '').replace('T', ' ').slice(5, 16);
    const name = o.display_name || o.market || '';
    const sym = (o.market || '').replace('KRW-', '');
    const cnt = (o.count && o.count > 1) ? ` <span style="color:var(--text-muted)">×${o.count}</span>` : '';
    return `<div style="padding:6px 9px;border:1px solid var(--border);border-radius:7px;margin-top:5px;background:var(--bg-elevated)">
      <div style="display:flex;justify-content:space-between;font-size:11px;color:var(--text-muted)">
        <span><b style="color:var(--text)">${escHtml(name)}</b> ${escHtml(sym)}${cnt}</span>
        <span>${t} · ${stageLabel[o.stage] || o.stage}</span>
      </div>
      <div style="font-size:12px;margin-top:2px;color:#f59e0b">${escHtml(o.reason_kr || (o.reasons || []).join('; '))}</div>
    </div>`;
  }).join('');
  return `<div class="section-box" style="border-left:3px solid #f59e0b">
    <div class="section-title">⚠️ 최근 매수 보류 (코인별 최신) <span style="font-size:11px;color:var(--text-muted)">· 왜 안 샀는지</span></div>
    ${rows}
    <div style="font-size:11px;color:var(--text-muted);margin-top:6px">코인별로 묶어 최신 1건만 표시(×N=반복횟수). "매도벽/스프레드/미체결" = 손해 볼 자리라 보류.</div>
  </div>`;
}

async function cancelOpenOrder(idx) {
  const o = (window._openOrders || [])[idx];
  if (!o) return;
  const label = _MKT_LABEL[o.market_type] || '';
  if (!confirm(`[${label}] ${o.name || o.symbol} ${o.side} 미체결 주문을 취소하시겠습니까?`)) return;
  try {
    const body = { market_type: o.market_type, ...(o.cancel || {}) };
    const res = await POST('/api/orders/cancel', body);
    toast(res.message || (res.ok ? '취소됨' : '실패'), res.ok ? 'success' : 'error');
    if (res.ok) await loadDashboard();
  } catch (e) {
    toast('취소 요청 실패: ' + e.message, 'error');
  }
}

// TP/SL 시각화 바 — 손절가↔익절가 사이 현재 손익 위치
function buildTpslBar(pnlPct, tpPct, slPct, grade) {
  if (tpPct == null || slPct == null) return '<span class="text-muted">-</span>';
  const tp = Number(tpPct), sl = Number(slPct);
  const pnl = Number(pnlPct ?? 0);
  const range = tp - sl;
  if (range <= 0) return '<span class="text-muted">-</span>';
  // 현재 위치 (0~100%), 범위 벗어나면 클램프
  const pos = Math.max(0, Math.min(100, ((pnl - sl) / range) * 100));
  const markerCls = pnl >= 0 ? 'tpsl-marker-up' : 'tpsl-marker-down';
  const gradeBadge = grade ? `<span class="tpsl-grade">${grade}</span>` : '';
  return `<div class="tpsl-wrap" title="손절 ${sl.toFixed(1)}% · 현재 ${pnl>=0?'+':''}${pnl.toFixed(2)}% · 익절 +${tp.toFixed(1)}%">
    <span class="tpsl-end sl">${sl.toFixed(1)}%</span>
    <div class="tpsl-track">
      <div class="tpsl-marker ${markerCls}" style="left:${pos}%"></div>
    </div>
    <span class="tpsl-end tp">+${tp.toFixed(1)}%</span>
    ${gradeBadge}
  </div>`;
}

// ① 총 자산 면적 차트 (넓은 SVG area chart)
function buildAssetAreaChart(values, { height = 60, color = 'var(--accent)' } = {}) {
  if (!values || values.length < 2) return '';
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const W = 400;
  const pts = values.map((v, i) => {
    const x = ((i / (values.length - 1)) * W).toFixed(1);
    const y = (height - ((v - min) / range) * (height - 8) - 4).toFixed(1);
    return [x, y];
  });
  const lineStr = pts.map(p => p.join(',')).join(' ');
  const areaStr = `0,${height} ${lineStr} ${W},${height}`;
  const gid = 'ag' + Math.random().toString(36).slice(2, 7);
  return `<svg viewBox="0 0 ${W} ${height}" width="100%" height="${height}" preserveAspectRatio="none" style="display:block">
    <defs>
      <linearGradient id="${gid}" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="${color}" stop-opacity=".3"/>
        <stop offset="100%" stop-color="${color}" stop-opacity="0"/>
      </linearGradient>
    </defs>
    <polygon points="${areaStr}" fill="url(#${gid})"/>
    <polyline points="${lineStr}" fill="none" stroke="${color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
    <circle cx="${pts[pts.length-1][0]}" cy="${pts[pts.length-1][1]}" r="3" fill="${color}"/>
  </svg>`;
}

// ① 포트폴리오 도넛 차트
function buildDonutChart(segments, { size = 110, strokeW = 13 } = {}) {
  if (!segments || !segments.length) return '';
  const total = segments.reduce((s, sg) => s + (sg.value || 0), 0);
  if (!total) return '';
  const r = size / 2 - strokeW / 2 - 2;
  const cx = size / 2;
  const C = 2 * Math.PI * r;
  let arcs = '';
  let cumPct = 0;
  for (const sg of segments) {
    if (!sg.value) continue;
    const pct = sg.value / total;
    const deg = pct * 360 - 1.5; // 1.5° gap
    const dash = (C * deg / 360).toFixed(2);
    const gap  = (C - Number(dash)).toFixed(2);
    const rot  = (cumPct * 360 - 90).toFixed(2);
    arcs += `<circle cx="${cx}" cy="${cx}" r="${r}" fill="none"
      stroke="${sg.color}" stroke-width="${strokeW}"
      stroke-dasharray="${dash} ${gap}" stroke-linecap="round"
      transform="rotate(${rot} ${cx} ${cx})"/>`;
    cumPct += pct;
  }
  return `<svg viewBox="0 0 ${size} ${size}" width="${size}" height="${size}">${arcs}</svg>`;
}

function buildPortfolioDonut(holdings, stockHoldings, cryptoCashByExchange, stockBalance) {
  const COLORS = ['#f4f4f6','#b6b6bd','#86868f','#5a5a60','#d4d4d8','#9a9aa1','#6e6e76','#42424a'];
  const segments = [];
  holdings.forEach((h, i) => {
    if ((h.valuation_krw || 0) > 0)
      segments.push({ label: h.market.replace('KRW-',''), value: h.valuation_krw, color: COLORS[i % COLORS.length] });
  });
  const stockVal = stockHoldings.reduce((s, h) => s + (h.market_value || 0), 0);
  if (stockVal > 0)
    segments.push({ label: '국내주식', value: stockVal, color: '#86868f' });
  const upbitCash = Number(cryptoCashByExchange?.upbit || 0);
  const bithumbCash = Number(cryptoCashByExchange?.bithumb || 0);
  if (upbitCash > 0)
    segments.push({ label: 'Upbit 현금', value: upbitCash, color: 'rgba(148,163,184,.35)' });
  if (bithumbCash > 0)
    segments.push({ label: 'Bithumb 현금', value: bithumbCash, color: 'rgba(255,140,0,.25)' });
  const stockCash = Number(stockBalance || 0);
  if (stockCash > 0)
    segments.push({ label: '주식 예수금', value: stockCash, color: 'rgba(148,163,184,.2)' });
  if (!segments.length) return '';
  const total = segments.reduce((s, x) => s + x.value, 0);
  if (!total) return '';

  const avgPnl = holdings.length > 0
    ? holdings.reduce((s, h) => s + (h.pnl_pct || 0), 0) / holdings.length : null;
  const centerCls = avgPnl != null ? (avgPnl > 0 ? 'grad-text-up' : avgPnl < 0 ? 'grad-text-down' : '') : '';
  const centerVal = avgPnl != null ? (avgPnl > 0 ? '+' : '') + avgPnl.toFixed(2) + '%' : '—';

  const legend = segments.map(sg =>
    `<div class="donut-legend-item">
      <span class="donut-legend-dot" style="background:${sg.color}"></span>
      <span class="donut-legend-label">${sg.label}</span>
      <span class="donut-legend-pct">${(sg.value/total*100).toFixed(1)}%</span>
    </div>`).join('');

  return `
  <div class="section-box" style="margin-bottom:14px">
    <div class="section-title">포트폴리오 구성</div>
    <div class="portfolio-donut-section">
      <div class="donut-wrap">
        ${buildDonutChart(segments)}
        <div class="donut-center">
          <div class="donut-center-label">수익률</div>
          <div class="donut-center-value ${centerCls}">${centerVal}</div>
        </div>
      </div>
      <div class="donut-legend">${legend}</div>
    </div>
  </div>`;
}

// ③ AI 신호 패널
function _timeAgo(ts) {
  if (!ts) return '';
  try {
    const diff = Date.now() - new Date(ts).getTime();
    const m = Math.floor(diff / 60000);
    if (m < 1) return '방금';
    if (m < 60) return `${m}분 전`;
    const h = Math.floor(m / 60);
    return h < 24 ? `${h}시간 전` : `${Math.floor(h/24)}일 전`;
  } catch { return ''; }
}

function buildSignalPanel(scores, plan) {
  const sigs = [];
  if (plan && plan.market) {
    const sc = plan.final_score ?? 0;
    const symbol = plan.market.replace('KRW-','').replace('-KRW','');
    const kor    = coinKoreanName(plan.market);
    sigs.push({
      type:    plan.side === 'sell' ? 'sell' : 'buy',
      icon:    symbol.slice(0, 2),
      name:    symbol,
      korName: kor,
      label:   plan.side === 'sell' ? '매도 신호' : '매수 신호',
      score:   sc,
      sub:     `GSQS ${sc.toFixed(0)} · ${plan.macro_regime || 'neutral'}`,
      time:    _timeAgo(plan.created_at),
    });
  }
  const tops = (scores || []).filter(s => s.score > 55).slice(0, 5);
  for (const s of tops) {
    const mkt    = (s.symbol || '').replace('USDT','').replace('KRW','');
    const krwMkt = `KRW-${mkt}`;
    const kor    = coinKoreanName(krwMkt);
    if (sigs.find(x => x.name === mkt)) continue;
    sigs.push({
      type:    s.is_buy ? 'buy' : s.decision === 'WATCH' ? 'watch' : 'info',
      icon:    mkt.slice(0, 2),
      name:    mkt,
      korName: kor,
      label:   s.is_buy ? '매수 후보' : s.decision === 'WATCH' ? '관찰 중' : '스캔 중',
      score:   s.score,
      sub:     `GSQS ${s.score.toFixed(0)}`,
      time:    '실시간',
    });
    if (sigs.length >= 4) break;
  }

  const cards = sigs.map(sig => {
    const pct = Math.min((sig.score / 100) * 100, 100).toFixed(0);
    const fCls = sig.score >= 70 ? 'high' : sig.score >= 55 ? 'mid' : 'low';
    const badge = sig.type === 'buy'
      ? '<span class="side-badge buy" style="font-size:10px;padding:1px 6px">BUY</span>'
      : sig.type === 'sell'
        ? '<span class="side-badge sell" style="font-size:10px;padding:1px 6px">SELL</span>'
        : '<span class="badge badge-neutral" style="font-size:10px">WATCH</span>';
    return `<div class="signal-card ${sig.type}-signal">
      <div class="signal-card-top">
        <div class="signal-market-icon ${sig.type === 'sell' ? 'sell' : sig.type === 'watch' ? 'watch' : sig.type === 'info' ? 'info' : ''}">${escHtml(sig.icon)}</div>
        <div class="signal-meta">
          <div class="signal-market-name">${escHtml(sig.name)} ${badge}</div>
          ${sig.korName ? `<div class="signal-kor-name">${escHtml(sig.korName)}</div>` : ''}
          <div class="signal-time">${sig.label} · ${sig.time}</div>
        </div>
      </div>
      <div class="signal-score-bar"><div class="signal-score-fill ${fCls}" style="width:${pct}%"></div></div>
      <div class="signal-footer">
        <span class="signal-confidence">${escHtml(sig.sub)}</span>
        <span class="signal-confidence"><span class="val">${sig.score.toFixed(0)}</span>/100</span>
      </div>
    </div>`;
  }).join('');

  const empty = !sigs.length
    ? '<div class="text-muted" style="font-size:12px;padding:10px 0">신호 대기 중...</div>'
    : '';

  return `<div class="signal-panel">
    <div class="signal-panel-header">
      <span class="signal-panel-title">AI 실시간 신호</span>
      <span class="live-badge"><span class="live-badge-dot"></span>LIVE</span>
    </div>
    ${cards}${empty}
  </div>`;
}

// ── KPI sparkline & strip helpers ──────────────
function buildSparkline(values, { width = 80, height = 28, color = 'var(--accent)' } = {}) {
  if (!values || values.length < 2) return '';
  const min = Math.min(...values);
  const max = Math.max(...values);
  const absMax = Math.max(Math.abs(max), Math.abs(min)) || 1;
  // 변동폭이 최대값의 1% 미만이면 평탄선으로 표시 (착시 방지)
  const changePct = (max - min) / absMax * 100;
  const flatLine = changePct < 1;
  const effectiveMin = flatLine ? min - absMax * 0.05 : min;
  const effectiveMax = flatLine ? max + absMax * 0.05 : max;
  const range = (effectiveMax - effectiveMin) || 1;
  const pts = values.map((v, i) => {
    const x = ((i / (values.length - 1)) * width).toFixed(1);
    const y = (height - ((v - effectiveMin) / range) * (height - 4) - 2).toFixed(1);
    return `${x},${y}`;
  }).join(' ');
  return `<svg class="kpi-sparkline" viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" style="flex-shrink:0">
    <polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" opacity="${flatLine ? 0.5 : 1}"/>
  </svg>`;
}

function _kpiHistoryPush(key, value) {
  try {
    const stored = JSON.parse(localStorage.getItem('ds_kpi') || '{}');
    if (!stored[key]) stored[key] = [];
    stored[key].push(value);
    if (stored[key].length > 20) stored[key] = stored[key].slice(-20);
    localStorage.setItem('ds_kpi', JSON.stringify(stored));
    return stored[key];
  } catch { return [value]; }
}

function buildKpiStrip(d, holdings, stockHoldings, totalAsset, returnsData = null) {
  const r = d.runner || {};
  const coinVal  = holdings.reduce((s, h) => s + (h.valuation_krw || 0), 0);
  const stockVal = stockHoldings.reduce((s, h) => s + (h.market_value || 0), 0);
  const krwTotal = d.balance_krw_total ?? d.balance_krw ?? 0;
  const stockCash = d.stock_balance_krw || 0;
  const availableCash = krwTotal + stockCash;
  const coinCost = holdings.reduce((s, h) => s + (Number(h.avg_buy_price || 0) * Number(h.quantity || 0)), 0);
  const stockCost = stockHoldings.reduce((s, h) => s + (Number(h.avg_price || 0) * Number(h.quantity || 0)), 0);
  const investedCost = coinCost + stockCost;
  const unrealizedPnl = (coinVal + stockVal) - investedCost;
  const unrealizedPnlPct = investedCost > 0 ? unrealizedPnl / investedCost * 100 : null;
  const coinPnlPct  = holdings.length
    ? holdings.reduce((s, h) => s + (h.pnl_pct || 0), 0) / holdings.length : null;
  const stockPnlPct = stockHoldings.length
    ? stockHoldings.reduce((s, h) => s + (h.pnl_pct || 0), 0) / stockHoldings.length : null;
  const todayBuy = Number(r.buy_krw_today || 0);
  const todaySell = Number(r.sell_krw_today || 0);
  const todayNet = todaySell - todayBuy;
  const realizedToday = returnsData?.combined?.total_realized_krw ?? null;

  const totalHist  = _kpiHistoryPush('total',  totalAsset);
  const coinHist   = _kpiHistoryPush('coin',   coinPnlPct ?? 0);
  const stockHist  = _kpiHistoryPush('stock',  stockPnlPct ?? 0);
  const ordersHist = _kpiHistoryPush('orders', r.orders_today || 0);
  const pnlHist    = _kpiHistoryPush('pnl', unrealizedPnlPct ?? 0);

  const dc = v => v == null ? 'flat' : v > 0 ? 'up' : v < 0 ? 'down' : 'flat';
  const dl = v => v == null ? '—' : (v > 0 ? '+' : '') + v.toFixed(2) + '%';
  const krwCls = v => v > 0 ? 'text-up' : v < 0 ? 'text-down' : '';
  const signedKrw = v => {
    if (v == null) return '-';
    const n = Number(v || 0);
    return (n > 0 ? '+' : n < 0 ? '-' : '') + fmt_krw(Math.abs(Math.round(n)));
  };
  const positionItems = [
    ...stockHoldings.map(h => ({
      label: h.name || h.symbol,
      sub: `${h.quantity || 0}주 · ${fmt_krw(h.market_value || 0)}`,
      pct: h.pnl_pct,
    })),
    ...holdings.map(h => ({
      label: coinKoreanName(h.market) || (h.market || '').replace('KRW-', ''),
      sub: `${h.quantity || 0}개 · ${fmt_krw(h.valuation_krw || 0)}`,
      pct: h.pnl_pct,
    })),
  ].slice(0, 4);
  const stockAvgPct = stockHoldings.length ? stockPnlPct : null;
  const coinAvgPct = holdings.length ? coinPnlPct : null;

  return `
  <div class="dashboard-summary">
    <section class="summary-hero">
      <div class="summary-label">현재 총 자산</div>
      <div class="summary-asset">${fmt_krw(Math.round(totalAsset))}</div>
      <div class="summary-main-row">
        <span class="summary-pill ${dc(unrealizedPnlPct)}">평가손익 ${signedKrw(unrealizedPnl)} · ${dl(unrealizedPnlPct)}</span>
        <span class="summary-pill flat">가용 현금 ${fmt_krw(Math.round(availableCash))}</span>
      </div>
      <div class="summary-chart">${buildSparkline(totalHist, { width: 210, height: 34, color: 'var(--accent)' })}</div>
    </section>

    <section class="summary-panel">
      <div class="summary-panel-title">오늘 거래</div>
      <div class="summary-big ${realizedToday != null ? krwCls(realizedToday) : ''}">${realizedToday != null ? signedKrw(realizedToday) : '-'}</div>
      <div class="summary-muted">실현손익 ${returnsData?.combined ? `· ${returnsData.combined.trade_count || 0}건 · 승률 ${(returnsData.combined.win_rate || 0).toFixed(1)}%` : ''}</div>
      <div class="summary-metric-row"><span>매수</span><strong class="text-up">${fmt_krw(Math.round(todayBuy))}</strong></div>
      <div class="summary-metric-row"><span>매도</span><strong class="text-down">${fmt_krw(Math.round(todaySell))}</strong></div>
      <div class="summary-metric-row"><span>순현금</span><strong class="${todayNet > 0 ? 'text-up' : todayNet < 0 ? 'text-down' : ''}">${signedKrw(todayNet)}</strong></div>
    </section>

    <section class="summary-panel">
      <div class="summary-panel-title">보유 현황</div>
      <div class="summary-holding-split">
        <div><span>코인</span><strong>${holdings.length}개</strong><em class="${coinAvgPct != null ? pct_class(coinAvgPct) : ''}">${dl(coinAvgPct)}</em></div>
        <div><span>국내주식</span><strong>${stockHoldings.length}종목</strong><em class="${stockAvgPct != null ? pct_class(stockAvgPct) : ''}">${dl(stockAvgPct)}</em></div>
      </div>
      <div class="summary-position-list">
        ${positionItems.length ? positionItems.map(item => `
          <div class="summary-position-item">
            <div><strong>${escHtml(item.label)}</strong><span>${item.sub}</span></div>
            <b class="${item.pct != null ? pct_class(item.pct) : ''}">${dl(item.pct)}</b>
          </div>
        `).join('') : '<div class="summary-empty">보유 포지션 없음</div>'}
      </div>
    </section>
  </div>`;
}

function buildKimchiCard(kimchi) {
  if (!kimchi || !kimchi.premiums) {
    return `<div class="card"><div class="card-label">김치프리미엄</div><div class="card-value text-muted">-</div></div>`;
  }
  const btc = kimchi.premiums.BTC;
  if (!btc) return '';
  const pct = btc.premium_pct;
  const cardClass = pct >= 5 ? 'danger' : pct >= 2 ? 'warning' : pct <= -2 ? 'info' : '';
  const dotCls = pct >= 5 ? 'stopped' : pct >= 2 ? 'warn' : 'ok';
  const avg = kimchi.average_premium_pct;
  const fxRate = kimchi.usd_krw_rate ? kimchi.usd_krw_rate.toLocaleString('ko-KR', {maximumFractionDigits:1}) : '-';
  const rows = Object.entries(kimchi.premiums)
    .map(([sym, p]) => {
      const cls = pct_class(p.premium_pct);
      return `<span style="margin-right:8px">${sym} <span class="${cls}">${p.premium_pct >= 0 ? '+' : ''}${p.premium_pct.toFixed(2)}%</span></span>`;
    }).join('');
  return `
    <div class="card ${cardClass}" style="grid-column:span 2; min-width:0">
      <div class="card-label"><span class="dot-inline ${dotCls}"></span>김치프리미엄</div>
      <div class="card-value" style="font-size:17px"><span class="${pct_class(pct)}">${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%</span> <span class="card-value-sub">BTC</span></div>
      <div class="card-sub" style="font-size:11px;margin-top:4px">${rows}</div>
      <div class="card-sub" style="font-size:10px;color:var(--text-muted);margin-top:2px">평균 ${avg != null ? (avg >= 0 ? '+' : '') + avg.toFixed(2) + '%' : '-'} &nbsp;|&nbsp; USD/KRW ${fxRate}</div>
    </div>`;
}

async function renderDashboard() {
  clearInterval(dashInterval);
  const el = document.getElementById('page-dashboard');
  el.innerHTML = '<div class="page-header"><h1 class="page-title">대시보드</h1></div><div class="text-muted">데이터 로딩 중...</div>';
  await loadDashboard();
  dashInterval = setInterval(loadDashboard, 10000); // 10초 (KIS는 서버 20초 캐시로 보호)
}

// ── 투자공격성 다이얼 ──────────────────────────────
let _aggData = null;
let _aggInteracting = false;   // 슬라이더 조작 중엔 폴링이 덮어쓰지 않음
let _aggInteractTimer = null;

function _aggTouch() {
  _aggInteracting = true;
  clearTimeout(_aggInteractTimer);
  _aggInteractTimer = setTimeout(() => { _aggInteracting = false; }, 6000);
}

let _aggRenderedLevel = null;   // 현재 카드에 그려진 단계 (변동 없으면 재렌더 스킵 → 깜빡임 방지)

async function loadAggressionCard() {
  if (_aggInteracting) return;   // 조작 중이면 슬라이더 튐 방지
  // 템플릿이 캐시로 이미 동기 렌더했으므로 그 단계를 기준으로 기록 (불필요한 재렌더 방지)
  if (_aggData) _aggRenderedLevel = _aggData.level;
  try {
    const r = await GET('/api/aggression');
    _aggData = r;
    if (!_aggInteracting && _aggRenderedLevel !== r.level) {  // 서버 단계가 실제로 바뀐 경우에만 다시 그림
      renderAggressionCard(r.level);
    }
  } catch (e) { /* 카드 생략 */ }
}

function _bandColor(band) {
  return band === 'liquidation_possible' ? 'var(--danger)'
       : band === 'risky' ? 'var(--warning)' : 'var(--running)';
}

function aggressionCardInnerHTML(level) {
  if (!_aggData) return '';
  const row = (_aggData.table || []).find(t => t.level === level) || _aggData.current;
  if (!row) return '';
  const color = _bandColor(row.band);
  const amps = [];
  if (row.leverage_max > 1) amps.push(`레버리지 ${row.leverage_max}x`);
  if (row.take_profit_mode === 'trailing') amps.push('수익 달리기(트레일링)');
  if (row.pyramiding) amps.push('피라미딩');
  if (row.position_mult >= 1.3) amps.push(`포지션 ${row.position_mult}x`);
  if (!row.edge_gate_enforced) amps.push('⚠검증완화');
  return `
    <div class="section-box agg-card" style="border-left:3px solid ${color}">
      <div class="agg-head">
        <div>
          <span class="agg-title">투자 공격성</span>
          <span class="agg-badge" style="background:${color}1f;color:${color}">${row.level}단계 · ${row.band_kr}</span>
        </div>
        <div class="agg-mdd">예상 최대낙폭 <strong style="color:${row.est_mdd_pct<=-50?'var(--danger)':'var(--text-primary)'}">${row.est_mdd_pct}%</strong></div>
      </div>
      <input type="range" min="1" max="10" step="1" value="${level}" id="agg-slider"
        class="agg-slider" style="--c:${color}"
        oninput="onAggSlide(this.value)" onchange="onAggCommit(this.value)">
      <div class="agg-scale">
        <span class="green">1·안전</span><span class="amber">6·위험</span><span class="up">10·청산가능</span>
      </div>
      <div class="agg-note">${row.note}</div>
      ${amps.length ? `<div class="agg-amps">수익 증폭: ${amps.map(a=>`<span class="agg-amp">${a}</span>`).join('')}</div>` : ''}
      <div class="agg-strats">
        ${row.crypto_auto?'<span class="agg-on">코인</span>':'<span class="agg-off">코인</span>'}
        ${row.stock_auto?'<span class="agg-on">국내</span>':'<span class="agg-off">국내</span>'}
        ${row.overseas_auto?'<span class="agg-on">해외</span>':'<span class="agg-off">해외</span>'}
        ${row.leverage_etf?'<span class="agg-on">레버리지</span>':'<span class="agg-off">레버리지</span>'}
        ${row.inverse_etf?'<span class="agg-on">인버스</span>':'<span class="agg-off">인버스</span>'}
        <span id="agg-save-status" class="agg-status"></span>
      </div>
    </div>`;
}

function renderAggressionCard(level) {
  const el = document.getElementById('aggression-card');
  if (!el || !_aggData) return;
  const html = aggressionCardInnerHTML(level);
  if (html) { el.innerHTML = html; _aggRenderedLevel = level; }
}

function onAggSlide(v) {
  _aggTouch();                              // 조작 중 표시 (폴링 덮어쓰기 방지)
  // 슬라이더 요소는 유지하고 텍스트만 갱신 (드래그 끊김 방지)
  const lvl = parseInt(v, 10);
  const row = (_aggData?.table || []).find(t => t.level === lvl);
  if (!row) return;
  const c = _bandColor(row.band);
  const card = document.querySelector('.agg-card');
  if (card) card.style.borderLeftColor = c;
  const badge = document.querySelector('.agg-badge');
  if (badge) { badge.textContent = `${row.level}단계 · ${row.band_kr}`; badge.style.background = c + '1f'; badge.style.color = c; }
  const mdd = document.querySelector('.agg-mdd');
  if (mdd) mdd.innerHTML = `예상 최대낙폭 <strong style="color:${row.est_mdd_pct<=-50?'var(--danger)':'var(--text-primary)'}">${row.est_mdd_pct}%</strong>`;
  const note = document.querySelector('.agg-note');
  if (note) note.textContent = row.note;
}

async function onAggCommit(v) {
  _aggTouch();
  const lvl = parseInt(v, 10);
  const st = document.getElementById('agg-save-status');
  if (st) st.textContent = '저장 중...';
  try {
    const res = await POST('/api/aggression', { level: lvl });
    _aggData.level = res.level;
    if (res.current && _aggData.table) {
      const i = _aggData.table.findIndex(t => t.level === res.level);
      if (i >= 0) _aggData.table[i] = res.current;
    }
    const band = (res.current || {}).band;
    const warn = band === 'liquidation_possible' ? ' ⚠️청산 가능 구간!' : band === 'risky' ? ' ⚠️위험 구간' : '';
    toast(`공격성 ${lvl}단계 적용됨${warn}`, band === 'safe' ? 'success' : 'warning', 5000);
    if (st) st.textContent = '✓ 적용됨';
  } catch (e) {
    if (st) st.textContent = '저장 실패';
  }
}

// 실시간 이벤트 폭주 시 전체 재렌더를 묶어 깜빡임 방지 (디바운스)
let _dashRefreshTimer = null;
function scheduleDashboardRefresh(delay = 3000) {
  if (currentPage !== 'dashboard') return;
  if (_dashRefreshTimer) return;   // 이미 예약됨 → 합치기
  _dashRefreshTimer = setTimeout(() => { _dashRefreshTimer = null; loadDashboard(); }, delay);
}

async function loadDashboard() {
  if (currentPage !== 'dashboard') { clearInterval(dashInterval); return; }
  if (dashAnalyzing) return;

  // 한글 코인명 로드 (최초 1회, 이후 캐시)
  loadCoinNames();

  const [d, approval, kpos, kimchi, scoresRes, returnsData, openOrders, orderFailures, ospos] = await Promise.all([
    GET('/api/status'),
    GET('/api/approval').catch(() => ({ crypto: {}, stock: {} })),
    GET('/api/kstock/positions').catch(() => ({ exists: false, positions: [] })),
    GET('/api/kimchi-premium').catch(() => null),
    GET('/api/scalping/scores?limit=20').catch(() => ({ scores: [] })),
    GET(`/api/stats/returns?period=${_returnsPeriod}`).catch(() => null),
    GET('/api/orders/open?market=all').catch(() => ({ items: [] })),
    GET('/api/orders/failures?limit=5').catch(() => ({ items: [] })),
    GET('/api/overseas/positions').catch(() => ({ exists: false, positions: [] })),
  ]);
  // 수익률 캐시 갱신
  if (returnsData) _returnsCache[_returnsPeriod] = { ts: Date.now(), data: returnsData };
  const openOrderSection = buildOpenOrders(openOrders);
  const failuresSection = buildOrderFailures(orderFailures);

  const r = d.runner || {};
  const cryptoEx = cryptoExchangesFromStatus(d);
  const upbitEx = cryptoEx.upbit || {};
  const bithumbEx = cryptoEx.bithumb || {};
  const holdings = [...(upbitEx.holdings || []), ...(bithumbEx.holdings || [])];
  const stockHoldings = d.stock_holdings || [];
  const plan = d.last_plan || {};
  const thr  = d.thresholds || {};
  const cryptoCash = cryptoCashTotal(cryptoEx);
  // 종목별 동적 TP/SL 맵 (symbol → tpsl)
  const kposTpslMap = {};
  for (const p of (kpos.positions || [])) {
    if (p.symbol && p.tpsl) kposTpslMap[p.symbol] = p.tpsl;
  }

  updateRunnerBadge(r);
  updateSidebarAccount(d, approval);

  // 총 자산 계산
  // stock_total_equity: KIS tot_evlu_amt — 예수금 + 주식 평가액 + T+2 미결제 매도대금 포함
  // stock_balance_krw만 쓰면 매도 직후 T+2 대기금이 빠져 자산이 과소 표시됨
  const coinVal  = holdings.reduce((s, h) => s + (h.valuation_krw || 0), 0);
  const stockVal = stockHoldings.reduce((s, h) => s + (h.market_value || 0), 0);
  const krwTotal = cryptoCash;
  const kisTotal = d.stock_total_equity > 0
    ? d.stock_total_equity
    : stockVal + (d.stock_balance_krw || 0);
  const totalAsset = coinVal + krwTotal + kisTotal;

  const runColor = r.running ? (r.paused ? 'warning' : 'ok') : 'danger';
  const runState2 = r.running ? (r.paused ? 'paused' : 'running') : 'stopped';
  const runLabel = `<span class="dot-inline ${runState2}"></span>${r.running ? (r.paused ? '일시정지' : '실행 중') : '중지됨'}`;
  const cryptoPnl = holdings.reduce((s, h) => s + (h.pnl_pct || 0), 0);
  const stockPnl  = stockHoldings.reduce((s, h) => s + (h.pnl_pct || 0), 0);

  // 보유 평가손익(미실현) — 수익률 분석 카드의 "보유 평가" 행에 사용
  _holdingPnl = {
    coin:  { pct: holdings.length      ? cryptoPnl / holdings.length      : null, count: holdings.length },
    stock: { pct: stockHoldings.length ? stockPnl  / stockHoldings.length : null, count: stockHoldings.length },
  };

  // 리스크 알림 감지
  const riskAlerts = [];
  holdings.forEach(h => {
    if (h.pnl_pct != null && thr.stop_loss_pct != null && h.pnl_pct <= thr.stop_loss_pct * 0.8) {
      riskAlerts.push({ market: h.market, pnl_pct: h.pnl_pct, sl_pct: thr.stop_loss_pct });
    }
  });

  const holdingTable = (rows, cols) => rows.length === 0
    ? '<div class="text-muted" style="padding:12px 0">보유 없음</div>'
    : `<div class="table-wrap"><table>
        <thead><tr>${cols.map(c => `<th>${c}</th>`).join('')}</tr></thead>
        <tbody>${rows}</tbody>
      </table></div>`;

  // 승인 배너
  const approvalBanners = buildApprovalBanners(approval);

  // 리스크 배너
  const riskBanner = riskAlerts.length > 0 ? `
    <div class="alert-banner alert-danger">
      <div class="alert-banner-body">
        ${riskAlerts.map(a => `<div><span class="inline-warn">손절 근접</span> <strong>${a.market}</strong> 기준 ${fmt_pct(a.sl_pct)} &nbsp;현재 <span class="text-down">${fmt_pct(a.pnl_pct)}</span></div>`).join('')}
      </div>
      <div class="alert-banner-actions">
        <button class="btn btn-danger btn-sm" onclick="triggerStopLoss()">손절 분석 실행</button>
        <span class="alert-action-hint">분석 후 승인 배너에서 확인·거부 가능</span>
      </div>
    </div>` : '';

  // ── NEXORA 스타일 추가 요소 ──
  const scores = scoresRes.scores || [];

  // ② 총 자산 면적 차트
  const totalHist = (() => { try { return JSON.parse(localStorage.getItem('ds_kpi') || '{}').total || []; } catch { return []; } })();
  const assetDelta = totalHist.length >= 2
    ? ((totalHist[totalHist.length-1] - totalHist[0]) / (totalHist[0] || 1) * 100) : null;
  const assetChart = totalHist.length >= 2 ? `
    <div class="asset-chart-section">
      <div class="asset-chart-header">
        <span class="asset-chart-value">${fmt_krw(Math.round(totalAsset))}</span>
        ${assetDelta != null ? `<span class="asset-chart-delta ${assetDelta > 0 ? 'up' : assetDelta < 0 ? 'down' : 'flat'}">${assetDelta > 0 ? '+' : ''}${assetDelta.toFixed(2)}%</span>` : ''}
        <span class="asset-chart-label">총 자산 추이</span>
      </div>
      ${buildAssetAreaChart(totalHist, { height: 60, color: 'var(--accent)' })}
    </div>` : '';

  // ① 포트폴리오 도넛
  const donutSection = buildPortfolioDonut(holdings, stockHoldings, {
    upbit: Number(upbitEx.balance?.total ?? upbitEx.balance?.available ?? 0),
    bithumb: Number(bithumbEx.balance?.total ?? bithumbEx.balance?.available ?? 0),
  }, d.stock_balance_krw || 0);

  // ⑤ 수익률 그라디언트 — 코인/주식 행
  const cryptoCols = ['종목', '수량', '평단', '현재가', '평가금', '수익률'];
  const upbitRows = buildCryptoHoldingRows(upbitEx.holdings || []);
  const bithumbRows = buildCryptoHoldingRows(bithumbEx.holdings || []);
  const signalPanel = buildSignalPanel(scores, plan);

  const stockRows = stockHoldings.map(h => {
    const tpsl = kposTpslMap[h.symbol];
    const tpslCell = tpsl
      ? buildTpslBar(h.pnl_pct, tpsl.tp_pct, tpsl.sl_pct, tpsl.grade)
      : '<span class="text-muted">-</span>';
    return `<tr>
    <td><strong>${h.name}</strong><br><span style="font-size:11px;color:var(--text-muted)">${h.symbol}</span></td>
    <td>${h.quantity}주</td>
    <td>${fmt_krw(h.avg_price)}</td>
    <td>${fmt_krw(h.current_price)}</td>
    <td>${fmt_krw(h.market_value)}</td>
    <td>${gradPct(h.pnl_pct)}</td>
    <td>${tpslCell}</td>
  </tr>`;
  }).join('');

  document.getElementById('page-dashboard').innerHTML = `
    <div class="page-header">
      <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px">
        <div>
          <h1 class="page-title">대시보드</h1>
          <div class="page-subtitle">마지막 갱신: ${fmt_time(d.server_time)}</div>
        </div>
        <div class="quick-bar-inline ${r.paused ? 'paused-state' : ''}">
          <label class="toggle" title="${r.paused ? '클릭하면 매매 재개' : '클릭하면 전체 일시정지'}">
            <input type="checkbox" id="dash-toggle-pause" ${r.paused ? 'checked' : ''}
              onchange="togglePauseDash(this.checked)" ${!r.running ? 'disabled' : ''}>
            <span class="toggle-slider"></span>
          </label>
          <span class="quick-bar-label ${r.paused ? 'text-warning' : ''}" style="font-size:12px">${r.paused ? '<span class="dot-inline paused"></span> 일시정지 중' : '전체 일시정지'}</span>
          <div class="quick-inline-divider"></div>
          <button class="btn btn-analyze-coin" onclick="triggerRecommendDash('crypto')">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/><line x1="2" y1="20" x2="22" y2="20"/></svg>
            코인
          </button>
          <button class="btn btn-analyze-stock" onclick="triggerRecommendDash('stock')">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>
            국내주식
          </button>
          <button class="btn btn-analyze-overseas" onclick="triggerRecommendDash('overseas')">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>
            해외주식
          </button>
          <span id="dash-recommend-status" class="quick-bar-status" style="font-size:11px"></span>
        </div>
        <div class="dash-plan-detail"></div>
      </div>
    </div>

    ${buildKpiStrip(d, holdings, stockHoldings, totalAsset, returnsData)}
    ${returnsData ? buildReturnsSection(returnsData) : ''}
    ${assetChart}

    ${approvalBanners}
    ${riskBanner}
    ${openOrderSection}
    ${failuresSection}

    <div id="aggression-card">${_aggData ? aggressionCardInnerHTML(_aggData.level) : ''}</div>

    <div class="dash-main-layout">
      <div class="dash-main-left">
        <div class="status-cards">
          <!-- 러너 상태 -->
          <div class="status-card ${runColor}">
            <div class="status-card-label">러너 상태${helpBtn('runner_status')}</div>
            <div class="status-card-value">${runLabel}</div>
            <div class="status-card-row">
              <span class="sc-key">업타임</span><span class="sc-val">${fmt_uptime(r.uptime_sec)}</span>
            </div>
            <div class="status-card-row">
              <span class="sc-key">오늘 주문</span><span class="sc-val">${r.orders_today || 0}건</span>
            </div>
          </div>

          ${buildCryptoAssetCard(upbitEx, r, d.crypto_broker)}
          ${buildCryptoAssetCard(bithumbEx, r, d.crypto_broker)}

          <!-- 주식 자산 (보유현황 통합) -->
          <div class="status-card info">
            <div class="status-card-label">주식 자산 ${exchBadge('kis')}</div>
            <div class="status-card-value">${fmt_krw(Math.round(kisTotal))}</div>
            <div class="status-card-row">
              <span class="sc-key">예수금</span><span class="sc-val">${fmt_krw(d.stock_balance_krw)}</span>
            </div>
            ${(d.stock_total_equity > 0 && d.stock_total_equity > stockVal + (d.stock_balance_krw || 0) + 100)
              ? `<div class="status-card-row"><span class="sc-key" style="color:var(--warning)">정산대기</span><span class="sc-val" style="color:var(--warning)">${fmt_krw(Math.round(d.stock_total_equity - stockVal - (d.stock_balance_krw || 0)))}</span></div>`
              : `<div class="status-card-row"><span class="sc-key">출금가능</span><span class="sc-val">${fmt_krw(d.stock_withdrawable_krw)}</span></div>`}
            <div class="status-card-divider"></div>
            <div class="status-card-row">
              <span class="sc-key">보유 주식</span>
              <span class="sc-val ${stockHoldings.length > 0 ? 'text-up' : ''}">
                ${stockHoldings.length}종목${stockHoldings.length ? ` &nbsp;${gradPct(stockPnl / stockHoldings.length)}` : ''}
              </span>
            </div>
          </div>

          <!-- 해외 자산 (보유현황 통합) -->
          ${(() => {
            const op = ospos || {}; const ps = op.positions || [];
            const totUsd = op.total_usd_value || 0, pnlUsd = op.total_usd_pnl || 0;
            const pnlPct = (totUsd - pnlUsd) > 0 ? (pnlUsd / (totUsd - pnlUsd)) * 100 : 0;
            return `<div class="status-card info">
              <div class="status-card-label">해외 자산 ${exchBadge('kis')}</div>
              <div class="status-card-value">${fmt_krw(Math.round(op.total_krw_value || 0))}</div>
              <div class="status-card-row"><span class="sc-key">평가 (USD)</span><span class="sc-val">$${totUsd.toLocaleString(undefined,{maximumFractionDigits:2})}</span></div>
              <div class="status-card-row"><span class="sc-key">매수가능</span><span class="sc-val">$${(op.cash_usd||0).toLocaleString(undefined,{maximumFractionDigits:2})}</span></div>
              <div class="status-card-divider"></div>
              <div class="status-card-row">
                <span class="sc-key">보유 종목</span>
                <span class="sc-val ${ps.length ? (pnlUsd>=0?'text-up':'text-down') : ''}">${ps.length}종목${ps.length ? ` &nbsp;${gradPct(pnlPct)}` : ''}</span>
              </div>
            </div>`;
          })()}

          <!-- 김치프리미엄 (인라인 슬림) -->
          ${kimchi ? (() => {
            const prems = Object.values(kimchi.premiums || {});
            const avg = prems.length ? prems.reduce((s,p)=>s+(p.premium_pct||0),0)/prems.length : 0;
            const cls = avg > 1 ? 'text-up' : avg < -1 ? 'text-down' : '';
            const top3 = prems.slice(0,3).map(p=>`<span class="sc-key" style="margin-right:4px">${p.symbol}</span><span class="sc-val ${(p.premium_pct||0)>0?'text-up':'text-down'}" style="margin-right:8px">${(p.premium_pct||0)>0?'+':''}${(p.premium_pct||0).toFixed(2)}%</span>`).join('');
            return `<div class="status-card" style="border-left-color:var(--accent-2)">
              <div class="status-card-label">김치프리미엄</div>
              <div class="status-card-value ${cls}">${avg>0?'+':''}${avg.toFixed(2)}%</div>
              <div class="status-card-row" style="flex-wrap:wrap;gap:2px 0">${top3}</div>
              <div class="status-card-row"><span class="sc-key">USD/KRW</span><span class="sc-val">${(kimchi.usd_krw_rate||0).toLocaleString()}</span></div>
            </div>`;
          })() : ''}</div>

        <div class="dash-grid">
          <div class="section-box">
            <div class="section-title">코인 포지션 ${exchBadge('upbit')}${helpBtn('holding_pnl')}</div>
            ${holdingTable(upbitRows, ['종목', '수량', '평균단가', '현재가', '평가금액', `수익률${helpBtn('holding_pnl')}`])}
          </div>
          <div class="section-box">
            <div class="section-title">코인 포지션 ${exchBadge('bithumb')}${helpBtn('holding_pnl')}</div>
            ${holdingTable(bithumbRows, ['종목', '수량', '평균단가', '현재가', '평가금액', `수익률${helpBtn('holding_pnl')}`])}
          </div>
          <div class="section-box">
            <div class="section-title">국내주식 포지션 ${exchBadge('kis')}${helpBtn('holding_pnl')}</div>
            ${holdingTable(stockRows, ['종목', '수량', '평균단가', '현재가', '평가금액', `수익률${helpBtn('holding_pnl')}`, `동적 TP/SL${helpBtn('tp_sl')}`])}
          </div>
          <div class="section-box">
            <div class="section-title">해외주식 포지션 ${exchBadge('kis')}${helpBtn('holding_pnl')}</div>
            ${(() => {
              const ps = (ospos && ospos.positions) || [];
              if (!ps.length) return '<div class="text-muted" style="font-size:12px;padding:8px 2px">보유 없음</div>';
              const rows = ps.map(p => `<tr>
                <td><strong style="font-size:12px">${escHtml(p.name || p.symbol)}</strong> <span style="font-size:10px;color:var(--text-muted)">${escHtml(p.symbol)}</span></td>
                <td style="text-align:right">${Number(p.quantity||0).toLocaleString()}</td>
                <td style="text-align:right">$${(p.avg_price_usd||0).toLocaleString(undefined,{maximumFractionDigits:4})}</td>
                <td style="text-align:right">$${(p.cur_price_usd||0).toLocaleString(undefined,{maximumFractionDigits:4})}</td>
                <td style="text-align:right">${fmt_krw(p.value_krw)} <span style="font-size:10px;color:var(--text-muted)">($${(p.value_usd||0).toFixed(2)})</span></td>
                <td style="text-align:right" class="${(p.pnl_pct||0)>=0?'text-up':'text-down'}">${(p.pnl_pct||0)>=0?'+':''}${(p.pnl_pct||0).toFixed(2)}%</td>
              </tr>`).join('');
              return `<div class="table-wrap"><table>
                <thead><tr><th>종목</th><th style="text-align:right">수량</th><th style="text-align:right">평균단가</th><th style="text-align:right">현재가</th><th style="text-align:right">평가금액</th><th style="text-align:right">수익률</th></tr></thead>
                <tbody>${rows}</tbody></table></div>`;
            })()}
          </div>
        </div>

        ${plan.market ? `
        <div class="section-box">
          <div class="section-title">마지막 플랜${helpBtn('last_plan')}</div>
          <div class="flex-row" style="flex-wrap:wrap; gap:12px; margin-bottom:10px">
            <span><strong>${plan.market}</strong></span>
            <span class="badge ${plan.side === 'buy' ? 'badge-success' : 'badge-danger'}">${(plan.side || '').toUpperCase()}</span>
            <span class="badge badge-neutral">${plan.status || ''}</span>
            ${plan.sell_trigger ? `<span class="badge badge-warning">${plan.sell_trigger}</span>` : ''}
            ${plan.pnl_pct != null ? `<span>${gradPct(plan.pnl_pct)}</span>` : ''}
            ${plan.macro_regime ? `<span class="badge badge-info">레짐: ${plan.macro_regime}</span>` : ''}
          </div>
          ${plan.technical_score != null || plan.macro_score != null || plan.final_score != null ? `
          <div class="score-row">
            <div class="score-item"><span class="score-label">기술적</span>${score_bar(plan.technical_score, 100)}</div>
            <div class="score-item"><span class="score-label">거시</span>${score_bar(plan.macro_score, 100)}</div>
            <div class="score-item"><span class="score-label">최종</span>${score_bar(plan.final_score, 100)}</div>
          </div>` : ''}
          <div class="text-muted mt-8" style="font-size:12px">${escHtml(plan.reason || '')}</div>
          <div class="text-muted mt-4" style="font-size:11px">${fmt_time(plan.created_at)}</div>
        </div>` : ''}
      </div>

      <div class="signal-panel-wrap">${signalPanel}</div>
    </div>
  `;

  // 공격성 카드 채우기 (innerHTML 설정 후)
  loadAggressionCard();
}

function buildApprovalBanners(approval) {
  let html = '';
  const ca = approval.crypto || {};
  const sa = approval.stock || {};

  if (ca.show_banner || ca.pending) {
    const expired = !ca.pending && ca.status === 'EXPIRED';
    const isSell  = ca.side === 'sell';
    const icon    = (ca.market || '?').replace('-KRW','').replace('-USDT','').slice(0, 2);
    const cardCls = expired ? 'expired-card' : isSell ? 'sell-card' : 'buy-card';
    const iconCls = expired ? 'expired' : isSell ? 'sell' : '';

    const gsqs = ca.gsqs ?? ca.final_score ?? null;
    const pwin = ca.pwin ?? ca.win_rate ?? null;
    const gFill = gsqs != null ? Math.min(gsqs, 100) : 0;
    const gCls  = gsqs >= 72 ? 'high' : gsqs >= 55 ? 'mid' : 'low';

    html += `
    <div class="approval-card ${cardCls}">
      <div class="approval-card-header">
        <div class="asset-icon ${iconCls}">${icon}</div>
        <div class="approval-card-meta">
          <div class="approval-card-name">
            ${escHtml(ca.display_name || ca.market || '—')}
            ${(() => { const k = coinKoreanName(ca.market); return k ? `<span class="approval-kor-name">${escHtml(k)}</span>` : ''; })()}
            <span class="side-badge ${isSell ? 'sell' : 'buy'}">${isSell ? 'SELL' : 'BUY'}</span>
            ${expired ? '<span class="badge badge-neutral" style="font-size:10px">만료됨</span>' : ''}
          </div>
          <div class="approval-card-time">${expired ? '만료됨' : '유효: ' + fmt_time(ca.expires_at)}</div>
        </div>
      </div>
      <div class="approval-card-body">
        <div class="approval-metrics">
          <div class="approval-metric">
            <div class="approval-metric-label">주문금액</div>
            <div class="approval-metric-value">${fmt_krw(ca.krw_amount)}</div>
          </div>
          <div class="approval-metric">
            <div class="approval-metric-label">현재가</div>
            <div class="approval-metric-value">${fmt_krw(ca.current_price)}</div>
          </div>
          <div class="approval-metric">
            <div class="approval-metric-label">승률</div>
            <div class="approval-metric-value">${pwin != null ? (pwin * 100).toFixed(0) + '%' : '—'}</div>
          </div>
        </div>
        ${gsqs != null ? `
        <div class="approval-score-row">
          <span class="approval-score-label">GSQS</span>
          <div class="approval-score-track"><div class="approval-score-fill ${gCls}" style="width:${gFill}%"></div></div>
          <span class="approval-score-num">${gsqs.toFixed(0)}</span>
        </div>` : ''}
        ${ca.reason ? `<div class="approval-reason-text">${escHtml(ca.reason.slice(0, 200))}</div>` : ''}
        ${(() => {
          // 체결 품질 약점 표시
          const flags = [];
          if (ca.spread_pct != null) {
            const ok = ca.spread_pct <= 0.3;
            flags.push(`<span class="exec-flag ${ok ? 'ok' : 'warn'}">스프레드 ${ca.spread_pct.toFixed(2)}%</span>`);
          }
          if (ca.bid_ask_ratio != null) {
            const ok = ca.bid_ask_ratio >= 1.0;
            flags.push(`<span class="exec-flag ${ok ? 'ok' : 'warn'}">매수벽 ${ca.bid_ask_ratio.toFixed(2)}x</span>`);
          }
          if (ca.regime) {
            flags.push(`<span class="exec-flag info">레짐: ${ca.regime}</span>`);
          }
          return flags.length ? `<div class="exec-flags">${flags.join('')}</div>` : '';
        })()}
      </div>
      <div class="approval-card-actions">
        ${expired
          ? `<button class="btn btn-primary btn-sm" onclick="triggerRecommendDash('crypto')">재분석</button>
             <button class="btn btn-ghost btn-sm" onclick="doApproval('crypto','reject','${escHtml(ca.token)}')">닫기</button>`
          : `<button class="btn btn-success btn-sm" id="approve-btn-crypto" onclick="doApproval('crypto','approve','${escHtml(ca.token)}')">승인</button>
             <button class="btn btn-danger btn-sm"  onclick="doApproval('crypto','reject','${escHtml(ca.token)}')">거부</button>`
        }
      </div>
    </div>`;
  }

  if (sa.pending) {
    const warnCount = (sa.plan_warnings || []).length;
    html += `
    <div class="approval-card stock-card">
      <div class="approval-card-header">
        <div class="asset-icon stock">KR</div>
        <div class="approval-card-meta">
          <div class="approval-card-name">
            국내주식 주문 플랜
            <span class="side-badge buy">BUY</span>
            ${warnCount > 0 ? `<span class="badge badge-warning">경고 ${warnCount}</span>` : ''}
          </div>
          <div class="approval-card-time">유효: ${fmt_time(sa.expires_at)}</div>
        </div>
      </div>
      <div class="approval-card-body">
        <div class="approval-metrics">
          <div class="approval-metric">
            <div class="approval-metric-label">주문 건수</div>
            <div class="approval-metric-value">${sa.order_count ?? '—'}건</div>
          </div>
          <div class="approval-metric">
            <div class="approval-metric-label">총 주문금액</div>
            <div class="approval-metric-value">${fmt_krw(sa.total_order_value)}</div>
          </div>
          <div class="approval-metric">
            <div class="approval-metric-label">경고</div>
            <div class="approval-metric-value ${warnCount > 0 ? 'text-warning' : ''}">${warnCount}개</div>
          </div>
        </div>
        ${sa.manual_live_approve_command
          ? `<div class="approval-reason-text" style="font-family:var(--font-mono);font-size:11px">${escHtml(sa.manual_live_approve_command)}</div>`
          : ''}
      </div>
      <div class="approval-card-actions">
        <button class="btn btn-success btn-sm" onclick="doApproval('stock','approve','${escHtml(sa.token)}')">승인</button>
        <button class="btn btn-danger btn-sm"  onclick="doApproval('stock','reject','${escHtml(sa.token)}')">거부</button>
        <button class="btn btn-ghost btn-sm"   onclick="doApproval('stock','halt','${escHtml(sa.token)}')">오늘 중단</button>
      </div>
    </div>`;
  }
  return html;
}

async function doApproval(type, action, token) {
  const labels = { approve: '승인', reject: '거부', halt: '오늘 중단' };
  if (action === 'approve' && !confirm(`${labels[action]}하시겠습니까?\n체결 품질은 실행 시점에 재검사됩니다.`)) return;
  if (action !== 'approve' && !confirm(`${labels[action] || action}하시겠습니까?`)) return;

  // 승인 버튼 로딩 상태
  const approveBtn = document.getElementById(`approve-btn-${type}`);
  if (approveBtn) { approveBtn.disabled = true; approveBtn.textContent = '처리 중...'; }

  try {
    const res = await POST('/api/approval/action', { type, action, token });
    if (!res.ok) {
      // 체결 품질 실패 — 경고 토스트 (더 길게 표시)
      const isQualityFail = res.message && (res.message.includes('매수벽') || res.message.includes('스프레드') || res.message.includes('체결 불가'));
      toast(res.message || '처리 실패', 'error', isQualityFail ? 6000 : 3000);
      if (approveBtn) { approveBtn.disabled = false; approveBtn.textContent = '승인'; }
    } else {
      toast(res.message, 'success');
      await loadDashboard();
    }
  } catch (e) {
    toast('요청 실패: ' + e.message, 'error');
    if (approveBtn) { approveBtn.disabled = false; approveBtn.textContent = '승인'; }
  }
}

// ══════════════════════════════════════════════
// 러너 제어
// ══════════════════════════════════════════════
let runnerInterval = null;

async function renderRunner() {
  clearInterval(runnerInterval);
  const el = document.getElementById('page-runner');
  el.innerHTML = '<div class="page-header"><h1 class="page-title">러너 제어</h1></div><div class="text-muted">상태 로딩 중...</div>';
  await loadRunner();
  runnerInterval = setInterval(loadRunner, 5000);
}

async function loadRunner() {
  if (currentPage !== 'runner') { clearInterval(runnerInterval); return; }
  const d = await GET('/api/status');
  const r = d.runner || {};
  updateRunnerBadge(r);

  const runState = r.running ? (r.paused ? 'paused' : 'running') : 'stopped';
  const stateLabelText = { running: '실행 중', stopped: '중지됨', paused: '일시정지' }[runState];
  const stateLabel = stateLabelText;  // 점은 status-dot 하나만 (중복 제거)

  document.getElementById('page-runner').innerHTML = `
    <div class="page-header">
      <h1 class="page-title">러너 제어</h1>
      <div class="page-subtitle">crypto-auto-runner 프로세스 관리 (${r.source || 'unknown'})</div>
    </div>

    <div class="status-row">
      <div class="status-indicator">
        <div class="status-dot ${runState}"></div>
        <span class="status-label">${stateLabel}</span>
      </div>
      ${r.pid ? `<span class="status-meta">PID ${r.pid}</span>` : ''}
      ${r.uptime_sec ? `<span class="status-meta">업타임 ${fmt_uptime(r.uptime_sec)}</span>` : ''}
      ${r.paused && r.pause_reason ? `<span class="badge badge-warning">${r.pause_reason}</span>` : ''}
    </div>

    <div class="control-card">
      <div class="control-card-title">프로세스 제어</div>
      <div class="btn-group">
        <button class="btn btn-success" onclick="runnerAction('start')"   ${r.running ? 'disabled' : ''}>▶ 시작</button>
        <button class="btn btn-danger"  onclick="runnerAction('stop')"    ${!r.running ? 'disabled' : ''}>⏹ 중지</button>
        <button class="btn btn-warning" onclick="runnerAction('restart')">↺ 재시작</button>
      </div>
    </div>

    <div class="control-card">
      <div class="control-card-title">일시정지 (프로세스 유지, 매매만 중단)</div>
      <div class="form-row-inline">
        <div class="label-group">
          <div class="main-label">전체 일시정지</div>
          <div style="font-size:12px;color:var(--text-muted)">다음 틱부터 BUY/SELL 모두 건너뜀</div>
        </div>
        <label class="toggle">
          <input type="checkbox" id="toggle-pause" ${r.paused ? 'checked' : ''}
            onchange="togglePause(this.checked)" ${!r.running ? 'disabled' : ''}>
          <span class="toggle-slider"></span>
        </label>
      </div>
    </div>

    <div class="control-card">
      <div class="control-card-title">분석 요청 (텔레그램 메뉴 기능)</div>
      <div class="btn-group">
        <button class="btn btn-analyze-coin" onclick="triggerRecommendDash('crypto')">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/><line x1="2" y1="20" x2="22" y2="20"/></svg>
          코인
        </button>
        <button class="btn btn-analyze-stock" onclick="triggerRecommendDash('stock')">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>
          국내주식
        </button>
        <button class="btn btn-analyze-overseas" onclick="triggerRecommendDash('overseas')">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>
          해외주식
        </button>
      </div>
      <div id="dash-recommend-status" class="text-muted mt-8" style="font-size:12px"></div>
      <div class="dash-plan-detail"></div>
    </div>

    <div class="section-box">
      <div class="section-title">오늘 통계</div>
      <div class="cards" style="margin-bottom:0">
        <div class="card"><div class="card-label">주문 수</div><div class="card-value">${r.orders_today ?? 0}</div></div>
        <div class="card"><div class="card-label">매수 금액</div><div class="card-value" style="font-size:15px">${fmt_krw(r.buy_krw_today)}</div></div>
        <div class="card"><div class="card-label">매도 금액</div><div class="card-value" style="font-size:15px">${fmt_krw(r.sell_krw_today)}</div></div>
        <div class="card"><div class="card-label">순현금</div><div class="card-value" style="font-size:15px">${fmt_krw((r.sell_krw_today || 0) - (r.buy_krw_today || 0))}</div></div>
        <div class="card"><div class="card-label">마지막 종목</div><div class="card-value" style="font-size:14px">${r.last_market || '-'}</div></div>
        <div class="card"><div class="card-label">일자 키</div><div class="card-value" style="font-size:13px">${r.daily_key || '-'}</div></div>
      </div>
    </div>
  `;
}

async function runnerAction(action) {
  const btn = event.target;
  btn.disabled = true;
  const labels = { start: '시작', stop: '중지', restart: '재시작' };
  btn.textContent = labels[action] + '...';
  try {
    const res = await POST(`/api/runner/${action}`);
    toast(res.message, res.ok ? 'success' : 'error');
    await loadRunner();
  } catch (e) {
    toast('요청 실패: ' + e.message, 'error');
  }
}

async function togglePause(paused) {
  const res = await POST('/api/runner/pause', { paused, reason: paused ? '웹UI 일시정지' : '' });
  toast(res.message, res.ok ? 'success' : 'error');
  await loadRunner();
}

async function togglePauseDash(paused) {
  const res = await POST('/api/runner/pause', { paused, reason: paused ? '웹UI 일시정지' : '' });
  toast(res.message, res.ok ? 'success' : 'error');
  await loadDashboard();
}

async function triggerRecommend(type) {
  const statusEl = document.getElementById('recommend-status');
  if (statusEl) statusEl.textContent = '분석 요청 중...';
  try {
    const res = await POST('/api/runner/recommend', { type });
    toast(res.message, res.ok ? 'success' : 'error');
    if (statusEl) statusEl.textContent = res.ok ? '완료 — 대시보드에서 승인/거부하세요' : res.message;
  } catch (e) {
    if (statusEl) statusEl.textContent = '오류: ' + e.message;
    toast('요청 실패', 'error');
  }
}

async function triggerStopLoss() {
  if (!confirm('손절 분석을 실행합니다.\n분석 결과는 승인 배너에서 확인 후 직접 승인/거부할 수 있습니다.\n계속하시겠습니까?')) return;
  await triggerRecommendDash('crypto');
}

function renderPlanDetail(plan) {
  const els = document.querySelectorAll('.dash-plan-detail');
  if (!els.length) return;
  if (!plan || !plan.orders || !plan.orders.length) { els.forEach(e => e.innerHTML = ''); return; }
  const rows = plan.orders.map(o => {
    const sideColor = o.side === '매수' ? '#ef4444' : '#3b82f6';
    const unit = o.price_unit || '₩';
    const priceStr = (o.price != null)
      ? (unit === '$' ? `$${Number(o.price).toLocaleString(undefined,{maximumFractionDigits:2})}`
                      : `${Number(o.price).toLocaleString()}원`) : '-';
    const qtyStr = (o.qty != null) ? Number(o.qty).toLocaleString() : '-';
    const amtStr = (o.amount_krw != null) ? `${Number(o.amount_krw).toLocaleString()}원` : '-';
    const usdExtra = (o.amount_usd != null) ? ` <span style="color:var(--text-muted)">($${Number(o.amount_usd).toLocaleString()})</span>` : '';
    const score = (o.score != null) ? `점수 ${o.score}` : '';
    const tp = (o.tp_pct) ? ` · 익절 +${o.tp_pct}%` : '';
    const sl = (o.sl_pct) ? ` · 손절 ${o.sl_pct}%` : '';
    return `<div style="padding:8px 10px;border:1px solid var(--border);border-radius:8px;margin-top:6px;background:var(--bg-elevated)">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:8px">
        <span><b>${o.name || o.symbol}</b> <span style="color:var(--text-muted);font-size:11px">${o.symbol}</span></span>
        <span style="color:${sideColor};font-weight:700">${o.side}</span>
      </div>
      <div style="font-size:12px;margin-top:4px">${qtyStr}주 × ${priceStr} = <b>${amtStr}</b>${usdExtra}</div>
      <div style="font-size:11px;color:var(--text-muted);margin-top:3px">${score}${tp}${sl}</div>
      ${o.reason ? `<div style="font-size:11px;color:var(--text-muted);margin-top:3px">${o.reason}</div>` : ''}
    </div>`;
  }).join('');
  const html = `<div style="margin-top:10px">
    <div style="font-size:12px;font-weight:700;display:flex;justify-content:space-between">
      <span>📋 생성된 매매계획 (${plan.kind} · ${plan.orders.length}건)</span>
      <span>합계 ${Number(plan.total_krw||0).toLocaleString()}원</span>
    </div>
    ${rows}
    <div style="font-size:11px;color:var(--text-muted);margin-top:6px">※ 무승인 자동매매 ON이면 러너가 다음 틱에 자동 실행합니다.</div>
  </div>`;
  els.forEach(e => e.innerHTML = html);
}

async function triggerRecommendDash(type) {
  const label = type === 'crypto' ? '코인' : type === 'overseas' ? '해외주식' : '국내주식';
  dashAnalyzing = true;
  document.querySelectorAll('.quick-analyze-group .btn').forEach(b => b.disabled = true);
  const s0 = document.getElementById('dash-recommend-status');
  if (s0) { s0.textContent = `⏳ ${label} 분석 중...`; s0.className = 'quick-bar-status'; }
  renderPlanDetail(null);
  const loadingToast = toastLoading(`⏳ ${label} 분석 중... (최대 3분 소요)`);
  let msg = '', cls = '', planToShow = null;
  try {
    const res = await POST('/api/runner/recommend', { type });
    msg = res.message || (res.ok ? '분석 완료' : '분석 실패');
    cls = 'quick-bar-status ' + (res.ok ? (res.has_plan ? 'text-ok' : '') : 'text-danger');
    planToShow = res.plan || null;
    loadingToast.update(msg, res.ok ? 'success' : 'error', 8000);
    dashAnalyzing = false;
    await loadDashboard();
  } catch (e) {
    msg = '오류: ' + e.message;
    cls = 'quick-bar-status text-danger';
    loadingToast.update('요청 실패: ' + e.message, 'error', 8000);
  } finally {
    dashAnalyzing = false;
    document.querySelectorAll('.quick-analyze-group .btn').forEach(b => b.disabled = false);
    const s = document.getElementById('dash-recommend-status');
    if (s && msg) { s.textContent = msg; s.className = cls; }
    renderPlanDetail(planToShow);  // loadDashboard 재렌더 이후에 표시
  }
}

// ══════════════════════════════════════════════
// 환경설정
// ══════════════════════════════════════════════
let settingsData = null;
let activeSettingsTab = null;

async function renderSettings() {
  const el = document.getElementById('page-settings');
  el.innerHTML = '<div class="page-header"><h1 class="page-title">환경설정</h1></div><div class="text-muted">로딩 중...</div>';
  try {
    const data = await GET('/api/settings');
    settingsData = data;
    const firstTab = Object.keys(data.sections)[0];
    if (!activeSettingsTab) activeSettingsTab = firstTab;
    updateAutoExecBanner(data);
    renderSettingsPage(data);
  } catch (e) {
    el.innerHTML = `<div class="page-header"><h1 class="page-title">환경설정</h1></div>
      <div class="section-box" style="color:var(--danger)">오류: ${e.message}</div>`;
  }
}

function renderSettingsPage(data) {
  try {
    const sections = data.sections;
    const labels   = data.section_labels;
    const el = document.getElementById('page-settings');

    const tabsHtml = Object.entries(labels).map(([key, label]) =>
      `<button class="tab-btn ${key === activeSettingsTab ? 'active' : ''} ${key === 'private' ? 'private-tab' : ''}"
        onclick="switchSettingsTab('${key}')">${label}</button>`
    ).join('');

    const panelsHtml = Object.entries(sections).map(([key, items]) => `
      <div class="tab-panel ${key === activeSettingsTab ? 'active' : ''}" id="settings-tab-${key}">
        ${renderSettingsSection(items, key)}
      </div>
    `).join('');

    el.innerHTML = `
      <div class="page-header">
        <h1 class="page-title">환경설정</h1>
        <div class="page-subtitle">「코인 (거래소)」 탭에서 거래소를 선택하면 해당 API 키·Dry Run 설정이 표시됩니다. 저장 후 START.bat 재시작.</div>
      </div>
      <div class="section-box">
        <div class="tabs">${tabsHtml}</div>
        ${panelsHtml}
        <hr class="divider">
        <div class="flex-row">
          <div class="spacer"></div>
          <button class="btn btn-ghost" onclick="renderSettings()">↺ 초기화</button>
          <button class="btn btn-primary" onclick="saveSettings()">저장</button>
        </div>
      </div>
    `;
  } catch(e) {
    document.getElementById('page-settings').innerHTML =
      `<div class="page-header"><h1 class="page-title">환경설정</h1></div>
       <div class="section-box" style="color:var(--danger)">렌더링 오류: ${e.message}</div>`;
  }
}

const _PRIVATE_GROUPS = ["KIS API", "Telegram", "알림 서비스"];

function renderSettingsSection(items, sectionKey) {
  if (sectionKey === 'private') return renderPrivateSection(items);
  if (sectionKey === 'upbit') return renderCryptoExchangeSection(items);
  return renderItemList(items);
}

function _selectedCryptoBroker() {
  const sel = document.querySelector('[data-key="CRYPTO_BROKER"]');
  if (sel && sel.value) return String(sel.value).toLowerCase();
  const item = (settingsData?.sections?.upbit || []).find(i => i.key === 'CRYPTO_BROKER');
  return String(item?.value || 'upbit').toLowerCase();
}

function renderCryptoExchangeSection(items) {
  const broker = _selectedCryptoBroker();
  const common = items.filter(i => i.exchange === 'common' || !i.exchange);
  const brokerItems = items.filter(i => i.exchange === broker);
  const runnerOnly = items.filter(i => i.exchange === 'upbit' && broker === 'bithumb');

  const brokerLabel = broker === 'bithumb' ? 'Bithumb' : 'Upbit';
  const brokerBadge = broker === 'bithumb'
    ? '<span class="exchange-badge bithumb">Bithumb</span>'
    : '<span class="exchange-badge upbit">Upbit</span>';

  const brokerSelect = common.filter(i => i.key === 'CRYPTO_BROKER');
  const commonRest = common.filter(i => i.key !== 'CRYPTO_BROKER');

  const onBrokerChange = `onchange="onCryptoBrokerChange(this.value)"`;

  const brokerSelectHtml = brokerSelect.map(item => {
    const tip = helpIcon(item.desc);
    const opts = (item.options || []).map(o =>
      `<option value="${o}" ${o === item.value ? 'selected' : ''}>${o}</option>`
    ).join('');
    return `<div class="form-row">
      <label class="form-label">${item.label} ${tip}</label>
      <select class="form-input form-select" data-key="${item.key}" ${onBrokerChange}>${opts}</select>
    </div>`;
  }).join('');

  const runnerNote = broker === 'bithumb' ? `
    <div class="exchange-runner-note">
      빗썸 러너는 REST 시세 폴링(약 2초) + 텔레그램 승인 매매를 지원합니다. Upbit는 WebSocket 실시간 TP/SL이 더 빠릅니다.
    </div>` : '';

  return `
    ${brokerSelectHtml}
    <div class="exchange-settings-panel" id="exchange-settings-panel">
      <div class="exchange-settings-header">
        ${brokerBadge}
        <span class="exchange-settings-title">${brokerLabel} 연결 설정</span>
      </div>
      ${renderItemList(brokerItems)}
    </div>
    <hr class="divider" style="margin:20px 0">
    <div class="settings-group-title">공통 (모든 거래소)</div>
    ${renderItemList(commonRest)}
    ${runnerNote}
    ${broker === 'bithumb' && runnerOnly.length ? `
      <details class="exchange-runner-details">
        <summary>Upbit 러너 전용 설정 (참고)</summary>
        ${renderItemList(runnerOnly)}
      </details>` : ''}
  `;
}

function onCryptoBrokerChange(value) {
  const panel = document.getElementById('settings-tab-upbit');
  if (!panel || !settingsData) return;
  const items = settingsData.sections.upbit || [];
  // CRYPTO_BROKER 값을 메모리에 반영 (재렌더 시 select 유지)
  const brokerItem = items.find(i => i.key === 'CRYPTO_BROKER');
  if (brokerItem) brokerItem.value = value;
  panel.innerHTML = renderCryptoExchangeSection(items);
}

function renderPrivateSection(items) {
  const warning = `
    <div class="privacy-warning">
      <span class="warn-icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg></span>
      <div>
        <strong>개인정보 보호</strong> — API 키·토큰은 절대 타인과 공유하지 마세요.<br>
        테스터에게 공유 시 이 탭을 비워두거나 별도 계정·키를 발급하세요.
      </div>
    </div>`;
  const grouped = {};
  for (const item of items) {
    const g = item.group || '기타';
    (grouped[g] = grouped[g] || []).push(item);
  }
  const order = [..._PRIVATE_GROUPS, ...Object.keys(grouped).filter(g => !_PRIVATE_GROUPS.includes(g))];
  const groupsHtml = order.filter(g => grouped[g]).map(g => `
    <div class="settings-group">
      <div class="settings-group-title">${g}</div>
      ${renderItemList(grouped[g])}
    </div>`).join('');
  return warning + groupsHtml;
}

function helpIcon(desc, title) {
  if (!desc) return '';
  const t = escHtml(title || '설명');
  const d = escHtml(desc);
  return `<button class="help-icon" data-help-title="${t}" data-help-text="${d}" onclick="showHelpFromEl(event)">?</button>`;
}

function renderItemList(items) {
  return items.map(item => {
    const key = item.key;
    const tip = helpIcon(item.desc);

    if (item.type === 'bool') {
      const checked = ['true', '1', 'yes'].includes((item.value || '').toLowerCase());
      return `<div class="form-row-inline">
        <div class="label-group">
          <div class="main-label">${item.label} ${tip}</div>
        </div>
        <label class="toggle">
          <input type="checkbox" data-key="${key}" ${checked ? 'checked' : ''}>
          <span class="toggle-slider"></span>
        </label>
      </div>`;
    }
    if (item.type === 'select') {
      const opts = (item.options || []).map(o =>
        `<option value="${o}" ${o === item.value ? 'selected' : ''}>${o}</option>`
      ).join('');
      return `<div class="form-row">
        <label class="form-label">${item.label} ${tip}</label>
        <select class="form-input form-select" data-key="${key}">${opts}</select>
      </div>`;
    }
    const inputType = item.type === 'secret' ? 'password' : 'text';
    const setHint = item.set ? '<span class="set-dot" title="설정됨">●</span>' : '';
    return `<div class="form-row">
      <label class="form-label">${item.label} ${setHint} ${tip}</label>
      <input type="${inputType}" class="form-input" data-key="${key}"
        value="${item.value || ''}" placeholder="${item.set ? '(설정됨)' : '미설정'}">
    </div>`;
  }).join('');
}

function switchSettingsTab(key) {
  activeSettingsTab = key;
  document.querySelectorAll('.tab-btn').forEach((b, i) => {
    const keys = Object.keys(settingsData.sections);
    b.classList.toggle('active', keys[i] === key);
  });
  document.querySelectorAll('.tab-panel').forEach(p => {
    p.classList.toggle('active', p.id === `settings-tab-${key}`);
  });
}

async function saveSettings() {
  const updates = {};
  document.querySelectorAll('[data-key]').forEach(el => {
    const key = el.dataset.key;
    if (el.type === 'checkbox') updates[key] = el.checked ? 'true' : 'false';
    else updates[key] = el.value;
  });
  const res = await POST('/api/settings', { updates });
  toast(res.message, res.ok ? 'success' : 'error');
  if (res.ok) fetchAndUpdateAutoExecBanner();
}

// ══════════════════════════════════════════════
// 로그 뷰어
// ══════════════════════════════════════════════
let logWs = null;
let logAutoScroll = true;
let logFile = 'crypto_auto_runner';

async function renderLogs() {
  if (logWs) { logWs.close(); logWs = null; }
  const el = document.getElementById('page-logs');
  el.innerHTML = `
    <div class="page-header">
      <h1 class="page-title">로그 뷰어</h1>
    </div>
    <div class="section-box">
      <div class="log-controls">
        <select class="form-input form-select" id="log-file-select" style="width:240px" onchange="changeLogFile(this.value)">
          <option value="crypto_auto_runner">crypto_auto_runner</option>
          <option value="crypto_auto_runner.error">crypto_auto_runner (에러)</option>
          <option value="binance_stream">binance_stream</option>
        </select>
        <label class="toggle-wrap">
          <label class="toggle">
            <input type="checkbox" id="autoscroll-toggle" checked onchange="logAutoScroll=this.checked">
            <span class="toggle-slider"></span>
          </label>
          <span style="font-size:13px;color:var(--text-muted)">자동 스크롤</span>
        </label>
        <button class="btn btn-ghost" onclick="clearLogView()">지우기</button>
        <button class="btn btn-ghost" id="ws-status-btn" style="font-size:12px">● 연결 중...</button>
      </div>
      <div class="log-container" id="log-container"></div>
    </div>
  `;
  connectLogWs(logFile);
}

function changeLogFile(file) {
  logFile = file;
  if (logWs) { logWs.close(); logWs = null; }
  document.getElementById('log-container').innerHTML = '';
  connectLogWs(file);
}

function connectLogWs(file) {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  logWs = new WebSocket(`${proto}://${location.host}/ws/logs?file=${file}`);
  const btn = document.getElementById('ws-status-btn');
  logWs.onopen  = () => { if (btn) btn.innerHTML = '<span class="dot-inline running"></span>실시간 연결됨'; };
  logWs.onclose = () => { if (btn) btn.innerHTML = '<span class="dot-inline stopped"></span>연결 끊김'; };
  logWs.onerror = () => { if (btn) btn.innerHTML = '<span class="dot-inline stopped"></span>오류'; };
  logWs.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    if (msg.type === 'line') appendLogLine(msg.data);
  };
}

function appendLogLine(line) {
  const container = document.getElementById('log-container');
  if (!container) return;
  const el = document.createElement('div');
  el.className = 'log-line';
  const lower = line.toLowerCase();
  if (lower.includes('error') || lower.includes('traceback') || lower.includes('exception')) el.classList.add('error');
  else if (lower.includes('warn')) el.classList.add('warn');
  else if (lower.includes('[gate]') || lower.includes('[tick]') || lower.includes('[scan]')) el.classList.add('tick');
  else if (lower.includes('[info]') || lower.includes('startup') || lower.includes('started')) el.classList.add('info');
  el.textContent = line;
  container.appendChild(el);
  if (logAutoScroll) container.scrollTop = container.scrollHeight;
  while (container.children.length > 500) container.removeChild(container.firstChild);
}

function clearLogView() {
  const c = document.getElementById('log-container');
  if (c) c.innerHTML = '';
}

// ══════════════════════════════════════════════
// 신호 분석
// ══════════════════════════════════════════════
let analysisInterval = null;

// ══════════════════════════════════════════════
// 통합 스코어보드 — 코인/국내주식/해외주식 공통 템플릿
//   헤더 통계 그리드 + miniBar 셀 + 행 클릭 상세 펼침
// ══════════════════════════════════════════════

/** 통일된 서브스코어 셀 (바 + 숫자) */
function sbBar(val) {
  const pct = Math.min(100, Math.max(0, Number(val) || 0));
  const cls = pct >= 70 ? 'sb-hi' : pct >= 40 ? 'sb-mid' : 'sb-lo';
  return `<div class="sb-cell"><div class="sb-fill ${cls}" style="width:${pct}%"></div><span class="sb-num">${Math.round(pct)}</span></div>`;
}

/**
 * 공통 스코어보드 렌더.
 * cfg = {
 *   title, helpKey, statusHtml,           // 헤더
 *   stats: [{label, value, cls, help}],   // 통계 그리드 카드
 *   subKeys: ['trend',...], subLabels: {trend:'추세',...}, subHelp: {trend:'kgsqs_trend',...},
 *   rows: [adapt(s), ...],                // 각 행: adapt() 결과
 *   lastColLabel, footerNote, emptyHtml,
 * }
 * adapt(s) → { name, badge, score, scoreCls, subs:{key:val}, lastCol, features:[{k,v,help}], notes:[], blocked }
 */
function buildScoreboardUnified(cfg) {
  const { title, helpKey = '', statusHtml = '', stats = [], subKeys, subLabels,
          subHelp = {}, rows = [], lastColLabel = '', footerNote = '', emptyHtml = '' } = cfg;
  const colCount = 3 + subKeys.length + (lastColLabel ? 1 : 0);

  const statGrid = stats.length ? `<div class="gsqs-stat-grid" style="margin-bottom:12px">
    ${stats.map(st => `<div class="gsqs-stat">
      <div class="gsqs-stat-label">${st.label}${st.help ? helpBtn(st.help) : ''}</div>
      <div class="gsqs-stat-value ${st.cls || ''}">${st.value}</div>
    </div>`).join('')}
  </div>` : '';

  const headCells = subKeys.map(k => `<th>${subLabels[k] || k}${subHelp[k] ? helpBtn(subHelp[k]) : ''}</th>`).join('');

  const bodyRows = rows.map((r, idx) => {
    const subCells = subKeys.map(k => `<td>${sbBar(r.subs[k] ?? 0)}</td>`).join('');
    const hasDetail = (r.features && r.features.length) || (r.notes && r.notes.length) || r.blocked;
    const featGrid = (r.features && r.features.length) ? `<div class="kgsqs-feat-grid">
      ${r.features.map(f => `<div class="kgsqs-feat"><span class="kgsqs-feat-k">${f.k}${f.help ? helpBtn(f.help) : ''}</span><span>${f.v}</span></div>`).join('')}
    </div>` : '';
    const subBarsDetail = `<div class="kgsqs-sub-bars">
      ${subKeys.map(k => `<div class="kgsqs-sub-row">
        <span class="kgsqs-sub-name">${subLabels[k] || k}</span>${sbBar(r.subs[k] ?? 0)}
        <span class="kgsqs-sub-val">${r.subs[k] != null ? Math.round(r.subs[k]) : '-'}</span>
      </div>`).join('')}
    </div>`;
    const notesHtml = (r.notes && r.notes.length) ? `<div class="text-muted mt-8" style="font-size:11px">${escHtml(r.notes.join(' · '))}</div>` : '';
    const blockedHtml = r.blocked ? `<div class="text-danger mt-8" style="font-size:11px">하드블록: ${escHtml(r.blocked)}</div>` : '';
    const detailRow = hasDetail ? `<tr class="kgsqs-detail"><td colspan="${colCount}">
      <div class="kgsqs-detail-inner">${featGrid}${subBarsDetail}${notesHtml}${blockedHtml}</div>
    </td></tr>` : '';
    const clickAttr = hasDetail ? `onclick="this.nextElementSibling.classList.toggle('kgsqs-detail-open')" style="cursor:pointer"` : '';
    return `<tr class="kgsqs-row" ${clickAttr}>
      <td>${r.name}</td>
      <td>${r.badge}</td>
      <td class="${r.scoreCls || ''}"><strong>${r.score}</strong></td>
      ${subCells}
      ${lastColLabel ? `<td>${r.lastCol ?? '-'}</td>` : ''}
    </tr>${detailRow}`;
  }).join('');

  const body = rows.length ? `
    <div class="table-wrap">
      <table class="kgsqs-table">
        <thead><tr>
          <th>종목</th><th>신호</th><th>총점${helpKey ? helpBtn(helpKey) : ''}</th>
          ${headCells}
          ${lastColLabel ? `<th>${lastColLabel}</th>` : ''}
        </tr></thead>
        <tbody>${bodyRows}</tbody>
      </table>
    </div>
    ${footerNote ? `<div class="text-muted mt-8" style="font-size:11px">${footerNote}</div>` : ''}
  ` : (emptyHtml || '<div class="text-muted" style="padding:16px 0;text-align:center">데이터 집계 중...</div>');

  return `<div class="section-box">
    <div class="section-title-row">
      <span class="section-title">${title}${helpKey ? helpBtn(helpKey) : ''}</span>
      ${statusHtml}
    </div>
    ${statGrid}
    ${body}
  </div>`;
}

async function renderAnalysis() {
  clearInterval(analysisInterval);
  await loadCoinNames();   // 코인 한글명 (스코어보드용, 최초 1회)
  const el = document.getElementById('page-analysis');
  el.innerHTML = '<div class="page-header"><h1 class="page-title">신호 분석</h1></div><div class="text-muted">데이터 로딩 중...</div>';
  await loadAnalysis();
  analysisInterval = setInterval(loadAnalysis, 15000);
}

async function loadAnalysis() {
  if (currentPage !== 'analysis') { clearInterval(analysisInterval); return; }

  const [plan, sizing, universe, slippage, reconcile, scalpScores, scalpSignals, scalpWeights, macroStatus, stockAnalysis, kstockStream, kstockSignals, kstockPositions, kstockUniverse, kstockWeights, kstockSlippage, kstockSizing, kstockReconcile, overseasStream, overseasSignals, overseasPositions, overseasUniverse, overseasWeights, overseasSlippage, overseasSizing, overseasReconcile, tradeHistory] = await Promise.all([
    GET('/api/plan/detail').catch(() => ({})),
    GET('/api/sizing').catch(() => ({})),
    GET('/api/universe').catch(() => ({})),
    GET('/api/slippage?limit=30').catch(() => ({ entries: [] })),
    GET('/api/reconcile').catch(() => ({})),
    GET('/api/scalping/scores').catch(() => ({ scores: [], exists: false })),
    GET('/api/scalping/signals').catch(() => ({ total_signals: 0, recent: [] })),
    GET('/api/scalping/weights').catch(() => ({})),
    GET('/api/macro/status').catch(() => ({ active: false, data_available: false })),
    GET('/api/stock/analysis').catch(() => ({ exists: false })),
    GET('/api/kstock/stream').catch(() => ({ running: false, scores: [] })),
    GET('/api/kstock/signals').catch(() => ({ total_signals: 0, recent: [], win_rates: {}, symbol_stats: [] })),
    GET('/api/kstock/positions').catch(() => ({ exists: false })),
    GET('/api/kstock/universe').catch(() => ({ symbols: [], market_status: 'closed' })),
    GET('/api/kstock/weight_optimizer').catch(() => ({ n_complete_signals: 0 })),
    GET('/api/kstock/slippage?limit=30').catch(() => ({ entries: [], exists: false })),
    GET('/api/kstock/sizing').catch(() => ({ recommendations: [] })),
    GET('/api/kstock/reconcile').catch(() => ({ success: false, matched: [], warnings: [] })),
    GET('/api/overseas/stream').catch(() => ({ running: false, scores: [], market_hours: false })),
    GET('/api/overseas/signals').catch(() => ({ recent: [], win_rates: {} })),
    GET('/api/overseas/positions').catch(() => ({ exists: false, positions: [] })),
    GET('/api/overseas/universe').catch(() => ({ symbols: [], symbol_count: 0 })),
    GET('/api/overseas/weight_optimizer').catch(() => ({ n_complete_signals: 0 })),
    GET('/api/overseas/slippage?limit=30').catch(() => ({ entries: [], exists: false })),
    GET('/api/overseas/sizing').catch(() => ({ recommendations: [] })),
    GET('/api/overseas/reconcile').catch(() => ({ success: false, matched: [], warnings: [] })),
    GET('/api/account/trades?tab=crypto&period=1m&limit=50').catch(() => ({ items: [], total: 0 })),
  ]);

  // ── 거래내역 모듈 (Upbit 스타일, 탭별 독립 상태) ──────────────────
  const _tradeState = {
    crypto:   { period: '1m', type: 'all', symbol: '', data: tradeHistory, loading: false },
    stock:    { period: '1m', type: 'all', symbol: '', data: null, loading: false },
    overseas: { period: '1m', type: 'all', symbol: '', data: null, loading: false },
  };

  async function _loadTradeData(tab) {
    const s = _tradeState[tab];
    if (!s) return;
    loadCoinNames();   // 코인 한글명 (거래내역용, 최초 1회)
    s.loading = true;
    _renderTradeTable(tab);
    try {
      const url = `/api/account/trades?tab=${tab}&period=${s.period}&type=${s.type}&symbol=${encodeURIComponent(s.symbol)}&limit=500`;
      s.data = await GET(url);
    } catch(e) {
      s.data = { items: [], total: 0 };
    }
    s.loading = false;
    _renderTradeTable(tab);
  }

  function _renderTradeTable(tab) {
    const wrap = document.getElementById(`trade-table-wrap-${tab}`);
    if (!wrap) return;
    const s = _tradeState[tab];
    if (s.loading) { wrap.innerHTML = '<div class="text-muted" style="padding:12px">로딩 중...</div>'; return; }

    const items = (s.data && s.data.items) || [];
    const total = (s.data && s.data.total) || 0;

    const fmt_ts = ts => {
      if (!ts) return '-';
      try {
        const d = new Date(ts);
        return `<span style="font-size:11px">${(d.getMonth()+1).toString().padStart(2,'0')}.${d.getDate().toString().padStart(2,'0')}</span><br><span style="font-size:10px;color:var(--text-muted)">${d.getHours().toString().padStart(2,'0')}:${d.getMinutes().toString().padStart(2,'0')}</span>`;
      } catch { return ts.slice(0,16).replace('T',' '); }
    };

    const fmt_qty = (q, sym) => {
      if (q == null) return '-';
      const n = Number(q);
      if (n >= 1) return n.toLocaleString(undefined, {maximumFractionDigits:4});
      return n.toFixed(8).replace(/0+$/, '').replace(/\.$/, '');
    };

    const fmt_price_cell = (p, market) => {
      if (p == null) return '-';
      const n = Number(p);
      const suffix = market === 'USD' ? '$' : '';
      const prefix = market === 'USD' ? '' : '';
      if (market === 'USD') return suffix + n.toLocaleString(undefined, {minimumFractionDigits:2, maximumFractionDigits:2});
      if (n >= 1000) return n.toLocaleString(undefined, {maximumFractionDigits:0});
      if (n >= 1)    return n.toFixed(1);
      return n.toFixed(4);
    };

    const fmt_amount = (a, market) => {
      if (a == null) return '-';
      const n = Number(a);
      if (market === 'USD') return '$' + n.toLocaleString(undefined, {minimumFractionDigits:2, maximumFractionDigits:2});
      return n.toLocaleString(undefined, {maximumFractionDigits:0});
    };

    const slip_cell = (bps) => {
      if (bps == null) return '<span class="text-muted" style="font-size:10px">-</span>';
      const n = Number(bps);
      const cls = n < 5 ? 'text-success' : n < 30 ? '' : 'text-danger';
      return `<span class="${cls}" style="font-size:11px" title="${n.toFixed(1)}bps">${n < 0.5 ? '±0' : (n > 0 ? '+' : '') + n.toFixed(1) + 'bp'}</span>`;
    };

    if (!items.length) {
      wrap.innerHTML = '<div class="text-muted" style="padding:12px;text-align:center">해당 기간 거래 내역이 없습니다</div>';
      return;
    }

    const isOverseas = tab === 'overseas';
    const rows = items.map(t => {
      const sideBadge = t.side === 'buy'
        ? '<span style="color:#b6b6bd;font-weight:700;font-size:12px">매수</span>'
        : '<span style="color:#e53935;font-weight:700;font-size:12px">매도</span>';
      const amtDisplay = isOverseas && t.trade_amount_krw
        ? `<span title="$${Number(t.trade_amount||0).toFixed(2)}">${fmt_amount(t.trade_amount_krw,'KRW')}</span>`
        : fmt_amount(t.trade_amount, t.market);
      const feeDisplay = isOverseas && t.fee_krw
        ? fmt_amount(t.fee_krw, 'KRW')
        : fmt_amount(t.fee, t.market);
      const setlDisplay = isOverseas && t.settlement_krw
        ? fmt_amount(t.settlement_krw, 'KRW')
        : fmt_amount(t.settlement, t.market);
      return `<tr>
        <td style="white-space:nowrap">${fmt_ts(t.executed_at)}</td>
        <td>
          <strong style="font-size:12px">${escHtml(t.symbol||'-')}</strong>
          ${tab==='crypto'&&t.broker?`<br><span class="exchange-badge ${t.broker==='bithumb'?'bithumb':'upbit'}" style="font-size:9px;padding:1px 4px">${t.broker==='bithumb'?'Bithumb':'Upbit'}</span>`:''}
          ${(() => { let nm = ''; if (t.tab === 'crypto') { nm = coinKoreanName('KRW-' + (t.symbol||'')); } else if (t.name && t.name !== t.symbol) { nm = t.name; } return nm ? `<br><span style="font-size:10px;color:var(--text-muted)">${escHtml(nm)}</span>` : ''; })()}
        </td>
        <td style="text-align:center">${sideBadge}</td>
        <td style="font-size:12px;text-align:right">${fmt_qty(t.quantity, t.symbol)}</td>
        <td style="font-size:12px;text-align:right">${fmt_price_cell(t.unit_price, t.market)}</td>
        <td style="font-size:12px;text-align:right;font-weight:500">${amtDisplay}</td>
        <td style="font-size:11px;text-align:right;color:var(--text-muted)">${feeDisplay}</td>
        <td style="font-size:12px;text-align:right;font-weight:600">${setlDisplay}</td>
        ${tab==='crypto' ? `<td style="text-align:center">${slip_cell(t.slippage_bps)}</td>` : ''}
      </tr>`;
    }).join('');

    const extraTh = tab === 'crypto' ? '<th style="text-align:center">체결오차</th>' : '';
    wrap.innerHTML = `<div class="table-wrap" style="overflow-x:auto">
      <table style="width:100%;font-size:12px;border-collapse:collapse">
        <thead><tr style="background:var(--bg-secondary);font-size:11px;color:var(--text-muted)">
          <th style="padding:6px 8px;text-align:left">체결시각</th>
          <th style="padding:6px 8px;text-align:left">코인/종목</th>
          <th style="padding:6px 8px;text-align:center">종류</th>
          <th style="padding:6px 8px;text-align:right">거래수량</th>
          <th style="padding:6px 8px;text-align:right">거래단가</th>
          <th style="padding:6px 8px;text-align:right">거래금액</th>
          <th style="padding:6px 8px;text-align:right">수수료${helpBtn('trade_fee')}</th>
          <th style="padding:6px 8px;text-align:right">정산금액${helpBtn('trade_settlement')}</th>
          ${extraTh}
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
    <div class="text-muted mt-8" style="font-size:11px;padding:0 4px">전체 ${total}건 중 ${items.length}건 표시</div>`;
  }

  function buildTradeHistoryHtml(tab) {
    const s = _tradeState[tab] || {};
    const periods = [
      { key:'1w', label:'1주일' },
      { key:'1m', label:'1개월' },
      { key:'3m', label:'3개월' },
    ];
    const types = [
      { key:'all', label:'전체' },
      { key:'buy', label:'매수' },
      { key:'sell', label:'매도' },
    ];

    const periodBtns = periods.map(p =>
      `<button class="trade-filter-btn ${s.period===p.key?'active':''}" onclick="_tradeFilter('${tab}','period','${p.key}')">${p.label}</button>`
    ).join('');
    const typeBtns = types.map(t =>
      `<button class="trade-filter-btn ${s.type===t.key?'active':''}" onclick="_tradeFilter('${tab}','type','${t.key}')">${t.label}</button>`
    ).join('');

    return `
    <div class="section-box" id="trade-section-${tab}" style="margin-bottom:12px">
      <div class="section-title-row" style="flex-wrap:wrap;gap:8px">
        <span class="section-title">거래내역</span>
        <div class="trade-filter-row">
          <div class="trade-filter-group">${periodBtns}</div>
          <div class="trade-filter-group">${typeBtns}</div>
          <input class="trade-search-input" id="trade-search-${tab}" type="text"
            placeholder="${tab==='crypto'?'코인':'종목'} 검색"
            value="${escHtml(s.symbol||'')}"
            oninput="_tradeSearch('${tab}', this.value)"
            style="width:90px;padding:3px 7px;border-radius:6px;border:1px solid var(--border);font-size:12px;background:var(--bg-card);color:var(--text)">
        </div>
      </div>
      <div id="trade-table-wrap-${tab}">
        <div class="text-muted" style="padding:12px;text-align:center">로딩 중...</div>
      </div>
    </div>`;
  }

  // 전역 핸들러 (onclick에서 호출)
  window._tradeFilter = function(tab, key, val) {
    if (!_tradeState[tab]) return;
    _tradeState[tab][key] = val;
    // 버튼 active 상태 업데이트
    const sec = document.getElementById(`trade-section-${tab}`);
    if (sec) {
      sec.querySelectorAll(`.trade-filter-btn`).forEach(btn => {
        const btnVal = btn.textContent.trim();
        const periodMap = {'1주일':'1w','1개월':'1m','3개월':'3m'};
        const typeMap = {'전체':'all','매수':'buy','매도':'sell'};
        const mapped = periodMap[btnVal] || typeMap[btnVal] || btnVal;
        btn.classList.toggle('active', mapped === _tradeState[tab].period || mapped === _tradeState[tab].type);
      });
    }
    _loadTradeData(tab);
  };

  window._tradeSearch = function(tab, val) {
    if (!_tradeState[tab]) return;
    clearTimeout(_tradeState[tab]._searchTimer);
    _tradeState[tab]._searchTimer = setTimeout(() => {
      _tradeState[tab].symbol = val.trim();
      _loadTradeData(tab);
    }, 400);
  };

  // window에 노출: switchAnalysisTab에서 lazy-load 호출용
  window._tradeState    = _tradeState;
  window._loadTradeData = _loadTradeData;

  // 초기 렌더: 코인은 이미 데이터 있음, 나머지는 지연 로드
  setTimeout(() => {
    _renderTradeTable('crypto');
    // stock/overseas는 탭 전환 시 로드 (lazy)
  }, 0);

  // 스코어 패널
  const sb = plan.score_breakdown || {};
  const gates = plan.quality_gates || {};
  const GATE_ICON_LABEL = { ok: '✓', fail: '✕', info: 'i' };
  const gateHtml = Object.entries(gates).map(([k, v]) => {
    const status = _gateStatus(k, v);
    const meta   = GATE_META[k] || { kr: k, desc: '' };
    const iconLbl = GATE_ICON_LABEL[status] || '?';
    return `<div class="gate-item gate-${status}">
      <span class="gate-icon"><span class="gate-icon-wrap">${iconLbl}</span></span>
      <div class="gate-content">
        <div class="gate-header">
          <span class="gate-kr">${meta.kr}</span>
          <span class="gate-key-raw">${k}</span>
          <span class="gate-val">${escHtml(String(v))}</span>
        </div>
        ${meta.desc ? `<div class="gate-desc">${escHtml(meta.desc)}</div>` : ''}
      </div>
    </div>`;
  }).join('') || '<div class="text-muted">게이트 정보 없음</div>';

  // 유니버스 테이블
  const markets = universe.markets || [];
  const displayNames = universe.display_names || {};
  const universeRows = markets.map(m => `<tr>
    <td><strong>${m}</strong></td>
    <td>${escHtml(displayNames[m] || '-')}</td>
  </tr>`).join('');

  // 사이징 노트
  const sizingNotes = (sizing.notes || []).map(n => `<li>${escHtml(n)}</li>`).join('');

  // 슬리피지 테이블
  const slipRows = (slippage.entries || []).map(e => `<tr>
    <td><strong>${e.market || '-'}</strong></td>
    <td><span class="badge ${e.side === 'buy' ? 'badge-success' : 'badge-danger'}">${e.side || '-'}</span></td>
    <td>${fmt_krw(e.order_krw)}</td>
    <td>${e.limit_price != null ? e.limit_price : '-'}</td>
    <td>${e.fill_price  != null ? e.fill_price  : '-'}</td>
    <td class="${(e.slippage_bps || 0) > 5 ? 'text-danger' : ''}">${fmt_bps(e.slippage_bps)}</td>
  </tr>`).join('');

  // 리콘사일 상태
  const recOk = reconcile.success;
  const recMissDb = (reconcile.missing_in_db || []).join(', ') || '없음';
  const recMissBr = (reconcile.missing_in_broker || []).join(', ') || '없음';
  const recWarns  = (reconcile.warnings || []).join(', ') || '없음';

  // ── GSQS 스코어보드 ──────────────────────────────────────────
  const DECISION_BADGE = {
    'STRONG_BUY':    '<span class="badge badge-strong-buy">STRONG BUY</span>',
    'BUY_CANDIDATE': '<span class="badge badge-buy">BUY</span>',
    'WATCH':         '<span class="badge badge-neutral">WATCH</span>',
    'NO_TRADE':      '<span class="badge badge-muted">NO TRADE</span>',
    'BLOCKED':       '<span class="badge badge-danger">BLOCKED</span>',
  };
  const SUB_KEYS = ['trend','volume','orderbook','tradeflow','futures','risk','market'];
  const SUB_KR   = {trend:'추세', volume:'거래량', orderbook:'호가창', tradeflow:'체결흐름', futures:'선물', risk:'리스크', market:'시장'};
  const SUB_HELP = {trend:'trend', volume:'volume', orderbook:'orderbook', tradeflow:'tradeflow', futures:'futures', risk:'risk', market:'market'};

  // 코인 행 어댑터 → 통합 스코어보드 형식
  const coinRows = (scalpScores.scores || []).map(s => {
    const dmap = {
      'STRONG_BUY':    'badge-strong-buy',
      'BUY_CANDIDATE': 'badge-buy',
      'WATCH':         'badge-neutral',
      'NO_TRADE':      'badge-muted',
      'BLOCKED':       'badge-danger',
    };
    const dlabel = { STRONG_BUY:'STRONG BUY', BUY_CANDIDATE:'BUY', WATCH:'WATCH', NO_TRADE:'NO TRADE', BLOCKED:'BLOCKED' };
    return {
      name: `<strong>${escHtml(s.symbol.replace('USDT',''))}</strong><span class="text-muted" style="font-size:10px">/USDT</span>${(() => { const k = coinKoreanName('KRW-' + s.symbol.replace('USDT','')); return k ? `<br><span class="text-muted" style="font-size:10px">${escHtml(k)}</span>` : ''; })()}`,
      badge: `<span class="badge ${dmap[s.decision] || 'badge-muted'}">${dlabel[s.decision] || s.decision}</span>`,
      score: s.score,
      scoreCls: s.decision === 'STRONG_BUY' || s.is_buy ? 'text-success' : '',
      subs: s.sub_scores || {},
      notes: s.notes || [],
      blocked: s.decision === 'BLOCKED' ? (s.notes && s.notes.length ? s.notes.join(' · ') : '하드블록') : '',
    };
  });

  const scoreBoardHtml = scalpScores.exists
    ? buildScoreboardUnified({
        title: 'GSQS 실시간 스코어보드', helpKey: 'scoreboard',
        statusHtml: `<span class="section-meta"><span class="dot-inline running"></span>실시간 수신 중</span>`,
        stats: [
          { label: '감시 심볼', value: `${scalpScores.total || 0}종목` },
          { label: 'BUY 후보',  value: `${scalpScores.buy_count || 0}개`, cls: (scalpScores.buy_count||0) > 0 ? 'text-success' : '' },
          { label: '갱신 시각', value: fmt_time(scalpScores.updated_at) },
        ],
        subKeys: SUB_KEYS, subLabels: SUB_KR, subHelp: SUB_HELP,
        rows: coinRows,
        footerNote: '▶ 행 클릭 시 세부 신호 확인 &nbsp;|&nbsp; 5초마다 갱신',
      })
    : `<div class="section-box">
        <div class="section-title">GSQS 실시간 스코어보드${helpBtn('scoreboard')}</div>
        <div class="text-muted">binance-stream 실행 전 — feature_vectors.json 없음</div>
      </div>`;

  // ── 신호 이력 & 승률 ───────────────────────────────────────────
  const totalSigs  = scalpSignals.total_signals || 0;
  const pendingSigs= scalpSignals.pending_signals || 0;
  const winRate5m  = scalpSignals.overall_win_rate;
  const autoThr    = scalpSignals.auto_threshold;
  const sufficient = scalpSignals.data_sufficient;

  const bandRows = (scalpSignals.bands || []).map(b => {
    const wr = b['win_rate_5m'];
    const wrStr = wr != null ? (wr * 100).toFixed(1) + '%' : '-';
    const wrCls = wr != null ? (wr >= 0.55 ? 'text-success' : wr >= 0.45 ? '' : 'text-danger') : '';
    return `<tr>
      <td>${b.band}점</td>
      <td>${b.n}건 ${b.reliable ? '<span class="badge badge-success" style="font-size:10px">신뢰</span>' : '<span class="badge badge-muted" style="font-size:10px">부족</span>'}</td>
      <td class="${wrCls}"><strong>${wrStr}</strong></td>
    </tr>`;
  }).join('');

  const recentRows = (scalpSignals.recent || []).map(r => {
    const ret = r.ret_5m;
    const retStr = ret != null ? (ret * 100).toFixed(2) + '%' : '대기중';
    const retCls = ret != null ? (ret > 0 ? 'text-success' : 'text-danger') : 'text-muted';
    const ts = r.ts_ms ? new Date(r.ts_ms).toLocaleString('ko-KR', {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}) : '-';
    return `<tr>
      <td>${ts}</td>
      <td><strong>${r.symbol || '-'}</strong></td>
      <td>${DECISION_BADGE[r.decision] || r.decision || '-'}</td>
      <td><strong>${r.score != null ? Number(r.score).toFixed(1) : '-'}</strong></td>
      <td class="${retCls}">${retStr}</td>
    </tr>`;
  }).join('');

  const signalsHtml = `
    <div class="section-box">
      <div class="section-title-row">
        <span class="section-title">신호 이력 &amp; 승률${helpBtn('signal_history')}</span>
        ${sufficient ? '<span class="badge badge-success" style="font-size:11px">데이터 충분</span>' : '<span class="badge badge-muted" style="font-size:11px">데이터 축적 중</span>'}
      </div>
      <div class="gsqs-stat-grid">
        <div class="gsqs-stat">
          <div class="gsqs-stat-label">총 기록 신호</div>
          <div class="gsqs-stat-value">${totalSigs}건</div>
        </div>
        <div class="gsqs-stat">
          <div class="gsqs-stat-label">결과 대기 중</div>
          <div class="gsqs-stat-value">${pendingSigs}건</div>
        </div>
        <div class="gsqs-stat">
          <div class="gsqs-stat-label">5분 후 승률${helpBtn('win_rate')}</div>
          <div class="gsqs-stat-value ${winRate5m != null ? (winRate5m >= 0.55 ? 'text-success' : '') : 'text-muted'}">
            ${winRate5m != null ? (winRate5m * 100).toFixed(1) + '%' : sufficient ? '-' : '집계 중'}
          </div>
        </div>
        <div class="gsqs-stat">
          <div class="gsqs-stat-label">자동 임계값${helpBtn('auto_threshold')}</div>
          <div class="gsqs-stat-value">${autoThr != null ? autoThr + '점' : '-'}</div>
        </div>
      </div>

      ${bandRows ? `
      <div class="section-subtitle mt-12">점수 구간별 승률 (5분 후)</div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>점수 구간</th><th>신호 수</th><th>5분 후 승률</th></tr></thead>
          <tbody>${bandRows}</tbody>
        </table>
      </div>` : `<div class="text-muted mt-8">신호 30건 이상 쌓이면 구간별 통계가 표시됩니다</div>`}

      ${recentRows ? `
      <div class="section-subtitle mt-12">최근 신호 이력</div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>시각</th><th>심볼</th><th>결정</th><th>점수</th><th>5분 수익률</th></tr></thead>
          <tbody>${recentRows}</tbody>
        </table>
      </div>` : `<div class="text-muted mt-8">신호 없음 — binance-stream 구동 후 BUY 신호 발생 시 기록됩니다</div>`}
    </div>`;

  // ── 가중치 최적화 ──────────────────────────────────────────────
  const wn       = scalpWeights.n_complete_signals || 0;
  const wMin     = scalpWeights.min_for_optimization || 200;
  const wReady   = scalpWeights.ready_to_optimize || false;
  const wPct     = scalpWeights.progress_pct != null ? scalpWeights.progress_pct : Math.min(100, Math.round(wn / wMin * 100));
  const wNextAt  = scalpWeights.next_run_at;
  const wCurr    = scalpWeights.current_weights || {};
  const wOptAt   = scalpWeights.optimized_at;
  const wOptAtKr = scalpWeights.last_optimized_at;
  const wExpWr   = scalpWeights.expected_win_rate;
  const wDefWr   = scalpWeights.default_win_rate;
  const wImprov  = scalpWeights.improvement;
  const wApplied = scalpWeights.applied;
  const wBands   = scalpWeights.win_rate_bands || [];

  const weightRows = Object.entries(wCurr).map(([k, v]) => {
    const barPct = Math.round(Number(v) * 100);
    return `<tr>
      <td>${SUB_KR[k] || k}</td>
      <td><div class="mini-bar-wrap"><div class="mini-bar mini-bar-mid" style="width:${barPct * 2.5}%"></div><span class="mini-bar-val">${(Number(v) * 100).toFixed(1)}%</span></div></td>
    </tr>`;
  }).join('');

  const wBandRows = wBands.map(b => {
    const wr = b.win_rate_5m != null ? (b.win_rate_5m * 100).toFixed(1) + '%' : '-';
    const cls = b.win_rate_5m != null && b.win_rate_5m >= 0.55 ? 'text-success' : (b.win_rate_5m != null && b.win_rate_5m < 0.45 ? 'text-danger' : '');
    return `<tr><td>${b.band}점</td><td>${b.n}건</td><td class="${cls}">${wr}</td></tr>`;
  }).join('');

  // 적용 상태 뱃지
  const wAppliedBadge = wApplied === false
    ? '<span class="badge badge-warning" title="퇴보로 미적용 — 기본 가중치 유지 중">미적용</span>'
    : wApplied === true ? '<span class="badge badge-success">적용됨</span>' : '';

  const weightsHtml = `
    <div class="section-box">
      <div class="section-title">GSQS 가중치 자동 최적화${helpBtn('weight_optimizer')}</div>
      <div class="weight-progress-wrap">
        <div class="weight-progress-label">
          <span>완성 신호 ${wn} / ${wMin}건</span>
          <span>${wReady ? '<span class="badge badge-success">최적화 가능</span>' : (wNextAt ? wNextAt + '건 남음' : wPct + '%')}</span>
        </div>
        <div class="weight-progress-track">
          <div class="weight-progress-fill ${wReady ? 'weight-progress-done' : ''}" style="width:${wPct}%"></div>
        </div>
      </div>

      ${wOptAt ? `
      <div class="gsqs-stat-grid mt-12">
        <div class="gsqs-stat">
          <div class="gsqs-stat-label">기본 승률</div>
          <div class="gsqs-stat-value">${wDefWr != null ? (wDefWr * 100).toFixed(1) + '%' : '-'}</div>
        </div>
        <div class="gsqs-stat">
          <div class="gsqs-stat-label">최적화 후 승률</div>
          <div class="gsqs-stat-value text-success">${wExpWr != null ? (wExpWr * 100).toFixed(1) + '%' : '-'}</div>
        </div>
        <div class="gsqs-stat">
          <div class="gsqs-stat-label">개선 ${wAppliedBadge}</div>
          <div class="gsqs-stat-value ${wImprov > 0 ? 'text-success' : 'text-danger'}">
            ${wImprov != null ? (wImprov >= 0 ? '+' : '') + (wImprov * 100).toFixed(1) + '%' : '-'}
          </div>
        </div>
        <div class="gsqs-stat">
          <div class="gsqs-stat-label">최적화 시각</div>
          <div class="gsqs-stat-value" style="font-size:11px">${wOptAtKr || fmt_time(new Date(wOptAt * 1000).toISOString())}</div>
        </div>
      </div>
      ${wApplied === false ? '<div class="weight-no-apply-note">퇴보 방지: 최적화된 가중치가 기본값 대비 성능이 낮아 적용되지 않았습니다.</div>' : ''}
      ` : `<div class="text-muted mt-8" style="font-size:12px">완성 신호 ${wMin}건 이상 축적되면 자동으로 가중치를 최적화합니다</div>`}

      <div class="section-subtitle mt-12">현재 적용 가중치</div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>컴포넌트</th><th>비중</th></tr></thead>
          <tbody>${weightRows}</tbody>
        </table>
      </div>

      ${wBandRows ? `
      <div class="section-subtitle mt-12">점수 구간별 5분 승률</div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>점수 구간</th><th>신호 수</th><th>5분 후 승률</th></tr></thead>
          <tbody>${wBandRows}</tbody>
        </table>
      </div>` : ''}
    </div>`;

  // ── 매크로 이벤트 배너 ─────────────────────────────────────────
  const macroActive  = macroStatus.active === true;
  const macroReason  = macroStatus.trigger_reason || '';
  const macroSync    = ((macroStatus.sync_ratio  || 0) * 100).toFixed(0);
  const macroCorr    = (macroStatus.mean_correlation || 0).toFixed(2);
  const macroDecay   = macroStatus.decay_remaining_seconds || 0;
  const macroMovers  = (macroStatus.top_movers || []).slice(0, 5);
  const macroSymbols = macroStatus.n_symbols || 0;

  const macroMoverTags = macroMovers.map(m => {
    const sym = m.symbol.replace('USDT','');
    const r   = (m.ret_1m * 100).toFixed(2);
    const cls = m.ret_1m >= 0 ? 'text-success' : 'text-danger';
    return `<span class="macro-mover-tag ${cls}">${sym} ${r >= 0 ? '+' : ''}${r}%</span>`;
  }).join('');

  const macroHtml = macroActive ? `
    <div class="macro-alert-banner macro-alert-active">
      <div class="macro-alert-title">매크로 이벤트 감지 중</div>
      <div class="macro-alert-body">
        <span class="macro-alert-stat">사유: <strong>${escHtml(macroReason)}</strong></span>
        <span class="macro-alert-stat">동시급변동${helpBtn('sync_ratio')}: <strong>${macroSync}%</strong></span>
        <span class="macro-alert-stat">상관계수${helpBtn('correlation')}: <strong>${macroCorr}</strong></span>
        <span class="macro-alert-stat">해제까지: <strong>${macroDecay}초</strong></span>
      </div>
      ${macroMoverTags ? `<div class="macro-movers">${macroMoverTags}</div>` : ''}
      <div class="macro-alert-note">BUY 신호에 경고 태그 적용 중 — 매크로 노이즈 가능성</div>
    </div>` : `
    <div class="macro-alert-banner macro-alert-normal">
      <span class="macro-alert-label">매크로 상태${helpBtn('macro_status')}</span>
      <span class="macro-status-ok">● 정상</span>
      <span class="macro-alert-stat">동시급변동${helpBtn('sync_ratio')}: ${macroSync}%</span>
      <span class="macro-alert-stat">상관계수${helpBtn('correlation')}: ${macroCorr}</span>
      <span class="macro-alert-stat">추적 심볼: ${macroSymbols}개</span>
    </div>`;

  // ── 주식 AI 분석 ───────────────────────────────────────────────
  const sa = stockAnalysis;
  const saRecs = sa.exists ? (sa.recommendations || []) : [];
  const saStatusMap = {
    'AI_DAILY_TRADE_PLAN_READY':     { cls: 'badge-success',  label: '매수 주문 준비됨' },
    'AI_DAILY_TRADE_PLAN_NO_ORDERS': { cls: 'badge-muted',    label: '오늘 매수 없음 (HOLD)' },
    'AI_DAILY_TRADE_PLAN_FAILED':    { cls: 'badge-danger',   label: '분석 실패' },
    'AI_RECOMMENDATION_NO_PLAN_ORDERS': { cls: 'badge-muted', label: '오늘 매수 없음 (HOLD)' },
    'AI_RECOMMENDATION_READY':       { cls: 'badge-success',  label: '추천 준비됨' },
    'AI_RECOMMENDATION_FAILED':      { cls: 'badge-danger',   label: '추천 실패' },
  };
  const saStatus = saStatusMap[sa.status] || { cls: 'badge-muted', label: sa.status || '-' };
  const saRiskCls = {
    'SAFETY_AUDIT_OK': 'text-success', 'SAFETY_AUDIT_WARNING': 'text-warning', 'SAFETY_AUDIT_BLOCKED': 'text-danger',
  };
  const saActionBadge = (action) => {
    const m = { BUY:'badge-success', INCREASE:'badge-success', HOLD:'badge-muted', SKIP:'badge-muted', REDUCE:'badge-warning', SELL:'badge-danger' };
    return `<span class="badge ${m[action]||'badge-muted'}">${action}</span>`;
  };
  const saRecRows = saRecs.map(r => {
    const score = r.final_score != null ? Number(r.final_score).toFixed(1) : '-';
    const tech  = r.tech_score  != null ? Number(r.tech_score).toFixed(1)  : '-';
    const mac   = r.macro_score != null ? (r.macro_score >= 0 ? '+' : '') + Number(r.macro_score).toFixed(1) : '-';
    const scoreCls = r.final_score != null && r.final_score >= 60 ? 'text-success' : r.final_score != null && r.final_score < 40 ? 'text-danger' : '';
    return `<tr>
      <td><strong>${escHtml(r.symbol || '-')}</strong></td>
      <td>${saActionBadge(r.action || '-')}</td>
      <td class="${scoreCls}">${score}</td>
      <td>${tech}</td>
      <td>${mac}</td>
      <td style="font-size:11px;color:var(--text-muted)">${escHtml(r.reason || '-')}</td>
    </tr>`;
  }).join('');

  const stockAnalysisHtml = sa.exists ? `
    <div class="section-box">
      <div class="section-title-row">
        <span class="section-title">주식 AI 분석 (KIS)${helpBtn('signal_history')}</span>
        <span class="section-meta">${fmt_time(sa.generated_at)}</span>
      </div>
      <div class="gsqs-stat-grid">
        <div class="gsqs-stat">
          <div class="gsqs-stat-label">분석 상태</div>
          <div class="gsqs-stat-value"><span class="badge ${saStatus.cls}" style="font-size:12px">${saStatus.label}</span></div>
        </div>
        <div class="gsqs-stat">
          <div class="gsqs-stat-label">추천 종목 수</div>
          <div class="gsqs-stat-value">${sa.recommendation_count || 0}종목</div>
        </div>
        <div class="gsqs-stat">
          <div class="gsqs-stat-label">현금</div>
          <div class="gsqs-stat-value" style="font-size:13px">${fmt_krw(sa.account?.cash)}</div>
        </div>
        <div class="gsqs-stat">
          <div class="gsqs-stat-label">총 자산</div>
          <div class="gsqs-stat-value" style="font-size:13px">${fmt_krw(sa.account?.total_equity)}</div>
        </div>
        <div class="gsqs-stat">
          <div class="gsqs-stat-label">매크로 점수</div>
          <div class="gsqs-stat-value ${(sa.macro?.score||0) >= 0 ? 'text-success' : 'text-danger'}">${sa.macro?.score != null ? (sa.macro.score >= 0 ? '+' : '') + sa.macro.score : '-'}</div>
        </div>
        <div class="gsqs-stat">
          <div class="gsqs-stat-label">시장 국면</div>
          <div class="gsqs-stat-value">${escHtml(sa.macro?.regime || '-')}</div>
        </div>
        <div class="gsqs-stat">
          <div class="gsqs-stat-label">안전 감사${helpBtn('quality_gates')}</div>
          <div class="gsqs-stat-value ${saRiskCls[sa.risk?.safety_audit]||''}" style="font-size:11px">${escHtml(sa.risk?.safety_audit || '-')}</div>
        </div>
        <div class="gsqs-stat">
          <div class="gsqs-stat-label">리콘사일${helpBtn('reconcile')}</div>
          <div class="gsqs-stat-value" style="font-size:11px">${escHtml(sa.risk?.reconcile || '-')}</div>
        </div>
      </div>
      ${saRecRows ? `
      <div class="section-subtitle mt-12">AI 추천 종목 현황</div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>종목</th><th>AI 판단</th><th>최종 점수</th><th>기술 점수</th><th>매크로</th><th>사유</th></tr></thead>
          <tbody>${saRecRows}</tbody>
        </table>
      </div>` : '<div class="text-muted mt-8">추천 데이터 없음 — 주식 분석 실행 후 표시됩니다</div>'}
      ${sa.macro?.reason ? `<div class="text-muted mt-8" style="font-size:11px;white-space:pre-line">${escHtml(sa.macro.reason)}</div>` : ''}
    </div>` : `
    <div class="section-box">
      <div class="section-title">주식 AI 분석 (KIS)</div>
      <div class="text-muted">주식 분석 미실행 — 대시보드에서 주식 분석 버튼을 눌러주세요</div>
    </div>`;

  // ── K-GSQS 실시간 스코어보드 ─────────────────────────────────────
  const ks = kstockStream || {};
  const ksScores = ks.scores || [];
  const ksRunning = ks.running || false;
  const ksMarketHours = ks.market_hours || false;
  const KS_SUB = ['trend','volume','orderbook','momentum','market','risk'];
  const KS_SUB_KR = {trend:'추세',volume:'거래량',orderbook:'호가',momentum:'모멘텀',market:'시장',risk:'리스크'};
  const KS_SUB_HELP = {trend:'kgsqs_trend',volume:'kgsqs_volume',orderbook:'kgsqs_orderbook',momentum:'kgsqs_momentum',market:'kgsqs_market',risk:'kgsqs_risk'};
  const _ksActionBadge = (a) => {
    const m = {STRONG_BUY:'badge-success',BUY:'badge-success',NOTIFY:'badge-warning',HOLD:'badge-muted',SKIP:'badge-danger'};
    return `<span class="badge ${m[a]||'badge-muted'}">${a}</span>`;
  };

  // 국내주식 행 어댑터
  const _ksRow = (s) => {
    const sub = s.sub_scores || {};
    const feat = s.features || {};
    const name = s.name && s.name !== s.symbol
      ? `<strong>${escHtml(s.symbol)}</strong><br><span style="font-size:10px;color:var(--text-muted)">${escHtml(s.name)}</span>`
      : `<strong>${escHtml(s.symbol)}</strong>`;
    return {
      name,
      badge: _ksActionBadge(s.action),
      score: s.total_score != null ? s.total_score.toFixed(1) : '-',
      scoreCls: s.total_score >= (ks.threshold_notify||72) ? 'text-success' : '',
      subs: sub,
      lastCol: feat.ret_5m != null ? `<span class="${feat.ret_5m>=0?'text-up':'text-down'}">${feat.ret_5m>=0?'+':''}${feat.ret_5m.toFixed(2)}%</span>` : '-',
      features: [
        {k:'체결강도', v: feat.strength != null ? feat.strength.toFixed(0) : '-', help:'kgsqs_strength'},
        {k:'거래량배수', v: feat.vol_ratio_5m != null ? feat.vol_ratio_5m.toFixed(2)+'x' : '-'},
        {k:'매수비율', v: feat.buy_ratio_5m != null ? (feat.buy_ratio_5m*100).toFixed(0)+'%' : '-'},
        {k:'호가비율', v: feat.bid_ask_ratio != null ? feat.bid_ask_ratio.toFixed(2) : '-', help:'kgsqs_bid_ask'},
        {k:'스프레드', v: feat.spread_bps != null ? feat.spread_bps.toFixed(1)+'bps' : '-', help:'bps'},
        {k:'ATR%', v: feat.atr_pct != null ? feat.atr_pct.toFixed(2)+'%' : '-', help:'atr'},
        {k:'1분수익', v: feat.ret_1m != null ? (feat.ret_1m>=0?'+':'')+feat.ret_1m.toFixed(3)+'%' : '-'},
        {k:'현재가', v: s.price != null ? s.price.toLocaleString()+'원' : '-'},
      ],
      blocked: s.hard_blocked ? (s.blocked_reason || '') : '',
    };
  };

  const ksStatus = `<span class="section-meta pipeline-status ${ksRunning ? (ksMarketHours ? 'active' : 'waiting') : 'offline'}">
    <span class="dot-inline ${ksRunning ? (ksMarketHours ? 'running' : 'warn') : 'muted'}"></span>${
    ksRunning ? (ksMarketHours ? '실시간 수신 중' : '장 시작 대기 중') : '파이프라인 미연결'}</span>`;

  const kgsqsHtml = buildScoreboardUnified({
    title: 'K-GSQS 실시간 스코어보드', helpKey: 'kgsqs_total', statusHtml: ksStatus,
    stats: [
      { label:'감시 종목', value:`${ksScores.length}종목` },
      { label:'알림 임계값', value:`${ks.threshold_notify || 72}pt↑`, help:'kgsqs_threshold' },
      { label:'자동 임계값', value:`${ks.threshold_auto || 82}pt↑`, help:'auto_threshold' },
      { label:'누적 신호 수', value:`${ks.signal_count || 0}건` },
      ...(ks.win_rate != null ? [{ label:'5분 승률', value:`${ks.win_rate}%`, cls: ks.win_rate>=55?'text-success':'text-warning' }] : []),
    ],
    subKeys: KS_SUB, subLabels: KS_SUB_KR, subHelp: KS_SUB_HELP,
    rows: ksScores.map(_ksRow),
    lastColLabel: '5분수익',
    footerNote: '▶ 행 클릭 시 상세 피처 보기 &nbsp;|&nbsp; 장 운영시간(09:05~15:15 KST)에 실시간 업데이트',
    emptyHtml: `<div class="text-muted" style="padding:16px 0;text-align:center">${
      ksRunning ? (ksMarketHours ? '스코어 데이터 집계 중입니다...' : '파이프라인 실행 중 — 장 운영 시간(09:05~15:15 KST)이 되면 실시간 K-GSQS 스코어가 자동으로 표시됩니다.') : '파이프라인 연결 대기 중... 잠시 후 자동으로 갱신됩니다.'
    }</div>`,
  });

  // ── K-GSQS 신호 이력 & 승률 ─────────────────────────────────────
  const ksig = kstockSignals;
  const ksigWr = ksig.win_rates || {};
  const horizonKr = { ret_1m:'1분', ret_3m:'3분', ret_5m:'5분', ret_15m:'15분' };
  const winRateBars = Object.entries(horizonKr).map(([k, label]) => {
    const wr = ksigWr[k];
    if (!wr) return `<div class="gsqs-stat"><div class="gsqs-stat-label">${label} 승률</div><div class="gsqs-stat-value text-muted">-</div></div>`;
    const cls = wr.win_rate >= 55 ? 'text-success' : wr.win_rate >= 45 ? '' : 'text-danger';
    return `<div class="gsqs-stat">
      <div class="gsqs-stat-label">${label} 승률</div>
      <div class="gsqs-stat-value ${cls}">${wr.win_rate}%<span class="text-muted" style="font-size:10px"> (${wr.count}건)</span></div>
    </div>`;
  }).join('');

  const ksigRecentRows = (ksig.recent || []).map(r => {
    const dBadge = { BUY_CANDIDATE:'badge-buy', STRONG_BUY:'badge-strong-buy', HOLD:'badge-muted' };
    const ret5 = r.ret_5m != null ? `<span class="${r.ret_5m > 0 ? 'text-success' : 'text-danger'}">${r.ret_5m > 0 ? '+' : ''}${(r.ret_5m * 100).toFixed(2)}%</span>` : '<span class="text-muted">대기</span>';
    const ts = r.ts_ms ? new Date(r.ts_ms).toLocaleString('ko-KR', { month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit' }) : '-';
    const symDisplay = r.name && r.name !== r.symbol
      ? `<strong>${escHtml(r.symbol || '-')}</strong><br><small style="color:var(--text-muted)">${escHtml(r.name)}</small>`
      : `<strong>${escHtml(r.symbol || '-')}</strong>`;
    return `<tr>
      <td>${ts}</td>
      <td>${symDisplay}</td>
      <td><span class="badge ${dBadge[r.decision] || 'badge-muted'}" style="font-size:10px">${r.decision || '-'}</span></td>
      <td>${r.score != null ? Number(r.score).toFixed(1) : '-'}</td>
      <td>${r.entry_price != null ? r.entry_price.toLocaleString() + '원' : '-'}</td>
      <td>${ret5}</td>
    </tr>`;
  }).join('');

  const symStatRows = (ksig.symbol_stats || []).map(s => {
    const sNameDisplay = s.name && s.name !== s.symbol
      ? `<strong>${escHtml(s.symbol)}</strong><br><small style="color:var(--text-muted)">${escHtml(s.name)}</small>`
      : `<strong>${escHtml(s.symbol)}</strong>`;
    return `<tr><td>${sNameDisplay}</td><td>${s.count}건</td>
    <td class="${(s.win_rate||0) >= 55 ? 'text-success' : ''}">${s.win_rate != null ? s.win_rate + '%' : '-'}</td>
    <td class="${(s.avg_ret||0) > 0 ? 'text-success' : 'text-danger'}">${s.avg_ret != null ? (s.avg_ret > 0 ? '+' : '') + s.avg_ret + '%' : '-'}</td></tr>`;
  }).join('');

  const kStockSignalsHtml = `
    <div class="section-box">
      <div class="section-title-row">
        <span class="section-title">K-GSQS 신호 이력 &amp; 승률${helpBtn('kgsqs_signals')}${helpBtn('kgsqs_winrate')}</span>
        <span class="section-meta">총 ${ksig.total_signals || 0}건 · 완료 ${ksig.completed_count || 0}건</span>
      </div>
      <div class="gsqs-stat-grid" style="margin-bottom:12px">
        ${winRateBars}
      </div>
      ${ksigRecentRows ? `
      <div class="section-subtitle">최근 신호 이력</div>
      <div class="table-wrap"><table>
        <thead><tr><th>시각</th><th>종목</th><th>결정</th><th>점수</th><th>진입가</th><th>5분 수익</th></tr></thead>
        <tbody>${ksigRecentRows}</tbody>
      </table></div>` : '<div class="text-muted mt-8">아직 기록된 신호 없음</div>'}
      ${symStatRows ? `
      <div class="section-subtitle mt-12">종목별 5분 승률 (최근 100신호)</div>
      <div class="table-wrap"><table>
        <thead><tr><th>종목</th><th>신호수</th><th>5분 승률</th><th>5분 평균수익</th></tr></thead>
        <tbody>${symStatRows}</tbody>
      </table></div>` : ''}
    </div>`;

  // ── KIS 계좌 & 포지션 ─────────────────────────────────────────────
  const kpos = kstockPositions;
  const posRows = (kpos.positions || []).map(p => {
    const pnlCls = (p.pnl_pct || 0) > 0 ? 'text-success' : (p.pnl_pct || 0) < 0 ? 'text-danger' : '';
    // 동적 TP/SL 표시
    let tpslHtml = '-';
    if (p.tpsl) {
      const t = p.tpsl;
      const gradeColor = t.grade === 'A' ? '#ef4444' : t.grade === 'B' ? '#9a9aa1' : t.grade === 'C' ? '#F0B90B' : '#6e6e76';
      const mktIcon = t.market_state === 'TREND_UP' ? '↑' : t.market_state === 'TREND_DOWN' ? '↓' : t.market_state === 'CRASH' ? '⚠' : '→';
      // P&L 게이지 바 (SL ~ TP 사이에서 현재 위치)
      let gaugeHtml = '';
      if (p.pnl_pct != null) {
        const sl = t.sl_pct, tp = t.tp_pct;
        const range = tp - sl;
        const pos_ratio = range > 0 ? Math.max(0, Math.min(1, (p.pnl_pct - sl) / range)) : 0.5;
        const gaugeColor = p.pnl_pct >= tp ? '#ef4444' : p.pnl_pct <= sl ? '#3b82f6' : p.pnl_pct > 0 ? '#ef4444' : '#c73050';
        gaugeHtml = `<div style="margin-top:3px;height:4px;background:var(--border);border-radius:2px;position:relative">
          <div style="position:absolute;left:${(pos_ratio*100).toFixed(0)}%;top:-1px;width:6px;height:6px;border-radius:50%;background:${gaugeColor};transform:translateX(-50%)"></div>
        </div>`;
      }
      tpslHtml = `<div style="font-size:11px;line-height:1.4">
        <span style="color:#ef4444">TP +${t.tp_pct}%</span> / <span style="color:#3b82f6">SL ${t.sl_pct}%</span>
        <span style="color:var(--text-muted);margin-left:4px">ATR ${t.atr_pct}%</span><br>
        <span style="color:${gradeColor}">${t.grade}등급</span>
        <span style="color:var(--text-muted)">${mktIcon}${t.market_state.replace('_',' ')}</span>
        ${gaugeHtml}
      </div>`;
    }
    return `<tr>
      <td><strong>${escHtml(p.symbol || '-')}</strong></td>
      <td>${escHtml(p.name || '-')}</td>
      <td>${p.quantity != null ? p.quantity.toLocaleString() : '-'}주</td>
      <td>${p.avg_price != null ? Math.round(p.avg_price).toLocaleString() + '원' : '-'}</td>
      <td>${p.current_price != null ? p.current_price.toLocaleString() + '원' : '-'}</td>
      <td>${p.market_value != null ? fmt_krw(p.market_value) : '-'}</td>
      <td class="${pnlCls}">${p.pnl_pct != null ? (p.pnl_pct > 0 ? '+' : '') + p.pnl_pct.toFixed(2) + '%' : '-'}</td>
      <td class="${pnlCls}">${p.pnl_amt != null ? (p.pnl_amt > 0 ? '+' : '') + Math.round(p.pnl_amt).toLocaleString() + '원' : '-'}</td>
      <td>${tpslHtml}</td>
    </tr>`;
  }).join('');

  const kStockPositionsHtml = kpos.exists ? `
    <div class="section-box">
      <div class="section-title-row">
        <span class="section-title">KIS 계좌 &amp; 포지션${helpBtn('kstock_positions')}</span>
        <span class="section-meta ${kpos.stale ? 'text-warning' : 'text-muted'}">${kpos.snapshot_time ? fmt_time(kpos.snapshot_time) + (kpos.stale ? ' (오래됨)' : '') : '-'} · <span class="env-badge ${kpos.kis_env === 'live' ? 'live' : 'paper'}">${kpos.kis_env === 'live' ? '실전' : '모의'}</span></span>
      </div>
      <div class="gsqs-stat-grid" style="margin-bottom:12px">
        <div class="gsqs-stat"><div class="gsqs-stat-label">현금</div><div class="gsqs-stat-value">${fmt_krw(kpos.cash)}</div></div>
        <div class="gsqs-stat"><div class="gsqs-stat-label">주식 평가액</div><div class="gsqs-stat-value">${fmt_krw(kpos.total_stock_value)}</div></div>
        <div class="gsqs-stat"><div class="gsqs-stat-label">총 평가자산</div><div class="gsqs-stat-value">${fmt_krw(kpos.total_equity)}</div></div>
        <div class="gsqs-stat"><div class="gsqs-stat-label">평가 손익</div><div class="gsqs-stat-value ${(kpos.total_pnl||0) > 0 ? 'text-success' : (kpos.total_pnl||0) < 0 ? 'text-danger' : ''}">${kpos.total_pnl != null ? (kpos.total_pnl > 0 ? '+' : '') + Math.round(kpos.total_pnl).toLocaleString() + '원' : '-'}</div></div>
      </div>
      ${posRows ? `<div class="table-wrap"><table>
        <thead><tr><th>종목코드</th><th>종목명</th><th>수량</th><th>평균단가</th><th>현재가</th><th>평가금액</th><th>수익률</th><th>손익</th><th>동적 TP/SL</th></tr></thead>
        <tbody>${posRows}</tbody>
      </table></div>` : '<div class="text-muted">보유 종목 없음</div>'}
    </div>` : `
    <div class="section-box">
      <div class="section-title">KIS 계좌 & 포지션${helpBtn('kstock_positions')}</div>
      <div class="text-muted">계좌 스냅샷 없음 — 주식 분석 실행 시 자동 저장됩니다</div>
    </div>`;

  // ── 감시 종목 유니버스 ────────────────────────────────────────────
  const kuniv = kstockUniverse;
  const mktStatusCls = kuniv.market_status === 'open' ? 'text-success' : 'text-muted';
  const mktStatusLabel = kuniv.market_status === 'open'
    ? `<span class="dot-inline running"></span>장 운영 중`
    : `<span class="dot-inline muted"></span>장 마감 (${kuniv.current_time_kst} KST)`;
  const univRows = (kuniv.symbols || []).map(s => {
    const ageCls = (s.bar_age_min || 0) < 5 ? 'text-success' : (s.bar_age_min || 0) < 30 ? '' : 'text-muted';
    const nameLabel = s.name && s.name !== s.symbol ? s.name : '';
    return `<tr>
      <td><strong>${s.symbol}</strong>${nameLabel ? `<br><span style="font-size:11px;color:var(--text-muted)">${nameLabel}</span>` : ''}</td>
      <td>${s.last_close != null ? s.last_close.toLocaleString() + '원' : '-'}</td>
      <td>${s.last_volume != null ? s.last_volume.toLocaleString() : '-'}</td>
      <td>${s.bar_count != null ? s.bar_count + '개' : '-'}</td>
      <td class="${ageCls}">${s.bar_age_min != null ? s.bar_age_min + '분 전' : '-'}</td>
    </tr>`;
  }).join('');

  const kuAutoTag = kuniv.auto_universe !== false
    ? `<span style="background:#ef4444;color:#000;font-size:10px;padding:1px 6px;border-radius:4px;margin-left:6px">동적 갱신</span>`
    : `<span style="background:var(--bg-card);color:var(--text-muted);font-size:10px;padding:1px 6px;border-radius:4px;margin-left:6px">고정 목록</span>`;

  const kStockUniverseHtml = `
    <div class="section-box">
      <div class="section-title-row">
        <span class="section-title">감시 종목 유니버스 (${kuniv.symbol_count || 0}개)${kuAutoTag}${helpBtn('kstock_universe')}</span>
        <span class="section-meta ${mktStatusCls}">${mktStatusLabel}</span>
      </div>
      <div style="font-size:11px;color:var(--text-muted);margin-bottom:10px">
        장 운영: ${kuniv.market_open_at || '09:05'} ~ ${kuniv.market_close_at || '15:15'} KST &nbsp;|&nbsp;
        KOSPI 지수 구독${helpBtn('kstock_kospi')}: H0UPCNT0 (0001)
        ${kuniv.auto_universe !== false ? `&nbsp;|&nbsp; 거래대금 상위 ${kuniv.universe_size || 30}개 자동선정 · 30분 주기 갱신` : ''}
      </div>
      ${univRows ? `<div class="table-wrap"><table>
        <thead><tr><th>종목코드</th><th>마지막 체결가</th><th>마지막 거래량</th><th>1분봉 수</th><th>데이터 신선도</th></tr></thead>
        <tbody>${univRows}</tbody>
      </table></div>` : '<div class="text-muted">파이프라인 실행 전 — 기본 감시 대상: 삼성전자·SK하이닉스·NAVER·현대차 등 10종목</div>'}
    </div>`;

  // ── K-GSQS 가중치 현황 ────────────────────────────────────────────
  const kgsqsWeights = [
    { key: 'trend',     label: '추세',   pct: 20, color: '#ef4444' },
    { key: 'volume',    label: '거래량', pct: 20, color: '#9a9aa1' },
    { key: 'orderbook', label: '호가',   pct: 20, color: '#F0B90B' },
    { key: 'momentum',  label: '모멘텀', pct: 20, color: '#6e6e76' },
    { key: 'market',    label: '시장',   pct: 10, color: '#ef4444' },
    { key: 'risk',      label: '리스크', pct: 10, color: '#9a9aa1' },
  ];
  const weightBars = kgsqsWeights.map(w => `
    <div class="kgsqs-weight-row">
      <span class="kgsqs-weight-label">${w.label}${helpBtn('kgsqs_' + w.key)}</span>
      <div class="kgsqs-weight-bar-wrap">
        <div class="kgsqs-weight-bar" style="width:${w.pct * 3}%;background:${w.color}"></div>
      </div>
      <span class="kgsqs-weight-pct">${w.pct}%</span>
    </div>`).join('');

  const kgsqsWeightsHtml = `
    <div class="section-box">
      <div class="section-title">K-GSQS 가중치 구성${helpBtn('kgsqs_weights')}</div>
      <div style="font-size:11px;color:var(--text-muted);margin-bottom:10px">
        총점 = 각 서브스코어(0~100pt) × 가중치의 합 &nbsp;|&nbsp; 임계값: NOTIFY ${ks.threshold_notify || 72}pt / AUTO ${ks.threshold_auto || 82}pt / STRONG 88pt
      </div>
      <div class="kgsqs-weights-grid">
        ${weightBars}
      </div>
    </div>`;

  // ── K-GSQS 가중치 자동최적화 (국내주식/해외주식 공통 빌더) ──────────────────
  function buildKgsqsWeightOptHtml(wData, assetLabel) {
    const w = wData || {};
    const n        = w.n_complete_signals || 0;
    const minN     = w.min_for_optimization || 50;
    const pct      = Math.min(100, Math.round(n / minN * 100));
    const ready    = w.ready_to_optimize || false;
    const nextAt   = w.next_run_at || 0;
    const optAt    = w.last_optimized_at;
    const expWr    = w.last_win_rate;
    const defWr    = w.last_default_win_rate;
    const improv   = w.last_improvement;
    const applied  = w.last_applied;
    const curW     = w.current_weights || {};
    const KLABELS  = { trend:'추세', volume:'거래량', orderbook:'호가', momentum:'모멘텀', market:'시장', risk:'리스크' };
    const KCOLORS  = { trend:'#F0B90B', volume:'#ef4444', orderbook:'#9a9aa1', momentum:'#6e6e76', market:'#ef4444', risk:'#9a9aa1' };

    const appliedBadge = applied === false
      ? '<span class="badge badge-warning">미적용</span>'
      : applied === true ? '<span class="badge badge-success">적용됨</span>' : '';

    const weightRows = Object.entries(curW).map(([k, v]) => {
      const pctW = Math.round(v * 100);
      return `<tr>
        <td>${KLABELS[k] || k}${helpBtn('kgsqs_'+k)}</td>
        <td><div style="display:flex;align-items:center;gap:6px">
          <div style="width:${pctW * 3}px;height:6px;border-radius:3px;background:${KCOLORS[k]||'#888'}"></div>
          <span>${pctW}%</span>
        </div></td>
      </tr>`;
    }).join('');

    return `
    <div class="section-box">
      <div class="section-title">K-GSQS ${assetLabel} 가중치 자동 최적화${helpBtn('weight_optimizer')}</div>
      <div class="weight-progress-wrap">
        <div class="weight-progress-label">
          <span>완성 신호 ${n} / ${minN}건 ${w.error ? '<span class="text-danger" style="font-size:11px">(오류)</span>' : ''}</span>
          <span>${ready ? '<span class="badge badge-success">최적화 가능</span>' : (nextAt > 0 ? nextAt + '건 남음' : pct + '%')}</span>
        </div>
        <div class="weight-progress-track">
          <div class="weight-progress-fill ${ready ? 'weight-progress-done' : ''}" style="width:${pct}%"></div>
        </div>
      </div>

      ${optAt ? `
      <div class="gsqs-stat-grid mt-12">
        <div class="gsqs-stat"><div class="gsqs-stat-label">기본 승률</div><div class="gsqs-stat-value">${defWr != null ? (defWr * 100).toFixed(1) + '%' : '-'}</div></div>
        <div class="gsqs-stat"><div class="gsqs-stat-label">최적화 후 승률</div><div class="gsqs-stat-value text-success">${expWr != null ? (expWr * 100).toFixed(1) + '%' : '-'}</div></div>
        <div class="gsqs-stat"><div class="gsqs-stat-label">개선 ${appliedBadge}</div>
          <div class="gsqs-stat-value ${improv > 0 ? 'text-success' : 'text-danger'}">
            ${improv != null ? (improv >= 0 ? '+' : '') + (improv * 100).toFixed(1) + '%' : '-'}
          </div></div>
        <div class="gsqs-stat"><div class="gsqs-stat-label">최적화 시각</div><div class="gsqs-stat-value" style="font-size:11px">${optAt}</div></div>
      </div>
      ${applied === false ? '<div class="weight-no-apply-note">퇴보 방지: 최적화된 가중치가 기본값 대비 성능이 낮아 적용되지 않았습니다.</div>' : ''}
      ` : `<div class="text-muted mt-8" style="font-size:12px">완성 신호 ${minN}건 이상 축적되면 자동으로 가중치를 최적화합니다 (국내주식: 장중 매 신호 완성 시 자동 체크)</div>`}

      <div class="section-subtitle mt-12">현재 적용 가중치</div>
      <div class="table-wrap"><table>
        <thead><tr><th>서브스코어</th><th>비중</th></tr></thead>
        <tbody>${weightRows}</tbody>
      </table></div>
    </div>`;
  }

  const kstockWeightOptHtml = buildKgsqsWeightOptHtml(kstockWeights, '국내주식');
  const overseasWeightOptHtml = buildKgsqsWeightOptHtml(overseasWeights, '해외주식');

  // ── 국내/해외 체결오차 섹션 빌더 ────────────────────────────────────
  function buildKstockSlippageHtml(slipData, assetLabel, currency, helpKey) {
    const d = slipData || {};
    const entries = d.entries || [];
    const stats = d.stats || {};
    const avgBps = stats.avg_slippage_bps;
    const maxBps = stats.max_slippage_bps;
    const p90Bps = stats.p90_slippage_bps;

    const statCards = entries.length > 0 ? `
      <div class="cards" style="margin-bottom:10px">
        <div class="card ${avgBps == null ? '' : avgBps <= 5 ? 'ok' : avgBps <= 10 ? '' : 'danger'}">
          <div class="card-label">평균 체결오차</div>
          <div class="card-value" style="font-size:18px">${avgBps != null ? avgBps.toFixed(1) + ' bps' : '-'}</div>
          <div class="card-sub">${avgBps != null ? (avgBps <= 5 ? '<span class="inline-ok">양호</span>' : avgBps <= 10 ? '<span class="inline-warn">주의</span>' : '<span class="inline-err">높음</span>') : '-'}</div>
        </div>
        <div class="card">
          <div class="card-label">최대 체결오차</div>
          <div class="card-value" style="font-size:18px">${maxBps != null ? maxBps.toFixed(1) + ' bps' : '-'}</div>
          <div class="card-sub">전체 기간</div>
        </div>
        <div class="card">
          <div class="card-label">P90 체결오차</div>
          <div class="card-value" style="font-size:18px">${p90Bps != null ? p90Bps.toFixed(1) + ' bps' : '-'}</div>
          <div class="card-sub">상위 10% 기준</div>
        </div>
        <div class="card">
          <div class="card-label">기록 건수</div>
          <div class="card-value" style="font-size:18px">${stats.sample_count != null ? stats.sample_count : '-'}</div>
          <div class="card-sub">전체 ${d.total || 0}건</div>
        </div>
      </div>` : '';

    const slipRows = entries.slice(0, 20).map(e => {
      const bps = e.slippage_bps != null ? parseFloat(e.slippage_bps) : null;
      const bpsCls = bps != null && bps > 10 ? 'text-danger' : bps != null && bps > 5 ? 'text-warning' : '';
      const priceFmt = currency === 'USD'
        ? (v => v != null ? '$' + parseFloat(v).toFixed(4) : '-')
        : (v => v != null ? parseFloat(v).toLocaleString() + '원' : '-');
      const amtFmt = currency === 'USD'
        ? (v => v != null ? '$' + parseFloat(v).toLocaleString() : '-')
        : (v => fmt_krw(v));
      return `<tr>
        <td><strong>${escHtml(e.symbol || '-')}</strong></td>
        <td><span class="badge ${e.side === 'buy' ? 'badge-success' : 'badge-danger'}">${e.side === 'buy' ? '매수' : '매도'}</span></td>
        <td>${amtFmt(e.order_krw)}</td>
        <td style="font-size:11px">${priceFmt(e.limit_price)}</td>
        <td style="font-size:11px">${priceFmt(e.fill_price)}</td>
        <td class="${bpsCls}">${bps != null ? bps.toFixed(1) + ' bps' : '-'}</td>
        <td style="font-size:10px;color:var(--text-muted)">${e.ts ? e.ts.replace('T', ' ').slice(0, 16) : '-'}</td>
      </tr>`;
    }).join('');

    const noDataNote = currency === 'USD'
      ? '거래내역 탭에서 해외주식 탭을 조회하면 자동으로 기록됩니다.'
      : '거래내역 탭에서 국내주식 탭을 조회하면 자동으로 기록됩니다.';

    return `<div class="section-box">
      <div class="section-title">${assetLabel} 체결오차 상세 기록${helpBtn(helpKey)}</div>
      <div class="trade-explain-strip" style="margin-bottom:8px">
        <strong>체결오차(슬리피지)</strong> — 지정가 주문가와 실제 체결 평균가의 차이입니다. 5bps(0.05%) 이하면 정상, 높을수록 불리한 조건에서 체결된 것입니다.
      </div>
      ${entries.length > 0 ? `
      ${statCards}
      <div class="table-wrap"><table>
        <thead><tr><th>종목</th><th>방향</th><th>주문금액</th><th>주문가</th><th>체결가</th><th>체결오차${helpBtn('bps')}</th><th>시각</th></tr></thead>
        <tbody>${slipRows}</tbody>
      </table></div>
      <div class="text-muted mt-8" style="font-size:11px">최근 ${entries.length}건 표시 (전체 ${d.total || 0}건) — 거래내역 탭 조회 시 자동 누적됩니다</div>
      ` : `<div class="text-muted">체결오차 기록 없음 — ${noDataNote}</div>`}
    </div>`;
  }

  const kstockSlippageHtml   = buildKstockSlippageHtml(kstockSlippage,  '국내주식', 'KRW', 'kstock_slippage');
  const overseasSlippageHtml = buildKstockSlippageHtml(overseasSlippage, '해외주식', 'USD', 'os_slippage');

  // ── 해외주식 K-GSQS 스코어보드 ─────────────────────────────────────
  const os = overseasStream || {};
  const osScores = os.scores || [];
  const osRunning = os.running || false;
  const osMarketHours = os.market_hours || false;

  const OS_NAMES = {
    NVDA:'NVIDIA', AAPL:'Apple', MSFT:'Microsoft', TSLA:'Tesla',
    META:'Meta', AMZN:'Amazon', GOOGL:'Alphabet', AVGO:'Broadcom',
    AMD:'AMD', MU:'Micron', INTC:'Intel',
    JPM:'JP Morgan', GS:'Goldman Sachs', XOM:'ExxonMobil',
    SPY:'S&P500 ETF', QQQ:'Nasdaq 100 ETF', ONEQ:'Nasdaq Composite ETF',
    TQQQ:'QQQ 3x Bull', SQQQ:'QQQ 3x Bear', SPXL:'S&P500 3x Bull', SPXS:'S&P500 3x Bear',
    SOXL:'반도체 3x Bull', SOXS:'반도체 3x Bear', FNGU:'FANG+ 3x Bull', FNGD:'FANG+ 3x Bear',
    LABU:'바이오 3x Bull', LABD:'바이오 3x Bear', UVXY:'VIX Short',
    TLT:'미국채 20Y ETF', GLD:'금 ETF', SLV:'은 ETF',
    BABA:'Alibaba', JD:'JD.com', PDD:'Temu(PDD)',
  };

  const osSymbolDisplay = (sym, showName = false) => {
    // "NASD:NVDA" → "NVDA <small>(NASD)</small>"
    const parts = sym.split(':');
    const ticker = parts.length === 2 ? parts[1] : sym;
    const exchange = parts.length === 2 ? parts[0] : null;
    const name = OS_NAMES[ticker];
    let html = `<strong>${escHtml(ticker)}</strong>`;
    if (exchange) html += ` <small style="color:var(--text-muted)">(${escHtml(exchange)})</small>`;
    if (showName && name) html += `<br><small style="color:var(--text-muted)">${escHtml(name)}</small>`;
    return html;
  };

  // 해외주식 행 어댑터
  const _osRow = (s) => ({
    name: osSymbolDisplay(s.symbol, true),
    badge: _ksActionBadge(s.action),
    score: s.total_score != null ? s.total_score.toFixed(1) : '-',
    scoreCls: s.total_score >= (os.threshold_notify || 72) ? 'text-success' : '',
    subs: s.sub_scores || {},
    lastCol: s.price != null ? `$${s.price.toLocaleString()}` : '-',
    blocked: s.hard_blocked ? (s.blocked_reason || '') : '',
  });

  const osStatus = `<span class="section-meta pipeline-status ${osRunning ? (osMarketHours ? 'active' : 'waiting') : 'offline'}">
    <span class="dot-inline ${osRunning ? (osMarketHours ? 'running' : 'warn') : 'muted'}"></span>${
    osRunning ? (osMarketHours ? '실시간 수신 중' : '장 시작 대기 중') : '파이프라인 미연결'}${helpBtn('os_stream')}</span>`;

  const overseasKgsqsHtml = buildScoreboardUnified({
    title: 'K-GSQS 해외주식 스코어보드', helpKey: 'os_scoreboard', statusHtml: osStatus,
    stats: [
      { label:'감시 심볼', value:`${osScores.length}종목`, help:'os_universe' },
      { label:'알림 임계값', value:`${os.threshold_notify || 72}pt↑`, help:'os_threshold_notify' },
      { label:'자동 임계값', value:`${os.threshold_auto || 82}pt↑`, help:'os_threshold_auto' },
      { label:'누적 신호 수', value:`${os.signal_count || 0}건`, help:'os_signal_count' },
    ],
    subKeys: KS_SUB, subLabels: KS_SUB_KR, subHelp: KS_SUB_HELP,
    rows: osScores.map(_osRow),
    lastColLabel: '현재가',
    footerNote: '▶ 행 클릭 시 상세 피처 보기 &nbsp;|&nbsp; 미국 정규장(22:30~05:00 KST)에 실시간 업데이트',
    emptyHtml: `<div class="text-muted" style="padding:16px 0;text-align:center">${
      osRunning ? (osMarketHours ? '스코어 데이터 집계 중입니다...' : '파이프라인 실행 중 — 미국 장 시간(22:30~05:00 KST)이 되면 실시간 K-GSQS 스코어가 자동으로 표시됩니다.') : '파이프라인 연결 대기 중... 잠시 후 자동으로 갱신됩니다.'
    }</div>`,
  });

  // ── 해외주식 신호 이력 ──────────────────────────────────────────────
  const ossig = overseasSignals || {};
  const ossigWr = ossig.win_rates || {};
  const osWinRateBars = Object.entries(horizonKr).map(([k, label]) => {
    const wr = ossigWr[k];
    if (!wr) return `<div class="gsqs-stat"><div class="gsqs-stat-label">${label} 승률</div><div class="gsqs-stat-value text-muted">-</div></div>`;
    const cls = wr.win_rate >= 55 ? 'text-success' : wr.win_rate >= 45 ? '' : 'text-danger';
    return `<div class="gsqs-stat">
      <div class="gsqs-stat-label">${label} 승률</div>
      <div class="gsqs-stat-value ${cls}">${wr.win_rate}%<span class="text-muted" style="font-size:10px"> (${wr.count}건)</span></div>
    </div>`;
  }).join('');

  const ossigRecentRows = (ossig.recent || []).map(r => {
    const dBadge = { BUY_CANDIDATE:'badge-buy', STRONG_BUY:'badge-strong-buy', HOLD:'badge-muted' };
    const ret5 = r.ret_5m != null ? `<span class="${r.ret_5m > 0 ? 'text-success' : 'text-danger'}">${r.ret_5m > 0 ? '+' : ''}${(r.ret_5m * 100).toFixed(2)}%</span>` : '<span class="text-muted">대기</span>';
    const ts = r.ts_ms ? new Date(r.ts_ms).toLocaleString('ko-KR', { month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit' }) : '-';
    return `<tr>
      <td>${ts}</td>
      <td>${osSymbolDisplay(r.symbol || '-', true)}</td>
      <td><span class="badge ${dBadge[r.decision] || 'badge-muted'}" style="font-size:10px">${r.decision || '-'}</span></td>
      <td>${r.score != null ? Number(r.score).toFixed(1) : '-'}</td>
      <td>${r.entry_price != null ? '$' + r.entry_price.toLocaleString() : '-'}</td>
      <td>${ret5}</td>
    </tr>`;
  }).join('');

  const overseasSignalsHtml = `
    <div class="section-box">
      <div class="section-title-row">
        <span class="section-title">해외주식 K-GSQS 신호 이력 &amp; 승률${helpBtn('os_signals')}${helpBtn('kgsqs_winrate')}</span>
        <span class="section-meta">총 ${(ossig.recent || []).length}건</span>
      </div>
      <div class="gsqs-stat-grid" style="margin-bottom:12px">
        ${osWinRateBars}
      </div>
      ${ossigRecentRows ? `
      <div class="section-subtitle">최근 신호 이력</div>
      <div class="table-wrap"><table>
        <thead><tr><th>시각</th><th>심볼</th><th>결정</th><th>점수</th><th>진입가</th><th>5분 수익</th></tr></thead>
        <tbody>${ossigRecentRows}</tbody>
      </table></div>` : '<div class="text-muted mt-8">아직 기록된 신호 없음</div>'}
    </div>`;

  // ── 국내/해외 동적 포지션 사이징 빌더 ───────────────────────────────────
  function buildKstockSizingHtml(sizData, assetLabel, currency) {
    const d = sizData || {};
    const recs = d.recommendations || [];
    const availCash = d.available_cash || 0;
    const totalEquity = d.total_equity || 0;
    const baseAlloc = d.base_alloc_krw || 0;
    const maxAlloc = d.max_alloc_krw || 0;
    const env = `<span class="env-badge ${d.kis_env === 'live' ? 'live' : 'paper'}">${d.kis_env === 'live' ? '실전' : '모의'}</span>`;
    const envCls = d.kis_env === 'live' ? 'text-danger' : 'text-warning';
    const isCurUSD = currency === 'USD';
    const usdRate = d.usd_rate || 1300;

    const fmtAmt = v => isCurUSD
      ? `$${(v / usdRate).toFixed(2)} <small style="color:var(--text-muted)">(${fmt_krw(v)})</small>`
      : fmt_krw(v);

    const summaryCards = `
      <div class="cards" style="margin-bottom:10px">
        <div class="card">
          <div class="card-label">가용 현금</div>
          <div class="card-value" style="font-size:16px">${isCurUSD ? '$' + (availCash / usdRate).toFixed(2) : fmt_krw(availCash)}</div>
          <div class="card-sub">${isCurUSD ? fmt_krw(availCash) : ''}</div>
        </div>
        <div class="card">
          <div class="card-label">총 평가금액</div>
          <div class="card-value" style="font-size:16px">${isCurUSD ? '$' + (totalEquity / usdRate).toFixed(2) : fmt_krw(totalEquity)}</div>
          <div class="card-sub">${isCurUSD ? fmt_krw(totalEquity) : ''}</div>
        </div>
        <div class="card">
          <div class="card-label">기본 진입 한도</div>
          <div class="card-value" style="font-size:16px">${fmt_krw(baseAlloc)}</div>
          <div class="card-sub">NOTIFY × 1.0</div>
        </div>
        <div class="card">
          <div class="card-label">최대 진입 한도</div>
          <div class="card-value" style="font-size:16px">${fmt_krw(maxAlloc)}</div>
          <div class="card-sub">STRONG × 1.5</div>
        </div>
      </div>`;

    const recRows = recs.map(r => {
      const scoreCls = r.score >= 88 ? 'text-success' : r.score >= 82 ? 'text-warning' : '';
      const factorBadge = r.score_label === 'STRONG'
        ? '<span class="badge badge-success">STRONG</span>'
        : r.score_label === 'AUTO'
        ? '<span class="badge badge-warning">AUTO</span>'
        : '<span class="badge">NOTIFY</span>';
      const tpsl = r.tp_pct != null
        ? `<span style="color:#ef4444">+${r.tp_pct}%</span>/<span style="color:#3b82f6">${r.sl_pct}%</span>`
        : '-';
      const mktState = r.market_state ? `<br><small style="color:var(--text-muted)">${r.market_state}</small>` : '';
      const priceStr = isCurUSD
        ? `$${(r.current_price / usdRate).toFixed(2)}`
        : r.current_price ? r.current_price.toLocaleString() + '원' : '-';
      const recAmtStr = isCurUSD
        ? `$${(r.recommended_krw / usdRate).toFixed(2)}<br><small>${fmt_krw(r.recommended_krw)}</small>`
        : fmt_krw(r.recommended_krw);
      return `<tr>
        <td><strong>${escHtml(r.symbol)}</strong><br><small style="color:var(--text-muted)">${escHtml(r.name || r.symbol)}</small></td>
        <td class="${scoreCls}">${r.score.toFixed(1)}</td>
        <td>${factorBadge}<br><small>×${r.score_factor}</small></td>
        <td style="font-size:11px">${priceStr}</td>
        <td>${recAmtStr}</td>
        <td style="text-align:center">${r.recommended_shares}주</td>
        <td>${tpsl}${mktState}</td>
        <td>${r.blocked ? '블록' : r.atr_pct != null ? r.atr_pct.toFixed(1) + '%' : '-'}</td>
      </tr>`;
    }).join('');

    return `<div class="section-box">
      <div class="section-title-row">
        <span class="section-title">${assetLabel} 동적 포지션 사이징${helpBtn('position_sizing')}</span>
        <span class="section-meta ${envCls}">${env}</span>
      </div>
      <div class="trade-explain-strip" style="margin-bottom:8px">
        K-GSQS 점수에 따라 NOTIFY(×1.0)·AUTO(×1.25)·STRONG(×1.5) 비중 팩터를 적용한 권고 주문금액입니다.
      </div>
      ${summaryCards}
      ${recs.length > 0 ? `
      <div class="section-subtitle">임계값 이상 종목 권고 (${recs.length}개)</div>
      <div class="table-wrap"><table>
        <thead><tr><th>종목</th><th>점수</th><th>신호</th><th>현재가</th><th>권고 금액</th><th>권고 주수</th><th>TP/SL${helpBtn('tp_sl')}</th><th>ATR${helpBtn('atr')}</th></tr></thead>
        <tbody>${recRows}</tbody>
      </table></div>
      <div class="text-muted mt-8" style="font-size:11px">파이프라인 실행 중일 때만 실시간 권고가 생성됩니다. 점수 ≥72(NOTIFY) 종목만 표시됩니다.</div>
      ` : `<div class="text-muted">${d.error ? '계산 오류: ' + escHtml(d.error) : '현재 임계값(72점) 이상 종목 없음 — 파이프라인 실행 후 자동 갱신됩니다'}</div>`}
    </div>`;
  }

  const kstockSizingHtml   = buildKstockSizingHtml(kstockSizing,  '국내주식', 'KRW');
  const overseasSizingHtml = buildKstockSizingHtml(overseasSizing, '해외주식', 'USD');

  // ── 국내/해외 리콘사일 섹션 빌더 ─────────────────────────────────────
  function buildKstockReconcileHtml(recData, assetLabel) {
    const d = recData || {};
    const ok = d.success === true;
    const matched = d.matched || [];
    const missingSnap = d.missing_in_snap || [];
    const missingBroker = d.missing_in_broker || [];
    const qtyMismatch = d.quantity_mismatch || [];
    const warns = d.warnings || [];
    const snapAge = d.snap_age_min;
    const stale = d.stale || false;

    const statusIcon = ok ? '<span class="inline-ok">✓</span>' : '<span class="inline-warn">!</span>';
    const statusLabel = ok ? '정상' : '불일치 감지';
    const statusCls = ok ? 'ok' : 'danger';

    const warnItems = warns.map(w => `<li class="text-warning" style="font-size:12px">${escHtml(w)}</li>`).join('');
    const missingSnapItems = missingSnap.map(i =>
      `<li class="text-danger" style="font-size:12px"><span class="dot-inline stopped"></span>${escHtml(i.symbol)}: 브로커에 있으나 스냅샷에 없음 (${i.broker_qty || i.snap_qty}주)</li>`
    ).join('');
    const missingBrokerItems = missingBroker.map(i =>
      `<li class="text-danger" style="font-size:12px"><span class="dot-inline stopped"></span>${escHtml(i.symbol)}: 스냅샷에 있으나 브로커에 없음 (${i.snap_qty}주)</li>`
    ).join('');
    const mismatchItems = qtyMismatch.map(i =>
      `<li class="text-warning" style="font-size:12px"><span class="dot-inline warn"></span>${escHtml(i.symbol)}: 수량 불일치 — 브로커 ${i.broker_qty}주 vs 스냅샷 ${i.snap_qty}주</li>`
    ).join('');

    const issues = missingSnapItems + missingBrokerItems + mismatchItems + warnItems;

    return `<div class="section-box">
      <div class="section-title">${assetLabel} 리콘사일 (계좌 대사)${helpBtn('reconcile')}</div>
      <div class="cards" style="margin-bottom:12px">
        <div class="card ${statusCls}">
          <div class="card-label">대사 결과</div>
          <div class="card-value" style="font-size:16px">${statusIcon} ${statusLabel}</div>
          <div class="card-sub">${d.timestamp ? d.timestamp.slice(0, 16) : '-'}</div>
        </div>
        <div class="card ${matched.length > 0 ? 'ok' : ''}">
          <div class="card-label">일치 종목</div>
          <div class="card-value">${matched.length}개</div>
          <div class="card-sub">${matched.slice(0, 5).join(', ') || '-'}</div>
        </div>
        <div class="card ${(missingSnap.length + missingBroker.length) > 0 ? 'danger' : ''}">
          <div class="card-label">누락 종목</div>
          <div class="card-value">${missingSnap.length + missingBroker.length}개</div>
          <div class="card-sub">스냅샷 누락: ${missingSnap.length} | 브로커 누락: ${missingBroker.length}</div>
        </div>
        ${snapAge != null ? `<div class="card ${stale ? 'danger' : ''}">
          <div class="card-label">스냅샷 경과</div>
          <div class="card-value">${snapAge}분</div>
          <div class="card-sub">${stale ? '<span class="inline-warn">오래된 데이터</span>' : '최신'}</div>
        </div>` : ''}
      </div>
      ${issues ? `<ul style="margin:0;padding-left:16px">${issues}</ul>` : (ok ? '<div class="text-muted" style="font-size:12px">✅ 모든 포지션 일치 — 계좌 불일치 없음</div>' : '')}
      ${d.error ? `<div class="text-danger mt-8" style="font-size:12px">오류: ${escHtml(d.error)}</div>` : ''}
    </div>`;
  }

  const kstockReconcileHtml   = buildKstockReconcileHtml(kstockReconcile,  '국내주식');
  const overseasReconcileHtml = buildKstockReconcileHtml(overseasReconcile, '해외주식');

  // ── 해외주식 보유 포지션 ──────────────────────────────────────────────
  const osp = overseasPositions || {};
  const ospPositions = osp.positions || [];
  const ospEnv = `<span class="env-badge ${osp.kis_env === 'live' ? 'live' : 'paper'}">${osp.kis_env === 'live' ? '실전' : '모의'}</span>`;
  const ospEnvCls = osp.kis_env === 'live' ? 'text-danger' : 'text-warning';
  const ospRate = osp.usd_rate || 1300;

  const ospRows = ospPositions.map(p => {
    const pnlCls = (p.pnl_pct || 0) > 0 ? 'text-success' : (p.pnl_pct || 0) < 0 ? 'text-danger' : '';
    const pnlSign = (p.pnl_pct || 0) > 0 ? '+' : '';
    const ticker = p.symbol || '';
    const uName = OS_NAMES[ticker] || p.name || ticker;
    return `<tr>
      <td>
        <strong>${escHtml(ticker)}</strong>
        <br><small style="color:var(--text-muted)">${escHtml(uName)}</small>
        <small style="font-size:10px;color:var(--text-muted)"> (${p.exchange || '-'})</small>
      </td>
      <td style="text-align:right">${p.quantity != null ? p.quantity.toLocaleString() : '-'}</td>
      <td style="text-align:right">$${p.avg_price != null ? p.avg_price.toFixed(2) : '-'}<br><small style="color:var(--text-muted)">${p.avg_price_krw != null ? fmt_krw(p.avg_price_krw) : ''}</small></td>
      <td style="text-align:right">$${p.current_price != null ? p.current_price.toFixed(2) : '-'}</td>
      <td style="text-align:right">$${p.market_value != null ? p.market_value.toFixed(2) : '-'}<br><small style="color:var(--text-muted)">${p.market_value_krw != null ? fmt_krw(p.market_value_krw) : ''}</small></td>
      <td class="${pnlCls}" style="text-align:right">
        ${pnlSign}${p.pnl_pct != null ? p.pnl_pct.toFixed(2) + '%' : '-'}<br>
        <small>${p.pnl_usd != null ? (p.pnl_usd >= 0 ? '+' : '') + '$' + p.pnl_usd.toFixed(2) : ''}</small>
      </td>
    </tr>`;
  }).join('');

  const overseasPositionsHtml = `
    <div class="section-box">
      <div class="section-title-row">
        <span class="section-title">해외주식 보유 포지션${helpBtn('holding_pnl')}</span>
        <span class="section-meta ${ospEnvCls}">${ospEnv} &nbsp;|&nbsp; 환율 ${ospRate.toLocaleString()}원/USD</span>
      </div>
      ${osp.exists ? `
      <div class="cards" style="margin-bottom:12px">
        <div class="card ${(osp.total_usd_pnl || 0) > 0 ? 'ok' : (osp.total_usd_pnl || 0) < 0 ? 'danger' : ''}">
          <div class="card-label">주식 평가금액</div>
          <div class="card-value">$${(osp.total_usd_value || 0).toFixed(2)}</div>
          <div class="card-sub">${fmt_krw(osp.total_krw_value)}</div>
        </div>
        <div class="card ${(osp.total_usd_pnl || 0) > 0 ? 'ok' : (osp.total_usd_pnl || 0) < 0 ? 'danger' : ''}">
          <div class="card-label">평가손익</div>
          <div class="card-value ${(osp.total_usd_pnl || 0) > 0 ? 'text-success' : (osp.total_usd_pnl || 0) < 0 ? 'text-danger' : ''}">${(osp.total_usd_pnl || 0) >= 0 ? '+' : ''}$${(osp.total_usd_pnl || 0).toFixed(2)}</div>
          <div class="card-sub">${(osp.total_krw_pnl || 0) >= 0 ? '+' : ''}${fmt_krw(osp.total_krw_pnl)}</div>
        </div>
        <div class="card">
          <div class="card-label">달러 예수금</div>
          <div class="card-value">$${(osp.cash_usd || 0).toFixed(2)}</div>
          <div class="card-sub">${fmt_krw(osp.cash_krw)}</div>
        </div>
        <div class="card">
          <div class="card-label">보유 종목</div>
          <div class="card-value" style="font-size:22px">${ospPositions.length}개</div>
          <div class="card-sub">NASD / NYSE / AMEX</div>
        </div>
      </div>
      ${ospRows ? `<div class="table-wrap"><table>
        <thead><tr><th>종목</th><th style="text-align:right">수량</th><th style="text-align:right">평균단가</th><th style="text-align:right">현재가</th><th style="text-align:right">평가금액</th><th style="text-align:right">수익률</th></tr></thead>
        <tbody>${ospRows}</tbody>
      </table></div>` : '<div class="text-muted mt-8">보유 해외주식 없음</div>'}
      ` : `<div class="text-muted">${osp.error ? '조회 실패: ' + escHtml(osp.error) : '보유 포지션 없음 (모의 계좌 또는 KIS API 미설정)'}</div>`}
    </div>`;

  // ── 해외주식 유니버스 ────────────────────────────────────────────────
  const osuniv = overseasUniverse || {};
  const osMktStatusCls = osuniv.market_status === 'open' ? 'text-success' : 'text-muted';
  const osMktStatusLabel = osuniv.market_status === 'open'
    ? `<span class="dot-inline running"></span>장 운영 중`
    : `<span class="dot-inline muted"></span>장 마감 (${osuniv.current_time_kst} KST)`;
  const osUnivRows = (osuniv.symbols || []).map(s => {
    const ageCls = (s.bar_age_min || 0) < 5 ? 'text-success' : (s.bar_age_min || 0) < 30 ? '' : 'text-muted';
    const symParts = (s.symbol || '').split(':');
    const ticker = symParts.length === 2 ? symParts[1] : (s.symbol || '');
    const exchange = symParts.length === 2 ? symParts[0] : null;
    const uName = OS_NAMES[ticker];
    let symDisplay = `<strong>${escHtml(ticker)}</strong>`;
    if (exchange) symDisplay += ` <small style="color:var(--text-muted)">(${escHtml(exchange)})</small>`;
    if (uName) symDisplay += `<br><small style="color:var(--text-muted)">${escHtml(uName)}</small>`;
    return `<tr>
      <td>${symDisplay}</td>
      <td>${s.last_close != null ? '$' + s.last_close.toLocaleString() : '-'}</td>
      <td>${s.last_volume != null ? s.last_volume.toLocaleString() : '-'}</td>
      <td>${s.bar_count != null ? s.bar_count + '개' : '-'}</td>
      <td class="${ageCls}">${s.bar_age_min != null ? s.bar_age_min + '분 전' : '-'}</td>
    </tr>`;
  }).join('');

  const osAutoTag = osuniv.auto_universe !== false
    ? `<span style="background:#ef4444;color:#000;font-size:10px;padding:1px 6px;border-radius:4px;margin-left:6px">동적 갱신</span>`
    : `<span style="background:var(--bg-card);color:var(--text-muted);font-size:10px;padding:1px 6px;border-radius:4px;margin-left:6px">고정 목록</span>`;

  const overseasUniverseHtml = `
    <div class="section-box">
      <div class="section-title-row">
        <span class="section-title">해외주식 감시 유니버스 (${osuniv.symbol_count || 0}개)${osAutoTag}${helpBtn('os_universe')}</span>
        <span class="section-meta ${osMktStatusCls}">${osMktStatusLabel}</span>
      </div>
      <div style="font-size:11px;color:var(--text-muted);margin-bottom:10px">
        장 운영: 22:30~05:00 KST (미국 정규장)${helpBtn('os_market_hours')} &nbsp;|&nbsp; ${escHtml(osuniv.market_name || '미국 (US Regular)')}
        ${osuniv.auto_universe !== false ? `&nbsp;|&nbsp; 거래량 상위 ${osuniv.universe_size || 30}개 자동선정 · 30분 주기 갱신` : ''}
      </div>
      ${osUnivRows ? `<div class="table-wrap"><table>
        <thead><tr><th>심볼</th><th>마지막 체결가</th><th>마지막 거래량</th><th>1분봉 수</th><th>데이터 신선도${helpBtn('os_data_freshness')}</th></tr></thead>
        <tbody>${osUnivRows}</tbody>
      </table></div>` : `<div class="text-muted">파이프라인 실행 전 — 기본 감시 대상: NVDA·AAPL·MSFT·TSLA·META 등 ${osuniv.universe_size || 30}종목 (주식+ETF, 필수 레버리지 ETF 포함)</div>`}
    </div>`;

  // ── 탭 상태 유지 ─────────────────────────────────────────────────
  const savedTab = window._analysisTab || 'crypto';
  const tabCryptoActive  = savedTab === 'crypto';
  const tabStockActive   = savedTab === 'stock';
  const tabOverseasActive = savedTab === 'overseas';

  document.getElementById('page-analysis').innerHTML = `
    <div class="page-header">
      <h1 class="page-title">신호 분석</h1>
      <div class="page-subtitle">마지막 플랜 기준 — ${fmt_time(plan.generated_at || plan.created_at)}</div>
    </div>

    <div class="analysis-tabs">
      <button class="analysis-tab ${tabCryptoActive ? 'active' : ''}" onclick="switchAnalysisTab('crypto')">코인</button>
      <button class="analysis-tab ${tabStockActive ? 'active' : ''}" onclick="switchAnalysisTab('stock')">국내주식</button>
      <button class="analysis-tab ${tabOverseasActive ? 'active' : ''}" onclick="switchAnalysisTab('overseas')">해외주식</button>
    </div>

    <div id="analysis-tab-crypto" class="analysis-tab-panel ${tabCryptoActive ? '' : 'hidden'}">
      ${macroHtml}
      ${scoreBoardHtml}
      ${signalsHtml}
      ${weightsHtml}

      ${plan.market ? `
    <div class="section-box">
      <div class="section-title">신호 점수 — ${escHtml(plan.display_name || plan.market || '')}</div>
      <div class="score-grid">
        <div class="score-col">
          <div class="score-row-full">
            <span class="score-label">기술적 점수</span>
            ${score_bar(plan.technical_score, 100)}
          </div>
          <div class="score-row-full">
            <span class="score-label">거시경제 점수</span>
            ${score_bar(plan.macro_score, 100)}
          </div>
          <div class="score-row-full">
            <span class="score-label">최종 점수</span>
            ${score_bar(plan.final_score, 100)}
          </div>
          ${plan.macro_regime ? `<div class="score-meta">레짐: <strong>${plan.macro_regime}</strong></div>` : ''}
          ${plan.side ? `<div class="score-meta">방향: <span class="badge ${plan.side === 'buy' ? 'badge-success' : 'badge-danger'}">${plan.side.toUpperCase()}</span></div>` : ''}
          ${plan.sell_trigger ? `<div class="score-meta">SELL 트리거: <span class="badge badge-warning">${plan.sell_trigger}</span></div>` : ''}
        </div>
        <div class="score-col">
          <div class="gates-title">품질 게이트${helpBtn('quality_gates')}</div>
          ${gateHtml}
        </div>
      </div>
      ${plan.reason ? `<div class="text-muted mt-8" style="font-size:12px;border-top:1px solid var(--border);padding-top:10px">${escHtml(plan.reason)}</div>` : ''}
    </div>` : `<div class="section-box"><div class="text-muted">플랜 데이터 없음</div></div>`}

    <div class="dash-grid">
      <div class="section-box">
        <div class="section-title">코인 유니버스 (${markets.length}개 스캔)${helpBtn('universe')}</div>
        ${markets.length > 0 ? `<div class="table-wrap"><table>
          <thead><tr><th>마켓</th><th>이름</th></tr></thead>
          <tbody>${universeRows}</tbody>
        </table></div>` : '<div class="text-muted">유니버스 정보 없음</div>'}
        ${universe.min_acc_trade_price_24h ? `<div class="text-muted mt-8" style="font-size:11px">24h 거래대금 최소: ${(universe.min_acc_trade_price_24h / 1e8).toFixed(0)}억원</div>` : ''}
      </div>

      <div class="section-box">
        <div class="section-title">활성 포지션 사이징${helpBtn('position_sizing')}</div>
        ${Object.keys(sizing).length > 0 ? `
        <div class="sizing-grid">
          <div class="sizing-item"><span class="sizing-key">가용 KRW</span><span class="sizing-val">${fmt_krw(sizing.available_krw)}</span></div>
          <div class="sizing-item"><span class="sizing-key">총 포트폴리오</span><span class="sizing-val">${fmt_krw(sizing.total_portfolio_krw)}</span></div>
          <div class="sizing-item"><span class="sizing-key">주문 한도</span><span class="sizing-val">${fmt_krw(sizing.max_order_krw)}</span></div>
          <div class="sizing-item"><span class="sizing-key">일일 최대 주문</span><span class="sizing-val">${sizing.max_orders_per_day}회</span></div>
          <div class="sizing-item"><span class="sizing-key">ATR${helpBtn('atr')}</span><span class="sizing-val">${sizing.atr_pct != null ? Number(sizing.atr_pct).toFixed(2) + '%' : '-'}</span></div>
          <div class="sizing-item"><span class="sizing-key">레짐${helpBtn('regime')}</span><span class="sizing-val">${sizing.macro_regime || '-'}</span></div>
          <div class="sizing-item"><span class="sizing-key">TP / SL${helpBtn('tp_sl')}</span><span class="sizing-val">
            <span style="color:#ef4444">+${sizing.take_profit_pct}%</span> / <span style="color:#3b82f6">${sizing.stop_loss_pct}%</span>
            ${sizing.tp_source ? `<span style="font-size:10px;color:var(--text-muted)">[${sizing.tp_source}]</span>` : ''}
            ${sizing.dynamic_grade ? `<br><span style="font-size:10px;color:var(--text-muted)">${sizing.dynamic_grade}등급 · ${(sizing.dynamic_market_state||'').replace('_',' ')}</span>` : ''}
          </span></div>
          <div class="sizing-item"><span class="sizing-key">스코어 팩터${helpBtn('score_factor')}</span><span class="sizing-val">${sizing.score_factor || '-'}</span></div>
        </div>
        ${sizingNotes ? `<ul class="sizing-notes">${sizingNotes}</ul>` : ''}
        ` : '<div class="text-muted">사이징 정보 없음</div>'}
      </div>
    </div>

    <div class="section-box">
      <div class="section-title">체결오차 상세 기록${helpBtn('slippage')}</div>
      <div class="trade-explain-strip" style="margin-bottom:8px">
        <strong>체결오차(슬리피지)</strong> — 주문 지정가와 실제 체결가의 차이입니다. 5bps(0.05%) 이하면 정상, 높을수록 불리한 조건에서 체결된 것입니다.
      </div>
      ${slipRows ? `<div class="table-wrap"><table>
        <thead><tr><th>마켓</th><th>방향</th><th>주문금액</th><th>주문가</th><th>실제 체결가</th><th>체결오차${helpBtn('bps')}</th></tr></thead>
        <tbody>${slipRows}</tbody>
      </table></div>
      <div class="text-muted mt-8" style="font-size:11px">최근 ${(slippage.entries || []).length}건 (전체 ${slippage.total || 0}건)</div>`
      : '<div class="text-muted">체결 기록 없음</div>'}
    </div>

    <div class="section-box">
      <div class="section-title">리콘사일 (계좌 대사)${helpBtn('reconcile')}</div>
      <div class="cards" style="margin-bottom:12px">
        <div class="card ${recOk ? 'ok' : 'danger'}">
          <div class="card-label">상태</div>
          <div class="card-value" style="font-size:16px">${recOk ? '<span class="inline-ok">정상</span>' : '<span class="inline-warn">불일치</span>'}</div>
          <div class="card-sub">${fmt_time(reconcile.timestamp)}</div>
        </div>
        <div class="card ${(reconcile.matched || []).length > 0 ? 'ok' : ''}">
          <div class="card-label">일치 종목</div>
          <div class="card-value">${(reconcile.matched || []).length}개</div>
          <div class="card-sub">${(reconcile.matched || []).join(', ') || '-'}</div>
        </div>
      </div>
      <div style="font-size:12px;color:var(--text-muted)">
        DB 누락: ${recMissDb} &nbsp;|&nbsp; 브로커 누락: ${recMissBr} &nbsp;|&nbsp; 경고: ${recWarns}
      </div>
    </div>

    </div><!-- /analysis-tab-crypto -->

    <div id="analysis-tab-stock" class="analysis-tab-panel ${tabStockActive ? '' : 'hidden'}">
      ${kgsqsHtml}
      ${kStockSignalsHtml}
      ${kStockPositionsHtml}
      ${kstockSizingHtml}
      ${kstockReconcileHtml}
      ${kStockUniverseHtml}
      ${kgsqsWeightsHtml}
      ${kstockWeightOptHtml}
      ${kstockSlippageHtml}
      ${stockAnalysisHtml}
    </div><!-- /analysis-tab-stock -->

    <div id="analysis-tab-overseas" class="analysis-tab-panel ${tabOverseasActive ? '' : 'hidden'}">
      ${overseasKgsqsHtml}
      ${overseasSignalsHtml}
      ${overseasPositionsHtml}
      ${overseasSizingHtml}
      ${overseasReconcileHtml}
      ${overseasUniverseHtml}
      ${overseasWeightOptHtml}
      ${overseasSlippageHtml}
    </div><!-- /analysis-tab-overseas -->

  `;
}

function switchAnalysisTab(tab) {
  window._analysisTab = tab;
  const crypto   = document.getElementById('analysis-tab-crypto');
  const stock    = document.getElementById('analysis-tab-stock');
  const overseas = document.getElementById('analysis-tab-overseas');
  document.querySelectorAll('.analysis-tab').forEach(b => b.classList.remove('active'));
  // Hide all panels
  crypto   && crypto.classList.add('hidden');
  stock    && stock.classList.add('hidden');
  overseas && overseas.classList.add('hidden');
  if (tab === 'crypto') {
    crypto && crypto.classList.remove('hidden');
    document.querySelectorAll('.analysis-tab')[0]?.classList.add('active');
  } else if (tab === 'stock') {
    stock && stock.classList.remove('hidden');
    document.querySelectorAll('.analysis-tab')[1]?.classList.add('active');
    // lazy-load: 첫 탭 전환 시에만 데이터 로드
    if (typeof _tradeState !== 'undefined' && _tradeState.stock && _tradeState.stock.data === null && !_tradeState.stock.loading) {
      _loadTradeData('stock');
    }
  } else if (tab === 'overseas') {
    overseas && overseas.classList.remove('hidden');
    document.querySelectorAll('.analysis-tab')[2]?.classList.add('active');
    // lazy-load: 첫 탭 전환 시에만 데이터 로드
    if (typeof _tradeState !== 'undefined' && _tradeState.overseas && _tradeState.overseas.data === null && !_tradeState.overseas.loading) {
      _loadTradeData('overseas');
    }
  }
}

// ══════════════════════════════════════════════
// 차트
// ══════════════════════════════════════════════
const CHART_WATCHLIST = {
  crypto: [
    { symbol: 'KRW-BTC',  label: '비트코인',      ticker: 'BTC'  },
    { symbol: 'KRW-ETH',  label: '이더리움',      ticker: 'ETH'  },
    { symbol: 'KRW-XRP',  label: '리플',          ticker: 'XRP'  },
    { symbol: 'KRW-SOL',  label: '솔라나',        ticker: 'SOL'  },
    { symbol: 'KRW-DOGE', label: '도지코인',      ticker: 'DOGE' },
    { symbol: 'KRW-ADA',  label: '에이다',        ticker: 'ADA'  },
    { symbol: 'KRW-AVAX', label: '아발란체',      ticker: 'AVAX' },
    { symbol: 'KRW-LINK', label: '체인링크',      ticker: 'LINK' },
    { symbol: 'KRW-DOT',  label: '폴카닷',        ticker: 'DOT'  },
    { symbol: 'KRW-TRX',  label: '트론',          ticker: 'TRX'  },
    { symbol: 'KRW-NEAR', label: '니어프로토콜',  ticker: 'NEAR' },
    { symbol: 'KRW-XLM',  label: '스텔라루멘',   ticker: 'XLM'  },
  ],
  stock: [
    { symbol: '360750', label: 'TIGER 미국S&P500', ticker: '360750' },
    { symbol: '069500', label: 'KODEX 200',        ticker: '069500' },
    { symbol: '114800', label: 'KODEX 인버스',     ticker: '114800' },
    { symbol: '133690', label: 'TIGER 나스닥100',  ticker: '133690' },
    { symbol: '005930', label: '삼성전자',         ticker: '005930' },
    { symbol: '000660', label: 'SK하이닉스',       ticker: '000660' },
    { symbol: '035420', label: 'NAVER',            ticker: '035420' },
    { symbol: '035720', label: '카카오',           ticker: '035720' },
    { symbol: '005380', label: '현대차',           ticker: '005380' },
    { symbol: '051910', label: 'LG화학',           ticker: '051910' },
    { symbol: '006400', label: '삼성SDI',          ticker: '006400' },
    { symbol: '207940', label: '삼성바이오로직스', ticker: '207940' },
  ],
  overseas: [
    { symbol: 'SPY',   label: 'S&P500 ETF',    ticker: 'SPY'  },
    { symbol: 'QQQ',   label: '나스닥100 ETF', ticker: 'QQQ'  },
    { symbol: 'NVDA',  label: '엔비디아',      ticker: 'NVDA' },
    { symbol: 'AAPL',  label: '애플',          ticker: 'AAPL' },
    { symbol: 'MSFT',  label: '마이크로소프트',ticker: 'MSFT' },
    { symbol: 'TSLA',  label: '테슬라',        ticker: 'TSLA' },
    { symbol: 'META',  label: '메타',          ticker: 'META' },
    { symbol: 'AMZN',  label: '아마존',        ticker: 'AMZN' },
    { symbol: 'GOOGL', label: '알파벳',        ticker: 'GOOGL'},
    { symbol: 'AMD',   label: 'AMD',           ticker: 'AMD'  },
    { symbol: 'AVGO',  label: '브로드컴',      ticker: 'AVGO' },
    { symbol: 'GLD',   label: '금 ETF',        ticker: 'GLD'  },
  ],
};

let _chartTab    = 'crypto';
let _chartPeriod = '1mo';
let _chartSearch = '';
const _chartCache = {};

// 기간 버튼 (값, 표시이름)
const CHART_PERIODS = [['1d','오늘'],['1wk','1주'],['1mo','1개월'],['3mo','3개월'],['1y','1년']];
// 사이드바 정렬 (값, 표시이름)
const SIDEBAR_SORTS = [['volume','거래대금'],['change','등락율'],['price','현재가'],['name','이름']];
let _sidebarSort = 'volume';   // 기본: 거래대금 많은 순
const SIDEBAR_TOP_N = 40;      // 필터별 상위 N개만 표시 (속도)

// 큰 금액을 짧게 (KRW: 조/억/만, USD: B/M/K)
function _fmtCompactKRW(v, isUsd) {
  v = Number(v) || 0;
  if (isUsd) {
    if (v >= 1e9) return '$' + (v/1e9).toFixed(1) + 'B';
    if (v >= 1e6) return '$' + (v/1e6).toFixed(1) + 'M';
    if (v >= 1e3) return '$' + (v/1e3).toFixed(0) + 'K';
    return '$' + Math.round(v);
  }
  if (v >= 1e12) return (v/1e12).toFixed(1) + '조';
  if (v >= 1e8)  return Math.round(v/1e8).toLocaleString() + '억';
  if (v >= 1e4)  return Math.round(v/1e4).toLocaleString() + '만';
  return Math.round(v).toLocaleString() + '원';
}

function _chartFmtPrice(price, currency) {
  if (!price) return '—';
  return currency === 'USD'
    ? `$${price.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2})}`
    : fmt_krw(Math.round(price));
}

let _sidebarSearch = '';

function renderCharts() {
  const el = document.getElementById('page-charts');
  const tabs = ['crypto','stock','overseas'];
  const tabLabels = { crypto:'코인', stock:'국내주식', overseas:'해외주식' };
  el.innerHTML = `
    <div class="page-header">
      <h1 class="page-title">차트</h1>
    </div>
    <div class="analysis-tabs">
      ${tabs.map(t => `<button class="analysis-tab ${_chartTab===t?'active':''}" onclick="switchChartTab('${t}')">${tabLabels[t]}</button>`).join('')}
    </div>
    <div class="chart-toolbar">
      <div class="chart-period-btns">
        ${CHART_PERIODS.map(([v,label]) =>
          `<button class="chart-period-btn ${_chartPeriod===v?'active':''}" onclick="switchChartPeriod('${v}')">${label}</button>`
        ).join('')}
      </div>
    </div>
    <div class="chart-layout">
      <div class="chart-main">
        <div id="chart-grid" class="chart-grid"></div>
      </div>
      <div class="chart-sidebar" id="chart-sidebar">
        <div class="chart-sidebar-header">
          <input class="chart-sidebar-search" id="sidebar-search"
            placeholder="${_chartTab==='crypto'?'코인명·심볼 검색':_chartTab==='stock'?'종목명·코드 검색':'종목명·티커 검색'}"
            oninput="onSidebarSearch(this.value)" value="${escHtml(_sidebarSearch)}">
          <div class="sidebar-sort">
            ${SIDEBAR_SORTS.map(([v,label]) =>
              `<button class="sidebar-sort-btn ${_sidebarSort===v?'active':''}" onclick="setSidebarSort('${v}')">${label}</button>`
            ).join('')}
          </div>
          <div class="sidebar-count" id="sidebar-count"></div>
        </div>
        <div class="chart-sidebar-list" id="sidebar-list">
          <div class="text-muted" style="padding:16px;font-size:12px;text-align:center">로딩 중...</div>
        </div>
      </div>
    </div>
    <div id="chart-modal" class="chart-modal" style="display:none" onclick="closeChartModal(event)">
      <div class="chart-modal-panel" onclick="event.stopPropagation()">
        <div class="chart-modal-header">
          <div>
            <span class="chart-modal-ticker" id="cm-ticker"></span>
            <span class="chart-modal-label text-muted" id="cm-label"></span>
          </div>
          <button class="sd-close" onclick="closeChartModal()">✕</button>
        </div>
        <div class="chart-modal-price" id="cm-price"></div>
        <div id="cm-chart"></div>
        <div class="chart-modal-stats" id="cm-stats"></div>
      </div>
    </div>`;
  // 사이드바 먼저 로드 (1위 종목 자동 선택) → 그 후 그리드/차트 렌더
  loadSidebar();
}

function switchChartTab(tab) {
  _chartTab = tab; _chartSearch = ''; _sidebarSearch = ''; _selectedChart = null;
  renderCharts();
}
function switchChartPeriod(p) {
  _chartPeriod = p;
  renderCharts();
}
function onSidebarSearch(v) {
  _sidebarSearch = v;
  const listEl = document.getElementById('sidebar-list');
  if (listEl) renderSidebarList(listEl, listEl._allItems || [], v);
}
function setSidebarSort(s) {
  _sidebarSort = s;
  document.querySelectorAll('.sidebar-sort-btn').forEach(b =>
    b.classList.toggle('active', b.getAttribute('onclick').includes(`'${s}'`)));
  const listEl = document.getElementById('sidebar-list');
  if (listEl) renderSidebarList(listEl, listEl._allItems || [], _sidebarSearch);
}

async function loadSidebar() {
  const listEl = document.getElementById('sidebar-list');
  if (!listEl) return;
  try {
    const data = await GET(`/api/markets?asset_type=${_chartTab}`);
    const items = data.items || [];
    listEl._allItems = items;
    renderSidebarList(listEl, items, _sidebarSearch);
    // 진입 시 1위 종목(거래대금 최다) 자동 선택
    if (items.length > 0 && !_selectedChart) {
      const top = items[0];
      addToChartGrid(top.symbol, top.name, top.ticker);
    } else {
      // 이미 선택된 종목이 있으면 그리드로 복귀
      loadChartGrid();
    }
  } catch(e) {
    if (listEl) listEl.innerHTML = '<div class="text-muted" style="padding:16px;font-size:12px;text-align:center">로드 실패</div>';
    loadChartGrid(); // 사이드바 실패해도 그리드는 보여줌
  }
}

function renderSidebarList(listEl, items, search = '') {
  if (!listEl) return;
  const q = (search || '').toUpperCase().trim();
  let filtered = q
    ? items.filter(i => i.ticker?.toUpperCase().includes(q) || i.name?.includes(q) || i.symbol?.toUpperCase().includes(q))
    : items.slice();

  // 정렬
  const s = _sidebarSort;
  filtered.sort((a, b) => {
    if (s === 'name')   return (a.name || a.ticker || '').localeCompare(b.name || b.ticker || '', 'ko');
    if (s === 'price')  return (b.price || 0) - (a.price || 0);
    if (s === 'change') return (b.change_pct ?? -999) - (a.change_pct ?? -999);
    return (b.volume_krw || 0) - (a.volume_krw || 0); // 거래대금
  });

  // 검색이 없으면 상위 N개만 (속도)
  const limited = q ? filtered.slice(0, 100) : filtered.slice(0, SIDEBAR_TOP_N);
  const countEl = document.getElementById('sidebar-count');
  if (countEl) {
    const sortLabel = (SIDEBAR_SORTS.find(x => x[0] === s) || ['', ''])[1];
    countEl.textContent = q ? `검색 ${limited.length}개`
      : `${sortLabel} 상위 ${limited.length}개 · 전체 ${items.length}`;
  }

  listEl.innerHTML = limited.map(item => {
    const chg = item.change_pct ?? null;
    const chgStr = chg != null
      ? `<span class="${chg>=0?'text-up':'text-down'}" style="font-size:11px">${chg>=0?'+':''}${chg.toFixed(2)}%</span>`
      : '';
    const priceStr = item.price
      ? (_chartTab === 'overseas'
          ? `$${item.price.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2})}`
          : fmt_krw(Math.round(item.price)))
      : '';
    // 코인: 티커 우선 / 국내·해외주식: 한글명 우선 (크게)
    const nameFirst = _chartTab !== 'crypto';
    const primary   = nameFirst ? item.name   : item.ticker;
    const secondary = nameFirst ? item.ticker : item.name;
    // 거래대금 정렬일 때 거래대금을 작게 표시 (억/조 단위)
    const subText = (s === 'volume' && item.volume_krw)
      ? `거래대금 ${_fmtCompactKRW(item.volume_krw, _chartTab === 'overseas')}`
      : escHtml(secondary);
    return `<div class="sidebar-item" data-symbol="${escHtml(item.symbol)}" onclick="addToChartGrid('${escHtml(item.symbol)}','${escHtml(item.name)}','${escHtml(item.ticker)}')">
      <div class="sidebar-item-left">
        <span class="sidebar-ticker">${escHtml(primary)}</span>
        <span class="sidebar-name text-muted">${subText}</span>
      </div>
      <div class="sidebar-item-right">
        <span class="sidebar-price">${priceStr}</span>
        ${chgStr}
      </div>
    </div>`;
  }).join('') || '<div class="text-muted" style="padding:16px;font-size:12px;text-align:center">검색 결과 없음</div>';
}

let _selectedChart = null; // { symbol, name, ticker }

function addToChartGrid(symbol, name, ticker) {
  _selectedChart = { symbol, name, ticker };

  // 사이드바 활성 항목 표시
  document.querySelectorAll('.sidebar-item').forEach(el => {
    el.classList.toggle('active', el.dataset.symbol === symbol);
  });

  renderMainChart(symbol, name, ticker);
}

async function renderMainChart(symbol, name, ticker) {
  const main = document.getElementById('chart-grid');
  if (!main) return;
  main.style.display = 'block';   // grid 해제 → 전체 폭 사용

  const fetchPeriod = _chartPeriod;
  const cacheKey = `${_chartTab}:${symbol}:${fetchPeriod}`;

  main.innerHTML = `
    <div class="chart-main-view">
      <div class="chart-main-header">
        <div class="chart-main-info">
          <span class="chart-main-ticker">${escHtml(ticker)}</span>
          <span class="chart-main-name text-muted">${escHtml(name)}</span>
        </div>
        <div class="chart-main-meta" id="main-chart-meta">로딩 중...</div>
        <button class="chart-back-btn" onclick="backToChartGrid()">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M19 12H5M12 5l-7 7 7 7"/></svg>
          목록
        </button>
      </div>
      <div id="main-chart-body" style="height:480px;border-radius:6px;overflow:hidden"></div>
      <div class="chart-main-stats" id="main-chart-stats"></div>
    </div>`;

  let data = _chartCache[cacheKey];
  if (!data || (Date.now() - data.ts) > 300000) {
    try {
      data = await GET(`/api/charts?asset_type=${_chartTab}&symbol=${encodeURIComponent(symbol)}&period=${fetchPeriod}`);
      data.ts = Date.now();
      _chartCache[cacheKey] = data;
    } catch(e) { data = { candles:[], price:0, currency:'KRW' }; }
  }

  const candles  = data.candles  || [];
  const price    = data.price    || 0;
  const currency = data.currency || 'KRW';
  const first = candles[0], last = candles[candles.length-1];
  const chgPct = (first?.close && last?.close) ? ((last.close-first.close)/first.close*100) : null;
  const hi = candles.length ? Math.max(...candles.map(c=>c.high)) : 0;
  const lo = candles.length ? Math.min(...candles.map(c=>c.low))  : 0;

  const metaEl = document.getElementById('main-chart-meta');
  if (metaEl) metaEl.innerHTML =
    `<span class="chart-main-price">${_chartFmtPrice(price, currency)}</span>` +
    (chgPct != null ? ` <span class="${chgPct>=0?'text-up':'text-down'}">${chgPct>=0?'+':''}${chgPct.toFixed(2)}%</span>` : '');

  if (candles.length >= 2) {
    setTimeout(() => _lwRender('main-chart-body', candles, { height: 480, interactive: true }), 0);
  } else {
    const b = document.getElementById('main-chart-body');
    if (b) b.innerHTML = '<div class="text-muted" style="padding:60px;text-align:center">데이터 없음</div>';
  }

  const statsEl = document.getElementById('main-chart-stats');
  if (statsEl && candles.length) {
    statsEl.innerHTML = `
      <div class="chart-stat-row" style="margin-top:12px">
        <span class="chart-stat"><span class="text-muted">기간 고가</span><strong>${_chartFmtPrice(hi,currency)}</strong></span>
        <span class="chart-stat"><span class="text-muted">기간 저가</span><strong>${_chartFmtPrice(lo,currency)}</strong></span>
        <span class="chart-stat"><span class="text-muted">기간</span><strong>${fetchPeriod} · ${candles.length}개</strong></span>
      </div>`;
  }
}

function switchChartPeriodMain(p) {
  _chartPeriod = p;
  // period 버튼 업데이트
  document.querySelectorAll('.chart-period-btn').forEach(btn => {
    btn.classList.toggle('active', btn.textContent.trim() === p);
  });
  if (_selectedChart) renderMainChart(_selectedChart.symbol, _selectedChart.name, _selectedChart.ticker);
}

function backToChartGrid() {
  _selectedChart = null;
  document.querySelectorAll('.sidebar-item').forEach(el => el.classList.remove('active'));
  loadChartGrid();
}

// ── LightweightCharts 렌더러 ─────────────────────────────────
const _LW_UP   = '#ef4444';  // 상승 빨강 (한국 관례)
const _LW_DOWN = '#3b82f6';  // 하락 파랑 (한국 관례)

function _lwRender(containerId, candles, { height = 160, interactive = false } = {}) {
  const el = typeof containerId === 'string' ? document.getElementById(containerId) : containerId;
  if (!el || !window.LightweightCharts || !candles || candles.length < 2) return null;

  if (el._lwChart) { try { el._lwChart.remove(); } catch(_){} el._lwChart = null; }
  el.innerHTML = '';

  const chart = LightweightCharts.createChart(el, {
    autoSize: true,          // 컨테이너 너비에 자동 맞춤 (offsetWidth=0 우회)
    height,
    layout: {
      background: { color: interactive ? '#13131a' : '#0d0d10' },
      textColor:  interactive ? '#a1a1aa' : '#0d0d10',
    },
    grid: {
      vertLines: { color: interactive ? '#1c1c24' : '#0d0d10' },
      horzLines: { color: interactive ? '#1c1c24' : '#0d0d10' },
    },
    crosshair: {
      mode: interactive
        ? LightweightCharts.CrosshairMode.Normal
        : LightweightCharts.CrosshairMode.Hidden,
    },
    rightPriceScale: {
      visible: interactive,
      borderColor: '#27272a',
      scaleMargins: { top: 0.05, bottom: 0.22 },
    },
    leftPriceScale: { visible: false },
    timeScale: {
      visible: interactive,
      borderColor: '#27272a',
      timeVisible: false,
      fixLeftEdge: true,
      fixRightEdge: true,
    },
    handleScroll: interactive,
    handleScale:  interactive,
  });

  const candleSeries = chart.addCandlestickSeries({
    upColor:          _LW_UP,   downColor:          _LW_DOWN,
    borderUpColor:    _LW_UP,   borderDownColor:    _LW_DOWN,
    wickUpColor:      _LW_UP,   wickDownColor:      _LW_DOWN,
    priceScaleId:     'right',
  });
  candleSeries.setData(candles.map(c => ({
    time: c.time ?? c.date, open: c.open, high: c.high, low: c.low, close: c.close,
  })));

  if (interactive && candles.some(c => c.volume > 0)) {
    const volSeries = chart.addHistogramSeries({
      color:         'rgba(100,100,100,.3)',
      priceScaleId:  'vol',
      priceFormat:   { type: 'volume' },
    });
    chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    volSeries.setData(candles.map(c => ({
      time:  c.time ?? c.date,
      value: c.volume || 0,
      color: c.close >= c.open
        ? 'rgba(239,68,68,.35)'
        : 'rgba(59,130,246,.35)',
    })));
  }

  chart.timeScale().fitContent();

  // 반응형 리사이즈
  const ro = new ResizeObserver(() => {
    if (el.offsetWidth > 0) chart.applyOptions({ width: el.offsetWidth });
  });
  ro.observe(el);
  el._lwChart = chart;  // 재사용/destroy용
  return chart;
}
function onChartSearch(v) {
  _chartSearch = v.trim().toUpperCase();
  loadChartGrid();
}

async function loadChartGrid() {
  // 이미 선택된 종목이 있으면 메인 차트 렌더링
  if (_selectedChart) {
    renderMainChart(_selectedChart.symbol, _selectedChart.name, _selectedChart.ticker);
    return;
  }
  const grid = document.getElementById('chart-grid');
  if (!grid) return;
  grid.style.display = '';   // grid 레이아웃 복원
  let list = [...(CHART_WATCHLIST[_chartTab] || [])];

  // 검색어가 있으면 watchlist 필터 + 커스텀 심볼 추가
  if (_chartSearch) {
    const q = _chartSearch;
    list = list.filter(i => i.ticker.includes(q) || i.label.includes(q) || i.symbol.includes(q));
    const sym = _chartTab === 'crypto'
      ? (q.startsWith('KRW-') ? q : 'KRW-' + q)
      : q;
    if (!list.find(i => i.symbol === sym)) {
      list.unshift({ symbol: sym, label: sym, ticker: _chartTab==='crypto' ? q.replace('KRW-','') : q });
    }
  }

  grid.innerHTML = list.map(item => `
    <div class="chart-card" id="cc-${item.symbol.replace(/[^a-z0-9]/gi,'_')}" onclick="addToChartGrid('${escHtml(item.symbol)}','${escHtml(item.label)}','${escHtml(item.ticker)}')">
      <div class="chart-card-header">
        <span class="chart-card-ticker">${item.ticker}</span>
        <span class="chart-card-label text-muted">${item.label}</span>
        <span class="chart-card-expand">⤢</span>
      </div>
      <div class="chart-card-price text-muted" style="font-size:12px">로딩 중...</div>
      <div class="chart-card-body" style="height:160px;background:rgba(255,255,255,.03);border-radius:4px;animation:skeleton-pulse 1.4s infinite"></div>
    </div>`).join('');

  await Promise.all(list.map(item => _loadChartCard(item)));
}

async function _loadChartCard(item) {
  const cacheKey = `${_chartTab}:${item.symbol}:${_chartPeriod}`;
  let data = _chartCache[cacheKey];
  if (!data || (Date.now() - data.ts) > 300000) {
    try {
      data = await GET(`/api/charts?asset_type=${_chartTab}&symbol=${encodeURIComponent(item.symbol)}&period=${_chartPeriod}`);
      data.ts = Date.now();
      _chartCache[cacheKey] = data;
    } catch(e) { data = { candles:[], price:0, currency:'KRW', ts: Date.now() }; }
  }
  const card = document.getElementById('cc-' + item.symbol.replace(/[^a-z0-9]/gi,'_'));
  if (!card) return;
  const candles = data.candles || [];
  const price    = data.price    || 0;
  const currency = data.currency || 'KRW';
  const first = candles[0], last = candles[candles.length-1];
  const chgPct = (first?.close && last?.close) ? ((last.close - first.close)/first.close*100) : null;
  const chgStr = chgPct != null
    ? `<span class="${chgPct>=0?'text-up':'text-down'}">${chgPct>=0?'+':''}${chgPct.toFixed(2)}%</span>` : '';
  const bodyId = 'lwbody_' + item.symbol.replace(/[^a-z0-9]/gi,'_');

  card.innerHTML = `
    <div class="chart-card-header">
      <span class="chart-card-ticker">${item.ticker}</span>
      <span class="chart-card-label text-muted">${item.label}</span>
      <span class="chart-card-expand">⤢</span>
    </div>
    <div class="chart-card-price">${_chartFmtPrice(price,currency)} ${chgStr}
      <span class="text-muted" style="font-size:10px">${_chartPeriod}</span>
    </div>
    <div id="${bodyId}" class="chart-card-body" style="height:160px"></div>`;

  if (candles.length >= 2) {
    setTimeout(() => _lwRender(bodyId, candles, { height: 160, interactive: false }), 0);
  } else {
    const body = document.getElementById(bodyId);
    if (body) body.innerHTML = '<div class="text-muted" style="padding:20px;font-size:12px;text-align:center">데이터 없음</div>';
  }
}

async function openChartModal(symbol, label, ticker) {
  const modal = document.getElementById('chart-modal');
  if (!modal) return;
  modal.style.display = 'flex';
  document.getElementById('cm-ticker').textContent = ticker;
  document.getElementById('cm-label').textContent  = label;
  document.getElementById('cm-price').textContent  = '로딩 중...';
  document.getElementById('cm-chart').innerHTML    = '<div id="cm-chart-lw" style="height:360px;border-radius:6px;overflow:hidden"></div>';
  document.getElementById('cm-stats').innerHTML    = '';

  const fetchPeriod = _chartPeriod;
  const cacheKey = `${_chartTab}:${symbol}:${fetchPeriod}`;
  let data = _chartCache[cacheKey];
  if (!data || (Date.now()-data.ts)>300000) {
    try {
      data = await GET(`/api/charts?asset_type=${_chartTab}&symbol=${encodeURIComponent(symbol)}&period=${fetchPeriod}`);
      data.ts = Date.now();
      _chartCache[cacheKey] = data;
    } catch(e) { data = { candles:[], price:0, currency:'KRW' }; }
  }
  const candles  = data.candles  || [];
  const price    = data.price    || 0;
  const currency = data.currency || 'KRW';
  const first = candles[0], last = candles[candles.length-1];
  const chgPct = (first?.close && last?.close) ? ((last.close-first.close)/first.close*100) : null;
  const hi = candles.length ? Math.max(...candles.map(c=>c.high)) : 0;
  const lo = candles.length ? Math.min(...candles.map(c=>c.low))  : 0;

  document.getElementById('cm-price').innerHTML =
    `${_chartFmtPrice(price, currency)}` +
    (chgPct!=null ? ` <span class="${chgPct>=0?'text-up':'text-down'}" style="font-size:15px">${chgPct>=0?'+':''}${chgPct.toFixed(2)}%</span>` : '');

  if (candles.length >= 2) {
    _lwRender('cm-chart-lw', candles, { height: 360, interactive: true });
  } else {
    document.getElementById('cm-chart-lw').innerHTML = '<div class="text-muted" style="padding:60px;text-align:center">데이터 없음</div>';
  }

  document.getElementById('cm-stats').innerHTML = candles.length ? `
    <div class="chart-stat-row">
      <span class="chart-stat"><span class="text-muted">기간 고가</span><strong>${_chartFmtPrice(hi,currency)}</strong></span>
      <span class="chart-stat"><span class="text-muted">기간 저가</span><strong>${_chartFmtPrice(lo,currency)}</strong></span>
      <span class="chart-stat"><span class="text-muted">기간</span><strong>${fetchPeriod} · ${candles.length}개</strong></span>
    </div>` : '';
}

function closeChartModal(e) {
  const modal = document.getElementById('chart-modal');
  if (modal) modal.style.display = 'none';
}

// ══════════════════════════════════════════════
// 리포트 뷰어
// ══════════════════════════════════════════════
let activeReport = null;

// 파일명 → 사람이 읽을 수 있는 한국어 라벨 + 갱신 주기
const REPORT_META = {
  'AI_DAILY_TRADE_PLAN.md':              { icon: 'RP', label: '오늘의 매매계획 (주식)',     category: '매매', refresh: '매일 자동\n시간: 약 11:55 KST\n주식 러너 daily-ai-trade-plan 실행 시' },
  'CRYPTO_DAILY_TRADE_PLAN.md':          { icon: 'RP', label: '오늘의 매매계획 (코인)',     category: '매매', refresh: '자동 갱신 (약 1분마다)\n코인 러너 틱마다 최신 코인 계획 덮어씀' },
  'AI_LIVE_TRADE_RECOMMENDATION.md':     { icon: 'REC', label: '실시간 매매 추천',           category: '매매', refresh: '매일 자동\n시간: 약 11:55 KST\n주식 AI 추천 생성 시' },
  'SELL_PLAN.md':                        { icon: '', label: '매도 계획',                  category: '매매', refresh: '매일 자동\n시간: 약 19:00 KST\n일일 운영 요약 실행 시' },
  'TELEGRAM_APPROVAL_REQUEST.md':        { icon: '', label: '승인 요청 내역',             category: '매매', refresh: '승인 요청 생성 시마다\n자동/수동 분석 후 텔레그램 발송 시' },

  'AI_DAILY_TRADE_REPORT.md':            { icon: 'STAT', label: '오늘의 매매 결과',           category: '성과', refresh: '매일 자동\n시간: 약 15:40 KST\ndaily-ai-trade-report 실행 시' },
  'RECOMMENDATION_PERFORMANCE.md':       { icon: 'PERF', label: '추천 적중률 분석 (주식)',    category: '성과', refresh: '매일 자동\n시간: 약 19:55 KST\n주간 점검 또는 ops-summary 실행 시' },
  'CRYPTO_RECOMMENDATION_PERFORMANCE.md':{ icon: 'PERF', label: '추천 적중률 분석 (코인)',    category: '성과', refresh: '매일 자동\n시간: 약 19:55 KST\n주간 점검 실행 시' },
  'AI_RECOMMENDATION_VALIDATION.md':     { icon: 'VAL', label: '추천 신뢰도 검증',           category: '성과', refresh: '매일 자동\nAI 추천 실행 시마다 생성' },

  'LIVE_ACCOUNT_SNAPSHOT.md':            { icon: 'ACC', label: '실시간 계좌 현황',           category: '계좌', refresh: '매일 자동\n시간: 약 09:00, 11:55, 19:00 KST\n계좌 동기화 시' },
  'RECONCILE_LIVE_ACCOUNT.md':           { icon: 'SYNC', label: '계좌 잔고 대사',             category: '계좌', refresh: '매일 자동\n시간: 약 11:55 KST\n잔고 대사 실행 시' },
  'EXECUTE_APPROVED_AUDIT.md':           { icon: '', label: '주문 실행 이력',             category: '계좌', refresh: '주문 실행 시마다\n승인 후 실제 주문이 들어갈 때마다 갱신' },

  'RISK_ALERT.md':                       { icon: '', label: '위험 경보',                  category: '점검', refresh: '매일 자동\n시간: 약 19:00 KST\n위험 점검 실행 시' },
  'SAFETY_AUDIT.md':                     { icon: 'SAFE', label: '안전 점검 기록',            category: '점검', refresh: '매일 자동\n시간: 약 11:55 KST\ndaily-ai-trade-plan 실행 시 포함' },
  'REPORT_HEALTH.md':                    { icon: '', label: '리포트 시스템 상태',         category: '점검', refresh: '수동 / 주 1회\nweekly-maintenance 명령 실행 시\n또는 report-health-check 직접 실행 시' },
  'AI_DAILY_STATUS.md':                  { icon: 'AI', label: 'AI 시스템 상태',             category: '점검', refresh: '매일 자동\n시간: 약 11:55 KST' },
  'WEEKLY_MAINTENANCE.md':               { icon: 'SYS', label: '주간 시스템 점검',           category: '점검', refresh: '수동 / 주 1회\nweekly-maintenance 명령 실행 시\n자동 스케줄 없음' },

  'CRYPTO_THRESHOLD_TUNING.md':          { icon: 'CFG', label: '코인 임계값 조정 내역',     category: '설정', refresh: '수동\ncrypto-threshold-tune 명령 실행 시' },
  'OUTCOME_THRESHOLD_TUNING.md':         { icon: 'CFG', label: '성과 임계값 조정 내역',     category: '설정', refresh: '수동\noutcome-threshold-tune 명령 실행 시' },
  'ANALYSIS_CONDITIONS.md':              { icon: '', label: '분석 조건 설정',             category: '설정', refresh: '수동\n설정 변경 시 수동 실행 필요' },

  'DAILY_OPS_SUMMARY.md':                { icon: '', label: '일일 운영 요약',             category: '기타', refresh: '매일 자동\n시간: 약 19:55 KST\ndaily-ops-summary 실행 시' },
  'ARCHIVE_VIEWER_SUMMARY.md':           { icon: '', label: '과거 데이터 요약',           category: '기타', refresh: '수동\narchive-viewer 명령 실행 시' },
  'REPORT_INDEX.md':                     { icon: '', label: '전체 리포트 목록',           category: '기타', refresh: '수동 / 주 1회\nweekly-maintenance 또는 report-index 실행 시' },
};

const REPORT_CATEGORY_ORDER = ['매매', '성과', '계좌', '점검', '설정', '기타'];

function reportDisplayName(name) {
  const m = REPORT_META[name];
  if (m) return `${m.icon} ${m.label}`;
  return name.replace(/\.md$/i, '').replace(/_/g, ' ').toLowerCase()
    .replace(/\b\w/g, c => c.toUpperCase());
}

function reportRefreshTooltip(name) {
  const m = REPORT_META[name];
  return m?.refresh ?? '갱신 주기 정보 없음';
}

function reportCategory(name) {
  return REPORT_META[name]?.category ?? '기타';
}

// ══════════════════════════════════════════════
// 거래내역 전용 페이지 (코인·국내주식·해외주식 통합)
// ══════════════════════════════════════════════
let _tpState = null;   // trades-page 전용 상태 (렌더링마다 초기화)

async function renderTrades() {
  const el = document.getElementById('page-trades');
  if (!el) return;

  // 상태 초기화 (탭 유지를 위해 기존 상태 재활용)
  if (!_tpState) {
    _tpState = {
      crypto:   { period: '1m', type: 'all', symbol: '', data: null, loading: false },
      stock:    { period: '1m', type: 'all', symbol: '', data: null, loading: false },
      overseas: { period: '1m', type: 'all', symbol: '', data: null, loading: false },
      activeTab: 'crypto',
    };
  }

  // ── 테이블 렌더러 (분석 탭과 동일 로직) ──
  function _tpRenderTable(tab) {
    const wrap = document.getElementById(`tp-table-wrap-${tab}`);
    if (!wrap) return;
    const s = _tpState[tab];
    if (s.loading) { wrap.innerHTML = '<div class="text-muted" style="padding:12px">로딩 중...</div>'; return; }
    const items = (s.data && s.data.items) || [];
    const total = (s.data && s.data.total) || 0;

    const fmt_ts = ts => {
      if (!ts) return '-';
      try {
        const d = new Date(ts);
        return `<span style="font-size:11px">${(d.getMonth()+1).toString().padStart(2,'0')}.${d.getDate().toString().padStart(2,'0')}</span><br><span style="font-size:10px;color:var(--text-muted)">${d.getHours().toString().padStart(2,'0')}:${d.getMinutes().toString().padStart(2,'0')}</span>`;
      } catch { return ts.slice(0,16).replace('T',' '); }
    };
    const fmt_qty = q => {
      if (q == null) return '-';
      const n = Number(q);
      if (n >= 1) return n.toLocaleString(undefined, {maximumFractionDigits:4});
      return n.toFixed(8).replace(/0+$/, '').replace(/\.$/, '');
    };
    const fmt_price = (p, mkt) => {
      if (p == null) return '-';
      const n = Number(p);
      if (mkt === 'USD') return '$' + n.toLocaleString(undefined, {minimumFractionDigits:2, maximumFractionDigits:2});
      if (n >= 1000) return n.toLocaleString(undefined, {maximumFractionDigits:0});
      if (n >= 1)    return n.toFixed(1);
      return n.toFixed(4);
    };
    const fmt_amt = (a, mkt) => {
      if (a == null) return '-';
      const n = Number(a);
      if (mkt === 'USD') return '$' + n.toLocaleString(undefined, {minimumFractionDigits:2, maximumFractionDigits:2});
      return n.toLocaleString(undefined, {maximumFractionDigits:0});
    };
    const slip_cell = bps => {
      if (bps == null) return '<span class="text-muted" style="font-size:10px">-</span>';
      const n = Number(bps);
      const cls = n < 5 ? 'text-success' : n < 30 ? '' : 'text-danger';
      return `<span class="${cls}" style="font-size:11px">${n < 0.5 ? '±0' : (n > 0 ? '+' : '') + n.toFixed(1) + 'bp'}</span>`;
    };

    // update summary strip
    const summaryEl = document.getElementById(`tp-summary-${tab}`);
    if (summaryEl) summaryEl.innerHTML = _buildTradeSummary(items);

    if (!items.length) {
      wrap.innerHTML = '<div class="text-muted" style="padding:12px;text-align:center">해당 기간 거래 내역이 없습니다</div>';
      return;
    }
    const isOverseas = tab === 'overseas';
    const rows = items.map(t => {
      const sideBadge = t.side === 'buy'
        ? '<span class="side-badge buy">BUY</span>'
        : '<span class="side-badge sell">SELL</span>';
      const amtDisplay = isOverseas && t.trade_amount_krw
        ? `<span title="$${Number(t.trade_amount||0).toFixed(2)}">${fmt_amt(t.trade_amount_krw,'KRW')}</span>`
        : fmt_amt(t.trade_amount, t.market);
      const feeDisplay = isOverseas && t.fee_krw ? fmt_amt(t.fee_krw,'KRW') : fmt_amt(t.fee, t.market);
      const setlDisplay = isOverseas && t.settlement_krw ? fmt_amt(t.settlement_krw,'KRW') : fmt_amt(t.settlement, t.market);
      const slipBps = t.slippage_bps != null ? Number(t.slippage_bps) : null;
      const slipBar = slipBps != null
        ? `<div class="pnl-bar-cell"><div class="pnl-bar-track"><div class="pnl-bar-fill ${slipBps < 10 ? 'up' : 'down'}" style="width:${Math.min(slipBps/30*100,100).toFixed(0)}%"></div></div><span class="pnl-num" style="font-size:11px;color:${slipBps<5?'var(--up)':slipBps<30?'var(--text)':'var(--down)'}">${slipBps < 0.5 ? '±0' : (slipBps > 0 ? '+' : '') + slipBps.toFixed(1)}bp</span></div>`
        : '<span class="text-muted" style="font-size:10px">-</span>';
      return `<tr>
        <td style="white-space:nowrap">${fmt_ts(t.executed_at)}</td>
        <td><strong style="font-size:12px">${escHtml(t.name || t.symbol || '-')}</strong>${(t.name && t.symbol && t.name !== t.symbol) ? ` <span style="font-size:10px;color:var(--text-muted)">${escHtml(t.symbol)}</span>` : ''}</td>
        <td style="text-align:center">${sideBadge}</td>
        <td style="font-size:12px;text-align:right">${fmt_qty(t.quantity)}</td>
        <td style="font-size:12px;text-align:right">${fmt_price(t.unit_price, t.market)}</td>
        <td style="font-size:12px;text-align:right;font-weight:500">${amtDisplay}</td>
        <td style="font-size:11px;text-align:right;color:var(--text-muted)">${feeDisplay}</td>
        <td style="font-size:12px;text-align:right;font-weight:600">${setlDisplay}</td>
        ${tab==='crypto' ? `<td style="text-align:center">${slipBar}</td>` : ''}
      </tr>`;
    }).join('');

    const extraTh = tab === 'crypto' ? `<th style="padding:6px 8px;text-align:center">체결오차${helpBtn('slippage')}</th>` : '';
    wrap.innerHTML = `<div class="table-wrap" style="overflow-x:auto">
      <table style="width:100%;font-size:12px;border-collapse:collapse">
        <thead><tr style="background:var(--bg-secondary);font-size:11px;color:var(--text-muted)">
          <th style="padding:6px 8px;text-align:left">체결시각</th>
          <th style="padding:6px 8px;text-align:left">${tab==='crypto' ? '코인' : '종목'}</th>
          <th style="padding:6px 8px;text-align:center">종류</th>
          <th style="padding:6px 8px;text-align:right">거래수량</th>
          <th style="padding:6px 8px;text-align:right">거래단가</th>
          <th style="padding:6px 8px;text-align:right">거래금액</th>
          <th style="padding:6px 8px;text-align:right">수수료${helpBtn('trade_fee')}</th>
          <th style="padding:6px 8px;text-align:right">정산금액${helpBtn('trade_settlement')}</th>
          ${extraTh}
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
    <div class="text-muted mt-8" style="font-size:11px;padding:0 4px">전체 ${total}건 중 ${items.length}건 표시</div>`;
  }

  async function _tpLoad(tab) {
    const s = _tpState[tab];
    if (!s) return;
    s.loading = true;
    _tpRenderTable(tab);
    try {
      const url = `/api/account/trades?tab=${tab}&period=${s.period}&type=${s.type}&symbol=${encodeURIComponent(s.symbol)}&limit=100`;
      s.data = await GET(url);
    } catch(e) { s.data = { items: [], total: 0 }; }
    s.loading = false;
    _tpRenderTable(tab);
  }

  function _tpFilterSection(tab) {
    const s = _tpState[tab] || {};
    const periods = [{key:'1w',label:'1주일'},{key:'1m',label:'1개월'},{key:'3m',label:'3개월'}];
    const types   = [{key:'all',label:'전체'},{key:'buy',label:'매수'},{key:'sell',label:'매도'}];
    const pBtns = periods.map(p => `<button class="trade-filter-btn ${s.period===p.key?'active':''}" onclick="_tpFilter('${tab}','period','${p.key}')">${p.label}</button>`).join('');
    const tBtns = types.map(t => `<button class="trade-filter-btn ${s.type===t.key?'active':''}" onclick="_tpFilter('${tab}','type','${t.key}')">${t.label}</button>`).join('');
    return `<div class="trade-filter-row" style="margin-bottom:12px">
      <div class="trade-filter-group"><span style="font-size:11px;color:var(--text-muted);margin-right:4px">기간${helpBtn('trade_period_filter')}</span>${pBtns}</div>
      <div class="trade-filter-group"><span style="font-size:11px;color:var(--text-muted);margin-right:4px">유형${helpBtn('trade_type_filter')}</span>${tBtns}</div>
      <input class="trade-search-input" type="text" placeholder="${tab==='crypto'?'코인':'종목'} 검색"
        value="${escHtml(s.symbol||'')}"
        oninput="_tpSearch('${tab}', this.value)"
        style="width:90px;padding:3px 7px;border-radius:6px;border:1px solid var(--border);font-size:12px;background:var(--bg-card);color:var(--text)">
    </div>`;
  }

  // 전역 핸들러
  window._tpFilter = function(tab, key, val) {
    if (!_tpState[tab]) return;
    _tpState[tab][key] = val;
    document.querySelectorAll(`#tp-panel-${tab} .trade-filter-btn`).forEach(btn => {
      const v = btn.textContent.trim();
      const pm = {'1주일':'1w','1개월':'1m','3개월':'3m'};
      const tm = {'전체':'all','매수':'buy','매도':'sell'};
      const m = pm[v] || tm[v] || v;
      btn.classList.toggle('active', m === _tpState[tab].period || m === _tpState[tab].type);
    });
    _tpLoad(tab);
  };
  window._tpSearch = function(tab, val) {
    if (!_tpState[tab]) return;
    clearTimeout(_tpState[tab]._searchTimer);
    _tpState[tab]._searchTimer = setTimeout(() => {
      _tpState[tab].symbol = val.trim();
      _tpLoad(tab);
    }, 400);
  };
  window._tpSwitch = function(tab) {
    if (!_tpState) return;
    _tpState.activeTab = tab;
    ['crypto','stock','overseas'].forEach(t => {
      const p = document.getElementById(`tp-panel-${t}`);
      if (p) p.classList.toggle('hidden', t !== tab);
    });
    document.querySelectorAll('.tp-tab-btn').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.tab === tab);
    });
    // lazy-load
    if (_tpState[tab].data === null && !_tpState[tab].loading) _tpLoad(tab);
  };

  function _buildTradeSummary(items) {
    if (!items || !items.length) return '';
    const buys  = items.filter(t => t.side === 'buy');
    const sells = items.filter(t => t.side === 'sell');
    const totalAmt = items.reduce((s, t) => s + (Number(t.trade_amount_krw || t.trade_amount) || 0), 0);
    const totalFee = items.reduce((s, t) => s + (Number(t.fee_krw || t.fee) || 0), 0);
    return `
    <div class="trade-summary-strip">
      <div class="trade-summary-card">
        <div class="trade-summary-label">총 체결건</div>
        <div class="trade-summary-value">${items.length}건</div>
        <div class="trade-summary-sub">전체 기간</div>
      </div>
      <div class="trade-summary-card">
        <div class="trade-summary-label">매수 / 매도</div>
        <div class="trade-summary-value">${buys.length} / ${sells.length}</div>
        <div class="trade-summary-sub">건수</div>
      </div>
      <div class="trade-summary-card">
        <div class="trade-summary-label">총 거래금액</div>
        <div class="trade-summary-value" style="font-size:14px">${fmt_krw(Math.round(totalAmt))}</div>
        <div class="trade-summary-sub">합산</div>
      </div>
      <div class="trade-summary-card">
        <div class="trade-summary-label">총 수수료</div>
        <div class="trade-summary-value" style="font-size:14px">${fmt_krw(Math.round(totalFee))}</div>
        <div class="trade-summary-sub">합산</div>
      </div>
    </div>`;
  }

  // ── HTML 렌더링 ──
  const tabLabels = { crypto:'코인', stock:'국내주식', overseas:'해외주식' };
  const tabs = ['crypto','stock','overseas'];
  const active = _tpState.activeTab || 'crypto';

  const tabBtns = tabs.map(t =>
    `<button class="tp-tab-btn trade-period-tab ${t===active?'active':''}" data-tab="${t}" onclick="_tpSwitch('${t}')">${tabLabels[t]}</button>`
  ).join('');

  const panels = tabs.map(t => `
    <div id="tp-panel-${t}" class="${t===active?'':'hidden'}">
      <div class="section-box">
        ${_tpFilterSection(t)}
        <div id="tp-summary-${t}"></div>
        <div id="tp-table-wrap-${t}">
          <div class="text-muted" style="padding:12px;text-align:center">로딩 중...</div>
        </div>
      </div>
    </div>`
  ).join('');

  el.innerHTML = `
    <div class="page-header">
      <h1 class="page-title">거래내역</h1>
      <div class="page-sub">코인·국내주식·해외주식 체결 이력</div>
    </div>
    <div class="content-wrap">
      <div class="trade-period-tabs" style="margin-bottom:16px">${tabBtns}</div>
      ${panels}
    </div>`;

  // 초기 로드 — 현재 활성 탭
  setTimeout(() => _tpLoad(active), 0);
}

async function renderReports() {
  const el = document.getElementById('page-reports');
  el.innerHTML = '<div class="page-header"><h1 class="page-title">리포트</h1></div><div class="text-muted">목록 로딩 중...</div>';
  try {
    const data = await GET('/api/reports');
    const reports = data.reports || [];
    renderReportsPage(reports);
  } catch (e) {
    el.innerHTML = `<div class="page-header"><h1 class="page-title">리포트</h1></div>
      <div class="section-box" style="color:var(--danger)">오류: ${e.message}</div>`;
  }
}

function renderReportsPage(reports) {
  const el = document.getElementById('page-reports');

  // 카테고리별로 그룹핑
  const groups = {};
  reports.forEach(r => {
    const cat = reportCategory(r.name);
    if (!groups[cat]) groups[cat] = [];
    groups[cat].push(r);
  });

  const listHtml = reports.length === 0
    ? '<div class="text-muted" style="padding:12px">리포트 없음</div>'
    : REPORT_CATEGORY_ORDER.filter(c => groups[c]).map(cat => `
      <div class="report-category-header">${cat}</div>
      ${groups[cat].map(r => `
        <div class="report-item ${activeReport === r.name ? 'active' : ''}" data-name="${escHtml(r.name)}" onclick="loadReport('${escHtml(r.name)}')">
          <div class="report-item-row">
            <div class="report-label">${reportDisplayName(r.name)}</div>
            <button class="report-refresh-btn" data-help-title="갱신 주기" data-help-text="${escHtml(reportRefreshTooltip(r.name))}" onclick="showHelpFromEl(event)">?</button>
          </div>
          <div class="report-meta">${fmt_time(r.modified)}</div>
        </div>`).join('')}
    `).join('');

  el.innerHTML = `
    <div class="page-header">
      <h1 class="page-title">리포트 뷰어</h1>
      <div class="page-subtitle">분석·매매·점검 리포트 — ${reports.length}개</div>
    </div>
    <div class="reports-layout">
      <div class="reports-sidebar">
        <div class="reports-sidebar-header">리포트 목록</div>
        ${listHtml}
      </div>
      <div class="reports-content" id="report-content">
        <div class="text-muted report-placeholder">왼쪽에서 리포트를 선택하세요</div>
      </div>
    </div>
  `;

  if (activeReport) loadReport(activeReport);
}

async function loadReport(name) {
  activeReport = name;
  const contentEl = document.getElementById('report-content');
  if (!contentEl) return;
  contentEl.innerHTML = '<div class="text-muted">로딩 중...</div>';

  document.querySelectorAll('.report-item').forEach(el => {
    el.classList.toggle('active', el.dataset.name === name);
  });

  try {
    const data = await GET(`/api/reports/${encodeURIComponent(name)}`);
    const displayName = reportDisplayName(name);
    contentEl.innerHTML = `
      <div class="report-toolbar">
        <span class="report-display-name">${escHtml(displayName)}</span>
        <span class="report-filename-small">${escHtml(name)}</span>
        <button class="btn btn-ghost btn-sm" onclick="loadReport('${escHtml(name)}')">↺ 새로고침</button>
      </div>
      <div class="report-body">${md2html(data.content || '')}</div>
    `;
  } catch (e) {
    contentEl.innerHTML = `<div class="text-danger">오류: ${e.message}</div>`;
  }
}

// ══════════════════════════════════════════════
// 공통
// ══════════════════════════════════════════════
function updateSidebarAccount(d, approval) {
  const cryptoEx = cryptoExchangesFromStatus(d);
  const holdings = [
    ...(cryptoEx.upbit?.holdings || []),
    ...(cryptoEx.bithumb?.holdings || []),
  ];
  const stockHoldings = d.stock_holdings || [];
  const krwTotal = cryptoCashTotal(cryptoEx);
  const coinVal  = holdings.reduce((s, h) => s + (h.valuation_krw || 0), 0);
  const stockVal = stockHoldings.reduce((s, h) => s + (h.market_value || 0), 0);
  const kisTotal = d.stock_total_equity > 0
    ? d.stock_total_equity
    : stockVal + (d.stock_balance_krw || 0);
  const totalAsset = coinVal + krwTotal + kisTotal;

  // ── 구버전 사이드바 위젯 (하위 호환) ──
  const widget = document.getElementById('sidebar-account-widget');
  const elVal  = document.getElementById('sb-total-asset');
  const elSub  = document.getElementById('sb-account-sub');
  if (widget && elVal) {
    widget.style.display = '';
    elVal.textContent = fmt_krw(Math.round(totalAsset));
    if (elSub) elSub.textContent = `Upbit ${(cryptoEx.upbit?.holdings || []).length} · Bithumb ${(cryptoEx.bithumb?.holdings || []).length} · 주식 ${stockHoldings.length}종목`;
  }

  // ── 신버전 상단 Nav 자산 표시 ──
  const tnavAsset = document.getElementById('topnav-asset');
  const tnavVal   = document.getElementById('tnav-total-asset');
  if (tnavAsset && tnavVal) {
    tnavAsset.style.display = '';
    tnavVal.textContent = fmt_krw(Math.round(totalAsset));
  }

  // ── 승인 대기 배지 — 상단 Nav 대시보드 링크 ──
  const ca = (approval || {}).crypto || {};
  const sa = (approval || {}).stock  || {};
  const pendingCount = (ca.pending ? 1 : 0) + (sa.pending ? 1 : 0);
  // 구버전 사이드바
  const dashLink = document.querySelector('.nav-item[data-page="dashboard"]');
  if (dashLink) {
    let badge = dashLink.querySelector('.nav-badge');
    if (pendingCount > 0) {
      if (!badge) { badge = document.createElement('span'); badge.className = 'nav-badge danger'; dashLink.appendChild(badge); }
      badge.textContent = pendingCount;
    } else if (badge) { badge.remove(); }
  }
  // 신버전 상단 Nav
  const tnDashLink = document.querySelector('.topnav-link[data-page="dashboard"]');
  if (tnDashLink) {
    let tnBadge = tnDashLink.querySelector('.nav-badge');
    if (pendingCount > 0) {
      if (!tnBadge) { tnBadge = document.createElement('span'); tnBadge.className = 'nav-badge danger'; tnDashLink.appendChild(tnBadge); }
      tnBadge.textContent = pendingCount;
    } else if (tnBadge) { tnBadge.remove(); }
  }
}

function updateAutoExecBanner(data) {
  const banner = document.getElementById('auto-exec-banner');
  if (!banner) return;
  const sections = (data || {}).sections || {};
  const upbit = sections.upbit || [];
  const kis   = sections.kis   || [];
  const isTrue = v => ['true','1','yes','on'].includes((v || '').toLowerCase());
  const cryptoItem   = upbit.find(i => i.key === 'CRYPTO_AUTO_EXECUTE_WITHOUT_APPROVAL');
  const kisItem      = kis.find(i => i.key === '_KIS_FULL_AUTO');
  const overseasItem = kis.find(i => i.key === 'OVERSEAS_AUTO_EXECUTE_WITHOUT_APPROVAL');
  const active = [];
  if (isTrue(cryptoItem?.value))   active.push('코인');
  if (isTrue(kisItem?.value))      active.push('국내주식');
  if (isTrue(overseasItem?.value)) active.push('해외주식');
  banner.style.display = '';
  if (active.length > 0) {
    banner.className = 'auto-exec-banner danger';
    banner.textContent = '⚠ ' + active.join(', ') + ' 무승인 거래중입니다';
  } else {
    banner.className = 'auto-exec-banner ok';
    banner.textContent = '✓ 사용자 승인 실행중입니다';
  }
}

async function fetchAndUpdateAutoExecBanner() {
  try { updateAutoExecBanner(await GET('/api/settings')); } catch(e) {}
}

function updateRunnerBadge(r) {
  const dot  = document.querySelector('.badge-dot');
  const text = document.querySelector('.badge-text');
  if (!dot || !text) return;
  dot.className = 'badge-dot';
  if (r.running && !r.paused) {
    dot.classList.add('running');
    text.textContent = '실행 중';
  } else if (r.running && r.paused) {
    dot.classList.add('paused');
    text.textContent = '일시정지';
  } else {
    dot.classList.add('stopped');
    text.textContent = '중지됨';
  }
}

// ══════════════════════════════════════════════
// 실시간 이벤트 WebSocket (/ws/events)
// ══════════════════════════════════════════════
let eventsWs = null;
let eventsReconnectTimer = null;

function connectEventsWs() {
  if (eventsWs && eventsWs.readyState <= 1) return;
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  eventsWs = new WebSocket(`${proto}://${location.host}/ws/events`);

  eventsWs.onopen = () => {
    clearTimeout(eventsReconnectTimer);
  };

  eventsWs.onclose = () => {
    eventsReconnectTimer = setTimeout(connectEventsWs, 3000);
  };

  eventsWs.onerror = () => {
    eventsWs.close();
  };

  eventsWs.onmessage = (e) => {
    let msg;
    try { msg = JSON.parse(e.data); } catch { return; }
    handleRealtimeEvent(msg);
  };
}

function handleRealtimeEvent(msg) {
  const { type, data } = msg;
  if (!type || type === 'ping') return;

  switch (type) {
    // ── 초기 스냅샷 ──────────────────────────
    case 'snapshot':
      if (data.runner) updateRunnerBadge(data.runner);
      break;

    // ── 러너 상태 변화 ──────────────────────
    case 'runner_state':
    case 'runner_pid':
    case 'runner_control': {
      // 배지만 즉시 갱신 (전체 재렌더 안 함 — 깜빡임 방지). 데이터는 30초 폴링이 처리.
      const running = !data._deleted && (data.running !== false);
      updateRunnerBadge({ running, paused: data.runner_paused || false });
      // 사용자가 명시적으로 시작/중지/재시작한 경우에만 갱신 + 토스트
      if (type === 'runner_control') {
        const labels = { started: '러너 시작됨', stopped: '러너 중지됨', restarted: '러너 재시작됨' };
        const src = data.source === 'web_ui' ? '' : ' (텔레그램)';
        toast((labels[data.action] || data.action) + src, 'info');
        if (currentPage === 'dashboard') scheduleDashboardRefresh();
        if (currentPage === 'runner')    loadRunner();
      }
      break;
    }

    // ── 코인 승인 요청 ──────────────────────
    case 'crypto_approval_request':
      if (!data._deleted && data.status === 'PENDING') {
        toast(`코인 매수 승인 요청: ${data.market || ''}`, 'warning', 8000);
        if (currentPage === 'dashboard') scheduleDashboardRefresh();
      }
      break;

    // ── 코인 승인 결과 ──────────────────────
    case 'crypto_approval_update': {
      const icon = data.action === 'approved' ? '✓' : '✕';
      const src  = data.source === 'web_ui' ? '[웹UI]' : '[텔레그램]';
      toast(`${icon} ${src} ${data.market || ''} ${data.action === 'approved' ? '승인' : '거부'}됨`, 'success', 6000);
      if (currentPage === 'dashboard') scheduleDashboardRefresh();
      break;
    }

    // ── 주식 승인 변화 ──────────────────────
    case 'stock_approval_update': {
      const icon = data.action === 'approved' ? '✓' : data.action === 'halt' ? '■' : '✕';
      const src  = data.source === 'web_ui' ? '[웹UI]' : '[텔레그램]';
      toast(`${icon} ${src} ${data.symbol || '주식'} ${data.action}`, 'info', 6000);
      if (currentPage === 'dashboard') scheduleDashboardRefresh();
      break;
    }

    // ── 새 주문 플랜 (잦음 → 토스트만, 전체 재렌더 안 함) ──
    case 'order_plan':
      if (!data._deleted && data.market) {
        toast(`새 신호: ${data.market} (${data.side || '매수'} 검토 중)`, 'info', 6000);
        if (currentPage === 'analysis') loadAnalysis();
      }
      break;
  }
}

// ══════════════════════════════════════════════
// 초기화
// ══════════════════════════════════════════════

// 1) Telegram WebApp 인증 처리 (비동기, 화면 뒤에서 실행)
initTelegramAuth();

// 2) 초기 페이지 라우팅
const hash = window.location.hash.replace('#', '') || 'dashboard';
navigate(hash);

// 3) 실시간 이벤트 WebSocket 연결
connectEventsWs();

// 4) 무승인 자동실행 배너 초기화
fetchAndUpdateAutoExecBanner();

window.addEventListener('beforeunload', () => {
  clearInterval(dashInterval);
  clearInterval(runnerInterval);
  clearInterval(analysisInterval);
  if (logWs) logWs.close();
  if (eventsWs) eventsWs.close();
});
