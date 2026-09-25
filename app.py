"""A compact native Linux client for a QQ Music account."""

import asyncio
import os
import random
import threading
import time
import urllib.request
from bisect import bisect_right
from concurrent.futures import ThreadPoolExecutor
from collections import OrderedDict
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gst", "1.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gst, Gtk, Pango

import music_service as music
from lyrics_sync import highlight_markup, parse as parse_lyrics


def clock(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def artist(song):
    return "、".join(singer.name for singer in song.singer) or "未知歌手"


LYRIC_ANCHOR = 0.4


def lyric_scroll_target(row_y, row_height, viewport_height, max_scroll):
    return max(0, min(row_y + row_height / 2 - viewport_height * LYRIC_ANCHOR, max_scroll))


def title_scroll_offset(elapsed, overflow):
    if overflow <= 0:
        return 0
    travel = overflow / 42
    phase = elapsed % (2 * travel + 2.4)
    if phase < 1.2:
        return 0
    if phase < 1.2 + travel:
        return (phase - 1.2) * 42
    if phase < 2.4 + travel:
        return overflow
    return overflow - (phase - 2.4 - travel) * 42


class Player(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="io.github.timekeeper.qqmusic")
        self.connect("activate", self.on_activate)
        self.songs = []
        self.queue = []
        self.playlists = []
        self.history = []
        self.history_musicid = None
        self.index = -1
        self.page = 0
        self.loading_page = False
        self.has_more = True
        self.source = ("liked", None)
        self.request_id = 0
        self.play_request_id = 0
        self.current_song = None
        self.pending_history_song = None
        self.audio = None
        self.active_nav = None
        self.cover_pool = ThreadPoolExecutor(max_workers=4)
        self.cover_cache = OrderedDict()
        self.lyric_lines = []
        self.lyric_starts = []
        self.lyric_rows = []
        self.lyric_state = (-1, -1, False)
        self.lyric_scroll_motion = None
        self.lyric_geometry = None
        self.lyric_padding = None
        self.lyric_realign_pending = False
        self.title_scroll_started = GLib.get_monotonic_time()
        self.list_title_scrolls = []
        self.last_manual_scroll = 0
        self.manual_scroll_timer = None
        self.cover_target_size = 240
        self.cover_source = None
        self.cover_resize_timer = None
        self.player_return_page = "library"
        self.auto_quality = False
        self.current_quality = None
        self.buffering_since = None
        self.quality_downgrading = False
        self.pending_seek_ns = None
        self.lyric_size, self.lyric_color = music.load_lyric_settings()
        log_path = os.environ.get("TIMEKEEPER_SCROLL_LOG")
        self.scroll_log = open(log_path, "a", encoding="utf-8", buffering=1) if log_path else None

    def trace_scroll(self, event, **details):
        if self.scroll_log:
            self.scroll_log.write(f"{time.monotonic():.3f} {event} {details}\n")

    def on_activate(self, _app):
        if self.get_active_window():
            self.get_active_window().present()
            return

        Gst.init(None)
        self.audio = Gst.ElementFactory.make("playbin", "music")
        if self.audio is None:
            raise RuntimeError("GStreamer playbin 不可用")
        bus = self.audio.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self.on_audio_message)

        css = Gtk.CssProvider()
        css.load_from_path(str(Path(__file__).with_name("style.css")))
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        self.lyric_css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), self.lyric_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1
        )
        self.apply_lyric_style()

        self.window = Gtk.ApplicationWindow(application=self, title="Timekeeper · QQ 音乐")
        self.window.set_default_size(1120, 740)
        self.window.connect("close-request", self.on_close)
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.window.set_child(root)

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        body.set_vexpand(True)
        root.append(body)

        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        side.set_size_request(224, -1)
        side.add_css_class("sidebar")
        body.append(side)
        brand = Gtk.Label(label="♪  Timekeeper", xalign=0)
        brand.add_css_class("brand")
        side.append(brand)
        tagline = Gtk.Label(label="好音乐，随时听", xalign=0)
        tagline.add_css_class("tagline")
        side.append(tagline)
        library = Gtk.Label(label="资料库", xalign=0)
        library.add_css_class("section-label")
        library.set_margin_top(30)
        side.append(library)
        self.liked_button = self.sidebar_button("我喜欢", self.show_liked, "emblem-favorite-symbolic")
        side.append(self.liked_button)
        self.history_button = self.sidebar_button(
            "播放历史", self.show_history, "document-open-recent-symbolic"
        )
        side.append(self.history_button)
        playlist_title = Gtk.Label(label="我的歌单", xalign=0)
        playlist_title.add_css_class("section-label")
        playlist_title.set_margin_top(22)
        side.append(playlist_title)
        playlist_scroll = Gtk.ScrolledWindow()
        playlist_scroll.set_vexpand(True)
        playlist_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        side.append(playlist_scroll)
        self.playlist_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        playlist_scroll.set_child(self.playlist_box)
        separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        separator.add_css_class("side-separator")
        side.append(separator)
        self.settings_button = self.sidebar_button("设置", self.show_settings, "emblem-system-symbolic")
        side.append(self.settings_button)

        self.pages = Gtk.Stack()
        self.pages.set_hexpand(True)
        self.pages.set_vexpand(True)
        self.pages.connect(
            "notify::visible-child-name",
            lambda stack, *_: side.set_visible(stack.get_visible_child_name() != "player"),
        )
        body.append(self.pages)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        content.set_hexpand(True)
        content.add_css_class("content")
        self.pages.add_named(content, "library")
        top = Gtk.Box(spacing=24)
        top.add_css_class("topbar")
        content.append(top)
        top_label = Gtk.Label(label="你的音乐", xalign=0)
        top_label.add_css_class("topbar-label")
        top_label.set_hexpand(True)
        top.append(top_label)
        self.title = Gtk.Label(label="我喜欢", xalign=0)
        self.title.add_css_class("page-title")
        content.append(self.title)
        self.search_entry = Gtk.SearchEntry(placeholder_text="搜索歌曲")
        self.search_entry.set_size_request(250, -1)
        self.search_entry.set_valign(Gtk.Align.CENTER)
        self.search_entry.connect("activate", self.on_search)
        top.append(self.search_entry)
        self.quality = Gtk.DropDown.new_from_strings(["自动", *music.QUALITY])
        self.quality.set_selected(["自动", *music.QUALITY].index(music.load_quality()))
        self.quality.set_valign(Gtk.Align.CENTER)
        self.quality.set_tooltip_text("播放音质")
        self.quality.connect("notify::selected", lambda dropdown, *_: music.save_quality(dropdown.get_selected_item().get_string()))

        summary = Gtk.Box(spacing=12)
        summary.add_css_class("summary")
        content.append(summary)
        self.info = Gtk.Label(label="双击歌曲播放", xalign=0)
        self.info.add_css_class("muted")
        self.info.set_hexpand(True)
        summary.append(self.info)
        play_all = Gtk.Button(label="播放全部")
        play_all.add_css_class("primary-button")
        play_all.connect("clicked", lambda *_: self.play(0, self.songs.copy()))
        summary.append(play_all)
        self.status = Gtk.Label(label="", xalign=0)
        self.status.add_css_class("status")
        self.status.set_visible(False)
        content.append(self.status)
        columns = Gtk.Box(spacing=14)
        columns.add_css_class("column-header")
        content.append(columns)
        number_header = Gtk.Label(label="#", xalign=0.5)
        number_header.set_size_request(32, -1)
        columns.append(number_header)
        art_header = Gtk.Label()
        art_header.set_size_request(38, -1)
        columns.append(art_header)
        name_header = Gtk.Label(label="歌曲", xalign=0)
        name_header.set_hexpand(True)
        columns.append(name_header)
        album_header = Gtk.Label(label="专辑", xalign=0)
        album_header.set_size_request(190, -1)
        columns.append(album_header)
        duration_header = Gtk.Label(label="时长")
        columns.append(duration_header)
        scroll = Gtk.ScrolledWindow()
        self.song_scroll = scroll
        scroll.set_vexpand(True)
        scroll.add_css_class("song-scroll")
        scroll.get_vadjustment().connect("value-changed", self.maybe_load_next_page)
        content.append(scroll)
        self.track_list = Gtk.ListBox()
        self.track_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.track_list.add_css_class("track-list")
        self.track_list.connect("row-activated", self.on_track_activated)
        song_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        scroll.set_child(song_content)
        song_content.append(self.track_list)
        self.page_spinner = Gtk.Spinner()
        self.page_spinner.set_halign(Gtk.Align.CENTER)
        self.page_spinner.set_margin_top(18)
        self.page_spinner.set_margin_bottom(18)
        self.page_spinner.set_visible(False)
        song_content.append(self.page_spinner)

        self.build_player_page()
        self.build_settings_page()

        footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        footer.add_css_class("footer")
        root.append(footer)
        controls = Gtk.Box(spacing=24)
        controls.add_css_class("footer-controls")
        footer.append(controls)
        track = Gtk.Box(spacing=10)
        track.set_size_request(280, -1)
        track.set_hexpand(False)
        track.set_valign(Gtk.Align.CENTER)
        track.add_css_class("footer-track")
        controls.append(track)
        self.cover = Gtk.Picture()
        self.cover.set_size_request(52, 52)
        self.cover.set_can_shrink(True)
        self.cover.set_valign(Gtk.Align.CENTER)
        self.cover.set_content_fit(Gtk.ContentFit.COVER)
        self.cover.add_css_class("cover")
        cover_button = Gtk.Button()
        cover_button.add_css_class("cover-button")
        self.cover_button = cover_button
        cover_button.set_tooltip_text("打开播放页")
        cover_button.set_child(self.cover)
        cover_button.connect("clicked", self.toggle_player_page)
        track.append(cover_button)
        track_copy = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        track_copy.set_valign(Gtk.Align.CENTER)
        track_copy.set_hexpand(True)
        track.append(track_copy)
        self.title_scroll = Gtk.ScrolledWindow()
        self.title_scroll.set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.NEVER)
        self.title_scroll.set_hexpand(True)
        track_copy.append(self.title_scroll)
        self.now_playing = Gtk.Label(label="尚未播放", xalign=0)
        self.now_playing.set_halign(Gtk.Align.START)
        self.now_playing.add_css_class("track-title")
        self.title_scroll.set_child(self.now_playing)
        self.now_artist = Gtk.Label(label="选择一首歌曲", xalign=0)
        self.now_artist.set_ellipsize(Pango.EllipsizeMode.END)
        self.now_artist.set_max_width_chars(18)
        self.now_artist.add_css_class("muted")
        track_copy.append(self.now_artist)

        player_buttons = Gtk.Box(spacing=8)
        player_buttons.set_valign(Gtk.Align.CENTER)
        player_buttons.add_css_class("footer-transport")
        controls.append(player_buttons)
        previous = self.icon_button("media-skip-backward-symbolic", "上一首")
        previous.connect("clicked", lambda *_: self.step(-1))
        player_buttons.append(previous)
        self.pause_button = self.icon_button("media-playback-start-symbolic", "播放")
        self.pause_button.add_css_class("play-button")
        self.pause_button.connect("clicked", self.toggle_pause)
        player_buttons.append(self.pause_button)
        next_button = self.icon_button("media-skip-forward-symbolic", "下一首")
        next_button.connect("clicked", lambda *_: self.step(1))
        player_buttons.append(next_button)
        progress_row = Gtk.Box(spacing=7)
        progress_row.set_hexpand(True)
        progress_row.set_valign(Gtk.Align.CENTER)
        progress_row.set_size_request(220, -1)
        progress_row.add_css_class("footer-progress")
        controls.append(progress_row)
        self.elapsed_label = Gtk.Label(label="0:00")
        self.elapsed_label.add_css_class("time-label")
        progress_row.append(self.elapsed_label)
        self.progress = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.progress.set_hexpand(True)
        self.progress.set_draw_value(False)
        self.progress.connect("change-value", self.seek)
        progress_row.append(self.progress)
        self.duration_label = Gtk.Label(label="0:00")
        self.duration_label.add_css_class("time-label")
        progress_row.append(self.duration_label)
        tools = Gtk.Box(spacing=9)
        tools.set_valign(Gtk.Align.CENTER)
        tools.add_css_class("footer-tools")
        controls.append(tools)
        volume_icon = Gtk.Image.new_from_icon_name("audio-volume-high-symbolic")
        volume_icon.add_css_class("muted")
        tools.append(volume_icon)
        volume = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 1, 0.05)
        volume.set_size_request(80, -1)
        volume.set_valign(Gtk.Align.CENTER)
        volume.set_draw_value(False)
        volume.set_value(0.8)
        volume.set_tooltip_text("音量")
        volume.connect("value-changed", lambda scale: self.audio.set_property("volume", scale.get_value()))
        tools.append(volume)
        options = self.icon_button("view-more-symbolic", "播放选项")
        tools.append(options)
        popover = Gtk.Popover()
        popover.set_parent(options)
        popover.set_position(Gtk.PositionType.TOP)
        popover.add_css_class("playback-popover")
        option_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        option_list.add_css_class("playback-options")
        popover.set_child(option_list)
        option_list.append(Gtk.Label(label="播放选项", xalign=0))
        self.shuffle = Gtk.ToggleButton(label="随机播放")
        option_list.append(self.shuffle)
        self.repeat = Gtk.ToggleButton(label="单曲循环")
        option_list.append(self.repeat)
        options.connect("clicked", lambda *_: popover.popup())

        GLib.timeout_add(100, self.update_progress)
        GLib.timeout_add(16, self.animate_lyrics)
        self.window.present()
        credential = music.load_credential()
        if credential:
            self.history_musicid = credential.musicid
            self.history = music.load_history(credential.musicid)
            self.account_button.set_label("切换账号")
            self.account_state.set_text("已保存 QQ 音乐登录状态")
            self.load_library()
        else:
            self.set_status("扫码登录后即可读取你的 QQ 音乐资料库")

    def build_player_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.add_css_class("content")
        page.add_css_class("player-surface")
        self.pages.add_named(page, "player")
        header = Gtk.Box(spacing=12)
        header.add_css_class("player-header")
        page.append(header)
        close_button = self.icon_button("go-down-symbolic", "收起播放页")
        close_button.connect("clicked", self.close_player_page)
        header.append(close_button)
        main = Gtk.Box(spacing=96)
        main.add_css_class("player-page")
        main.set_vexpand(True)
        page.append(main)
        self.player_main = main
        self.artwork = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.artwork.set_size_request(240, -1)
        self.artwork.set_valign(Gtk.Align.CENTER)
        main.append(self.artwork)
        self.hero_cover = Gtk.Picture()
        self.hero_cover.set_size_request(240, 240)
        self.hero_cover.set_can_shrink(True)
        self.hero_cover.set_content_fit(Gtk.ContentFit.COVER)
        self.hero_cover.set_halign(Gtk.Align.START)
        self.hero_cover.set_overflow(Gtk.Overflow.HIDDEN)
        self.hero_cover.add_css_class("hero-cover")
        self.artwork.append(self.hero_cover)
        self.player_song_title = Gtk.Label(label="还没有播放歌曲", xalign=0)
        self.player_song_title.set_wrap(True)
        self.player_song_title.set_max_width_chars(20)
        self.player_song_title.add_css_class("player-song-title")
        self.player_song_title.set_margin_top(18)
        self.artwork.append(self.player_song_title)
        self.player_artist = Gtk.Label(label="从资料库选择一首歌", xalign=0)
        self.player_artist.set_wrap(True)
        self.player_artist.set_max_width_chars(24)
        self.player_artist.add_css_class("player-artist")
        self.artwork.append(self.player_artist)
        self.player_status = Gtk.Label(label="", xalign=0)
        self.player_status.add_css_class("player-status")
        self.player_status.set_visible(False)
        self.artwork.append(self.player_status)
        self.lyric_scroll = Gtk.ScrolledWindow()
        self.lyric_scroll.set_hexpand(True)
        self.lyric_scroll.set_vexpand(True)
        self.lyric_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.EXTERNAL)
        self.lyric_scroll.add_css_class("lyrics-scroll")
        main.append(self.lyric_scroll)
        self.lyric_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.lyric_box.add_css_class("lyric-lines")
        self.lyric_scroll.set_child(self.lyric_box)
        scroll_controller = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL | Gtk.EventControllerScrollFlags.HORIZONTAL
        )
        scroll_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        scroll_controller.connect("scroll", self.on_lyric_user_scroll)
        scroll_controller.connect("scroll-begin", lambda *_: self.trace_scroll("begin"))
        scroll_controller.connect("scroll-end", lambda *_: self.trace_scroll("end"))
        self.lyric_scroll.add_controller(scroll_controller)
        self.lyric_scroll.get_vadjustment().connect("value-changed", self.on_lyric_adjusted)
        self.show_lyric_message("播放歌曲后显示歌词")

    def show_lyric_message(self, message):
        self.lyric_scroll_motion = None
        self.lyric_realign_pending = False
        self.last_manual_scroll = 0
        self.lyric_box.remove_css_class("manual-scrolling")
        self.clear_box(self.lyric_box)
        label = Gtk.Label(label=message, xalign=0)
        label.add_css_class("lyric-message")
        self.lyric_box.append(label)

    def render_lyrics(self, raw):
        self.lyric_lines = parse_lyrics(raw)
        self.lyric_starts = [line.start for line in self.lyric_lines]
        self.lyric_rows = []
        self.lyric_state = (-1, -1, False)
        self.lyric_scroll_motion = None
        self.lyric_geometry = None
        self.lyric_padding = None
        self.lyric_realign_pending = True
        self.trace_scroll("lyrics_loaded", count=len(self.lyric_lines))
        self.last_manual_scroll = 0
        self.lyric_box.remove_css_class("manual-scrolling")
        if not self.lyric_lines:
            self.show_lyric_message("暂无歌词")
            return
        self.clear_box(self.lyric_box)
        self.lyric_top = Gtk.Box()
        self.lyric_box.append(self.lyric_top)
        for index, line in enumerate(self.lyric_lines):
            row = Gtk.Label(label=line.text, xalign=0)
            row.set_wrap(True)
            row.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            row.add_css_class("lyric-line")
            row.set_cursor_from_name("pointer")
            row.set_tooltip_text("点击从这里播放")
            click = Gtk.GestureClick.new()
            click.set_button(1)
            click.connect("released", self.on_lyric_clicked, index)
            row.add_controller(click)
            self.lyric_box.append(row)
            self.lyric_rows.append(row)
        self.lyric_bottom = Gtk.Box()
        self.lyric_box.append(self.lyric_bottom)

    def sync_lyrics(self, position_ms, force=False, instant=False):
        if not self.lyric_lines:
            return
        line_index = bisect_right(self.lyric_starts, position_ms) - 1
        line = self.lyric_lines[line_index] if line_index >= 0 else None
        word_index = bisect_right([word.start for word in line.words], position_ms) - 1 if line else -1
        word_active = bool(line and word_index >= 0 and position_ms < line.words[word_index].end)
        state = (line_index, word_index, word_active)
        old_line = self.lyric_state[0]
        if line is None:
            if old_line >= 0:
                self.lyric_rows[old_line].remove_css_class("active")
                self.lyric_rows[old_line].set_text(self.lyric_lines[old_line].text)
            self.lyric_state = (-1, -1, False)
        else:
            if old_line != line_index:
                if old_line >= 0:
                    self.lyric_rows[old_line].remove_css_class("active")
                    self.lyric_rows[old_line].set_text(self.lyric_lines[old_line].text)
                self.lyric_rows[line_index].add_css_class("active")
            self.lyric_state = state
            if line.words:
                current = line.words[word_index] if word_active else None
                self.lyric_rows[line_index].set_markup(highlight_markup(line, position_ms, current, self.lyric_color))
        if (old_line != line_index or force) and (force or not self.lyric_box.has_css_class("manual-scrolling")):
            adjustment = self.lyric_scroll.get_vadjustment()
            viewport = adjustment.get_page_size()
            first_height = self.lyric_rows[0].get_height()
            last_height = self.lyric_rows[-1].get_height()
            if viewport <= 0 or first_height <= 0 or last_height <= 0:
                return
            padding = (
                max(0, round(viewport * LYRIC_ANCHOR - first_height / 2)),
                max(0, round(viewport * (1 - LYRIC_ANCHOR) - last_height / 2)),
            )
            if padding != self.lyric_padding:
                self.lyric_top.set_size_request(-1, padding[0])
                self.lyric_bottom.set_size_request(-1, padding[1])
                self.lyric_padding = padding
                self.lyric_realign_pending = True
                return
            ok, location = self.lyric_rows[max(0, line_index)].compute_bounds(self.lyric_box)
            if not ok:
                return
            target = lyric_scroll_target(
                location.get_y(), location.get_height(), viewport,
                max(0, adjustment.get_upper() - viewport),
            )
            if instant:
                adjustment.set_value(target)
                self.lyric_scroll_motion = None
                self.lyric_realign_pending = False
            elif abs(target - adjustment.get_value()) > 1:
                self.lyric_scroll_motion = (
                    adjustment.get_value(), target, GLib.get_monotonic_time()
                )
            else:
                self.lyric_scroll_motion = None

    def on_lyric_user_scroll(self, controller, _dx, dy):
        if not self.lyric_lines:
            return False
        self.lyric_scroll_motion = None
        self.lyric_box.add_css_class("manual-scrolling")
        self.last_manual_scroll = GLib.get_monotonic_time()
        if self.manual_scroll_timer is None:
            self.manual_scroll_timer = GLib.timeout_add(80, self.finish_manual_scroll)
        adjustment = self.lyric_scroll.get_vadjustment()
        maximum = max(0, adjustment.get_upper() - adjustment.get_page_size())
        before = adjustment.get_value()
        unit = controller.get_unit()
        delta = dy if unit == Gdk.ScrollUnit.SURFACE else dy * 72
        adjustment.set_value(max(0, min(
            before + delta, maximum,
        )))
        self.trace_scroll("input", unit=unit.value_nick, dy=round(dy, 2),
                          before=round(before, 1), after=round(adjustment.get_value(), 1),
                          maximum=round(maximum, 1))
        return True

    def on_lyric_adjusted(self, _adjustment):
        self.trace_scroll("position", value=round(_adjustment.get_value(), 1),
                          maximum=round(max(0, _adjustment.get_upper() - _adjustment.get_page_size()), 1))
        if self.lyric_box.has_css_class("manual-scrolling"):
            self.last_manual_scroll = GLib.get_monotonic_time()

    def finish_manual_scroll(self):
        if GLib.get_monotonic_time() - self.last_manual_scroll < 2_000_000:
            return True
        self.lyric_box.remove_css_class("manual-scrolling")
        self.trace_scroll("manual_end")
        self.manual_scroll_timer = None
        return False

    def on_lyric_clicked(self, _gesture, _count, _x, _y, index):
        if not self.current_song or not 0 <= index < len(self.lyric_lines):
            return
        position = self.lyric_lines[index].start
        if self.audio.seek_simple(
            Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.ACCURATE,
            position * Gst.MSECOND,
        ):
            self.audio.set_state(Gst.State.PLAYING)
            self.set_playing(True)
            self.last_manual_scroll = 0
            self.lyric_box.remove_css_class("manual-scrolling")
            self.sync_lyrics(position, force=True)

    def animate_lyrics(self):
        title_adjustment = self.title_scroll.get_hadjustment()
        animations = Gtk.Settings.get_default().get_property("gtk-enable-animations")
        now = GLib.get_monotonic_time()
        offset = title_scroll_offset(
            (now - self.title_scroll_started) / 1_000_000,
            title_adjustment.get_upper() - title_adjustment.get_page_size(),
        ) if animations else 0
        title_adjustment.set_value(offset)
        if self.pages.get_visible_child_name() == "library":
            adjustment = self.song_scroll.get_vadjustment()
            first = self.track_list.get_row_at_y(int(adjustment.get_value()))
            last = self.track_list.get_row_at_y(int(adjustment.get_value() + adjustment.get_page_size()))
            if first:
                end = last.get_index() + 1 if last else len(self.list_title_scrolls)
                for title_scroll, started in self.list_title_scrolls[first.get_index():end]:
                    title_adjustment = title_scroll.get_hadjustment()
                    offset = title_scroll_offset(
                        (now - started) / 1_000_000,
                        title_adjustment.get_upper() - title_adjustment.get_page_size(),
                    ) if animations else 0
                    title_adjustment.set_value(offset)
        if self.pages.get_visible_child_name() != "player":
            return True
        self.update_player_layout()
        if not self.lyric_lines:
            return True
        adjustment = self.lyric_scroll.get_vadjustment()
        geometry = (
            self.lyric_scroll.get_width(), adjustment.get_page_size(), adjustment.get_upper()
        )
        if geometry != self.lyric_geometry:
            self.lyric_geometry = geometry
            self.lyric_realign_pending = True
            self.lyric_scroll_motion = None
            self.trace_scroll("geometry", width=geometry[0], page=round(geometry[1], 1),
                              upper=round(geometry[2], 1))
        ok, position = self.audio.query_position(Gst.Format.TIME)
        realign = self.lyric_realign_pending and not self.lyric_box.has_css_class("manual-scrolling")
        if realign:
            self.trace_scroll("realign", value=round(adjustment.get_value(), 1))
        self.sync_lyrics(
            int(position / 1_000_000) if ok else 0,
            force=realign, instant=realign,
        )
        if self.lyric_scroll_motion:
            start, target, started = self.lyric_scroll_motion
            progress = min(1, (GLib.get_monotonic_time() - started) / 360_000)
            progress = 1 - (1 - progress) ** 3
            self.lyric_scroll.get_vadjustment().set_value(start + (target - start) * progress)
            self.lyric_scroll.queue_draw()
            if progress == 1:
                self.lyric_scroll_motion = None
        return True

    def update_player_layout(self):
        width, height = self.player_main.get_width(), self.player_main.get_height()
        if width <= 0 or height <= 0:
            return
        size = max(180, min(width // 3, height - 110))
        if size == self.cover_target_size:
            return
        self.cover_target_size = size
        self.artwork.set_size_request(size, -1)
        self.hero_cover.set_size_request(size, size)
        if self.cover_source and self.cover_resize_timer is None:
            self.cover_resize_timer = GLib.timeout_add(100, self.render_cover)

    def render_cover(self):
        self.cover_resize_timer = None
        if self.cover_source:
            size = self.cover_target_size
            pixbuf = self.cover_source.scale_simple(size, size, GdkPixbuf.InterpType.BILINEAR)
            self.hero_cover.set_paintable(Gdk.Texture.new_for_pixbuf(pixbuf))
        return False

    def build_settings_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.add_css_class("content")
        self.pages.add_named(page, "settings")
        heading = Gtk.Label(label="设置", xalign=0)
        heading.add_css_class("page-title")
        page.append(heading)
        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        page.append(scroll)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.set_hexpand(True)
        content.add_css_class("settings-content")
        scroll.set_child(content)

        def add_section(title, rows):
            label = Gtk.Label(label=title, xalign=0)
            label.add_css_class("settings-heading")
            content.append(label)
            group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            group.add_css_class("settings-group")
            content.append(group)
            for name, description, control in rows:
                if group.get_first_child():
                    group.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
                row = Gtk.Box(spacing=24)
                row.add_css_class("settings-row")
                group.append(row)
                copy = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
                copy.set_hexpand(True)
                row.append(copy)
                title_label = Gtk.Label(label=name, xalign=0)
                title_label.add_css_class("settings-label")
                copy.append(title_label)
                hint = description if isinstance(description, Gtk.Widget) else Gtk.Label(label=description, xalign=0)
                hint.add_css_class("muted")
                hint.set_wrap(True)
                copy.append(hint)
                control.set_valign(Gtk.Align.CENTER)
                row.append(control)

        self.quality.set_halign(Gtk.Align.END)
        add_section("播放", [
            ("播放音质", "自动优先高品质，持续缓冲时切换为标准音质；下一首起生效", self.quality),
        ])

        size_control = Gtk.SpinButton.new_with_range(20, 48, 1)
        size_control.set_value(self.lyric_size)
        size_control.connect("value-changed", self.on_lyric_settings_changed)
        self.lyric_size_control = size_control
        color_dialog = Gtk.ColorDialog()
        color_dialog.set_title("选择逐字高光颜色")
        color_control = Gtk.ColorDialogButton.new(color_dialog)
        rgba = Gdk.RGBA()
        rgba.parse(self.lyric_color)
        color_control.set_rgba(rgba)
        color_control.connect("notify::rgba", self.on_lyric_settings_changed)
        self.lyric_color_control = color_control
        add_section("歌词", [
            ("字体大小", "调整播放页的歌词字号", size_control),
            ("逐字高光颜色", "用于逐字高光和当前播放的歌词", color_control),
        ])

        self.account_state = Gtk.Label(label="尚未登录", xalign=0)
        self.account_button = Gtk.Button(label="扫码登录")
        self.account_button.add_css_class("primary-button")
        self.account_button.connect("clicked", self.toggle_login)
        add_section("账号", [
            ("QQ 音乐", self.account_state, self.account_button),
        ])

    def apply_lyric_style(self):
        self.lyric_css.load_from_data(
            f".lyric-line {{ font-size: {self.lyric_size}px; }} "
            f".lyric-line.active {{ color: {self.lyric_color}; }} "
            f".lyric-message {{ font-size: {round(self.lyric_size * 0.8)}px; }}".encode()
        )

    def on_lyric_settings_changed(self, *_):
        self.lyric_size = self.lyric_size_control.get_value_as_int()
        rgba = self.lyric_color_control.get_rgba()
        self.lyric_color = "#{:02x}{:02x}{:02x}".format(
            *(round(channel * 255) for channel in (rgba.red, rgba.green, rgba.blue))
        )
        self.apply_lyric_style()
        music.save_lyric_settings(self.lyric_size, self.lyric_color)
        if self.current_song and self.pages.get_visible_child_name() == "player":
            self.lyric_realign_pending = True

    def sidebar_button(self, label, callback, icon=None):
        button = Gtk.Button()
        if icon:
            row = Gtk.Box(spacing=12)
            row.append(Gtk.Image.new_from_icon_name(icon))
            row.append(Gtk.Label(label=label, xalign=0))
            button.set_child(row)
        else:
            button.set_label(label)
        button.add_css_class("side-button")
        button.connect("clicked", callback)
        return button

    def icon_button(self, icon, tooltip, toggle=False):
        button = Gtk.ToggleButton() if toggle else Gtk.Button()
        button.set_icon_name(icon)
        button.set_tooltip_text(tooltip)
        button.add_css_class("icon-button")
        return button

    def set_playing(self, playing):
        self.pause_button.set_icon_name(
            "media-playback-pause-symbolic" if playing else "media-playback-start-symbolic"
        )
        self.pause_button.set_tooltip_text("暂停" if playing else "播放")

    def set_status(self, text):
        self.status.set_text(text)
        self.status.set_visible(bool(text))
        self.player_status.set_text(text)
        self.player_status.set_visible(bool(text))

    def work(self, task, done=None):
        def run():
            try:
                value = asyncio.run(task())
            except Exception as exc:
                GLib.idle_add(self.set_status, str(exc))
            else:
                if done:
                    GLib.idle_add(done, value)

        threading.Thread(target=run, daemon=True).start()

    def toggle_login(self, _button):
        self.login_window = Gtk.Window(title="QQ 音乐登录", transient_for=self.window, modal=True)
        self.login_window.set_default_size(320, 390)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        content.set_margin_top(24)
        content.set_margin_bottom(24)
        content.set_margin_start(24)
        content.set_margin_end(24)
        self.login_window.set_child(content)
        content.append(Gtk.Label(label="使用手机 QQ 扫码登录"))
        self.qr_picture = Gtk.Picture()
        self.qr_picture.set_size_request(260, 260)
        content.append(self.qr_picture)
        content.append(Gtk.Label(label="二维码将在 3 分钟后过期"))
        self.login_window.present()
        self.set_status("正在获取登录二维码…")

        def qr_ready(data):
            GLib.idle_add(self.set_qr, data)

        self.work(lambda: music.login(qr_ready), self.logged_in)

    def set_qr(self, data):
        if self.login_window.get_visible():
            self.qr_picture.set_paintable(Gdk.Texture.new_from_bytes(GLib.Bytes.new(data)))
            self.set_status("请使用手机 QQ 扫码并确认")

    def logged_in(self, credential):
        self.login_window.close()
        self.audio.set_state(Gst.State.NULL)
        self.play_request_id += 1
        self.request_id += 1
        self.current_song = None
        self.pending_history_song = None
        self.history_musicid = credential.musicid
        self.history = music.load_history(credential.musicid)
        self.queue = []
        self.index = -1
        self.songs.clear()
        self.clear_tracks()
        self.clear_box(self.playlist_box)
        self.now_playing.set_text("尚未播放")
        self.now_playing.set_tooltip_text(None)
        self.title_scroll_started = GLib.get_monotonic_time()
        self.now_artist.set_text("选择一首歌曲")
        self.now_artist.set_tooltip_text(None)
        self.player_song_title.set_text("还没有播放歌曲")
        self.player_artist.set_text("从资料库选择一首歌")
        self.show_lyric_message("播放歌曲后显示歌词")
        self.lyric_lines = []
        self.lyric_starts = []
        self.lyric_rows = []
        self.lyric_state = (-1, -1, False)
        self.cover_source = None
        self.cover.set_paintable(None)
        self.hero_cover.set_paintable(None)
        self.set_playing(False)
        self.account_button.set_label("切换账号")
        self.account_state.set_text("已保存 QQ 音乐登录状态")
        self.set_status("登录成功")
        self.load_library()

    def load_library(self):
        self.work(music.playlists, self.set_playlists)
        self.show_liked()

    def set_playlists(self, playlists):
        self.playlists = playlists
        self.clear_box(self.playlist_box)
        for playlist in playlists:
            if playlist.dirid == 201 and playlist.title == "我喜欢":
                continue
            button = self.sidebar_button(
                playlist.title, lambda b, p=playlist: self.show_playlist(p, b), "folder-music-symbolic"
            )
            button.set_tooltip_text(playlist.title)
            self.playlist_box.append(button)

    def show_liked(self, *_):
        self.pages.set_visible_child_name("library")
        self.source = ("liked", None)
        self.title.set_text("我喜欢")
        self.select_nav(self.liked_button)
        self.load_page(True)

    def show_history(self, *_):
        self.pages.set_visible_child_name("library")
        self.source = ("history", None)
        self.title.set_text("播放历史")
        self.select_nav(self.history_button)
        self.load_page(True)

    def show_playlist(self, playlist, button):
        self.pages.set_visible_child_name("library")
        self.source = ("playlist", playlist.id)
        self.title.set_text(playlist.title)
        self.select_nav(button)
        self.load_page(True)

    def select_nav(self, button):
        if self.active_nav:
            self.active_nav.remove_css_class("active")
        self.active_nav = button
        if button:
            button.add_css_class("active")

    def show_player_page(self, *_):
        current_page = self.pages.get_visible_child_name()
        if current_page != "player":
            self.player_return_page = current_page or "library"
        self.pages.set_visible_child_name("player")
        self.lyric_realign_pending = True

    def toggle_player_page(self, *_):
        if self.pages.get_visible_child_name() == "player":
            self.close_player_page()
        else:
            self.show_player_page()

    def close_player_page(self, *_):
        self.pages.set_visible_child_name(self.player_return_page)

    def show_settings(self, *_):
        self.pages.set_visible_child_name("settings")
        self.select_nav(self.settings_button)

    def on_search(self, entry):
        self.pages.set_visible_child_name("library")
        query = entry.get_text().strip()
        if not query:
            self.show_liked()
            return
        self.source = ("search", query)
        self.title.set_text(f"搜索：{query}")
        self.select_nav(None)
        self.load_page(True)

    def load_page(self, reset):
        if reset:
            self.request_id += 1
            self.page = 0
            self.loading_page = False
            self.has_more = True
            self.songs = []
            self.clear_tracks()
        if self.loading_page or not self.has_more:
            return
        self.loading_page = True
        source = self.source
        request_id = self.request_id
        page = self.page + 1
        self.page_spinner.set_visible(True)
        self.page_spinner.start()
        self.set_status("")

        async def fetch():
            try:
                kind, value = source
                if kind == "liked":
                    return await music.liked(page)
                if kind == "history":
                    return self.history[(page - 1) * 50:page * 50]
                if kind == "playlist":
                    return await music.playlist_songs(value, page)
                return await music.search(value, page)
            except Exception as error:
                return error

        def done(items):
            if request_id != self.request_id or source != self.source:
                return
            self.loading_page = False
            self.page_spinner.stop()
            self.page_spinner.set_visible(False)
            if isinstance(items, Exception):
                self.set_status(f"加载歌曲失败：{items}；重新打开列表可重试")
                return
            self.page = page
            self.songs.extend(items)
            self.append_tracks(items)
            self.has_more = len(items) == 50
            self.info.set_text(
                "暂无播放历史" if source[0] == "history" and not self.songs
                else f"{len(self.songs)} 首歌曲 · 双击播放"
            )
            self.set_status("")
            GLib.idle_add(self.maybe_load_next_page)

        self.work(fetch, done)

    def maybe_load_next_page(self, adjustment=None):
        if self.pages.get_visible_child_name() != "library" or self.loading_page or not self.has_more:
            return False
        adjustment = adjustment or self.song_scroll.get_vadjustment()
        remaining = adjustment.get_upper() - adjustment.get_value() - adjustment.get_page_size()
        if adjustment.get_upper() > adjustment.get_page_size() and remaining < 300:
            self.load_page(False)
        return False

    def clear_box(self, box):
        while child := box.get_first_child():
            box.remove(child)

    def clear_tracks(self):
        self.clear_box(self.track_list)
        self.list_title_scrolls.clear()
        self.page_spinner.stop()
        self.page_spinner.set_visible(False)

    def append_tracks(self, songs):
        for offset, song in enumerate(songs, len(self.songs) - len(songs)):
            row = Gtk.ListBoxRow()
            line = Gtk.Box(spacing=14)
            line.set_margin_top(10)
            line.set_margin_bottom(10)
            line.set_margin_start(16)
            line.set_margin_end(16)
            number = Gtk.Label(label=str(offset + 1))
            number.set_size_request(32, -1)
            number.add_css_class("muted")
            line.append(number)
            artwork = Gtk.Box()
            artwork.add_css_class("row-art")
            artwork.set_size_request(38, 38)
            placeholder = Gtk.Image.new_from_icon_name("audio-x-generic-symbolic")
            artwork.append(placeholder)
            line.append(artwork)
            self.load_row_art(song, artwork)
            labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            labels.set_hexpand(True)
            title_scroll = Gtk.ScrolledWindow()
            title_scroll.set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.NEVER)
            title_scroll.set_hexpand(True)
            labels.append(title_scroll)
            name = Gtk.Label(label=song.title or song.name, xalign=0)
            name.set_halign(Gtk.Align.START)
            name.set_tooltip_text(song.title or song.name)
            name.add_css_class("song-title")
            title_scroll.set_child(name)
            self.list_title_scrolls.append((title_scroll, GLib.get_monotonic_time()))
            singer = Gtk.Label(label=artist(song), xalign=0)
            singer.set_ellipsize(Pango.EllipsizeMode.END)
            singer.add_css_class("muted")
            labels.append(singer)
            line.append(labels)
            album = Gtk.Label(label=song.album.name, xalign=0)
            album.set_size_request(190, -1)
            album.set_ellipsize(Pango.EllipsizeMode.END)
            album.add_css_class("muted")
            line.append(album)
            duration = Gtk.Label(label=clock(song.interval))
            duration.add_css_class("muted")
            line.append(duration)
            row.set_child(line)
            self.track_list.append(row)

    def load_row_art(self, song, box):
        url = song.cover_url(150)
        if not url:
            return
        if url in self.cover_cache:
            self.show_row_art(url, box, self.cover_cache[url])
            return

        def fetch():
            try:
                with urllib.request.urlopen(url, timeout=8) as response:
                    data = response.read(200_000)
            except Exception:
                return
            GLib.idle_add(self.show_row_art, url, box, data)

        self.cover_pool.submit(fetch)

    def show_row_art(self, url, box, data):
        try:
            texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
        except GLib.Error:
            return False
        self.cover_cache[url] = data
        self.cover_cache.move_to_end(url)
        if len(self.cover_cache) > 64:
            self.cover_cache.popitem(last=False)
        box.remove(box.get_first_child())
        picture = Gtk.Picture.new_for_paintable(texture)
        picture.set_content_fit(Gtk.ContentFit.COVER)
        picture.set_can_shrink(True)
        picture.set_size_request(38, 38)
        box.append(picture)
        return False

    def on_track_activated(self, _list, row):
        self.play(row.get_index(), self.songs.copy())
        self.show_player_page()

    def play(self, index, queue=None):
        if queue is not None:
            self.queue = queue
        if not 0 <= index < len(self.queue):
            return
        self.index = index
        song = self.queue[index]
        self.current_song = song
        self.pending_history_song = None
        self.now_playing.set_text(song.title or song.name)
        self.now_playing.set_tooltip_text(song.title or song.name)
        self.title_scroll.get_hadjustment().set_value(0)
        self.title_scroll_started = GLib.get_monotonic_time()
        self.now_artist.set_text(artist(song))
        self.now_artist.set_tooltip_text(artist(song))
        self.player_song_title.set_text(song.title or song.name)
        self.player_artist.set_text(artist(song))
        self.show_lyric_message("正在加载歌词…")
        self.lyric_lines = []
        self.lyric_starts = []
        self.lyric_rows = []
        self.lyric_state = (-1, -1, False)
        self.set_status("正在获取播放地址…")
        self.play_request_id += 1
        request_id = self.play_request_id
        quality = self.quality.get_selected_item().get_string()
        self.load_cover(song, request_id)

        def show_lyric(lyric):
            if request_id == self.play_request_id:
                self.render_lyrics(lyric)

        async def fetch_lyric():
            try:
                return await music.lyrics(song)
            except Exception:
                return ""

        self.work(fetch_lyric, show_lyric)

        self.auto_quality = quality == "自动"
        self.current_quality = None
        self.buffering_since = None
        self.quality_downgrading = False
        self.pending_seek_ns = None

        def start(result):
            if request_id != self.play_request_id:
                return
            url, actual_quality = result
            self.current_quality = actual_quality
            self.audio.set_state(Gst.State.NULL)
            self.audio.set_property("uri", url)
            self.audio.set_state(Gst.State.PLAYING)
            self.set_playing(True)
            self.set_status("")
            self.pending_history_song = song

        async def resolve():
            if quality == "自动":
                return await music.auto_stream_url(song)
            return await music.stream_url(song, quality), quality

        self.work(resolve, start)

    def record_history(self, song):
        if self.history_musicid is None:
            return
        self.history = [song, *(item for item in self.history if item.mid != song.mid)][:music.HISTORY_LIMIT]
        try:
            music.save_history(self.history_musicid, self.history)
        except OSError:
            self.set_status("播放历史保存失败")
        if self.source[0] == "history":
            self.load_page(True)

    def step(self, amount):
        if not self.queue and self.songs:
            self.queue = self.songs.copy()
        if self.queue:
            index = random.randrange(len(self.queue)) if amount > 0 and self.shuffle.get_active() else (self.index + amount) % len(self.queue)
            self.play(index)

    def toggle_pause(self, _button):
        if self.current_song is None:
            self.step(1)
            return
        _ret, state, _pending = self.audio.get_state(0)
        if state == Gst.State.PLAYING:
            self.audio.set_state(Gst.State.PAUSED)
            self.set_playing(False)
        else:
            self.audio.set_state(Gst.State.PLAYING)
            self.set_playing(True)

    def on_audio_message(self, _bus, message):
        if message.type == Gst.MessageType.EOS:
            if self.repeat.get_active() and self.current_song:
                self.audio.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH, 0)
                self.audio.set_state(Gst.State.PLAYING)
            else:
                self.step(1)
        elif message.type == Gst.MessageType.BUFFERING:
            if self.auto_quality and self.current_quality == "高品质":
                if message.parse_buffering() == 100:
                    self.buffering_since = None
                elif self.buffering_since is None:
                    self.buffering_since = time.monotonic()
        elif message.type == Gst.MessageType.ASYNC_DONE and self.pending_seek_ns is not None:
            if self.audio.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT, self.pending_seek_ns):
                self.pending_seek_ns = None
                self.audio.set_state(Gst.State.PLAYING)
        elif message.type == Gst.MessageType.ERROR:
            if self.auto_quality and self.current_quality == "高品质" and not self.quality_downgrading:
                self.downgrade_quality()
                self.audio.set_state(Gst.State.NULL)
                return
            self.audio.set_state(Gst.State.NULL)
            self.set_playing(False)
            self.set_status("播放失败，请尝试其他音质或歌曲")

    def load_cover(self, song, request_id):
        self.cover_source = None
        self.cover.set_paintable(None)
        self.hero_cover.set_paintable(None)
        try:
            url = song.cover_url(1200)
        except ValueError:
            return
        if not url:
            return

        def fetch():
            try:
                with urllib.request.urlopen(url, timeout=8) as response:
                    data = response.read(4_000_000)
            except Exception:
                return
            GLib.idle_add(show, data)

        def show(data):
            if request_id == self.play_request_id:
                try:
                    loader = GdkPixbuf.PixbufLoader.new()
                    loader.write(data)
                    loader.close()
                    self.cover_source = loader.get_pixbuf()
                    thumbnail = self.cover_source.scale_simple(52, 52, GdkPixbuf.InterpType.BILINEAR)
                    self.cover.set_paintable(Gdk.Texture.new_for_pixbuf(thumbnail))
                    self.render_cover()
                except (GLib.Error, TypeError):
                    pass

        threading.Thread(target=fetch, daemon=True).start()

    def update_progress(self):
        if (self.buffering_since is not None and not self.quality_downgrading
                and time.monotonic() - self.buffering_since >= 3):
            self.downgrade_quality()
        if self.audio:
            ok_position, position = self.audio.query_position(Gst.Format.TIME)
            ok_duration, duration = self.audio.query_duration(Gst.Format.TIME)
            if ok_position and position > 0 and self.pending_history_song is self.current_song:
                self.pending_history_song = None
                self.record_history(self.current_song)
            if ok_duration and duration > 0:
                seconds = duration / Gst.SECOND
                self.progress.set_range(0, seconds)
                if ok_position:
                    self.progress.set_value(position / Gst.SECOND)
                self.elapsed_label.set_text(clock(position / Gst.SECOND if ok_position else 0))
                self.duration_label.set_text(clock(seconds))
        return True

    def downgrade_quality(self):
        self.quality_downgrading = True
        self.buffering_since = None
        request_id = self.play_request_id
        song = self.current_song
        ok, position = self.audio.query_position(Gst.Format.TIME)

        async def resolve():
            try:
                return await music.stream_url(song, "标准")
            except Exception as error:
                return error

        def restart(result):
            if request_id != self.play_request_id:
                return
            if isinstance(result, Exception):
                self.set_status(f"自动切换音质失败：{result}")
                return
            self.current_quality = "标准"
            self.set_status("")
            self.audio.set_state(Gst.State.NULL)
            self.audio.set_property("uri", result)
            self.pending_seek_ns = position if ok and position > 0 else None
            self.audio.set_state(Gst.State.PAUSED if self.pending_seek_ns is not None else Gst.State.PLAYING)
            self.set_playing(True)

        self.work(resolve, restart)

    def seek(self, _scale, _scroll, value):
        if self.current_song and value >= 0:
            self.audio.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT, int(value * Gst.SECOND))
            self.sync_lyrics(int(value * 1000), force=True)
        return False

    def on_close(self, *_):
        self.audio.set_state(Gst.State.NULL)
        self.cover_pool.shutdown(wait=False, cancel_futures=True)
        if self.scroll_log:
            self.scroll_log.close()
        return False


if __name__ == "__main__":
    raise SystemExit(Player().run())
