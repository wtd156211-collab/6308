#!/usr/bin/env python3
"""图表标注避让：给一批锚点与标签，算出每个标签放哪儿、哪些省掉。

库与命令行入口合一。用法：

    python3 labels.py place <图表.json> <结果.json>
    python3 labels.py page  <图表.json> <页面.html>

口径与公式见 README.md 第二节，此处不重复注释，实现严格照那个来。
"""

import html
import json
import sys

LABEL_H = 20
MIN_GAP = 6
NEAR_OFFSET = 8
FAR_LENGTHS = (20, 32, 44)
HALF_WIDTH = 7
FULL_WIDTH = 14
FULLWIDTH_MIN = 0x2E80
GRID_CELL = 64


def text_width(text):
    return 8 + sum(HALF_WIDTH if ord(ch) < FULLWIDTH_MIN else FULL_WIDTH
                   for ch in text)


def candidates(ax, ay, w):
    """按写死的顺序产出 20 个候选：(x, y, leader)，leader 为 None 或四元组。"""
    half = w // 2
    yield (ax + NEAR_OFFSET, ay - 10, None)                      # 1 E
    yield (ax - NEAR_OFFSET - w, ay - 10, None)                  # 2 W
    yield (ax - half, ay - 28, None)                             # 3 N
    yield (ax - half, ay + 8, None)                              # 4 S
    yield (ax + NEAR_OFFSET, ay - 28, None)                      # 5 NE
    yield (ax - NEAR_OFFSET - w, ay - 28, None)                  # 6 NW
    yield (ax + NEAR_OFFSET, ay + 8, None)                       # 7 SE
    yield (ax - NEAR_OFFSET - w, ay + 8, None)                   # 8 SW
    for length in FAR_LENGTHS:
        yield (ax + length, ay - 10, (ax, ay, ax + length, ay))          # E@l
        yield (ax - length - w, ay - 10, (ax, ay, ax - length, ay))      # W@l
        yield (ax - half, ay - length - 20, (ax, ay, ax, ay - length))   # N@l
        yield (ax - half, ay + length, (ax, ay, ax, ay + length))        # S@l


def _boxes_touch(ax0, ay0, ax1, ay1, bx0, by0, bx1, by1):
    """两个闭区域是否有公共点（压边也算）。"""
    return ax0 <= bx1 and bx0 <= ax1 and ay0 <= by1 and by0 <= ay1


class _Index:
    """已放下的标签矩形与引导线，用均匀网格做空间索引。"""

    def __init__(self, cell=GRID_CELL):
        self.cell = cell
        self.rects = []
        self.leaders = []
        self.rect_grid = {}
        self.leader_grid = {}

    def _cells(self, x0, y0, x1, y1):
        cell = self.cell
        for cx in range(x0 // cell, x1 // cell + 1):
            for cy in range(y0 // cell, y1 // cell + 1):
                yield (cx, cy)

    def add_rect(self, rect):
        x, y, w, h = rect
        self.rects.append(rect)
        for key in self._cells(x, y, x + w, y + h):
            self.rect_grid.setdefault(key, []).append(rect)

    def add_leader(self, seg):
        x1, y1, x2, y2 = seg
        self.leaders.append(seg)
        for key in self._cells(min(x1, x2), min(y1, y2),
                               max(x1, x2), max(y1, y2)):
            self.leader_grid.setdefault(key, []).append(seg)

    def rect_blocked(self, x, y, w, h):
        # 条件 2：与任一已放下标签的净距 dx、dy 都小于 6 即冲突
        margin = MIN_GAP - 1
        for key in self._cells(x - margin, y - margin,
                               x + w + margin, y + h + margin):
            for (rx, ry, rw, rh) in self.rect_grid.get(key, ()):
                dx = max(x - (rx + rw), rx - (x + w))
                dy = max(y - (ry + rh), ry - (y + h))
                if dx < MIN_GAP and dy < MIN_GAP:
                    return True
        # 条件 3：矩形与任一已放下引导线没有公共点
        for key in self._cells(x, y, x + w, y + h):
            for (x1, y1, x2, y2) in self.leader_grid.get(key, ()):
                if _boxes_touch(x, y, x + w, y + h,
                                min(x1, x2), min(y1, y2),
                                max(x1, x2), max(y1, y2)):
                    return True
        return False

    def leader_blocked(self, x1, y1, x2, y2):
        # 条件 4：引导线与已放下标签矩形、已放下引导线都没有公共点
        lx0, ly0 = min(x1, x2), min(y1, y2)
        lx1, ly1 = max(x1, x2), max(y1, y2)
        for key in self._cells(lx0, ly0, lx1, ly1):
            for (rx, ry, rw, rh) in self.rect_grid.get(key, ()):
                if _boxes_touch(lx0, ly0, lx1, ly1, rx, ry, rx + rw, ry + rh):
                    return True
            for (ox1, oy1, ox2, oy2) in self.leader_grid.get(key, ()):
                if _boxes_touch(lx0, ly0, lx1, ly1,
                                min(ox1, ox2), min(oy1, oy2),
                                max(ox1, ox2), max(oy1, oy2)):
                    return True
        return False


def validate_chart(chart):
    if not isinstance(chart, dict):
        raise ValueError("图表必须是 JSON 对象")
    canvas = chart.get("canvas")
    if not isinstance(canvas, dict):
        raise ValueError("缺少 canvas 对象")
    width = canvas.get("width")
    height = canvas.get("height")
    if not (isinstance(width, int) and isinstance(height, int)
            and width > 0 and height > 0):
        raise ValueError("canvas.width/height 必须是正整数")
    labels = chart.get("labels")
    if not isinstance(labels, list):
        raise ValueError("缺少 labels 数组")
    seen = set()
    for i, lab in enumerate(labels):
        if not isinstance(lab, dict):
            raise ValueError(f"labels[{i}] 必须是对象")
        lid = lab.get("id")
        if not isinstance(lid, str) or not lid:
            raise ValueError(f"labels[{i}].id 必须是非空字符串")
        if lid in seen:
            raise ValueError(f"id 重复: {lid}")
        seen.add(lid)
        text = lab.get("text")
        if not isinstance(text, str) or not text:
            raise ValueError(f"labels[{i}].text 必须是非空字符串")
        anchor = lab.get("anchor")
        if not (isinstance(anchor, list) and len(anchor) == 2
                and all(isinstance(v, int) for v in anchor)):
            raise ValueError(f"labels[{i}].anchor 必须是 [x, y] 整数对")
        if not (0 <= anchor[0] <= width and 0 <= anchor[1] <= height):
            raise ValueError(f"labels[{i}].anchor 超出画布")
        priority = lab.get("priority", 5)
        if not (isinstance(priority, int) and 1 <= priority <= 9):
            raise ValueError(f"labels[{i}].priority 必须在 1..9")
    return chart


def place(chart):
    """核心流程：排序 -> 依序取第一个可用候选 -> 记录结果。"""
    validate_chart(chart)
    width = chart["canvas"]["width"]
    height = chart["canvas"]["height"]
    labels = chart["labels"]
    order = sorted(
        range(len(labels)),
        key=lambda i: (not bool(labels[i].get("must_show", False)),
                       labels[i].get("priority", 5), i))
    index = _Index()
    placed = {}
    omitted = []
    missed = []
    for i in order:
        lab = labels[i]
        ax, ay = lab["anchor"]
        w = text_width(lab["text"])
        found = None
        for (x, y, leader) in candidates(ax, ay, w):
            if x < 0 or y < 0 or x + w > width or y + LABEL_H > height:
                continue
            if index.rect_blocked(x, y, w, LABEL_H):
                continue
            if leader is not None and index.leader_blocked(*leader):
                continue
            found = (x, y, leader)
            break
        if found is None:
            (missed if lab.get("must_show", False) else omitted).append(i)
            continue
        x, y, leader = found
        placed[i] = {"id": lab["id"], "x": x, "y": y, "w": w, "h": LABEL_H,
                     "leader": list(leader) if leader is not None else None}
        index.add_rect((x, y, w, LABEL_H))
        if leader is not None:
            index.add_leader(leader)
    return {
        "placed": [placed[i] for i in sorted(placed)],
        "omitted": [labels[i]["id"] for i in sorted(omitted)],
        "must_show_omitted": [labels[i]["id"] for i in sorted(missed)],
    }


def summary_lines(result):
    lines = []
    for p in result["placed"]:
        if p["leader"] is None:
            lines.append("place {id} {x} {y} {w} {h}".format(**p))
        else:
            x1, y1, x2, y2 = p["leader"]
            lines.append("lead {id} {x} {y} {w} {h} {x1} {y1} {x2} {y2}"
                         .format(x1=x1, y1=y1, x2=x2, y2=y2, **p))
    for lid in result["omitted"]:
        lines.append(f"omit {lid}")
    for lid in result["must_show_omitted"]:
        lines.append(f"miss {lid}")
    return lines


PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>图表标注避让</title>
<style>
body {{ margin: 16px; font-family: sans-serif; color: #222; }}
#verdict {{ font-size: 15px; font-weight: bold; margin-bottom: 8px; }}
#summary {{ font-size: 12px; background: #f6f6f6; padding: 8px;
  overflow: auto; max-height: 220px; }}
#chart {{ display: block; border: 1px solid #ccc; background: #fff; }}
.anchor {{ fill: #333; }}
.anchor[data-omitted="1"] {{ fill: none; stroke: #c00; stroke-width: 1.5; }}
.label {{ fill: #fff; stroke: #333; stroke-width: 1; }}
.label-text {{ font-family: monospace; font-size: 12px; fill: #111; }}
.leader {{ stroke: #999; stroke-width: 1; }}
</style>
</head>
<body>
<div id="verdict">省略 {k} 个，必须显示未放下 {j} 个</div>
<pre id="summary">{summary}</pre>
<svg id="chart" viewBox="0 0 {width} {height}" width="{width}" height="{height}">
{elements}
</svg>
</body>
</html>
"""


def render_page(chart, result, lines):
    width = chart["canvas"]["width"]
    height = chart["canvas"]["height"]
    placed_by_id = {p["id"]: p for p in result["placed"]}
    leaders = []
    boxes = []
    anchors = []
    for lab in chart["labels"]:
        lid = html.escape(lab["id"], quote=True)
        ax, ay = lab["anchor"]
        p = placed_by_id.get(lab["id"])
        if p is None:
            anchors.append(
                f'<circle class="anchor" data-id="{lid}" cx="{ax}" cy="{ay}"'
                f' r="4" fill="none" data-omitted="1"/>')
            continue
        if p["leader"] is not None:
            x1, y1, x2, y2 = p["leader"]
            leaders.append(
                f'<line class="leader" data-id="{lid}" x1="{x1}" y1="{y1}"'
                f' x2="{x2}" y2="{y2}"/>')
        boxes.append(
            f'<rect class="label" data-id="{lid}" x="{p["x"]}" y="{p["y"]}"'
            f' width="{p["w"]}" height="{p["h"]}"/>')
        text = html.escape(lab["text"])
        boxes.append(
            f'<text class="label-text" data-id="{lid}" x="{p["x"] + 4}"'
            f' y="{p["y"] + 15}">{text}</text>')
        anchors.append(
            f'<circle class="anchor" data-id="{lid}" cx="{ax}" cy="{ay}"'
            f' r="3"/>')
    elements = "\n".join(leaders + boxes + anchors)
    summary = html.escape("\n".join(lines))
    return PAGE_TEMPLATE.format(
        k=len(result["omitted"]), j=len(result["must_show_omitted"]),
        summary=summary, width=width, height=height, elements=elements)


def _fail(message):
    print(f"error: {message}", file=sys.stderr)
    return 2


def main(argv):
    if len(argv) != 4 or argv[1] not in ("place", "page"):
        return _fail("用法: python3 labels.py place|page <图表.json> <输出文件>")
    command, chart_path, out_path = argv[1], argv[2], argv[3]
    try:
        with open(chart_path, "r", encoding="utf-8") as f:
            chart = json.load(f)
    except OSError as exc:
        return _fail(f"打不开图表文件 {chart_path}: {exc.strerror or exc}")
    except json.JSONDecodeError as exc:
        return _fail(f"图表 JSON 不合法: {exc}")
    except UnicodeDecodeError as exc:
        return _fail(f"图表文件不是合法 UTF-8: {exc}")
    try:
        result = place(chart)
    except ValueError as exc:
        return _fail(f"图表内容不合法: {exc}")
    lines = summary_lines(result)
    try:
        if command == "place":
            payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        else:
            payload = render_page(chart, result, lines)
        with open(out_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(payload)
    except OSError as exc:
        return _fail(f"写不出产物 {out_path}: {exc.strerror or exc}")
    if command == "place":
        sys.stdout.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
