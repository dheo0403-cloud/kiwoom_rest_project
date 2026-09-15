@echo off
chcp 65001 > nul
echo ============================================================
echo 🚀 [Kiwoom Quant Bot] GitHub Actions 원클릭 자동 배포 실행기
echo ============================================================

cd /d "C:\Users\MZC01-MICHAEL\Desktop\허동진\프로젝트\kiwoom_rest_project"

echo 1. 변경 파일 스테이징 중...
git add -A

echo 2. 자동 배포 커밋 생성 중...
set COMMIT_MSG=fix(sync): remove 10-minute frontend display lock and enable instantaneous realtime state sync
git commit -m "%COMMIT_MSG%"

echo 3. GitHub 원격 저장소 푸시 중 (GitHub Actions 트리거)...
git push origin fix/rendering-optimization-and-safety-fixes

echo ============================================================
echo 🎉 GitHub Actions 자동 배포 파이프라인이 가동되었습니다!
echo 🌐 대시보드 확인: https://mcmportal.koreacentral.cloudapp.azure.com/kiwoom
echo ============================================================
pause
