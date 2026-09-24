"""Parse QQ Music's timed QRC lyrics, with LRC line timing as a fallback."""

import re
from dataclasses import dataclass
from html import escape, unescape


@dataclass
class Word:
    start: int
    end: int
    first: int
    last: int


@dataclass
class Line:
    start: int
    end: int
    text: str
    words: list[Word]


QRC_LINE = re.compile(r"^\[(\d+),(\d+)\](.*)$")
QRC_WORD = re.compile(r"(.*?)\((\d+),(\d+)\)")
LRC_TIME = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\]")


def parse(raw):
    if raw.lstrip().startswith("<?xml") or raw.lstrip().startswith("<QrcInfos"):
        match = re.search(r'\bLyricContent="(.*?)"\s*/>', raw, re.S)
        content = unescape(match[1]) if match else ""
        lines = []
        for item in content.splitlines():
            match = QRC_LINE.match(item)
            if not match:
                continue
            start, duration = int(match[1]), int(match[2])
            text = ""
            words = []
            for part, word_start, word_duration in QRC_WORD.findall(match[3]):
                first = len(text)
                text += part
                words.append(Word(int(word_start), int(word_start) + int(word_duration), first, len(text)))
            if text.strip():
                lines.append(Line(start, start + duration, text, words))
        if lines:
            return lines

    lines = []
    for item in raw.splitlines():
        times = LRC_TIME.findall(item)
        text = LRC_TIME.sub("", item).strip()
        if not times or not text:
            continue
        for minute, second in times:
            start = int((int(minute) * 60 + float(second)) * 1000)
            lines.append(Line(start, start + 5000, text, []))
    lines.sort(key=lambda line: line.start)
    for current, following in zip(lines, lines[1:]):
        current.end = following.start
    return lines


def highlight_color(progress, target="#ffffff"):
    """Fade the sung word from the inactive color to the chosen highlight."""
    progress = max(0, min(1, progress))
    progress = progress * progress * (3 - 2 * progress)
    channels = (int(target[index:index + 2], 16) for index in (1, 3, 5))
    return "#{:02x}{:02x}{:02x}".format(
        *(round(start + (end - start) * progress) for start, end in zip((168, 168, 178), channels))
    )


def highlight_markup(line, position_ms, current, target="#ffffff"):
    finished = [word for word in line.words if word.end <= position_ms]
    done_end = finished[-1].last if finished else 0
    word_start = current.first if current else done_end
    word_end = current.last if current else done_end
    color = highlight_color((position_ms - current.start) / max(1, current.end - current.start), target) if current else "#a8a8b2"
    return (
        f'<span foreground="{target}">{escape(line.text[:done_end])}</span>'
        f'<span foreground="#a8a8b2">{escape(line.text[done_end:word_start])}</span>'
        f'<span foreground="{color}">{escape(line.text[word_start:word_end])}</span>'
        f'<span foreground="#a8a8b2">{escape(line.text[word_end:])}</span>'
    )
