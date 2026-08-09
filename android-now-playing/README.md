# Android 网易云正在播放上报器

这个轻量 Android App 读取网易云音乐的活动媒体会话，并把当前歌曲快照通过 HTTPS 上报给本仓库的 MCP 服务。

## 隐私边界

- 仅处理包名 `com.netease.cloudmusic`。
- 不上传普通通知正文、联系人、短信或其他 App 数据。
- 不需要网易云 Cookie，也不接触 `MCP_SECRET`。
- `NOW_PLAYING_REPORTER_SECRET` 只允许写入最新播放快照，不能读取 MCP 数据。

## 构建

仓库自带 GitHub Actions 工作流。进入 GitHub 的 Actions 页面，运行 **Build Android now-playing reporter**，完成后下载 `netease-now-playing-reporter-debug` artifact 并解压安装 APK。

本地构建需要 JDK 17、Android SDK 35 和 Gradle 8.10.2：

```bash
cd android-now-playing
gradle :app:assembleDebug
```

## Real-time reliability

- Version 0.2 listens for active media-session changes and also refreshes every 5 seconds.
- The connection-test button verifies HTTPS credentials without replacing the latest song state.
- On phones with aggressive battery management, allow this app to auto-start and run in the background, and set battery usage to unrestricted.
- Keep NetEase Cloud Music playback notifications enabled. The app never reads notifications from other packages.

## 手机配置

1. 在 Zeabur 为 MCP 服务新增 `NOW_PLAYING_REPORTER_SECRET`，使用独立的至少 32 字符随机值，并重新部署。
2. 安装 APK。
3. 填写 MCP 服务的基础地址，例如 `https://example.zeabur.app`，不要附加 `/mcp/...`。
4. 填写 `NOW_PLAYING_REPORTER_SECRET` 并保存。
5. 点击“打开通知使用权设置”，只授权给本 App。
6. 点击“测试上报连接”。
7. 打开网易云音乐并播放歌曲。

## 说明

媒体会话状态变化时会立即上报；播放期间每 30 秒发送一次心跳。云端会根据位置、速度和接收时间估算当前进度。超过 90 秒没有新状态时，MCP 会报告手机离线。
