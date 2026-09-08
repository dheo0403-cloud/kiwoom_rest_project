import React, { useState } from 'react';
import { Sliders, X, Check, RotateCcw } from 'lucide-react';
import { getApiUrl } from '../utils/apiConfig';

interface ParamsModalProps {
  isOpen: boolean;
  onClose: () => void;
  onRefresh: () => void;
}

export const ParamsModal: React.FC<ParamsModalProps> = ({ isOpen, onClose, onRefresh }) => {
  const [kVal, setKVal] = useState(0.5);
  const [kellyVal, setKellyVal] = useState(0.2);
  const [isSaving, setIsSaving] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  if (!isOpen) return null;

  const handleSave = async () => {
    setIsSaving(true);
    setMsg(null);
    try {
      const res = await fetch(getApiUrl(`/bot/params?k_breakout=${kVal}&kelly_fraction=${kellyVal}`), {
        method: 'POST'
      });
      if (res.ok) {
        setMsg("✅ 퀀트 파라미터가 실시간 데몬에 반영되었습니다.");
        setTimeout(() => {
          onClose();
        }, 1200);
      } else {
        setMsg("❌ 파라미터 적용 실패");
      }
    } catch (e) {
      setMsg(`❌ 통신 오류: ${e}`);
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-md p-4 animate-fadeIn">
      <div className="bento-card max-w-lg w-full p-6 border-indigo-500/30">
        <div className="flex items-center justify-between pb-3 border-b border-white/10">
          <div className="flex items-center gap-2 text-indigo-400">
            <Sliders className="w-5 h-5" />
            <h3 className="text-base font-bold text-white">런타임 퀀트 파라미터 정밀 설정</h3>
          </div>
          <button onClick={onClose} className="p-1 rounded-lg text-slate-400 hover:text-white">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="mt-5 space-y-5 text-xs text-slate-300">
          {/* k_breakout */}
          <div className="space-y-2">
            <div className="flex justify-between font-medium">
              <span>ATR 변동성 돌파 계수 (k)</span>
              <span className="font-mono font-bold text-blue-400 text-sm">{kVal.toFixed(2)}</span>
            </div>
            <input
              type="range"
              min="0.3"
              max="0.8"
              step="0.05"
              value={kVal}
              onChange={(e) => setKVal(parseFloat(e.target.value))}
              className="w-full accent-blue-500 h-2 bg-slate-800 rounded-lg cursor-pointer"
            />
            <p className="text-[11px] text-slate-500">
              낮을수록(0.3~0.4) 빠른 진입, 높을수록(0.6~0.7) 강력한 추세 확인 후 진입
            </p>
          </div>

          {/* Kelly Fraction */}
          <div className="space-y-2">
            <div className="flex justify-between font-medium">
              <span>프랙셔널 켈리 자산 배분 비중 (Kelly Fraction)</span>
              <span className="font-mono font-bold text-indigo-400 text-sm">{Math.round(kellyVal * 100)}%</span>
            </div>
            <input
              type="range"
              min="0.05"
              max="0.4"
              step="0.05"
              value={kellyVal}
              onChange={(e) => setKellyVal(parseFloat(e.target.value))}
              className="w-full accent-indigo-500 h-2 bg-slate-800 rounded-lg cursor-pointer"
            />
            <p className="text-[11px] text-slate-500">
              1회 주문 시 총 자산 대비 배정 비중 한도 (권장: 15%~25%)
            </p>
          </div>

          {msg && (
            <div className="p-3 rounded-lg bg-blue-950/60 border border-blue-800/40 text-blue-300 font-semibold text-center">
              {msg}
            </div>
          )}
        </div>

        <div className="mt-6 flex justify-end gap-2.5">
          <button
            onClick={onClose}
            className="px-4 py-2 text-xs font-semibold rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300"
          >
            취소
          </button>
          <button
            onClick={handleSave}
            disabled={isSaving}
            className="px-5 py-2 text-xs font-bold rounded-lg bg-blue-600 hover:bg-blue-500 text-white flex items-center gap-1.5"
          >
            <Check className="w-4 h-4" />
            {isSaving ? '적용 중...' : '실시간 적용'}
          </button>
        </div>
      </div>
    </div>
  );
};
