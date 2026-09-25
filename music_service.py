"""Small adapter around the unofficial QQMusicApi package."""

import json
import os
import re
from pathlib import Path

from qqmusic_api import Client, Credential
from qqmusic_api.models.login import QRLoginType
from qqmusic_api.modules.login_utils import QRCodeLoginSession
from qqmusic_api.modules.song import SongFileInfo, SongFileType
from qqmusic_api.models.base import Song


CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "player"
CREDENTIAL_FILE = CONFIG_DIR / "credential.json"
QUALITY_FILE = CONFIG_DIR / "quality.txt"
LYRIC_SETTINGS_FILE = CONFIG_DIR / "lyrics.json"
APPEARANCE_FILE = CONFIG_DIR / "appearance.json"
HISTORY_LIMIT = 500
QUALITY = {
    "标准": SongFileType.MP3_128,
    "高品质": SongFileType.MP3_320,
    "无损": SongFileType.FLAC,
}


def load_quality():
    try:
        quality = QUALITY_FILE.read_text().strip()
    except FileNotFoundError:
        return "自动"
    return quality if quality in ("自动", *QUALITY) else "自动"


def save_quality(quality):
    CONFIG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    QUALITY_FILE.write_text(quality)


def load_lyric_settings():
    try:
        settings = json.loads(LYRIC_SETTINGS_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return 31, "theme"
    size = settings.get("size", 31) if isinstance(settings, dict) else 31
    color = settings.get("color", "theme") if isinstance(settings, dict) else "theme"
    return (
        size if isinstance(size, int) and 20 <= size <= 48 else 31,
        color if color == "theme" or isinstance(color, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", color) else "theme",
    )


def save_lyric_settings(size, color):
    CONFIG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    LYRIC_SETTINGS_FILE.write_text(json.dumps({"size": size, "color": color}))


def load_appearance():
    try:
        settings = json.loads(APPEARANCE_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return "dark", "#fa5268"
    if not isinstance(settings, dict):
        return "dark", "#fa5268"
    mode, color = settings.get("mode"), settings.get("color")
    return (
        mode if mode in ("dark", "light") else "dark",
        color if isinstance(color, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", color) else "#fa5268",
    )


def save_appearance(mode, color):
    CONFIG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    APPEARANCE_FILE.write_text(json.dumps({"mode": mode, "color": color}))


def history_file(musicid):
    return CONFIG_DIR / f"history-{int(musicid)}.json"


def load_history(musicid):
    try:
        entries = json.loads(history_file(musicid).read_text())
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(entries, list):
        return []
    songs = []
    for entry in entries[:HISTORY_LIMIT]:
        try:
            songs.append(Song.model_validate(entry))
        except (TypeError, ValueError):
            continue
    return songs


def save_history(musicid, songs):
    CONFIG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(CONFIG_DIR, 0o700)
    target = history_file(musicid)
    temporary = target.with_suffix(".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.chmod(temporary, 0o600)
    with os.fdopen(descriptor, "w") as file:
        json.dump([song.model_dump(mode="json") for song in songs[:HISTORY_LIMIT]], file, ensure_ascii=False)
    os.replace(temporary, target)


def load_credential():
    try:
        return Credential.model_validate_json(CREDENTIAL_FILE.read_text())
    except (OSError, ValueError):
        return None


def save_credential(credential):
    CONFIG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(CONFIG_DIR, 0o700)
    temporary = CREDENTIAL_FILE.with_suffix(".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.chmod(temporary, 0o600)
    with os.fdopen(descriptor, "w") as file:
        file.write(credential.model_dump_json(by_alias=True))
    os.replace(temporary, CREDENTIAL_FILE)


def forget_credential():
    CREDENTIAL_FILE.unlink(missing_ok=True)


async def login(qr_ready):
    async with Client() as client:
        session = QRCodeLoginSession(client.login, QRLoginType.QQ)
        qr = await session.get_qrcode()
        qr_ready(qr.data)
        credential = await session.wait_qrcode_login()
    save_credential(credential)
    return credential


async def _client():
    credential = load_credential()
    if credential is None:
        raise RuntimeError("请先扫码登录")
    client = Client(credential=credential)
    if credential.is_expired() and credential.refresh_key:
        async with client:
            credential = await client.login.refresh_credential(credential)
        save_credential(credential)
        client = Client(credential=credential)
    return client, credential


async def account_info():
    client, credential = await _client()
    async with client:
        return credential, await client.user.get_vip_info()


async def playlists():
    client, credential = await _client()
    async with client:
        result = await client.user.get_created_songlist(credential.musicid)
        return result.playlists


async def liked(page=1, size=50):
    client, credential = await _client()
    if not credential.encrypt_uin:
        raise RuntimeError("登录信息缺少加密账号 ID，无法读取「我喜欢」；请重新扫码登录")
    async with client:
        request = client.user.get_fav_song(credential.encrypt_uin, page=page, num=size)
        return list(request.items_extractor(await request))


async def playlist_songs(playlist_id, page=1, size=50):
    client, _ = await _client()
    async with client:
        result = await client.songlist.get_detail(playlist_id, page=page, num=size)
        return result.songs


async def search(query, page=1, size=50):
    client, _ = await _client()
    async with client:
        request = client.search.search_by_type(query, page=page, num=size, highlight=False)
        return list(request.items_extractor(await request))


async def stream_url(song, quality):
    client, _ = await _client()
    async with client:
        dispatch = await client.song.get_cdn_dispatch()
        file_info = SongFileInfo(mid=song.mid, media_mid=song.file.media_mid or None, song_type=song.type)
        urls = await client.song.get_song_urls([file_info], file_type=QUALITY[quality])
        playable = next((item for item in urls.data if item.result == 0 and item.purl), None)
        if playable is None:
            raise RuntimeError("无法播放：歌曲可能需要会员、受地区限制，或所选音质不可用")
        if not dispatch.sip:
            raise RuntimeError("未获取到播放服务器")
        return dispatch.sip[0] + playable.purl


async def auto_stream_url(song):
    try:
        return await stream_url(song, "高品质"), "高品质"
    except RuntimeError:
        return await stream_url(song, "标准"), "标准"


async def lyrics(song):
    client, _ = await _client()
    async with client:
        result = await client.lyric.get_lyric(song.mid, qrc=True)
        return result.lyric
