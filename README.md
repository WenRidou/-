# 响应消息 · 聊天记录查看器

把「响应」软件导出的聊天记录 HTML，变成**微信风格**的界面来查看。图片/语音可在线查看，也可下载到本地离线查看。

## 功能

- 微信风格界面：左侧会话列表 + 右侧气泡对话（自己的消息在右侧）
- **自动识别账号主人**，无需手动配置
- 图片/语音处理方式，解析时可选：
  - **在线**：图片和语音联网查看/播放，快、不占本地空间，但受网速影响
  - **下载到本地**：下载后可离线查看/播放；一次下载一劳永逸，需足够磁盘空间（会先检测）
- 语音（AMR）内置**浏览器端解码器**（amrnb.js），在线也能直接播放
- 通讯录、收藏夹、置顶、拖拽排序、搜索、按日期/图片/便签/群成员查找
- 收藏/置顶/排序保存在应用目录，重装不丢

## 下载安装

从 [Releases](https://github.com/WenRidou/XiangyingChatRecordsViewer/releases) 下载最新版本：

- **安装版** `版本号-Setup.exe`：双击安装，自动建开始菜单/桌面快捷方式、带卸载。
  - 安装到用户目录（多磁盘时默认装到**非 C 盘**）
  - **自动检测并安装 WebView2 Runtime**（应用必需）
  - 选项页可勾选是否创建桌面快捷方式
- **免安装版** `版本号-win.zip`：解压后直接双击 `ChatRecords.exe` 即可用，不写注册表，可随身携带。

## 使用

1. 打开应用
2. 首次运行点「选择聊天记录所在文件夹」，选择你从「响应」导出的聊天记录 html 所在文件夹
3. 选择图片/语音处理方式：**在线** 或 **下载到本地**
4. 自动解析后即可查看

之后想换记录文件夹：点侧栏右上角齿轮 →「更换文件夹」→ 重新生成。

## 从源码运行

```bash
pip install pywebview pythonnet imageio-ffmpeg
python wechat_viewer.py
```

## 打包

```bash
# 1) 打包 exe（pyinstaller）
pyinstaller --onefile --windowed --name ChatRecords \
  --icon icon/icon.ico \
  --collect-all webview --collect-all pythonnet --collect-all clr_loader \
  wechat_viewer.py

# 2) 打安装包（NSIS，需安装 NSIS，脚本见 setup.nsi）
makensis setup.nsi
```

## 常见问题

- **缺少 WebView2 Runtime？** 应用会自动下载安装；若失败会弹窗提示手动安装。
- **语音播不了？** 内置 AMR 解码器，在线也能播；个别云端语音链接失效（403）则无法播放。
- **收藏/置顶存哪？** 应用目录下的 `wxchat_data.json`，随应用目录一起拷走即可。

## 说明

- 本项目仅供学习交流使用；聊天记录由「响应」软件导出。
- `build_wechat.py` 为可选脚本：给不想用安装包、只想在浏览器里看的人，手动生成 `data.js` 用。
