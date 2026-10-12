#!/usr/bin/env python3
"""E2E check for pi-galley.

Runs the real `pi` binary in a pseudo-terminal of a fixed size, emulates the screen
with pyte, and checks where text lands. No model calls: every scenario resumes a
fixed session (fixture.md as the assistant reply) in an isolated agent dir, with
only this extension loaded.

Core invariant per screen: with every region set in the galley, every non-blank row
stays inside the column (centered or flush left), except the rows of the one wide
diagram that is allowed to break out. Region scenarios then check that tool output,
the input editor and the footer run full width when asked.

Artifacts (overwritten each run): e2e/artifacts/<scenario>/{screen-*.txt,pty.log,
session.jsonl,settings.json} and e2e/artifacts/report.json.
Run via e2e/run.sh (sets up pyte).
"""

from __future__ import annotations

import fcntl
import json
import os
import pty
import select
import shutil
import signal
import struct
import sys
import tempfile
import termios
import time
import uuid

import pyte

HERE = os.path.dirname(os.path.abspath(__file__))
EXTENSION = os.path.join(HERE, "..", "extension.ts")
FIXTURE = open(os.path.join(HERE, "fixture.md")).read()
ARTIFACTS = os.path.join(HERE, "artifacts")
PAD_X = 1  # Pi's default outputPad: assistant text starts one cell inside the column
WIDE_DIAGRAM = "Request received"
TOOL_LINE = "TOOLWIDE " + " ".join(f"word{i:02d}" for i in range(30))  # wider than the column
DEFAULT_MEASURE = 120
# Every region in the galley, so the whole screen obeys the column invariant.
IN_GALLEY = {"PI_GALLEY_TOOLS": "column", "PI_GALLEY_INPUT": "column", "PI_GALLEY_FOOTER": "column"}


# ---------------------------------------------------------------- pi driver


class Pi:
    def __init__(self, out_dir: str, agent_dir: str, session: str, cols: int, rows: int, env: dict):
        self.out_dir, self.cols, self.rows = out_dir, cols, rows
        self.screen = pyte.Screen(cols, rows)
        self.stream = pyte.ByteStream(self.screen)
        self.log = open(os.path.join(out_dir, "pty.log"), "wb")
        child_env = {k: v for k, v in os.environ.items() if not k.startswith(("PI_GALLEY_", "HERDR"))}
        # A dummy key makes the session's model available, so the footer shows it; --offline means no calls.
        child_env.update({"PI_CODING_AGENT_DIR": agent_dir, "TERM": "xterm-256color",
                          "ANTHROPIC_API_KEY": "e2e-dummy-key", **env})
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
            os.chdir(os.path.dirname(session))
            args = ["pi", "-ne", "-e", EXTENSION, "-ns", "-np", "--offline", "--session", session]
            os.execvpe("pi", args, child_env)
        # Answer terminal queries (device attributes, cursor position) like a real terminal.
        self.screen.write_process_input = lambda data: os.write(self.fd, data.encode())

    def pump(self, seconds: float):
        end = time.time() + seconds
        while (left := end - time.time()) > 0:
            ready, _, _ = select.select([self.fd], [], [], left)
            if not ready:
                continue
            try:
                data = os.read(self.fd, 65536)
            except OSError:
                return
            if not data:
                return
            self.log.write(data)
            self.stream.feed(data)

    def wait_for(self, predicate, timeout: float = 30) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            self.pump(0.25)
            if predicate(self.lines()):
                self.pump(0.75)  # let the frame settle
                return True
        return False

    def lines(self) -> list[str]:
        return list(self.screen.display)

    def send(self, text: str):
        for ch in text:  # type like a person so the editor/autocomplete see each key
            os.write(self.fd, ch.encode())
            self.pump(0.02)

    def save(self, name: str):
        with open(os.path.join(self.out_dir, f"screen-{name}.txt"), "w") as f:
            f.write("\n".join(line.rstrip() for line in self.lines()) + "\n")

    def close(self):
        try:
            os.kill(self.pid, signal.SIGTERM)
            self.pump(0.5)
            os.kill(self.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            os.waitpid(self.pid, 0)
        except ChildProcessError:
            pass
        self.log.close()


# ---------------------------------------------------------------- setup


def make_agent_dir(root: str, tui_mode: str, mermaid: str) -> str:
    agent = os.path.join(root, "agent")
    os.makedirs(agent, exist_ok=True)
    settings = {"tuiMode": tui_mode, "quietStartup": True, "markdown": {"mermaid": mermaid}}
    with open(os.path.join(agent, "settings.json"), "w") as f:
        json.dump(settings, f, indent=2)
    return agent


def make_session(root: str, tool_call: bool = False) -> str:
    work = os.path.join(root, "work")
    os.makedirs(work, exist_ok=True)
    now_ms = int(time.time() * 1000)
    iso = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    usage = {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "totalTokens": 0,
             "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "total": 0}}
    model = {"api": "anthropic-messages", "provider": "anthropic", "model": "claude-sonnet-4-5", "usage": usage}
    messages = [{"role": "user", "content": "Explain the delivery pipeline.", "timestamp": now_ms}]
    if tool_call:  # a bash call whose output line is wider than the column, before the reply
        messages += [
            {"role": "assistant", "content": [{"type": "toolCall", "id": "toolu_e2e", "name": "bash",
                                               "arguments": {"command": "cat pipeline.log"}}],
             **model, "stopReason": "toolUse", "timestamp": now_ms},
            {"role": "toolResult", "toolCallId": "toolu_e2e", "toolName": "bash",
             "content": [{"type": "text", "text": TOOL_LINE}], "isError": False, "timestamp": now_ms},
        ]
    messages.append({"role": "assistant", "content": [{"type": "text", "text": FIXTURE}],
                     **model, "stopReason": "stop", "timestamp": now_ms})
    entries = [{"type": "session", "version": 3, "id": str(uuid.uuid4()), "timestamp": iso, "cwd": work}]
    for i, message in enumerate(messages, 1):
        entries.append({"type": "message", "id": f"a{i:07d}", "parentId": f"a{i - 1:07d}" if i > 1 else None,
                        "timestamp": iso, "message": message})
    path = os.path.join(work, "session.jsonl")
    with open(path, "w") as f:
        f.write("".join(json.dumps(e) + "\n" for e in entries))
    return path


# ---------------------------------------------------------------- checks


def column_of(cols: int, max_width: int, align: str = "center") -> tuple[int, int]:
    """[left, right) cells of the column, as the extension computes it."""
    if not max_width or cols <= max_width:
        return 0, cols
    left = 0 if align == "left" else (cols - max_width) // 2
    return left, left + max_width


def escaping_rows(lines: list[str], cols: int, max_width: int, align: str) -> list[tuple[int, int, int, str]]:
    left, right = column_of(cols, max_width, align)
    out = []
    for i, line in enumerate(lines):
        body = line[: cols - 1].rstrip()  # last column may hold the fullscreen scrollbar
        if not body.strip():
            continue
        first = len(body) - len(body.lstrip())
        last = len(body) - 1
        if first < left or last >= right:
            out.append((i, first, last, body.strip()[:60]))
    return out


def start_col(lines: list[str], needle: str) -> int | None:
    for line in lines:
        if needle in line:
            return len(line) - len(line.lstrip())
    return None


def check_layout(lines, cols, max_width, *, expect_breakout: bool, diagrams_on: bool = True, align: str = "center") -> list[str]:
    """Return failure messages (empty = pass)."""
    errors = []
    text = "\n".join(lines)
    left, _ = column_of(cols, max_width, align)
    for marker in ("ALPHA paragraph", "BRAVO paragraph", "CHARLIE paragraph"):
        col = start_col(lines, marker)
        if col is None:
            errors.append(f"{marker!r} not on screen")
        elif col != left + PAD_X:
            errors.append(f"{marker!r} starts at col {col}, expected {left + PAD_X}")
    if diagrams_on and "│ SmallStart" not in text:
        errors.append("narrow diagram not rendered as box art")
    if not diagrams_on and "│ SmallStart" in text:
        errors.append("narrow diagram rendered although Pi's mermaid mode is off")
    for nested in ("Nested one", "Example one"):
        if f"│ {nested}" in text:
            errors.append(f"nested/example diagram {nested!r} was rendered; Pi leaves it as source")
        if f"[{nested}]" not in text:
            errors.append(f"source of {nested!r} missing")

    escapes = escaping_rows(lines, cols, max_width, align)
    wide_rows = [i for i, line in enumerate(lines) if f"│ {WIDE_DIAGRAM}" in line]
    if expect_breakout:
        if not wide_rows:
            errors.append("wide diagram not rendered as box art")
        else:
            box = {wide_rows[0] - 1, wide_rows[0], wide_rows[0] + 1}  # ┌ │ └
            stray = [e for e in escapes if e[0] not in box]
            if stray:
                errors.append(f"rows outside the column (not the wide diagram): {stray}")
            if len(escapes) != 3:
                errors.append(f"expected exactly 3 breakout rows, got {len(escapes)}: {escapes}")
            dia_col = start_col(lines, f"│ {WIDE_DIAGRAM}")
            diagram_width = max(e[2] - e[1] + 1 for e in escapes) if escapes else 0
            expected = min(left, cols - (diagram_width + 2 * PAD_X)) + PAD_X
            if dia_col != expected:
                errors.append(f"wide diagram starts at col {dia_col}, expected {expected}")
    else:
        if f"│ {WIDE_DIAGRAM}" in text:
            errors.append("wide diagram rendered although breakout should not happen here")
        if escapes:
            errors.append(f"rows outside the column: {escapes}")
    return errors


# ---------------------------------------------------------------- scenarios


def ready(lines):
    return any("CHARLIE paragraph" in line for line in lines)


def at_width(cols, max_width, align="center"):
    left, _ = column_of(cols, max_width, align)
    return lambda lines: start_col(lines, "ALPHA paragraph") == left + PAD_X


def scenario(name, *, cols=260, rows=110, tui="fullscreen", mermaid="streaming", env=None, root=None, tool_call=False):
    out = os.path.join(ARTIFACTS, name)
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    root = root or tempfile.mkdtemp(prefix=f"galley-{name}-")
    agent = make_agent_dir(root, tui, mermaid)
    session = make_session(root, tool_call)
    shutil.copy(session, os.path.join(out, "session.jsonl"))
    shutil.copy(os.path.join(agent, "settings.json"), os.path.join(out, "settings.json"))
    return Pi(out, agent, session, cols, rows, env or {}), root, agent


def run_layout(name, *, max_width, expect_breakout, align="center", **kw):
    diagrams_on = kw.get("mermaid", "streaming") != "off"
    env = {**IN_GALLEY, "PI_GALLEY_MEASURE": str(max_width), "PI_GALLEY_ALIGN": align}
    pi, _, _ = scenario(name, env=env, **kw)
    try:
        if not pi.wait_for(ready):
            pi.save("timeout")
            return ["pi never showed the fixture"]
        pi.save("final")
        return check_layout(pi.lines(), pi.cols, max_width, expect_breakout=expect_breakout, diagrams_on=diagrams_on, align=align)
    finally:
        pi.close()


def check_tools(lines, cols, max_width, tools) -> list[str]:
    """Bash output wider than the column: wrapped in the column, or full width with `tools full`."""
    left, right = column_of(cols, max_width)
    errors = []
    for marker in ("ALPHA paragraph", "CHARLIE paragraph"):
        if start_col(lines, marker) != left + PAD_X:
            errors.append(f"{marker!r} starts at col {start_col(lines, marker)}, expected {left + PAD_X}")
    rows = [line[: cols - 1].rstrip() for line in lines if "TOOLWIDE" in line or "cat pipeline.log" in line]
    if len(rows) < 2:
        return errors + [f"tool call or its output not on screen: {rows}"]
    for row in rows:
        first, last = len(row) - len(row.lstrip()), len(row) - 1
        if tools == "full" and first >= left:
            errors.append(f"tool row starts at col {first}, expected the terminal edge: {row.strip()[:60]}")
        if tools == "column" and (first < left or last >= right):
            errors.append(f"tool row spans cols {first}-{last}, outside the column: {row.strip()[:60]}")
    output = next(row for row in rows if "TOOLWIDE" in row)
    if tools == "full" and len(output) - 1 < right:
        errors.append(f"tool output ends at col {len(output) - 1}; expected one unwrapped line past col {right}")
    return errors


def tools_at(cols, max_width, tools):
    left, _ = column_of(cols, max_width)

    def placed(lines):
        col = start_col(lines, "cat pipeline.log")
        return col is not None and (col < left if tools == "full" else col >= left)

    return placed


def run_tools(name, *, env_tools, then=None):
    max_width = 100
    env = {**IN_GALLEY, "PI_GALLEY_MEASURE": str(max_width), "PI_GALLEY_ALIGN": "center", "PI_GALLEY_TOOLS": env_tools}
    pi, _, _ = scenario(name, env=env, tool_call=True)
    try:
        if not pi.wait_for(ready):
            pi.save("timeout")
            return ["pi never showed the fixture"]
        pi.save("1-" + env_tools)
        errors = check_tools(pi.lines(), pi.cols, max_width, env_tools)
        if then:
            pi.send(f"/galley tools {then}\r")
            if not pi.wait_for(tools_at(pi.cols, max_width, then), timeout=10):
                errors.append(f"/galley tools {then} did not move the tool output")
            pi.save("2-" + then)
            errors += [f"after /galley tools {then}: {e}" for e in check_tools(pi.lines(), pi.cols, max_width, then)]
        return errors
    finally:
        pi.close()


def run_live_change_and_reload():
    errors = []
    pi, root, agent = scenario("live-70-then-reload", env=IN_GALLEY)
    config = os.path.join(agent, "pi-galley.json")
    try:
        if not pi.wait_for(ready):
            pi.save("timeout")
            return ["pi never showed the fixture"], root
        pi.save(f"1-default-{DEFAULT_MEASURE}")
        errors += [f"default: {e}" for e in check_layout(pi.lines(), pi.cols, DEFAULT_MEASURE, expect_breakout=True, align="left")]

        pi.send("/galley 70\r")
        if not pi.wait_for(at_width(pi.cols, 70, "left"), timeout=10):
            errors.append("/galley 70 did not change the measure")
        pi.save("2-after-galley-70")
        errors += [f"after /galley 70: {e}" for e in check_layout(pi.lines(), pi.cols, 70, expect_breakout=True, align="left")]
        if (saved := read_json(config).get("measure")) != 70:
            errors.append(f"pi-galley.json holds measure {saved!r}, expected 70")

        pi.send("/galley center\r")
        if not pi.wait_for(at_width(pi.cols, 70, "center"), timeout=10):
            errors.append("/galley center did not move the column")
        pi.save("3-after-galley-center")
        errors += [f"after /galley center: {e}" for e in check_layout(pi.lines(), pi.cols, 70, expect_breakout=True)]
        if (saved := read_json(config)) != {"measure": 70, "align": "center"}:
            errors.append(f"pi-galley.json holds {saved!r}, expected measure 70 and align center")

        pi.send("/reload\r")
        pi.pump(4)
        pi.wait_for(ready, timeout=20)
        pi.save("4-after-reload")
        errors += [f"after /reload: {e}" for e in check_layout(pi.lines(), pi.cols, 70, expect_breakout=True)]
    finally:
        pi.close()
    return errors, root


def read_json(path: str) -> dict:
    try:
        return json.load(open(path))
    except Exception as exc:  # noqa: BLE001
        return {"unreadable": str(exc)}


def run_restart_uses_saved(root):
    """Same agent dir (saved 70, center), bad env vars: expect warnings and the saved settings."""
    out = os.path.join(ARTIFACTS, "restart-saved-bad-env")
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    agent = os.path.join(root, "agent")
    session = make_session(root)
    pi = Pi(out, agent, session, 260, 110, {**IN_GALLEY, "PI_GALLEY_MEASURE": "abc", "PI_GALLEY_ALIGN": "middle"})
    try:
        if not pi.wait_for(ready):
            pi.save("timeout")
            return ["pi never showed the fixture"]
        pi.wait_for(lambda lines: any("ignored" in line for line in lines), timeout=5)
        pi.save("final")
        errors = check_layout(pi.lines(), pi.cols, 70, expect_breakout=True)
        for warning in ('PI_GALLEY_MEASURE="abc" ignored', 'PI_GALLEY_ALIGN="middle" ignored'):
            if not any(warning in line for line in pi.lines()):
                errors.append(f"no warning: {warning}")
        return errors
    finally:
        pi.close()


# ---------------------------------------------------------------- input and footer


def row_span(line: str, cols: int) -> tuple[int, int]:
    body = line[: cols - 1].rstrip()
    return len(body) - len(body.lstrip()), len(body) - 1


def editor_borders(lines: list[str], cols: int) -> list[tuple[int, int]]:
    """Spans of the editor's top and bottom borders: rows of nothing but box lines."""
    out = []
    for line in lines:
        body = line[: cols - 1].strip()
        if len(body) >= MIN_BORDER and set(body) == {"─"}:
            out.append(row_span(line, cols))
    return out


MIN_BORDER = 20


def footer_spans(lines: list[str], cols: int) -> list[tuple[int, int]]:
    """The footer: the working directory, then usage on the left and the model flush right."""
    return [row_span(line, cols) for line in lines if "/work" in line or "claude-sonnet-4-5" in line]


def check_regions(lines, cols, measure, *, align, input_at, footer_at) -> list[str]:
    left, right = column_of(cols, measure, align)
    errors = []
    for name, spans, where in (
        ("editor border", editor_borders(lines, cols), input_at),
        ("footer", footer_spans(lines, cols), footer_at),
    ):
        if not spans:
            errors.append(f"{name} not on screen")
        elif where == "full" and (any(first != 0 for first, _ in spans) or max(last for _, last in spans) < right):
            errors.append(f"{name} spans {spans}; expected full width from col 0 past col {right}")
        elif where == "column" and any(first < left or last >= right for first, last in spans):
            errors.append(f"{name} spans {spans}, outside the column [{left}, {right})")
    if start_col(lines, "ALPHA paragraph") != left + PAD_X:
        errors.append(f"transcript starts at col {start_col(lines, 'ALPHA paragraph')}, expected {left + PAD_X}")
    return errors


def regions_at(cols, measure, align, input_at, footer_at):
    return lambda lines: not check_regions(lines, cols, measure, align=align, input_at=input_at, footer_at=footer_at)


def run_regions(name, *, input_at, footer_at, then=()):
    """Centered galley so full-width rows stand out at col 0; `then` runs /galley commands live."""
    measure = 100
    env = {"PI_GALLEY_MEASURE": str(measure), "PI_GALLEY_ALIGN": "center",
           "PI_GALLEY_INPUT": input_at, "PI_GALLEY_FOOTER": footer_at}
    pi, _, agent = scenario(name, env=env)
    try:
        if not pi.wait_for(ready):
            pi.save("timeout")
            return ["pi never showed the fixture"]
        pi.save("1-start")
        errors = check_regions(pi.lines(), pi.cols, measure, align="center", input_at=input_at, footer_at=footer_at)
        for step, (region, where) in enumerate(then, 2):
            input_at, footer_at = (where, footer_at) if region == "input" else (input_at, where)
            pi.send(f"/galley {region} {where}\r")
            if not pi.wait_for(regions_at(pi.cols, measure, "center", input_at, footer_at), timeout=10):
                errors.append(f"/galley {region} {where} did not move the {region}")
            pi.save(f"{step}-{region}-{where}")
            errors += [f"after /galley {region} {where}: {e}"
                       for e in check_regions(pi.lines(), pi.cols, measure, align="center", input_at=input_at, footer_at=footer_at)]
        if then and (saved := read_json(os.path.join(agent, "pi-galley.json"))) != dict(then):
            errors.append(f"pi-galley.json holds {saved!r}, expected {dict(then)!r}")
        return errors
    finally:
        pi.close()


def run_defaults():
    """No settings at all: measure 120, flush left, tool output, input and footer full width."""
    pi, _, _ = scenario("defaults", tool_call=True)
    try:
        if not pi.wait_for(ready):
            pi.save("timeout")
            return ["pi never showed the fixture"]
        pi.save("final")
        lines, cols = pi.lines(), pi.cols
        errors = []
        for marker in ("ALPHA paragraph", "CHARLIE paragraph"):
            if start_col(lines, marker) != PAD_X:
                errors.append(f"{marker!r} starts at col {start_col(lines, marker)}, expected {PAD_X}")
        prose = [row_span(line, cols) for line in lines if "paragraph" in line]
        if any(last >= DEFAULT_MEASURE for _, last in prose):
            errors.append(f"prose runs past the {DEFAULT_MEASURE}-cell measure: {prose}")
        output = [row_span(line, cols) for line in lines if "TOOLWIDE" in line]
        if len(output) != 1 or output[0][1] < DEFAULT_MEASURE:
            errors.append(f"tool output should be one unwrapped line past col {DEFAULT_MEASURE}: {output}")
        borders = editor_borders(lines, cols)
        if not borders or any(last < DEFAULT_MEASURE for _, last in borders):
            errors.append(f"editor borders should run past col {DEFAULT_MEASURE}: {borders}")
        return errors
    finally:
        pi.close()


def main() -> int:
    if not shutil.which("pi"):
        print("pi not found on PATH", file=sys.stderr)
        return 2
    os.makedirs(ARTIFACTS, exist_ok=True)
    results: dict[str, list[str]] = {}

    results["defaults"] = run_defaults()
    results["fullscreen-100"] = run_layout("fullscreen-100", max_width=100, expect_breakout=True)
    results["regular-100"] = run_layout("regular-100", max_width=100, expect_breakout=True, tui="regular")
    results["left-100"] = run_layout("left-100", max_width=100, expect_breakout=True, align="left")
    results["tools-full-100"] = run_tools("tools-full-100", env_tools="full")
    results["tools-column-then-full"] = run_tools("tools-column-then-full", env_tools="column", then="full")
    results["regions-full"] = run_regions("regions-full", input_at="full", footer_at="full")
    results["regions-column-then-full"] = run_regions(
        "regions-column-then-full", input_at="column", footer_at="column", then=(("input", "full"), ("footer", "full")))
    results["mermaid-off-100"] = run_layout("mermaid-off-100", max_width=100, expect_breakout=False, mermaid="off")
    results["narrow-terminal-90"] = run_layout("narrow-terminal-90", max_width=100, expect_breakout=False, cols=90)
    errors, root = run_live_change_and_reload()
    results["live-70-then-reload"] = errors
    results["restart-saved-bad-env"] = run_restart_uses_saved(root)

    with open(os.path.join(ARTIFACTS, "report.json"), "w") as f:
        json.dump({"pi": shutil.which("pi"), "results": results}, f, indent=2)
    failed = 0
    for name, errs in results.items():
        print(f"{'PASS' if not errs else 'FAIL'}  {name}")
        for e in errs:
            print(f"      - {e}")
        failed += bool(errs)
    print(f"\nArtifacts: {ARTIFACTS}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
