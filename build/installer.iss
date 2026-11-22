; Inno Setup 스크립트. PyInstaller 결과(dist\barrierfree)를 설치 프로그램으로 묶는다.
;   iscc /DAppVersion=0.1.0 build\installer.iss
; 결과: build\installer-out\barrierfree-setup.exe (+ 용량이 크면 barrierfree-setup-1.bin, -2.bin ...)
; 모델이 들어가면 3GB가 넘어서 GitHub Release 첨부 한도(파일당 2GiB)에 맞게 1.9GB씩 나눈다.
; 조각 파일은 setup.exe와 같은 폴더에 있어야 하므로 항상 함께 배포한다.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
; 같은 앱을 덮어 설치·제거하기 위한 고정 ID다. 바꾸면 다른 앱으로 취급된다.
AppId={{6F3B8E52-4C1D-4E7A-9B0A-2D5C7A1E8F94}
AppName=배리어프리 영상 점검
AppVersion={#AppVersion}
AppPublisher=EveryAI
DefaultDirName={autopf}\barrierfree
DefaultGroupName=배리어프리 영상 점검
UninstallDisplayIcon={app}\barrierfree.exe
OutputDir=installer-out
OutputBaseFilename=barrierfree-setup
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; 관리자 권한 없이 사용자 폴더에 설치할 수 있다(기관 PC 테스트용). 데이터는 사용자 AppData에 저장된다.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
; 모델은 압축이 거의 안 된다. 속도를 위해 빠른 압축을 쓰고 조각으로 나눈다.
Compression=lzma2/fast
SolidCompression=no
DiskSpanning=yes
DiskSliceSize=1900000000
WizardStyle=modern

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Tasks]
Name: "desktopicon"; Description: "바탕 화면에 바로 가기 만들기"; Flags: unchecked

[Files]
Source: "..\dist\barrierfree\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\배리어프리 영상 점검"; Filename: "{app}\barrierfree.exe"
Name: "{group}\배리어프리 영상 점검 제거"; Filename: "{uninstallexe}"
Name: "{autodesktop}\배리어프리 영상 점검"; Filename: "{app}\barrierfree.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\barrierfree.exe"; Description: "설치 후 바로 실행"; Flags: nowait postinstall skipifsilent

; 제거해도 사용자 데이터(분석 결과 DB 등, AppData)는 지우지 않는다.
