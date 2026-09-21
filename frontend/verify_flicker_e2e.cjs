const http = require('http');
const fs = require('fs');
const path = require('path');
const { WebSocketServer } = require('ws');
const puppeteer = require('puppeteer-core');

// 1. Mock 백엔드 서버 (REST API & WebSocket 동시 서빙)
const server = http.createServer((req, res) => {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');

  if (req.method === 'OPTIONS') {
    res.writeHead(200);
    res.end();
    return;
  }

  const rawUrl = req.url || '';
  const url = rawUrl.split('?')[0];

  if (url === '/api/status' || url === '/status') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ running: true, is_demo: true, circuit_breaker_open: false }));
  } else if (url === '/api/watchlist' || url === '/watchlist') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify([{ code: '005930', name: '삼성전자', current_price: 71500 }]));
  } else if (url.startsWith('/api/logs') || url.startsWith('/logs')) {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ logs: [] }));
  } else if (url === '/api/quant/performance' || url === '/quant/performance') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ daily_return_pct: 1.5, win_rate_pct: 75.0, total_trades: 12 }));
  } else if (url === '/api/quant/status' || url === '/quant/status') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ regime: 'BULL_TREND', regime_reason: '상승장' }));
  } else if (url === '/api/portfolio' || url === '/portfolio') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({
      total_asset: 10000000,
      current_capital: 8141000,
      invested_capital: 1859000,
      stock_count: 2,
      positions: [
        { code: '005930', name: '삼성전자', qty: 26, buy_price: 71500, current_price: 71500, pnl: 0, yield_rate: 0, sell_stage: 0 },
        { code: '000660', name: 'SK하이닉스', qty: 10, buy_price: 150000, current_price: 152000, pnl: 20000, yield_rate: 1.33, sell_stage: 1 }
      ]
    }));
  } else {
    // Vite 빌드 정적 파일 안전 서빙
    const cleanPath = url === '/' ? 'index.html' : url.replace(/^\/+/, '');
    let filePath = path.resolve(__dirname, 'dist', cleanPath);
    if (!fs.existsSync(filePath)) {
      filePath = path.resolve(__dirname, 'dist', 'index.html');
    }

    const ext = path.extname(filePath);
    let contentType = 'text/html';
    if (ext === '.js') contentType = 'application/javascript';
    if (ext === '.css') contentType = 'text/css';
    if (ext === '.json') contentType = 'application/json';
    if (ext === '.svg') contentType = 'image/svg+xml';

    fs.readFile(filePath, (err, content) => {
      if (err) {
        res.writeHead(404);
        res.end('Not Found');
      } else {
        res.writeHead(200, { 'Content-Type': contentType });
        res.end(content);
      }
    });
  }
});

// 2. WebSocket 서버 탑재
const wss = new WebSocketServer({ server });
const wsClients = new Set();

wss.on('connection', (ws, req) => {
  wsClients.add(ws);
  // 초기 포트폴리오 상태 전송
  ws.send(JSON.stringify({
    type: 'PORTFOLIO_INIT',
    data: {
      total_asset: 10000000,
      current_capital: 8141000,
      invested_capital: 1859000,
      stock_count: 2,
      positions: [
        { code: '005930', name: '삼성전자', qty: 26, buy_price: 71500, current_price: 71500, pnl: 0, yield_rate: 0, sell_stage: 0 },
        { code: '000660', name: 'SK하이닉스', qty: 10, buy_price: 150000, current_price: 152000, pnl: 20000, yield_rate: 1.33, sell_stage: 1 }
      ]
    }
  }));

  ws.on('close', () => wsClients.delete(ws));
});

function broadcastPortfolioTick(price, pnl, yieldRate) {
  const payload = JSON.stringify({
    type: 'PORTFOLIO_UPDATE',
    data: {
      total_asset: 10000000 + pnl,
      current_capital: 8141000,
      invested_capital: 1859000 + pnl,
      stock_count: 2,
      positions: [
        { code: '005930', name: '삼성전자', qty: 26, buy_price: 71500, current_price: price, pnl: pnl, yield_rate: yieldRate, sell_stage: 0 },
        { code: '000660', name: 'SK하이닉스', qty: 10, buy_price: 150000, current_price: 152000, pnl: 20000, yield_rate: 1.33, sell_stage: 1 }
      ]
    }
  });
  for (const client of wsClients) {
    if (client.readyState === 1) {
      client.send(payload);
    }
  }
}

async function runVerification() {
  const PORT = 8501;
  server.listen(PORT, async () => {
    console.log(`🚀 [Test Server] E2E 검증 서버 가동: http://localhost:${PORT}`);

    const chromePath = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
    const browser = await puppeteer.launch({
      executablePath: chromePath,
      headless: 'new',
      args: ['--no-sandbox', '--disable-setuid-sandbox']
    });

    try {
      const page = await browser.newPage();
      await page.setViewport({ width: 1600, height: 900 });

      const consoleErrors = [];
      page.on('console', msg => {
        console.log(`  [Browser Console ${msg.type()}]: ${msg.text()}`);
        if (msg.type() === 'error') {
          consoleErrors.push(msg.text());
        }
      });
      page.on('pageerror', err => {
        console.log(`  [Browser PageError]: ${err.message}`);
      });

      console.log('📡 [E2E] 브라우저 페이지 로드 중...');
      await page.goto(`http://localhost:${PORT}`, { waitUntil: 'networkidle0', timeout: 15000 });

      // 1. 초기 렌더링 확인
      await page.waitForSelector('.bento-card', { timeout: 8000 });
      console.log('✅ [E2E] Bento Grid 대시보드 렌더링 확인 완료');

      // 보유 종목 렌더링 대기
      await new Promise(r => setTimeout(r, 1000));

      // 2. 초기 보유 종목 카드 확인
      const initialCards = await page.$$eval('.bento-card', cards => {
        for (const card of cards) {
          if (card.textContent && card.textContent.includes('현재 보유 포지션')) {
            const stockElements = card.querySelectorAll('[class*="rounded-xl border transition"]');
            return stockElements.length;
          }
        }
        return 0;
      });
      console.log(`📦 [E2E] 초기 렌더링된 보유 종목 수: ${initialCards}개 (삼성전자, SK하이닉스)`);

      // 3. 0.4초 간격으로 10회 연속 WebSocket 실시간 데이터 브로드캐스트 주입 및 깜빡임 감시
      console.log('⚡ [E2E] 0.4초 간격 실시간 WebSocket 시세 주입 시작 (10회 연속 갱신 & 깜빡임 감시)...');

      let flickerDetectedCount = 0;
      let zeroCardsObserved = 0;
      let successfulPriceUpdates = 0;

      for (let i = 1; i <= 10; i++) {
        const simulatedPrice = 71500 + (i * 300); // 71,800 ~ 74,500원 변동
        const simulatedPnl = (simulatedPrice - 71500) * 26;
        const simulatedYield = ((simulatedPrice / 71500) - 1) * 100;

        // WebSocket으로 실시간 시세 브로드캐스트 전송
        broadcastPortfolioTick(simulatedPrice, simulatedPnl, simulatedYield);

        await new Promise(r => setTimeout(r, 400));

        // 해당 틱 시점에서 빈 화면(Empty State) 노출 여부 실측
        const checkResult = await page.evaluate(() => {
          const bodyText = document.body.innerText;
          const hasEmptyNotice = bodyText.includes('현재 보유 중인 포지션이 없습니다');
          const cards = document.querySelectorAll('.bento-card');
          let posCount = 0;
          for (const card of cards) {
            if (card.textContent && card.textContent.includes('현재 보유 포지션')) {
              posCount = card.querySelectorAll('[class*="rounded-xl border transition"]').length;
            }
          }
          return { hasEmptyNotice, posCount };
        });

        if (checkResult.hasEmptyNotice) {
          flickerDetectedCount++;
          console.log(`  ❌ [Flicker Detected] Tick ${i}: 빈 화면 깜빡임 발생!`);
        }
        if (checkResult.posCount === 0) {
          zeroCardsObserved++;
        } else {
          successfulPriceUpdates++;
        }

        process.stdout.write(`  • Tick ${i}/10: 포지션 ${checkResult.posCount}개 완벽 유지 | 삼성전자 현재가 ${simulatedPrice.toLocaleString()}원 (+${simulatedYield.toFixed(2)}%)\n`);
      }

      console.log('='.repeat(60));
      console.log('📊 [E2E 검증 결과 보고]');
      console.log(`  • 총 실시간 틱 주입 횟수: 10회`);
      console.log(`  • 깜빡임(Empty State) 발생 횟수: ${flickerDetectedCount}회`);
      console.log(`  • 포지션 카드 소멸(0건) 횟수: ${zeroCardsObserved}회`);
      console.log(`  • 콘솔 에러: ${consoleErrors.length}건`);

      if (flickerDetectedCount === 0 && zeroCardsObserved === 0 && initialCards === 2 && consoleErrors.length === 0) {
        console.log('\n🏆 [검증 대성공] 실시간 새로고침 시 깜빡임(Flickering) 0건 & Seamless In-Place 렌더링 100% 실측 완료!');
      } else {
        console.error('\n❌ [검증 실패] 깜빡임 또는 렌더링 결함 발생');
        process.exit(1);
      }

    } catch (err) {
      console.error('E2E 실행 에러:', err);
      process.exit(1);
    } finally {
      await browser.close();
      server.close();
    }
  });
}

runVerification();
