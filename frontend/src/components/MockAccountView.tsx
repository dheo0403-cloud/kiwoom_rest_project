import React, { useEffect, useState } from 'react';
import { FlaskConical, CalendarClock, Wallet, ListOrdered, ScrollText } from 'lucide-react';
import { MockSummary } from '../types';
import { getApiUrl } from '../utils/apiConfig';

interface MockAccountViewProps {
  colorMode: 'KRX' | 'GLOBAL';
}

const won = (v?: number | null) => (v === undefined || v === null ? '—' : `${Math.round(Number(v)).toLocaleString()}원`);

// 모의계좌(TOM 월말 스윙) 탭: 모의 봇 DB를 30초마다 읽기만 한다 — 주문·제어 버튼 없음
export const MockAccountView: React.FC<MockAccountViewProps> = ({ colorMode }) => {
  const [data, setData] = useState<MockSummary | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const res = await fetch(getApiUrl('/mock/summary'));
        if (!res.ok) throw new Error(String(res.status));
        const body: MockSummary = await res.json();
        if (alive) { setData(body); setFailed(false); }
      } catch {
        if (alive) setFailed(true);
      }
    };
    load();
    const timer = setInterval(load, 30000);
    return () => { alive = false; clearInterval(timer); };
  }, []);

  const upDown = (v: number) => v > 0
    ? (colorMode === 'KRX' ? 'text-rose-400' : 'text-emerald-400')
    : v < 0 ? (colorMode === 'KRX' ? 'text-blue-400' : 'text-rose-400') : 'text-slate-300';

  if (failed && !data) {
    return <div className="bento-card p-6 text-sm text-slate-400" data-testid="mock-state">모의계좌 정보를 불러오지 못했습니다 (API 서버 응답 없음)</div>;
  }
  if (!data) {
    return <div className="bento-card p-6 text-sm text-slate-500" data-testid="mock-state">모의계좌 정보를 불러오는 중…</div>;
  }
  if (!data.configured) {
    return (
      <div className="bento-card p-6 text-sm text-slate-400" data-testid="mock-state">
        모의계좌 봇이 아직 연결되지 않았습니다. (API 서버에 MOCK_DB_NAME 미설정)
      </div>
    );
  }
  if (data.error) {
    return <div className="bento-card p-6 text-sm text-amber-400" data-testid="mock-state">모의 DB({data.db}) 조회 실패: {data.error}</div>;
  }

  const latest = data.balance && data.balance.length ? data.balance[data.balance.length - 1] : null;
  const priceOf = new Map((data.positions || []).map(p => [p.code, p]));
  const tom = data.tom_open || [];

  return (
    <div className="flex flex-col gap-3.5" data-testid="mock-view">
      {/* 요약: 전략·다음 판단일·잔고 */}
      <div className="bento-card p-4 flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <FlaskConical className="w-4 h-4 text-violet-400" />
          <span className="text-sm font-bold text-slate-200">모의계좌 · TOM 월말 스윙</span>
          <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-violet-500/15 text-violet-300 font-mono">MOCK</span>
          <span className="text-[11px] text-slate-500">월말 종가 매수 → {data.hold_days}거래일 뒤 종가 매도 · 하락장(시장 120일 평균 아래)이면 진입 안 함</span>
        </div>
        <div className="grid grid-cols-2 lg:grid-cols-5 gap-2.5 text-[12px]">
          <Stat icon={<CalendarClock className="w-3.5 h-3.5 text-amber-400" />} label="다음 월말 판단" value={`${data.next_month_end} 15:21`} />
          <Stat label="보유" value={`${tom.length} / ${data.slots}종목`} />
          <Stat icon={<Wallet className="w-3.5 h-3.5 text-blue-400" />} label="총자산" value={won(latest?.total_asset)} />
          <Stat label="주문가능(D+2)" value={won(latest?.deposit)} />
          <Stat label="평가손익" value={latest ? `${won(latest.profit_loss)} (${Number(latest.yield).toFixed(2)}%)` : '—'}
                valueClass={latest ? upDown(Number(latest.profit_loss)) : undefined} />
        </div>
        <div className="text-[10px] text-slate-500">잔고 기록 {data.balance?.length || 0}일 · 마지막 {latest?.date?.slice(0, 10) || '—'} · DB {data.db}</div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-3.5">
        {/* TOM 보유 (예정 청산일) */}
        <div className="bento-card p-4 lg:col-span-7 flex flex-col min-h-[220px]">
          <span className="text-xs font-bold text-slate-300 mb-2">TOM 보유 종목</span>
          {tom.length === 0 ? (
            <div className="flex-1 flex items-center justify-center text-[11px] text-slate-500">보유 없음</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-[11px] font-mono">
                <thead className="text-slate-500 text-left">
                  <tr><th className="py-1 pr-2">종목</th><th className="pr-2 text-right">수량</th><th className="pr-2 text-right">매입가</th>
                    <th className="pr-2 text-right">현재가</th><th className="pr-2 text-right">수익률</th><th className="pr-2">매수</th><th>예정 매도</th></tr>
                </thead>
                <tbody className="text-slate-300">
                  {tom.map(t => {
                    const p = priceOf.get(t.code);
                    const y = p && p.buy_price > 0 ? (p.current_price / p.buy_price - 1) * 100 : null;
                    return (
                      <tr key={t.code} className="border-t border-white/5">
                        <td className="py-1 pr-2">{t.name || t.code} <span className="text-slate-500">{t.code}</span></td>
                        <td className="pr-2 text-right">{p ? p.qty : <span className="text-amber-400">체결 확인 전</span>}</td>
                        <td className="pr-2 text-right">{p ? Math.round(p.buy_price).toLocaleString() : '—'}</td>
                        <td className="pr-2 text-right">{p ? Math.round(p.current_price).toLocaleString() : '—'}</td>
                        <td className={`pr-2 text-right ${y !== null ? upDown(y) : ''}`}>{y !== null ? `${y.toFixed(2)}%` : '—'}</td>
                        <td className="pr-2">{t.entry_ts?.slice(5, 10)}</td>
                        <td>{t.planned_exit?.slice(5)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* 주문 기록 */}
        <div className="bento-card p-4 lg:col-span-5 flex flex-col min-h-[220px]">
          <span className="text-xs font-bold text-slate-300 flex items-center gap-1.5 mb-2">
            <ListOrdered className="w-3.5 h-3.5 text-slate-400" />주문 기록 (최근 50건)
          </span>
          <div className="space-y-1 overflow-y-auto max-h-64 text-[11px] font-mono">
            {(data.orders || []).length === 0 ? <div className="text-slate-500">주문 없음</div> : (data.orders || []).map(o => (
              <div key={o.id} className="flex justify-between gap-2 text-slate-300">
                <span className="truncate">
                  <strong className={o.side.endsWith('BUY') ? 'text-rose-400' : 'text-blue-400'}>{o.side.endsWith('BUY') ? '매수' : '매도'}</strong>{' '}
                  {o.name || o.code} {o.qty}주{o.price ? ` @ ${Math.round(o.price).toLocaleString()}` : ''}
                </span>
                <span className="text-slate-500 shrink-0">{o.timestamp?.slice(5, 16)}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* 로그 */}
      <div className="bento-card p-4 flex flex-col">
        <span className="text-xs font-bold text-slate-300 flex items-center gap-1.5 mb-2">
          <ScrollText className="w-3.5 h-3.5 text-slate-400" />모의 봇 로그 (최근 50건)
        </span>
        <div className="space-y-0.5 overflow-y-auto max-h-72 text-[11px] font-mono">
          {(data.logs || []).length === 0 ? <div className="text-slate-500">로그 없음</div> : (data.logs || []).map(l => (
            <div key={l.id} className="flex gap-2">
              <span className="text-slate-500 shrink-0">{l.timestamp?.slice(5, 16)}</span>
              <span className={l.level === 'ERROR' || l.level === 'CRITICAL' ? 'text-rose-400' : l.level === 'WARNING' ? 'text-amber-400' : l.level === 'TRADE' ? 'text-emerald-400' : 'text-slate-300'}>
                {l.message}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

const Stat: React.FC<{ label: string; value: string; icon?: React.ReactNode; valueClass?: string }> = ({ label, value, icon, valueClass }) => (
  <div className="rounded-lg bg-white/[0.03] border border-white/5 px-3 py-2">
    <div className="text-[10px] text-slate-500 flex items-center gap-1">{icon}{label}</div>
    <div className={`font-mono font-bold mt-0.5 ${valueClass || 'text-slate-200'}`}>{value}</div>
  </div>
);
