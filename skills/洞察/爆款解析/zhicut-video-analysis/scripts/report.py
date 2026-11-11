"""Render local, self-contained video reports and their archive index."""
from __future__ import annotations

import base64
import html
import json
import math
import os
import re
import uuid
from collections import defaultdict
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, urlsplit

ASSETS = Path(__file__).resolve().parents[1] / "assets"
MAX_FRAME_BYTES = 16 * 1024 * 1024
MAX_EMBEDDED_BYTES = 64 * 1024 * 1024


def _load(path: Path, default=None):
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    if default is not None:
        return default
    raise FileNotFoundError(path)


def _catalog(locale: str) -> dict:
    catalog = _load(ASSETS / "i18n" / "en.json")
    language = "zh-CN" if locale in {"zh-CN", "zh-Hans"} else "en"
    if language != "en":
        catalog.update(_load(ASSETS / "i18n" / f"{language}.json", {}))
    return catalog


def _esc(value) -> str:
    return html.escape(_text(value), quote=True)


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "; ".join(_text(item) for item in value)
    if isinstance(value, dict):
        return "; ".join(str(key) + ": " + _text(item) for key, item in value.items())
    return str(value)


def _items(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _number(value, fallback=0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else fallback
    except (TypeError, ValueError):
        return fallback


def _timestamp(value) -> str:
    seconds = max(0, int(_number(value)))
    if seconds >= 3600:
        return f"{seconds // 3600:02}:{seconds % 3600 // 60:02}:{seconds % 60:02}"
    return f"{seconds // 60:02}:{seconds % 60:02}"


def _duration(value, labels: dict) -> str:
    seconds = max(0, round(_number(value), 2))
    minutes = int(seconds) // 60
    return labels["duration_label"].format(minutes=minutes, seconds=f"{seconds - minutes * 60:g}")


def _time_value(value) -> float | None:
    if isinstance(value, (int, float)):
        return _number(value)
    match = re.search(r"(?<!\d)(\d{1,3}):(\d{2})(?::(\d{2}(?:\.\d+)?))?", str(value))
    if not match:
        return None
    if match[3] is not None:
        return int(match[1]) * 3600 + int(match[2]) * 60 + float(match[3])
    return int(match[1]) * 60 + int(match[2])


def _ul(values) -> str:
    return "<ul>" + "".join("<li>" + _esc(item) + "</li>" for item in _items(values)) + "</ul>"


def _section(title: str, content: str, identifier="", classes="") -> str:
    attrs = (f' id="{_esc(identifier)}"' if identifier else "")
    attrs += f' class="{_esc(classes)}"' if classes else ""
    return "<section" + attrs + "><h2>" + _esc(title) + "</h2>" + content + "</section>"


def _table(headers: list[str], rows: list[list[str]]) -> str:
    return '<div class="table-wrap"><table><thead><tr>' + "".join("<th>" + _esc(x) + "</th>" for x in headers) + "</tr></thead><tbody>" + "".join("<tr>" + "".join("<td>" + x + "</td>" for x in row) + "</tr>" for row in rows) + "</tbody></table></div>"


def _evidence(values, chapters: list[dict]) -> str:
    links = []
    for entry in _items(values):
        label = _text(entry)
        index = None
        at = None
        if isinstance(entry, dict):
            label = _text(entry.get("label", entry.get("time", "")))
            if isinstance(entry.get("chapter"), int) and 0 <= entry["chapter"] < len(chapters):
                index = entry["chapter"]
            at = _time_value(entry.get("seconds", entry.get("time", label)))
        else:
            at = _time_value(entry)
            if isinstance(entry, (float, int)):
                label = _timestamp(entry)
        if index is None and at is not None:
            index = next((i for i, chapter in enumerate(chapters) if _number(chapter.get("start_seconds")) <= at < _number(chapter.get("end_seconds"))), None)
            if index is None and chapters and at == _number(chapters[-1].get("end_seconds")):
                index = len(chapters) - 1
        if index is not None:
            links.append(f'<a href="#chapter-{index}">{_esc(label)}</a>')
        else:
            links.append('<span class="evidence-label">' + _esc(label) + "</span>")
    return '<div class="insight-evidence">' + "".join(links) + "</div>" if links else ""


def _local_file(root: Path, supplied, *, must_exist=True) -> Path:
    value = str(supplied)
    if not value or urlsplit(value).scheme or value.startswith("//"):
        raise ValueError("Only local artifact paths are accepted")
    path = Path(value)
    candidate = (path if path.is_absolute() else root / path).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as error:
        raise ValueError("Artifact path escapes its output directory") from error
    if must_exist and not candidate.is_file():
        raise FileNotFoundError(candidate)
    return candidate


def _image(path: Path) -> tuple[str, int]:
    size = path.stat().st_size
    if size <= 0 or size > MAX_FRAME_BYTES:
        raise ValueError("Frame size is outside the report image budget")
    raw = path.read_bytes()
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
    elif raw.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
    else:
        raise ValueError("Report frames must contain PNG or JPEG bytes")
    return "data:" + mime + ";base64," + base64.b64encode(raw).decode("ascii"), size


def _atomic_text(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class _Structure(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.anchors = []
        self.images = 0
        self.sections = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.append(attrs["id"])
        if tag == "a" and attrs.get("href", "").startswith("#"):
            self.anchors.append(attrs["href"][1:])
        if tag == "img":
            self.images += 1
        if tag == "section":
            self.sections.append(attrs.get("id", ""))


def _page(title: str, content: str, labels: dict) -> str:
    css = (ASSETS / "report.css").read_text(encoding="utf-8")
    return '<!DOCTYPE html><html lang="' + _esc(labels["language"]) + '"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>' + _esc(title) + "</title><style>" + css + "</style></head><body><main>" + content + "</main></body></html>"


def render_report(run_dir: Path, locale="zh-CN") -> Path:
    root = Path(run_dir).resolve()
    labels = _catalog(locale)
    data = _load(root / "analysis.json")
    meta = _load(root / "metadata.json")
    transcript = _load(root / "transcript.json")
    frames = _load(root / "frames.json")
    verification = _load(root / "verification.json", {})
    chapters = _items(data.get("chapters"))
    insights = data.get("whole_video_insights") or {}
    if not insights.get("thesis") or not _items(insights.get("items")):
        raise ValueError("Whole-video insights are required before rendering a complete report")
    segments = _items(transcript.get("segments"))
    words = _items(transcript.get("words"))
    source = str(meta.get("source", "")).replace("\\", "/").rsplit("/", 1)[-1]
    title = data.get("title") or source or labels["report_fallback_title"]
    date = meta.get("analysisDate") or meta.get("createdAt") or labels["unknown"]
    body = '<header><p class="eyebrow">' + _esc(labels["report_brand"]) + " · " + _esc(date) + "</p><h1>" + _esc(title) + '</h1><p class="subtitle">' + _esc(source) + '</p><div class="badges">'
    duration = _number(meta.get("durationSeconds"))
    submitted = _number(verification.get("audioSubmittedSeconds", meta.get("audioSubmittedSeconds")), -1)
    audio_label = labels["full_audio_badge"] if duration > 0 and abs(submitted - duration) < 0.1 else labels["transcription_badge"]
    resolution = labels["resolution_label"].format(width=meta.get("width", labels["unknown"]), height=meta.get("height", labels["unknown"]), fps=f'{_number(meta.get("fps")):g}')
    for badge in [_duration(duration, labels), resolution, audio_label, labels["frames_badge"].format(count=len(frames))]:
        body += "<span>" + _esc(badge) + "</span>"
    body += "</div></header>"
    content = '<p class="insights-lead">' + _esc(insights["thesis"]) + "</p>"
    if insights.get("basis"):
        content += '<p class="insights-basis">' + _esc(insights["basis"]) + "</p>"
    for i, entry in enumerate(_items(insights.get("items")), 1):
        content += '<article class="insight-item"><h3>' + str(i) + ". " + _esc(entry.get("title", "")) + "</h3><p>" + _esc(entry.get("text", "")) + "</p>" + _evidence(entry.get("evidence_times", entry.get("references")), chapters) + "</article>"
    body += _section(labels["insights_title"], content, "whole-video-insights", "whole-video-insights")
    content = _ul(data.get("overview"))
    if data.get("source_scope"):
        content += '<p class="scope">' + _esc(data["source_scope"]) + "</p>"
    body += _section(labels["overview_title"], content)
    if chapters:
        content = "<nav>" + "".join(f'<a href="#chapter-{i}">{_timestamp(c.get("start_seconds"))} {_esc(c.get("title", ""))}</a>' for i, c in enumerate(chapters)) + "</nav>"
        for i, chapter in enumerate(chapters):
            content += f'<article id="chapter-{i}"><p class="time">{_timestamp(chapter.get("start_seconds"))} — {_timestamp(chapter.get("end_seconds"))}</p><h3>{_esc(chapter.get("title", ""))}</h3>' + _ul(chapter.get("points")) + "</article>"
        body += _section(labels["chapters_title"], content)
    if data.get("workflow"):
        content = '<p class="scope">' + _esc(labels["workflow_note"]) + "</p>"
        for i, step in enumerate(_items(data["workflow"]), 1):
            content += '<article class="steps"><p class="step-number">' + _esc(labels["step_label"].format(step=step.get("step", i))) + "</p><h3>" + _esc(step.get("title", "")) + "</h3>" + _ul(step.get("actions")) + _evidence(step.get("evidence_times"), chapters) + "</article>"
        body += _section(labels["workflow_title"], content, classes="workflow-section")
    if data.get("tools"):
        rows = [[_esc(tool.get(key, "")) for key in ["name", "role", "source_evidence", "uncertainty"]] for tool in _items(data["tools"])]
        body += _section(labels["tools_title"], _table([labels[k] for k in ["tool_name", "tool_role", "tool_evidence", "tool_uncertainty"]], rows))
    if data.get("tracks"):
        rows = [[_esc(x.get("name", "")), _esc(x.get("account_count", "")), _esc(x.get("positioning", "")), _ul(x.get("findings"))] for x in _items(data["tracks"])]
        body += _section(labels["tracks_title"], _table([labels[k] for k in ["track_name", "track_count", "track_positioning", "track_findings"]], rows))
    if data.get("key_findings"):
        content = ""
        for finding in _items(data["key_findings"]):
            content += "<article><h3>" + _esc(finding.get("claim", "")) + "</h3><p>" + _esc(finding.get("support", "")) + "</p>" + _evidence(finding.get("evidence_times"), chapters)
            if finding.get("caveat"):
                content += '<p class="caveat">' + _esc(labels["caveat_label"].format(text=_text(finding["caveat"]))) + "</p>"
            content += "</article>"
        body += _section(labels["findings_title"], content)
    if data.get("creator_examples"):
        rows = [[_esc(x.get("name", "")), _esc(x.get("track", "")) + "<br>" + _timestamp(x.get("time_seconds")), _esc(x.get("observation", ""))] for x in _items(data["creator_examples"])]
        body += _section(labels["examples_title"], _table([labels[k] for k in ["example_name", "example_group_time", "example_observation"]], rows))
    for field, title_key in [("narrative_structure", "narrative_title"), ("practical_takeaways", "takeaways_title"), ("limitations", "limitations_title")]:
        if data.get(field):
            body += _section(labels[title_key], _ul(data[field]))
    quality = transcript.get("timingQuality", transcript.get("timing_quality", labels["unknown"]))
    content = "<p>" + _esc(labels["service_method"]) + "</p><p>" + _esc(labels["asr_summary"].format(segments=len(segments), words=len(words), quality=_text(quality))) + "</p><p>" + _esc(labels["timing_note"]) + "</p>"
    response = next((_load(root / name, {}) for name in ["evidence/model-response.json", "model-response.json", "analysis-response.json"] if (root / name).is_file()), {})
    status = response.get("status")
    content += "<p>" + _esc(labels["model_status"].format(status=status) if status else labels["model_status_unrecorded"]) + "</p>"
    if str(verification.get("firstModelStatus", "")).startswith("incomplete") or (root / "analysis-response-partial.json").is_file():
        content += "<p>" + _esc(labels["model_merge_note"]) + "</p>"
    links = []
    for name, title_key in [("transcript.txt", "transcript_link"), ("transcript.srt", "subtitles_link"), ("analysis.json", "analysis_link"), ("verification.json", "verification_link"), ("video_environment.json", "environment_link")]:
        if (root / name).is_file():
            _local_file(root, name)
            links.append(f'<a href="{quote(name)}">{_esc(labels[title_key])}</a>')
    content += '<div class="source-links">' + "".join(links) + "</div>"
    body += _section(labels["methods_title"], content, classes="notice method-list")
    total_bytes = 0
    if frames:
        content = '<p class="scope">' + _esc(labels["frames_note"]) + '</p><div class="gallery">'
        for i, frame in enumerate(frames):
            path = _local_file(root, frame["file"])
            source_bytes, size = _image(path)
            total_bytes += size
            if total_bytes > MAX_EMBEDDED_BYTES:
                raise ValueError("Combined frames exceed the report image budget")
            time_label = _timestamp(frame.get("at"))
            content += f'<details id="frame-{i}"><summary><img loading="lazy" src="{source_bytes}" alt="{_esc(labels["frame_alt"].format(time=time_label))}"><span>{_esc(labels["frame_caption"].format(time=time_label))}</span></summary><p>{_esc(labels["frame_seconds"].format(seconds=frame.get("at", 0)))}</p></details>'
        content += "</div>"
        body += _section(labels["frames_title"], content)
    content = '<p class="scope">' + _esc(labels["transcript_note"]) + "</p><details><summary>" + _esc(labels["transcript_expand"].format(count=len(segments))) + '</summary><div class="transcript">'
    for segment in segments:
        content += '<p><span>' + _timestamp(segment.get("start")) + " — " + _timestamp(segment.get("end")) + "</span> " + _esc(segment.get("text", "")) + "</p>"
    body += _section(labels["transcript_title"], content + "</div></details>")
    body += "<footer><p>" + _esc(labels["footer"]) + "</p></footer>"
    page = _page(_text(title), body, labels)
    check = _Structure()
    check.feed(page)
    broken = [anchor for anchor in check.anchors if anchor not in check.ids]
    if broken or len(check.ids) != len(set(check.ids)) or check.images != len(frames) or check.sections[0] != "whole-video-insights":
        raise ValueError("Report structure verification failed")
    destination = root / "report.html"
    _atomic_text(destination, page)
    result = {"report": str(destination), "reportBytes": len(page.encode("utf-8")), "chapters": len(chapters), "insights": len(_items(insights.get("items"))), "frames": len(frames), "embeddedImages": check.images, "imageEmbeddedOnce": check.images == len(frames), "firstContentSection": check.sections[0], "brokenAnchors": broken, "embeddedImageBytes": total_bytes, "modelStatusRecorded": status}
    _atomic_text(root / "report-render-verification.json", json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return destination


def render_index(output_root: Path, entries: list[dict], locale="zh-CN") -> Path:
    root = Path(output_root).resolve()
    labels = _catalog(locale)
    ordered = sorted(entries, key=lambda item: str(item.get("createdAt", "")), reverse=True)
    groups = defaultdict(list)
    for entry in ordered:
        groups[str(entry.get("videoKey", entry.get("videoName", "")))].append(entry)
    title = labels["index_title"]
    body = '<header><p class="eyebrow">' + _esc(labels["report_brand"]) + "</p><h1>" + _esc(title) + '</h1><p class="subtitle">' + _esc(labels["index_subtitle"].format(videos=len(groups), runs=len(entries))) + '</p><p class="archive-summary">' + _esc(labels["index_description"]) + "</p></header>"
    if not groups:
        body += _section(labels["index_empty"], "")
    links = []
    missing = 0
    for i, group in enumerate(groups.values()):
        latest = group[0]
        heading = latest.get("videoName") or latest.get("videoKey") or labels["report_fallback_title"]
        content = ""
        insight = latest.get("insights")
        if insight:
            content += '<p class="archive-summary">' + _esc(_items(insight)[0]) + "</p>"
        rows = []
        for j, entry in enumerate(group):
            status = str(entry.get("status", ""))
            translated = labels.get("status_" + status, status or labels["unknown"])
            created = _esc(entry.get("createdAt", labels["unknown"]))
            if j == 0:
                created += '<span class="latest-label">' + _esc(labels["index_latest"]) + "</span>"
            report = entry.get("report")
            target = None
            if report:
                candidate = _local_file(root, report, must_exist=False)
                if candidate.suffix.lower() != ".html":
                    raise ValueError("Archive report links must point to HTML files")
                if candidate.is_file():
                    target = quote(candidate.relative_to(root).as_posix(), safe="/")
            if target:
                report_html = f'<a href="{target}">{_esc(labels["index_open"])}</a>'
                links.append(target)
            else:
                report_html = '<span class="missing-report">' + _esc(labels["index_missing_report"]) + "</span>"
                missing += 1
            rows.append([created, '<span class="status-pill">' + _esc(translated) + "</span>", _esc(_duration(entry.get("durationSeconds"), labels)), report_html])
        content += _table([labels[k] for k in ["index_created", "index_status", "index_duration", "index_report"]], rows)
        body += _section(_text(heading), content, f"video-{i}", "archive-card")
    body += "<footer><p>" + _esc(labels["index_note"]) + "</p></footer>"
    destination = root / "index.html"
    page = _page(title, body, labels)
    _atomic_text(destination, page)
    _atomic_text(root / "index-render-verification.json", json.dumps({"videos": len(groups), "runs": len(entries), "localReportLinks": links, "missingReports": missing, "indexBytes": len(page.encode("utf-8"))}, ensure_ascii=False, indent=2) + "\n")
    return destination
