; 响应消息 · 聊天记录查看器 安装包（Modern UI 2）
Unicode true
!include "MUI2.nsh"
!include "FileFunc.nsh"
!include "LogicLib.nsh"
!include "nsDialogs.nsh"

Name "响应消息"
OutFile "响应消息-Setup.exe"
Icon "icon.ico"
RequestExecutionLevel user
BrandingText "响应消息 v1.0.0"   ; 底部状态栏文字（默认是 "Nullsoft Install System v3.04"）

; 安装包/卸载程序的版本信息（替代默认的 "Nullsoft Install System v3.04"）
VIProductVersion "1.0.0.0"
VIAddVersionKey "ProductName" "响应消息"
VIAddVersionKey "FileDescription" "响应消息 · 聊天记录查看器 安装程序"
VIAddVersionKey "FileVersion" "1.0.0"
VIAddVersionKey "ProductVersion" "1.0.0"
VIAddVersionKey "CompanyName" "WenRidou"
VIAddVersionKey "LegalCopyright" "Copyright (c) 2026 WenRidou"

Var DriveFound
Var DesktopShortcut

Function .onInit
  StrCpy $DesktopShortcut 1   ; 默认勾选"创建桌面快捷方式"
  ; 若上次安装过，复用其目录
  ReadRegStr $0 HKCU "Software\响应消息" "InstallDir"
  ${If} $0 != ""
  ${AndIf} ${FileExists} "$0"
    StrCpy $INSTDIR $0
  ${Else}
    ; 多盘时默认装到非 C 盘：遍历 D-Z 找第一个固定盘（GetDriveType==3）
    StrCpy $0 "DEFGHIJKLMNOPQRSTUVWXYZ"
    StrCpy $DriveFound ""
    ${Do}
      StrLen $1 $0
      ${If} $1 = 0
        ${Break}
      ${EndIf}
      StrCpy $2 $0 1
      StrCpy $0 $0 "" 1
      StrCpy $3 "$2:\"
      System::Call 'kernel32::GetDriveType(t) i("$3") .r4'
      ${If} $4 = 3
        StrCpy $DriveFound $3 2   ; 只取盘符 "D:"，避免拼路径时出现双反斜杠
        ${Break}
      ${EndIf}
    ${Loop}
    ${If} $DriveFound != ""
      StrCpy $INSTDIR "$DriveFound\Programs\ChatRecords"
    ${Else}
      StrCpy $INSTDIR "$LOCALAPPDATA\Programs\ChatRecords"
    ${EndIf}
  ${EndIf}
FunctionEnd

InstallDir "$LOCALAPPDATA\Programs\ChatRecords"

; ---- MUI2 配置 ----
!define MUI_ABORTWARNING
!define MUI_ICON "icon.ico"
!define MUI_UNICON "icon.ico"

!define MUI_WELCOMEPAGE_TITLE "欢迎安装 响应消息"
!define MUI_WELCOMEPAGE_TEXT "本向导将把「响应消息 · 聊天记录查看器」安装到你的电脑。$\r$\n$\r$\n它可以把你从「响应」导出的聊天记录，变成微信风格的界面来查看。$\r$\n$\r$\n点击「下一步」继续。"

!define MUI_DIRECTORYPAGE_TEXT_TOP "安装程序将把程序安装到下面的文件夹中。$\r$\n$\r$\n请安装到有写入权限的文件夹（应用需要写入自己的数据）。"

!define MUI_FINISHPAGE_RUN "$INSTDIR\ChatRecords.exe"
!define MUI_FINISHPAGE_RUN_TEXT "启动 响应消息"
!define MUI_FINISHPAGE_TITLE "安装完成"
!define MUI_FINISHPAGE_TEXT "「响应消息」已安装完成。$\r$\n$\r$\n启动后选择你的聊天记录文件夹，即可开始查看。"

; 自定义"选项"页：创建桌面快捷方式勾选框
Function DesktopPageCreate
  !insertmacro MUI_HEADER_TEXT "选项" "选择要创建的内容"
  nsDialogs::Create 1018
  Pop $0
  ${NSD_CreateCheckbox} 0 0 100% 14u "创建桌面快捷方式"
  Pop $1
  ${NSD_Check} $1
  StrCpy $DesktopShortcut $1
  nsDialogs::Show
FunctionEnd

Function DesktopPageLeave
  ${NSD_GetState} $DesktopShortcut $0
  StrCpy $DesktopShortcut $0   ; 1=勾选，0=未勾选
FunctionEnd

; ---- 页面 ----
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY

; 自定义"选项"页：桌面快捷方式勾选框（默认勾选）
Page custom DesktopPageCreate DesktopPageLeave

!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

; ---- 语言 ----
!insertmacro MUI_LANGUAGE "SimpChinese"

Section "安装"
  SetOutPath "$INSTDIR"
  File "ChatRecords.exe"
  File "ChatRecords.html"
  File "amrnb.js"

  WriteUninstaller "$INSTDIR\uninstall.exe"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\响应消息" "DisplayName" "响应消息"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\响应消息" "UninstallString" '"$INSTDIR\uninstall.exe"'
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\响应消息" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\响应消息" "Publisher" "响应消息"
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\响应消息" "NoModify" 1
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\响应消息" "NoRepair" 1

  CreateDirectory "$SMPROGRAMS\响应消息"
  CreateShortcut "$SMPROGRAMS\响应消息\响应消息.lnk" "$INSTDIR\ChatRecords.exe"
  CreateShortcut "$SMPROGRAMS\响应消息\卸载响应消息.lnk" "$INSTDIR\uninstall.exe"
  ${If} $DesktopShortcut <> 0
    CreateShortcut "$DESKTOP\响应消息.lnk" "$INSTDIR\ChatRecords.exe"
  ${EndIf}
SectionEnd

Section "Uninstall"
  SetOutPath "$TEMP"   ; 先离开安装目录，否则 RMDir 会因进程占用而失败
  Delete "$INSTDIR\ChatRecords.exe"
  Delete "$INSTDIR\ChatRecords.html"
  Delete "$INSTDIR\amrnb.js"
  Delete "$INSTDIR\uninstall.exe"
  RMDir "$INSTDIR"
  Delete "$SMPROGRAMS\响应消息\响应消息.lnk"
  Delete "$SMPROGRAMS\响应消息\卸载响应消息.lnk"
  RMDir "$SMPROGRAMS\响应消息"
  Delete "$DESKTOP\响应消息.lnk"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\响应消息"
  DeleteRegKey HKCU "Software\响应消息"
SectionEnd
