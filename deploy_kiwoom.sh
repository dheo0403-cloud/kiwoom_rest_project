#!/bin/bash
set -e

echo "============================================================"
echo "🚀 [Kiwoom Quant Bot] GitHub Actions 원클릭 자동 배포 실행기 (Bash)"
echo "============================================================"

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

echo "1. 변경 파일 스테이징 중..."
git add -A

echo "2. 자동 배포 커밋 생성 중..."
COMMIT_MSG="fix: 런타임 키움 TR 멀티레코드 전달 및 보유종목 동기화 오류 수정"
git commit -m "$COMMIT_MSG" || echo "이미 커밋된 상태이거나 변경사항이 없습니다."

echo "3. GitHub 원격 저장소 푸시 중 (GitHub Actions 트리거)..."
git push origin fix/rendering-optimization-and-safety-fixes

echo "============================================================"
echo "🎉 GitHub Actions 자동 배포 파이프라인이 정상적으로 가동되었습니다!"
echo "🌐 대시보드 확인: https://mcmportal.koreacentral.cloudapp.azure.com/kiwoom"
echo "============================================================"
