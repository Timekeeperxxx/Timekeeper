import asyncio
import json
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from qqmusic_api import Credential

import music_service as music
from app import Player, lyric_scroll_target, title_scroll_offset
from gi.repository import GLib, Gst
from lyrics_sync import Line


class ServiceTest(unittest.TestCase):
    def test_lyric_manual_scroll_stays_active_between_gestures(self):
        classes = {"manual-scrolling"}
        player = SimpleNamespace(
            last_manual_scroll=1_000_000,
            manual_scroll_timer=1,
            lyric_box=SimpleNamespace(remove_css_class=classes.discard),
        )
        with patch.object(GLib, "get_monotonic_time", return_value=2_500_000):
            self.assertTrue(Player.finish_manual_scroll(player))
        with patch.object(GLib, "get_monotonic_time", return_value=3_100_000):
            self.assertFalse(Player.finish_manual_scroll(player))
        self.assertNotIn("manual-scrolling", classes)

    def test_title_scroll_waits_at_both_ends_and_returns_smoothly(self):
        self.assertEqual(title_scroll_offset(0.5, 84), 0)
        self.assertAlmostEqual(title_scroll_offset(2.2, 84), 42)
        self.assertEqual(title_scroll_offset(3.7, 84), 84)
        self.assertAlmostEqual(title_scroll_offset(5.4, 84), 42)
        self.assertEqual(title_scroll_offset(20, 0), 0)

    def test_active_lyric_anchors_above_center(self):
        self.assertEqual(lyric_scroll_target(250, 40, 400, 1000), 110)
        self.assertEqual(lyric_scroll_target(160, 40, 400, 1000), 20)

    def test_builtin_liked_playlist_is_hidden_without_hiding_same_named_custom_list(self):
        shown = []
        player = SimpleNamespace(
            playlist_box=SimpleNamespace(append=lambda button: shown.append(button)),
            clear_box=lambda *_: None,
            sidebar_button=lambda label, callback, icon: SimpleNamespace(label=label, set_tooltip_text=lambda *_: None),
        )
        playlists = [SimpleNamespace(title="我喜欢", dirid=201), SimpleNamespace(title="我喜欢", dirid=2)]
        Player.set_playlists(player, playlists)
        self.assertEqual(len(shown), 1)

    def test_history_keeps_latest_500_unique_songs(self):
        saved = []
        history = [SimpleNamespace(mid=str(number)) for number in range(500)]
        player = SimpleNamespace(
            history_musicid=123, history=history, source=("liked", None),
            set_status=lambda *_: None,
        )
        replayed = SimpleNamespace(mid="42")
        with patch.object(music, "save_history", lambda owner, songs: saved.append((owner, songs.copy()))):
            Player.record_history(player, replayed)
            Player.record_history(player, SimpleNamespace(mid="new"))
        self.assertEqual([song.mid for song in player.history[:3]], ["new", "42", "0"])
        self.assertEqual(len(player.history), 500)
        self.assertEqual(saved[-1][0], 123)

    def test_history_file_is_private_and_capped(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "player"
            songs = [SimpleNamespace(model_dump=lambda mode, n=n: {"mid": str(n)}) for n in range(501)]
            with patch.object(music, "CONFIG_DIR", folder), patch.object(music.Song, "model_validate", side_effect=lambda data: SimpleNamespace(**data)):
                music.save_history(123, songs)
                path = music.history_file(123)
                self.assertEqual(len(json.loads(path.read_text())), 500)
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                self.assertEqual(len(music.load_history(123)), 500)
                self.assertNotEqual(path, music.history_file(456))

    def test_lyric_settings_persist(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "player"
            with patch.object(music, "CONFIG_DIR", folder), patch.object(music, "LYRIC_SETTINGS_FILE", folder / "lyrics.json"):
                self.assertEqual(music.load_lyric_settings(), (31, "#ffffff"))
                music.save_lyric_settings(37, "#ff6679")
                self.assertEqual(music.load_lyric_settings(), (37, "#ff6679"))

    def test_song_list_prefetches_before_bottom_once(self):
        calls = []
        adjustment = SimpleNamespace(
            get_upper=lambda: 1000, get_value=lambda: 550, get_page_size=lambda: 200,
        )
        player = SimpleNamespace(
            pages=SimpleNamespace(get_visible_child_name=lambda: "library"),
            loading_page=False, has_more=True,
            load_page=lambda reset: calls.append(reset),
        )
        Player.maybe_load_next_page(player, adjustment)
        self.assertEqual(calls, [False])
        player.loading_page = True
        Player.maybe_load_next_page(player, adjustment)
        self.assertEqual(calls, [False])

    def test_cover_uses_one_third_of_available_width(self):
        sizes = []
        widget = SimpleNamespace(set_size_request=lambda width, height: sizes.append((width, height)))
        player = SimpleNamespace(
            player_main=SimpleNamespace(get_width=lambda: 1200, get_height=lambda: 700),
            cover_target_size=240, artwork=widget, hero_cover=widget,
            cover_source=None, cover_resize_timer=None,
        )
        Player.update_player_layout(player)
        self.assertEqual(player.cover_target_size, 400)
        self.assertEqual(sizes, [(400, -1), (400, 400)])

    def test_clicking_lyric_seeks_and_starts_playback(self):
        calls = []
        audio = SimpleNamespace(
            seek_simple=lambda *args: calls.append(("seek", args)) or True,
            set_state=lambda state: calls.append(("state", state)),
        )
        player = SimpleNamespace(
            current_song=object(), audio=audio, lyric_lines=[Line(1234, 2000, "一句", [])],
            lyric_box=SimpleNamespace(remove_css_class=lambda name: calls.append(("class", name))),
            set_playing=lambda value: calls.append(("playing", value)),
            sync_lyrics=lambda position, force: calls.append(("sync", position, force)),
        )
        Player.on_lyric_clicked(player, None, 1, 0, 0, 0)
        self.assertEqual(calls[0][1][2], 1234 * Gst.MSECOND)
        self.assertIn(("state", Gst.State.PLAYING), calls)
        self.assertIn(("sync", 1234, True), calls)

    def test_cover_failure_does_not_interrupt_playback(self):
        picture = SimpleNamespace(set_paintable=lambda *_: None)
        player = SimpleNamespace(cover=picture, hero_cover=picture)
        sizes = []

        def cover_url(size):
            sizes.append(size)
            raise ValueError("unsupported cover size")

        Player.load_cover(player, SimpleNamespace(cover_url=cover_url), 1)
        self.assertEqual(sizes, [1200])

    def test_credential_is_private(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "player"
            credential_file = folder / "credential.json"
            credential = Credential(musicid=123, musickey="Q_H_L_example", encryptUin="encrypted")
            with patch.object(music, "CONFIG_DIR", folder), patch.object(music, "CREDENTIAL_FILE", credential_file):
                music.save_credential(credential)
                self.assertEqual(music.load_credential().musicid, 123)
                self.assertEqual(stat.S_IMODE(credential_file.stat().st_mode), 0o600)
                music.forget_credential()
                self.assertFalse(credential_file.exists())

    def test_stream_url_uses_selected_quality(self):
        requests = []

        class SongApi:
            async def get_cdn_dispatch(self):
                return SimpleNamespace(sip=["https://cdn.example/"])

            async def get_song_urls(self, file_info, file_type):
                requests.append((file_info[0].mid, file_type))
                return SimpleNamespace(data=[SimpleNamespace(result=0, purl="track.mp3")])

        class Client:
            song = SongApi()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

        async def fake_client():
            return Client(), None

        song = SimpleNamespace(mid="abc", type=1, file=SimpleNamespace(media_mid="abc"))
        with patch.object(music, "_client", fake_client):
            url = asyncio.run(music.stream_url(song, "高品质"))
        self.assertEqual(url, "https://cdn.example/track.mp3")
        self.assertEqual(requests, [("abc", music.QUALITY["高品质"])])

    def test_auto_quality_falls_back_to_standard(self):
        requested = []

        async def fake_stream(_song, quality):
            requested.append(quality)
            if quality == "高品质":
                raise RuntimeError("音质不可用")
            return "https://cdn.example/standard.mp3"

        with patch.object(music, "stream_url", fake_stream):
            result = asyncio.run(music.auto_stream_url(object()))
        self.assertEqual(result, ("https://cdn.example/standard.mp3", "标准"))
        self.assertEqual(requested, ["高品质", "标准"])

    def test_quality_setting_defaults_to_auto_and_persists(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "player"
            with patch.object(music, "CONFIG_DIR", folder), patch.object(music, "QUALITY_FILE", folder / "quality.txt"):
                self.assertEqual(music.load_quality(), "自动")
                music.save_quality("无损")
                self.assertEqual(music.load_quality(), "无损")

    def test_sustained_buffering_triggers_quality_downgrade(self):
        calls = []
        audio = SimpleNamespace(
            query_position=lambda _format: (False, 0),
            query_duration=lambda _format: (False, 0),
        )
        player = SimpleNamespace(
            buffering_since=10, quality_downgrading=False, audio=audio,
            downgrade_quality=lambda: calls.append("downgrade"),
        )
        with patch("app.time.monotonic", return_value=14):
            Player.update_progress(player)
        self.assertEqual(calls, ["downgrade"])

    def test_search_fetches_only_one_page(self):
        class Request:
            items_extractor = staticmethod(lambda response: response)

            def __await__(self):
                async def one_page():
                    return ["one", "two"]

                return one_page().__await__()

            async def collect_items(self):
                raise AssertionError("would fetch every page")

        class Client:
            search = SimpleNamespace(search_by_type=lambda *_args, **_kwargs: Request())

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

        async def fake_client():
            return Client(), None

        with patch.object(music, "_client", fake_client):
            self.assertEqual(asyncio.run(music.search("query")), ["one", "two"])


if __name__ == "__main__":
    unittest.main()
