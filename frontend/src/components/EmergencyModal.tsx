import React, { useState } from 'react';
import { ShieldAlert, AlertTriangle, X, Check } from 'lucide-react';

interface EmergencyModalProps {
  isOpen: boolean;
  onClose: () => void;
  onConfirmKillSwitch: () => void;
  isTriggering: boolean;
}

export const EmergencyModal: React.FC<EmergencyModalProps> = ({
  isOpen,
  onClose,
  onConfirmKillSwitch,
  isTriggering
}) => {
  const [sliderValue, setSliderValue] = useState<number>(0);

  if (!isOpen) return null;

  const isConfirmed = sliderValue >= 95;

  const handleSliderChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const val = parseInt(e.target.value, 10);
    setSliderValue(val);
    if (val >= 95) {
      onConfirmKillSwitch();
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-md p-4 animate-fadeIn">
      <div className="bento-card max-w-md w-full p-6 border-rose-500/40 glow-red">
        {/* Modal Header */}
        <div className="flex items-center justify-between pb-3 border-b border-rose-500/20">
          <div className="flex items-center gap-2 text-rose-400">
            <ShieldAlert className="w-6 h-6 animate-pulse" />
            <h3 className="text-base font-black tracking-tight text-white">
              🚨 긴급 비상 킬스위치 (Kill-Switch) 발동
            </h3>
          </div>
          <button
            onClick={onClose}
            className="p-1 rounded-lg text-slate-400 hover:text-white hover:bg-slate-800 transition"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Warning Content */}
        <div className="mt-4 space-y-3 text-xs leading-relaxed text-slate-300">
          <div className="p-3 rounded-lg bg-rose-950/40 border border-rose-800/40 text-rose-300 flex items-start gap-2.5">
            <AlertTriangle className="w-5 h-5 shrink-0 mt-0.5 text-rose-400" />
            <div>
              <strong className="block font-bold text-sm text-white mb-1">
                모든 보유 주식이 즉시 시장가로 전량 매도됩니다.
              </strong>
              진행 중인 모든 매수 주문이 자동 취소되며, 데몬은 신규 매수 차단 상태(Emergency Lock)로 전이됩니다.
            </div>
          </div>

          <p className="text-slate-400">
            실수 클릭을 방지하기 위해 아래 슬라이더를 오른쪽 끝까지 밀어주세요.
          </p>
        </div>

        {/* Slide to Confirm Slider */}
        <div className="mt-6">
          <div className="relative flex items-center bg-slate-900 border border-slate-700 rounded-xl p-1 h-12 overflow-hidden">
            <div
              className="absolute left-0 top-0 bottom-0 bg-gradient-to-r from-rose-600 to-red-600 opacity-30 transition-all"
              style={{ width: `${sliderValue}%` }}
            />
            <div className="absolute inset-0 flex items-center justify-center pointer-events-none text-xs font-bold text-slate-400">
              {isConfirmed ? '💥 킬스위치 발동 중...' : '밀어서 긴급 전량 청산 ➔'}
            </div>
            <input
              type="range"
              min="0"
              max="100"
              value={sliderValue}
              onChange={handleSliderChange}
              disabled={isTriggering}
              className="w-full h-full opacity-0 cursor-ew-resize z-10"
            />
          </div>
        </div>

        {/* Close Button */}
        <div className="mt-4 flex justify-end">
          <button
            onClick={onClose}
            disabled={isTriggering}
            className="px-4 py-2 text-xs font-semibold rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 transition"
          >
            취소 및 돌아가기
          </button>
        </div>
      </div>
    </div>
  );
};
