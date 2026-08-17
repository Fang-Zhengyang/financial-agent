Unicode true
!include "MUI2.nsh"

;------------------------------------------------------------
; 金融Agent Windows 安装程序
; 构建: makensis financialagent.nsi  (NSIS 3.x, UTF-8 BOM 编码)
; 打包内容来自同目录 package\ 下的预构建产物:
;   package\runtime\    内置 Python 3.11 运行时 + 全部依赖 (pip 安装)
;   package\finagent\   项目源码包
;   package\tests\      测试套件
;   package\README.md / requirements.txt / 两个 .bat / license.txt
;------------------------------------------------------------

Name "金融Agent"
OutFile "..\dist\FinancialAgent-Setup.exe"
InstallDir "C:\FinancialAgent"
InstallDirRegKey HKCU "Software\方块编程公司\金融Agent" "InstallDir"
RequestExecutionLevel user
SetCompressor zlib
BrandingText "方块编程公司"

!define PRODUCT_NAME "金融Agent"
!define PRODUCT_VERSION "1.0"
!define PRODUCT_PUBLISHER "方块编程公司"
!define UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\金融Agent"
!define APP_REG_KEY "Software\方块编程公司\金融Agent"

;------ 版本信息 (文件属性) ------
VIProductVersion "1.0.0.0"
VIAddVersionKey "ProductName" "金融Agent"
VIAddVersionKey "CompanyName" "方块编程公司"
VIAddVersionKey "FileDescription" "金融Agent 安装程序"
VIAddVersionKey "FileVersion" "1.0.0.0"
VIAddVersionKey "ProductVersion" "1.0"
VIAddVersionKey "LegalCopyright" "(c) 2026 方块编程公司"

;------ MUI2 配置 ------
!define MUI_ABORTWARNING
!define MUI_ICON "${NSISDIR}\Contrib\Graphics\Icons\modern-install.ico"
!define MUI_UNICON "${NSISDIR}\Contrib\Graphics\Icons\modern-uninstall.ico"

!define MUI_WELCOMEPAGE_TITLE "欢迎安装 $(^NameDA) ${PRODUCT_VERSION}"
!define MUI_WELCOMEPAGE_TEXT "本向导将引导您安装 金融Agent。$\r$\n$\r$\n金融Agent 内置完整 Python 运行时，目标电脑无需安装 WSL 或 Python，安装后双击即可使用。$\r$\n$\r$\n点击「下一步」继续。"

!define MUI_LICENSEPAGE_CHECKBOX
!define MUI_FINISHPAGE_TITLE "安装完成"
!define MUI_FINISHPAGE_TEXT "金融Agent 安装完成。$\r$\n$\r$\n使用步骤：$\r$\n1. 双击桌面「配置Key」快捷方式，填入 DeepSeek API Key$\r$\n2. 双击桌面「金融Agent」快捷方式启动 Web 界面$\r$\n$\r$\n浏览器将自动打开 http://127.0.0.1:8081"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "package\license.txt"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "SimpChinese"

;------ 主安装段 ------
Section "核心程序（必装）" SEC_MAIN
  SectionIn RO
  SetShellVarContext current
  SetOutPath "$INSTDIR"
  File /r "package\runtime"
  File /r "package\finagent"
  File /r "package\tests"
  File "package\README.md"
  File "package\requirements.txt"
  File "package\金融Agent-Web.bat"
  File "package\配置Key.bat"

  ; 运行时数据目录
  CreateDirectory "$INSTDIR\output"
  CreateDirectory "$INSTDIR\memory"
  CreateDirectory "$INSTDIR\data"

  ; 卸载器
  WriteUninstaller "$INSTDIR\uninstall.exe"

  ; 注册表: 安装目录 + 添加/删除程序
  WriteRegStr HKCU "${APP_REG_KEY}" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayName" "${PRODUCT_NAME}"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayVersion" "${PRODUCT_VERSION}"
  WriteRegStr HKCU "${UNINST_KEY}" "Publisher" "${PRODUCT_PUBLISHER}"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayIcon" "$INSTDIR\uninstall.exe"
  WriteRegStr HKCU "${UNINST_KEY}" "UninstallString" '"$INSTDIR\uninstall.exe"'
  WriteRegStr HKCU "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoRepair" 1

  ; 开始菜单
  CreateDirectory "$SMPROGRAMS\金融Agent"
  CreateShortcut "$SMPROGRAMS\金融Agent\金融Agent.lnk" "$INSTDIR\金融Agent-Web.bat"
  CreateShortcut "$SMPROGRAMS\金融Agent\配置Key.lnk" "$INSTDIR\配置Key.bat"
  CreateShortcut "$SMPROGRAMS\金融Agent\卸载金融Agent.lnk" "$INSTDIR\uninstall.exe"
SectionEnd

;------ 可选组件: 桌面快捷方式 (默认勾选) ------
Section "创建桌面快捷方式" SEC_SHORTCUT
  SetShellVarContext current
  CreateShortcut "$DESKTOP\金融Agent.lnk" "$INSTDIR\金融Agent-Web.bat"
  CreateShortcut "$DESKTOP\配置Key.lnk" "$INSTDIR\配置Key.bat"
SectionEnd

;------ 卸载段 ------
Section "Uninstall"
  SetShellVarContext current
  Delete "$DESKTOP\金融Agent.lnk"
  Delete "$DESKTOP\配置Key.lnk"
  RMDir /r "$SMPROGRAMS\金融Agent"
  DeleteRegKey HKCU "${UNINST_KEY}"
  DeleteRegKey HKCU "${APP_REG_KEY}"
  RMDir /r "$INSTDIR"
SectionEnd

;------ 组件描述 (必须放在所有 Section 之后) ------
!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
  !insertmacro MUI_DESCRIPTION_TEXT ${SEC_MAIN} "安装核心程序：内置 Python 运行时、Web 服务与命令行工具（必装）"
  !insertmacro MUI_DESCRIPTION_TEXT ${SEC_SHORTCUT} "在桌面创建「金融Agent」（启动 Web）与「配置Key」（设置 API Key）快捷方式"
!insertmacro MUI_FUNCTION_DESCRIPTION_END
