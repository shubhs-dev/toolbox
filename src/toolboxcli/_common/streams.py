"""ffmpeg stream mapping that keeps every track, for scripts that write a whole video file.

Without explicit -map arguments ffmpeg keeps only one video, one audio and one subtitle
track (picking audio by channel count, not language or default flag), and drops
attachments such as MKV subtitle fonts.
"""

from __future__ import annotations

from toolboxcli._common.console import warn

MKV_EXTS = {".mkv", ".mka", ".mks"}
MP4_EXTS = {".mp4", ".m4v", ".mov"}
TEXT_SUBTITLE_CODECS = {"subrip", "srt", "ass", "ssa", "webvtt", "mov_text", "text"}


def main_video_streams(streams: list[dict]) -> list[dict]:
    """ffprobe video streams, excluding cover art / attached pictures."""
    return [
        s for s in streams
        if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")
    ]


def subtitle_codec_for(codec: str, out_ext: str) -> str | None:
    """Codec to write a subtitle stream with: "copy", an encoder name, or None to drop it.
    Covers cutting into a different container: MP4 only holds mov_text, MKV can't hold it."""
    if out_ext in MKV_EXTS:
        return "srt" if codec == "mov_text" else "copy"
    if out_ext in MP4_EXTS:
        if codec == "mov_text":
            return "copy"
        if codec in TEXT_SUBTITLE_CODECS:
            return "mov_text"
        return None  # image subtitles (PGS, VobSub) have no MP4 equivalent
    return "copy"


def keep_all_streams_args(streams: list[dict], out_ext: str) -> list[str]:
    """-map / -c:s arguments that keep every video, audio and subtitle track, plus
    attachments for MKV output. Pass the input's ffprobe streams and the lowercased output
    extension. Put these after a blanket `-c copy` and any `-c:v`/`-c:a` overrides.

    Cover art and data streams (timecode, MP4 chapter text tracks) are skipped; chapters
    are carried over by ffmpeg separately. Streams the output container can't hold are
    dropped with a warning naming them.
    """
    maps: list[str] = []
    sub_codecs: list[str] = []
    dropped: list[str] = []

    for s in streams:
        idx = s["index"]
        kind = s.get("codec_type")
        codec = s.get("codec_name", "unknown")
        lang = s.get("tags", {}).get("language")
        label = f"#{idx} {kind} ({codec}{', ' + lang if lang else ''})"

        if kind == "video":
            if not s.get("disposition", {}).get("attached_pic"):
                maps += ["-map", f"0:{idx}"]
        elif kind == "audio":
            maps += ["-map", f"0:{idx}"]
        elif kind == "subtitle":
            target = subtitle_codec_for(codec, out_ext)
            if target is None:
                dropped.append(label)
                continue
            maps += ["-map", f"0:{idx}"]
            sub_codecs.append(target)
        elif kind == "attachment":
            if out_ext in MKV_EXTS:
                maps += ["-map", f"0:{idx}"]
            else:
                dropped.append(label)

    if dropped:
        warn(f"Not supported by {out_ext or 'this'} output, dropping: {', '.join(dropped)}")

    codec_args: list[str] = []
    for n, target in enumerate(sub_codecs):
        if target != "copy":
            codec_args += [f"-c:s:{n}", target]
    return maps + codec_args
