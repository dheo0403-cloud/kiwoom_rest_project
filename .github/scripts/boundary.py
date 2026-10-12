"""보호 영역 검사 (phil boundary.sh 이식): 푸시 범위의 커밋 중 보호 파일을 바꾼 커밋은 메시지에 [operator]가 있어야 한다.

[operator]는 사용자가 그 보호 파일 변경을 명시적으로 승인했을 때만 붙인다 (kiwoom-cycle 스킬 규칙).
실행: python .github/scripts/boundary.py <before_sha> <after_sha>   (before가 0만 있으면 after 커밋 하나만 검사)
"""
import subprocess
import sys

PROTECTED = ("limits.py", "test_limits.py", ".github/")
MARK = "[operator]"


def git(*args) -> str:
    return subprocess.run(("git",) + args, check=True, capture_output=True, text=True, encoding="utf-8").stdout


def main(before: str, after: str) -> int:
    rng = after if set(before) <= {"0"} else f"{before}..{after}"
    commits = git("rev-list", "--no-merges", rng if ".." in rng else f"{rng}~1..{rng}").split()
    bad = []
    for sha in commits:
        msg = git("log", "-1", "--format=%B", sha)
        files = git("diff-tree", "--no-commit-id", "--name-only", "-r", "--relative", sha).split()
        touched = [f for f in files if f.startswith(PROTECTED)]
        if touched and MARK not in msg:
            bad.append(f"{sha[:7]} {msg.splitlines()[0]} → {touched}")
    if bad:
        print("❌ 보호 영역을 [operator] 표시 없이 변경한 커밋:\n" + "\n".join(bad))
        return 1
    print(f"✅ 보호 영역 검사 통과 ({len(commits)}개 커밋)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
