"""labels.py 的 unittest 自测；只读 samples/**，产物一律写到临时目录。"""

import html as html_mod
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import labels

ROOT = Path(__file__).resolve().parent
SAMPLES = ROOT / "samples"
CHARTS = SAMPLES / "charts"
EXPECTED = SAMPLES / "expected"
SAMPLE_NAMES = [
    "01-dense-cluster",
    "02-long-labels",
    "03-edge-hugging",
    "04-mustshow-close",
    "05-low-priority-swarm",
]


def load_chart(name):
    with open(CHARTS / f"{name}.json", encoding="utf-8") as f:
        return json.load(f)


def expected_text(name):
    with open(EXPECTED / f"{name}.txt", encoding="utf-8") as f:
        return f.read()


def run_cli(*args, env=None):
    return subprocess.run(
        [sys.executable, str(ROOT / "labels.py"), *args],
        capture_output=True, text=True, env=env)


def seg_touches_rect(seg, rect):
    x1, y1, x2, y2 = seg
    rx, ry, rw, rh = rect
    return (max(min(x1, x2), rx) <= min(max(x1, x2), rx + rw)
            and max(min(y1, y2), ry) <= min(max(y1, y2), ry + rh))


def segs_touch(a, b):
    return (max(min(a[0], a[2]), min(b[0], b[2]))
            <= min(max(a[0], a[2]), max(b[0], b[2]))
            and max(min(a[1], a[3]), min(b[1], b[3]))
            <= min(max(a[1], a[3]), max(b[1], b[3])))


class PlaceCliTests(unittest.TestCase):
    def test_stdout_matches_expected(self):
        for name in SAMPLE_NAMES:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as td:
                out = Path(td) / "result.json"
                proc = run_cli("place", str(CHARTS / f"{name}.json"), str(out))
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(proc.stdout, expected_text(name))
                self.assertEqual(proc.stderr, "")
                data = json.loads(out.read_text(encoding="utf-8"))
                self.assertEqual(sorted(data),
                                 ["must_show_omitted", "omitted", "placed"])

    def test_page_exit_code_0(self):
        with tempfile.TemporaryDirectory() as td:
            proc = run_cli("page", str(CHARTS / "01-dense-cluster.json"),
                           str(Path(td) / "p.html"))
            self.assertEqual(proc.returncode, 0, proc.stderr)


class InvariantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.charts = {name: load_chart(name) for name in SAMPLE_NAMES}
        cls.results = {name: labels.place(cls.charts[name])
                       for name in SAMPLE_NAMES}

    def test_rects_inside_canvas(self):
        for name, chart in self.charts.items():
            with self.subTest(name=name):
                w = chart["canvas"]["width"]
                h = chart["canvas"]["height"]
                for p in self.results[name]["placed"]:
                    self.assertGreaterEqual(p["x"], 0)
                    self.assertGreaterEqual(p["y"], 0)
                    self.assertLessEqual(p["x"] + p["w"], w)
                    self.assertLessEqual(p["y"] + p["h"], h)

    def test_pairwise_gap_at_least_6(self):
        for name, result in self.results.items():
            with self.subTest(name=name):
                placed = result["placed"]
                for i, a in enumerate(placed):
                    for b in placed[i + 1:]:
                        dx = max(a["x"] - (b["x"] + b["w"]),
                                 b["x"] - (a["x"] + a["w"]))
                        dy = max(a["y"] - (b["y"] + b["h"]),
                                 b["y"] - (a["y"] + a["h"]))
                        self.assertTrue(
                            dx >= 6 or dy >= 6,
                            f"{name}: {a['id']} 与 {b['id']} 间距不足 6")

    def test_no_rect_touches_foreign_leader(self):
        for name, result in self.results.items():
            with self.subTest(name=name):
                leaders = [(p["id"], p["leader"]) for p in result["placed"]
                           if p["leader"] is not None]
                for p in result["placed"]:
                    rect = (p["x"], p["y"], p["w"], p["h"])
                    for owner, seg in leaders:
                        if owner == p["id"]:
                            continue
                        self.assertFalse(
                            seg_touches_rect(seg, rect),
                            f"{name}: {p['id']} 压到 {owner} 的引导线")

    def test_leaders_pairwise_disjoint_and_within_48(self):
        for name, result in self.results.items():
            with self.subTest(name=name):
                segs = [(p["id"], p["leader"]) for p in result["placed"]
                        if p["leader"] is not None]
                for i, (aid, a) in enumerate(segs):
                    length = abs(a[2] - a[0]) + abs(a[3] - a[1])
                    self.assertGreater(length, 0)
                    self.assertLessEqual(length, 48)
                    for bid, b in segs[i + 1:]:
                        self.assertFalse(
                            segs_touch(a, b),
                            f"{name}: {aid} 与 {bid} 的引导线相交")

    def test_positions_come_from_candidate_table(self):
        for name, chart in self.charts.items():
            with self.subTest(name=name):
                by_id = {lab["id"]: lab for lab in chart["labels"]}
                for p in self.results[name]["placed"]:
                    lab = by_id[p["id"]]
                    ax, ay = lab["anchor"]
                    w = labels.text_width(lab["text"])
                    want = {(x, y, None if leader is None else tuple(leader))
                            for (x, y, leader) in labels.candidates(ax, ay, w)}
                    got = (p["x"], p["y"], None if p["leader"] is None
                           else tuple(p["leader"]))
                    self.assertIn(got, want)
                    self.assertEqual(p["w"], w)
                    self.assertEqual(p["h"], 20)

    def test_result_covers_all_in_input_order(self):
        for name, chart in self.charts.items():
            with self.subTest(name=name):
                result = self.results[name]
                ids = [lab["id"] for lab in chart["labels"]]
                placed_ids = [p["id"] for p in result["placed"]]
                self.assertEqual(
                    sorted(placed_ids + result["omitted"]
                           + result["must_show_omitted"]),
                    sorted(ids))
                self.assertEqual(len(set(placed_ids)), len(placed_ids))
                order = {lid: i for i, lid in enumerate(ids)}
                for group in (placed_ids, result["omitted"],
                              result["must_show_omitted"]):
                    keys = [order[lid] for lid in group]
                    self.assertEqual(keys, sorted(keys))
                self.assertEqual(result["must_show_omitted"], [])

    def test_must_show_all_placed(self):
        for name, chart in self.charts.items():
            with self.subTest(name=name):
                placed_ids = {p["id"] for p in self.results[name]["placed"]}
                for lab in chart["labels"]:
                    if lab.get("must_show"):
                        self.assertIn(lab["id"], placed_ids)

    def test_swarm_scale_matches_notes(self):
        result = self.results["05-low-priority-swarm"]
        self.assertEqual(len(result["placed"]), 360)
        self.assertEqual(len(result["omitted"]), 2892 - 360)


class DeterminismTests(unittest.TestCase):
    def test_repeat_runs_byte_identical(self):
        name = "01-dense-cluster"
        with tempfile.TemporaryDirectory() as td:
            runs = []
            for seed in ("0", "1", "2", None):
                out = Path(td) / f"r{seed}.json"
                env = dict(os.environ)
                if seed is not None:
                    env["PYTHONHASHSEED"] = seed
                proc = run_cli("place", str(CHARTS / f"{name}.json"),
                               str(out), env=env)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                runs.append((proc.stdout, out.read_bytes()))
            for other in runs[1:]:
                self.assertEqual(runs[0], other)

    def test_large_chart_within_budget(self):
        name = "05-low-priority-swarm"
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "r.json"
            start = time.monotonic()
            proc = run_cli("place", str(CHARTS / f"{name}.json"), str(out))
            elapsed = time.monotonic() - start
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertLess(elapsed, 5.0)


class ErrorTests(unittest.TestCase):
    def test_bad_usage(self):
        proc = run_cli("place", str(CHARTS / "01-dense-cluster.json"))
        self.assertEqual(proc.returncode, 2)
        self.assertTrue(proc.stderr.startswith("error:"))

    def test_missing_input_file(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "r.json"
            proc = run_cli("place", str(Path(td) / "nope.json"), str(out))
            self.assertEqual(proc.returncode, 2)
            self.assertTrue(proc.stderr.startswith("error:"))
            self.assertFalse(out.exists())

    def test_invalid_json(self):
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "bad.json"
            bad.write_text("{ not json", encoding="utf-8")
            out = Path(td) / "r.json"
            proc = run_cli("place", str(bad), str(out))
            self.assertEqual(proc.returncode, 2)
            self.assertTrue(proc.stderr.startswith("error:"))
            self.assertFalse(out.exists())

    def test_invalid_chart_shape(self):
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "bad.json"
            bad.write_text(json.dumps(
                {"canvas": {"width": -1, "height": 10}, "labels": []}),
                encoding="utf-8")
            out = Path(td) / "r.json"
            proc = run_cli("place", str(bad), str(out))
            self.assertEqual(proc.returncode, 2)
            self.assertTrue(proc.stderr.startswith("error:"))
            self.assertFalse(out.exists())


class PageTests(unittest.TestCase):
    PAGE_NAME = "01-dense-cluster"

    @classmethod
    def setUpClass(cls):
        cls.chart = load_chart(cls.PAGE_NAME)
        cls.result = labels.place(cls.chart)
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "page.html"
            proc = run_cli("page", str(CHARTS / f"{cls.PAGE_NAME}.json"),
                           str(out))
            assert proc.returncode == 0, proc.stderr
            cls.html = out.read_text(encoding="utf-8")

    def test_verdict_counts(self):
        k = len(self.result["omitted"])
        j = len(self.result["must_show_omitted"])
        m = re.search(r'<div id="verdict">(.*?)</div>', self.html, re.S)
        self.assertIsNotNone(m)
        self.assertIn(f"省略 {k} 个", m.group(1))
        self.assertIn(f"必须显示未放下 {j} 个", m.group(1))

    def test_summary_block_matches_expected(self):
        m = re.search(r'<pre id="summary">(.*?)</pre>', self.html, re.S)
        self.assertIsNotNone(m)
        self.assertEqual(html_mod.unescape(m.group(1)),
                         expected_text(self.PAGE_NAME).rstrip("\n"))

    def test_geometry_matches_result(self):
        for p in self.result["placed"]:
            self.assertIn(
                f'<rect class="label" data-id="{p["id"]}" x="{p["x"]}"'
                f' y="{p["y"]}" width="{p["w"]}" height="{p["h"]}"/>',
                self.html)
            if p["leader"] is not None:
                x1, y1, x2, y2 = p["leader"]
                self.assertIn(
                    f'<line class="leader" data-id="{p["id"]}" x1="{x1}"'
                    f' y1="{y1}" x2="{x2}" y2="{y2}"/>', self.html)

    def test_anchor_marks(self):
        placed = {p["id"] for p in self.result["placed"]}
        for lab in self.chart["labels"]:
            ax, ay = lab["anchor"]
            if lab["id"] in placed:
                self.assertIn(
                    f'<circle class="anchor" data-id="{lab["id"]}"'
                    f' cx="{ax}" cy="{ay}" r="3"/>', self.html)
            else:
                self.assertIn(
                    f'<circle class="anchor" data-id="{lab["id"]}"'
                    f' cx="{ax}" cy="{ay}" r="4" fill="none"'
                    f' data-omitted="1"/>', self.html)

    def test_element_counts(self):
        self.assertEqual(self.html.count("<rect class=\"label\""),
                         len(self.result["placed"]))
        self.assertEqual(self.html.count("<line class=\"leader\""),
                         sum(1 for p in self.result["placed"]
                             if p["leader"] is not None))
        omitted = (len(self.result["omitted"])
                   + len(self.result["must_show_omitted"]))
        self.assertEqual(self.html.count("<circle"), len(self.chart["labels"]))
        self.assertEqual(self.html.count('data-omitted="1"'), omitted + 1)
        # +1：样式表里 .anchor[data-omitted="1"] 选择器也算一次

    def test_no_external_refs_or_script(self):
        low = self.html.lower()
        self.assertNotIn("<script", low)
        self.assertNotIn("http://", low)
        self.assertNotIn("https://", low)
        self.assertNotIn("src=", low)
        self.assertNotIn("href=", low)
        self.assertNotIn("fetch(", low)


if __name__ == "__main__":
    unittest.main()
