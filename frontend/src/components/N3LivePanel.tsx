import React, { useEffect, useState } from 'react';
import { Moon, Zap } from 'lucide-react';
import { N3Status } from '../types';
import { getApi } from '../utils/apiConfig';

interface N3LivePanelProps {
  colorMode: 'KRX' | 'GLOBAL';
  marketVolumePower: number | null;  // /quant/status 체결강도 (기본 삼성전자)
  marketCode?: string;
}

const POLL_MS = 15000;  // 서버도 15~60초 재사용하므로 더 자주 부를 필요 없음

const pct = (v: number) => `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`;

export const N3LivePanel: React.FC<N3LivePanelProps> = ({ colorMode, marketVolumePower, marketCode }) => {
  const [status, setStatus] = useState<N3Status | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const res = await getApi('/n3/status');
        if (!alive) return;
        if (res.ok) { setStatus(await res.json()); setFailed(false); } else setFailed(true);
      } catch {
        if (alive) setFailed(true);
      }
    };
    load();
    const id = setInterval(load, POLL_MS);
    return () => { alive = false; clearInterval(id); };
  }, []);

  // 한국식(KRX): 상승 빨강·하락 파랑
  const upDown = (v: number) => (v >= 0
    ? (colorMode === 'KRX' ? 'text-rose-400' : 'text-emerald-400')
    : (colorMode === 'KRX' ? 'text-blue-400' : 'text-rose-400'));

  const tl = status?.timeline;
  const cand = status?.candidates;
  const holdings = status?.holdings ?? [];

  return (
    <div className="lg:col-span-6 bg-slate-950/60 p-3 rounded-lg border border-slate-800 flex flex-col space-y-2.5 min-w-0" data-testid="n3-panel">
      {/* 머리글: 현재 단계 */}
      <div className="flex items-center justify-between gap-2 text-xs">
        <span className="font-bold text-slate-300 flex items-center gap-1.5 shrink-0">
          <Moon className="w-3.5 h-3.5 text-indigo-400" />
          N3 애프터마켓
        </span>
        <span className="text-[10px] text-slate-400 truncate">
          {!status ? (failed ? '조회 실패' : '불러오는 중…')
            : !tl?.trading_day ? '휴장일'
            : <>지금 <strong className="text-indigo-300">{tl.current}</strong>{tl.next && <> · 다음 {tl.next.time} {tl.next.label}</>}</>}
        </span>
      </div>

      {/* 2. 진행 단계 */}
      {tl && (
        <div className="flex flex-wrap gap-1" data-testid="n3-timeline">
          {tl.steps.map((s) => (
            <span
              key={s.time}
              className={`text-[10px] px-1.5 py-0.5 rounded border font-mono ${
                s.status === 'current' ? 'bg-indigo-500/25 text-indigo-200 border-indigo-400/50 font-bold'
                  : s.status === 'done' ? 'bg-slate-800/60 text-slate-500 border-slate-700'
                  : 'bg-transparent text-slate-400 border-slate-700'}`}
            >
              {s.time} {s.label}
            </span>
          ))}
        </div>
      )}

      {/* 3. 보유 중인 N3 종목 (있을 때만) */}
      {holdings.length > 0 && (
        <div className="space-y-1.5" data-testid="n3-holdings">
          {holdings.map((h) => {
            const ob = h.orderbook_imbalance;
            const depth = ob ? ob.total_bid_qty + ob.total_ask_qty : 0;
            const bidRatio = depth > 0 ? (ob!.total_bid_qty / depth) * 100 : null;
            return (
              <div key={h.code} className="rounded border border-indigo-500/30 bg-indigo-500/5 px-2 py-1.5 text-[11px]">
                <div className="flex justify-between gap-2">
                  <span className="text-slate-200 font-bold truncate">보유 {h.name} <span className="text-slate-500 font-mono">{h.qty}주</span></span>
                  <span className="font-mono shrink-0">
                    {h.buy_price ? `${Math.round(h.buy_price).toLocaleString()} → ` : ''}
                    {h.cur ? Math.round(h.cur).toLocaleString() : '-'}
                    {h.pnl_pct !== null && <strong className={`ml-1 ${upDown(h.pnl_pct)}`}>{pct(h.pnl_pct)}</strong>}
                  </span>
                </div>
                <div className="flex justify-between gap-2 text-[10px] text-slate-400 mt-0.5">
                  <span>{h.market} 체결강도 <strong className="text-slate-200 font-mono">{h.volume_power !== null ? `${h.volume_power.toFixed(1)}%` : '데이터 없음'}</strong></span>
                  <span>호가 매수 비중 <strong className="text-slate-200 font-mono">{bidRatio !== null ? `${bidRatio.toFixed(0)}%` : '데이터 없음'}</strong></span>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* 1. 오늘 밤 후보 미리보기 */}
      <div className="text-[11px]" data-testid="n3-candidates">
        {!cand ? null : cand.note ? (
          <div className="text-slate-500 py-1">후보: {cand.note}</div>
        ) : (
          <>
            <div className="text-slate-400 mb-1 flex flex-wrap gap-x-2">
              <span>대상 {cand.universe} · 시세 {cand.quoted}</span>
              <span>신호 <strong className={cand.signals ? 'text-amber-300' : 'text-slate-300'}>{cand.signals}건</strong></span>
              <span>빈 슬롯 {cand.slots} · 종목당 {Math.round(cand.budget ?? 0).toLocaleString()}원</span>
            </div>
            <table className="w-full text-[11px] font-mono">
              <thead>
                <tr className="text-slate-500 text-[10px]">
                  <th className="text-left font-normal">종목</th>
                  <th className="text-right font-normal">NXT 현재가</th>
                  <th className="text-right font-normal">종가 대비</th>
                </tr>
              </thead>
              <tbody>
                {(cand.items ?? []).map((r) => (
                  <tr key={r.code} className={r.signal ? 'text-amber-200' : 'text-slate-300'}>
                    <td className="truncate max-w-[9rem] font-sans">{r.signal && '● '}{r.name}</td>
                    <td className="text-right">{r.cur.toLocaleString()}</td>
                    <td className={`text-right ${upDown(r.a)}`}>{pct(r.a * 100)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="text-[10px] text-slate-500 mt-1">
              ● 신호(−3% 이하) · 지금 판단하면:{' '}
              {cand.picks && cand.picks.length > 0
                ? cand.picks.map((p) => `${p.name} ${p.qty}주@${p.price.toLocaleString()}`).join(', ')
                : '주문 없음'}
            </div>
          </>
        )}
      </div>

      {/* 마지막 판단 결과 */}
      {status?.last_judgment && (
        <div className="text-[10px] text-slate-500 truncate" title={status.last_judgment.message} data-testid="n3-last-judgment">
          마지막 판단 ({status.last_judgment.timestamp}): {status.last_judgment.message.replace(/^\[N3\/[^\]]*\]\s*19:50 판단:\s*/, '')}
        </div>
      )}
      {status?.error && <div className="text-[10px] text-rose-400">{status.error}</div>}

      {/* 시장 체결강도 */}
      <div className="flex items-center gap-1 pt-1 border-t border-white/5 text-[11px] text-slate-400" data-testid="market-volume-power">
        <Zap className="w-3.5 h-3.5 text-amber-400" />
        시장 체결강도{marketCode ? ` (${marketCode})` : ''}:
        <strong className={`ml-1 font-mono ${marketVolumePower !== null && marketVolumePower >= 120 ? 'text-amber-300' : 'text-slate-200'}`}>
          {marketVolumePower !== null ? `${marketVolumePower.toFixed(1)}%` : '데이터 없음'}
        </strong>
      </div>
    </div>
  );
};
