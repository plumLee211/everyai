테스트용 설치 파일이다. 모델이 들어 있어 첫 실행에 인터넷이 필요 없고, GPU(CUDA)는 쓰지 않고 CPU로 실행한다. 코드 서명이 없다.
Windows와 macOS 파일이 이 Release에 함께 올라온다(빌드가 끝난 쪽부터 보인다). 자기 OS 파일만 받는다.

## Windows

`barrierfree-setup.exe`와 `barrierfree-setup-*.bin`을 **모두 같은 폴더에** 받은 뒤 `barrierfree-setup.exe`를 실행한다.
SmartScreen 경고가 뜨면 "추가 정보" → "실행"을 누른다. 제거해도 분석 결과(사용자 AppData)는 남는다.

## macOS (Apple Silicon만)

`barrierfree-macos.zip.part-*`(조각 전부)와 `install-macos.sh`를 **모두 같은 폴더에** 받은 뒤, 터미널에서 그 폴더로 가서 실행한다.

    bash install-macos.sh

조각을 합쳐 풀고, 같은 폴더에 `barrierfree.app`을 만든 뒤 실행한다(약 3GB, 몇 분 걸린다). 끝나면 조각 파일은 지워도 된다.
Apple 개발자 서명·공증이 없는 앱이라 스크립트가 격리 속성(`xattr`)을 제거해 준다. 이 단계 없이 `.app`을 열면 "손상되었거나 열 수 없다"로 막힌다.
그래도 막히면 시스템 설정 → 개인정보 보호 및 보안 → "그래도 열기"를 누른다. Intel Mac은 지원하지 않는다.

스크립트 없이 직접 하려면:

    cat barrierfree-macos.zip.part-* | ditto -x -k - .
    xattr -dr com.apple.quarantine barrierfree.app
    open barrierfree.app
