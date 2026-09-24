# Timekeeper

轻量的原生 Linux QQ 音乐客户端。支持 QQ 扫码登录、「我喜欢」、自建歌单、播放历史（最多 500 首）、歌曲搜索、在线播放、上一首/下一首、进度跳转、设置页音质选择（默认自动）和独立的播放页。点击底栏封面可打开或关闭播放页。播放页优先使用 QRC 逐字歌词高光；无逐字数据时按 LRC 逐行跟随。账号切换位于设置页，歌曲列表接近底部时自动加载下一页。界面参考 Apple Music 的布局与色彩，也参考 [Lyrune](https://github.com/amtoaer/lyrune) 和 [YAQMC](https://github.com/YAQMC/YAQMC) 的功能组织；接口由非官方 [QQMusicApi](https://github.com/L-1124/QQMusicApi) 提供。

## 运行

需要 Python 3.12、GTK4、GStreamer 1.0（含网络和 MP3/FLAC 解码插件）以及 Python GObject 绑定。在 Debian/Ubuntu 上可先安装 `python3-gi gir1.2-gtk-4.0 gir1.2-gstreamer-1.0 gstreamer1.0-plugins-base gstreamer1.0-plugins-good python3-venv`。

```bash
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py
```

首次启动点击“扫码登录”。为保留旧版登录状态，账号凭证仍保存在 `$XDG_CONFIG_HOME/player/credential.json`（默认 `~/.config/player/credential.json`）；播放历史按账号保存在同一目录，最多 500 首不同歌曲，重播会移到最前。设置页可调整歌词字号和逐字高光颜色。播放链接临时获取，不下载或长期缓存歌曲。成功切换账号后，新凭证会覆盖旧凭证。

## 构建 deb

在 Ubuntu 24.04 amd64 上安装 `python3.12-venv` 和 `dpkg-dev`，然后运行 `./build-deb.sh`。脚本会创建临时虚拟环境、安装 Python 依赖，并生成 `dist/timekeeper_0.1.0_amd64.deb`。GTK、GStreamer 等由系统包提供，不包含在 deb 内。安装时系统仍需下载这些依赖。

本项目依赖的 `qqmusic-api-python` 采用 GPLv3+，发布衍生程序时需要遵守其许可证。接口为非官方接入，QQ 音乐接口变化、会员资格和地区版权限制都可能影响播放。
