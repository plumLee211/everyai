#!/bin/bash
# macOS 테스트 설치 스크립트. GitHub Release에서 받은 barrierfree-macos.zip.part-* 조각을 합쳐 풀고,
# 서명되지 않은 앱을 열 수 있게 격리 속성(quarantine)을 제거한다.
#
# 사용: 조각과 이 스크립트를 같은 폴더에 받은 뒤, 터미널에서 그 폴더로 가서
#   bash install-macos.sh
# 결과: 같은 폴더에 barrierfree.app이 만들어지고 실행된다.
set -euo pipefail

cd "$(dirname "$0")"

parts=(barrierfree-macos.zip.part-*)
if [ ! -e "${parts[0]}" ]; then
  echo "오류: barrierfree-macos.zip.part-* 파일이 이 폴더에 없습니다. 조각을 모두 이 스크립트와 같은 폴더에 받아 주세요." >&2
  exit 1
fi

if [ -e barrierfree.app ]; then
  echo "오류: barrierfree.app이 이미 있습니다. 지우거나 다른 폴더에서 다시 실행해 주세요." >&2
  exit 1
fi

echo "조각 ${#parts[@]}개를 합쳐 압축을 풉니다(약 3GB, 몇 분 걸릴 수 있습니다)..."
# 합친 zip을 따로 만들지 않고 바로 풀어서 디스크를 아낀다. 조각 이름 순서가 곧 이어 붙이는 순서다.
cat "${parts[@]}" | ditto -x -k - .

if [ ! -d barrierfree.app ]; then
  echo "오류: 압축을 풀었지만 barrierfree.app이 없습니다. 조각이 모두 받아졌는지 확인해 주세요." >&2
  exit 1
fi

# 서명·공증하지 않은 앱은 내려받은 파일의 격리 속성 때문에 "손상되었다"며 막힌다.
xattr -dr com.apple.quarantine barrierfree.app

echo "완료: $(pwd)/barrierfree.app"
echo "조각 파일(barrierfree-macos.zip.part-*)은 지워도 됩니다."
if [ -z "${NO_OPEN:-}" ]; then
  open barrierfree.app
fi
