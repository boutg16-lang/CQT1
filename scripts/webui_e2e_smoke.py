# -*- coding: utf-8 -*-
"""End-to-end WebUI smoke test driven by a real browser (Playwright).

Why this exists: the unit suite covers the WebUI *logic* but nothing proves the
real server boots and every tab renders in a browser on a real machine. This
tool launches (optionally) the project WebUI, drives a headless/headed Chrome
through every tab, screenshots each one, and collects console/page errors into
a machine-readable report — so a failure on Windows/GPU boxes is diagnosable
without a screen-share.

It is safe by design: it never publishes, uploads, or touches credentials; it
only loads the local UI and clicks tabs.

Usage (from the project root)::

    # 1) start the WebUI yourself, then:
    python -m scripts.webui_e2e_smoke --url http://127.0.0.1:7860

    # 2) or let the harness launch it:
    python -m scripts.webui_e2e_smoke --launch

    # headed (watch it click on your own machine):
    python -m scripts.webui_e2e_smoke --launch --headed

Requires Playwright (``pip install playwright``); it uses the system Chrome via
``channel="chrome"`` so no browser download is needed.

Exit codes: 0 = healthy, 1 = page/console errors, 2 = Playwright missing,
3 = WebUI never became reachable.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

DEFAULT_URL = "http://127.0.0.1:7860"
DEFAULT_OUT = "webui_smoke"
SETTLE_MS = 1500


def _wait_for_http(url: str, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                if response.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(2)
    return False


def _launch_webui(out_dir: str, url: str, timeout: float) -> subprocess.Popen:
    """Start the project WebUI in a network-free, preflight-free subprocess."""
    env = dict(os.environ)
    env["VIRALCUTTER_DISABLE_SAFETY_WATCHER"] = "1"
    env["VIRALCUTTER_SKIP_PREFLIGHT"] = "1"
    log_path = os.path.join(out_dir, "webui.log")
    log = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-c",
         "from webui.app import _launch; import sys; sys.exit(_launch(['--preflight', 'off']))"],
        stdout=log, stderr=subprocess.STDOUT, env=env,
    )
    print(f"[webui-e2e] launched WebUI (pid {proc.pid}) → {log_path}")
    if not _wait_for_http(url, timeout):
        proc.terminate()
        raise SystemExit(f"[webui-e2e] WebUI did not answer at {url} within {timeout:.0f}s "
                         f"(see {log_path})")
    return proc


def run(url: str, out_dir: str, *, headless: bool = True, tab_timeout_ms: int = 20000,
        max_tabs: int = 20) -> dict:
    from playwright.sync_api import sync_playwright  # local import: optional dependency

    os.makedirs(out_dir, exist_ok=True)
    console_errors: list[dict] = []
    page_errors: list[str] = []
    tabs: list[dict] = []
    report: dict = {"url": url, "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "headless": headless, "ok": False, "checks": {}}

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=headless,
                                    args=["--no-sandbox", "--disable-dev-shm-usage"])
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda msg: console_errors.append({"type": msg.type, "text": msg.text})
                if msg.type == "error" else None)
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))

        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_selector(".gradio-container", timeout=tab_timeout_ms)
            report["checks"]["gradio_container"] = True
        except Exception:
            report["checks"]["gradio_container"] = False
        page.wait_for_timeout(SETTLE_MS)

        report["title"] = page.title()
        home_png = os.path.join(out_dir, "tab_00_home.png")
        page.screenshot(path=home_png, full_page=False)
        report["screenshots"] = [os.path.basename(home_png)]

        # Gradio renders each top-level tab as a role=tab button.
        tab_buttons = page.query_selector_all("button[role='tab']")[:max_tabs]
        report["checks"]["tab_count"] = len(tab_buttons)
        for index, button in enumerate(tab_buttons, start=1):
            label = (button.inner_text() or "").strip().replace("\n", " ")[:80]
            entry = {"index": index, "label": label}
            try:
                button.click(timeout=tab_timeout_ms)
                page.wait_for_timeout(SETTLE_MS)
                png = os.path.join(out_dir, f"tab_{index:02d}.png")
                page.screenshot(path=png, full_page=False)
                report["screenshots"].append(os.path.basename(png))
                entry["clicked"] = True
            except Exception as exc:
                entry["clicked"] = False
                entry["error"] = str(exc)[:200]
            tabs.append(entry)

        browser.close()

    report["tabs"] = tabs
    report["console_errors"] = console_errors
    report["page_errors"] = page_errors
    report["checks"]["no_console_errors"] = not console_errors
    report["checks"]["no_page_errors"] = not page_errors
    report["ok"] = bool(
        report["checks"].get("gradio_container")
        and not console_errors and not page_errors
        and all(t.get("clicked") for t in tabs)
    )

    with open(os.path.join(out_dir, "report.json"), "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    _write_markdown(report, os.path.join(out_dir, "report.md"))
    return report


def _write_markdown(report: dict, path: str) -> None:
    lines = [
        "# WebUI E2E smoke report",
        "",
        f"- URL: {report['url']}",
        f"- Result: {'✅ OK' if report['ok'] else '❌ FAILED'}",
        f"- Title: {report.get('title', '')!r}",
        f"- Tabs clicked: {sum(1 for t in report.get('tabs', []) if t.get('clicked'))}/{len(report.get('tabs', []))}",
        f"- Console errors: {len(report.get('console_errors', []))}",
        f"- Page errors: {len(report.get('page_errors', []))}",
        "",
    ]
    if report.get("console_errors"):
        lines += ["## Console errors", ""] + [f"- {e['text'][:300]}" for e in report["console_errors"]]
    if report.get("page_errors"):
        lines += ["", "## Page errors", ""] + [f"- {e[:300]}" for e in report["page_errors"]]
    if report.get("tabs"):
        lines += ["", "## Tabs", ""] + [
            f"- {'✅' if t.get('clicked') else '❌'} {t.get('label') or t['index']}"
            for t in report["tabs"]]
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Browser-driven WebUI smoke test.")
    parser.add_argument("--url", default=DEFAULT_URL, help="WebUI URL (default %(default)s)")
    parser.add_argument("--out", default=DEFAULT_OUT, help="Output directory (default %(default)s)")
    parser.add_argument("--launch", action="store_true", help="Launch the WebUI before testing")
    parser.add_argument("--headed", action="store_true", help="Show the browser window")
    parser.add_argument("--launch-timeout", type=float, default=180.0, help="Seconds to wait for boot")
    parser.add_argument("--max-tabs", type=int, default=20)
    args = parser.parse_args(argv)

    try:
        import playwright  # noqa: F401
    except ImportError:
        print("[webui-e2e] Playwright is not installed. Run:\n"
              "    pip install playwright", file=sys.stderr)
        return 2

    proc = None
    if args.launch:
        proc = _launch_webui(args.out, args.url, args.launch_timeout)
    try:
        report = run(args.url, args.out, headless=not args.headed, max_tabs=args.max_tabs)
    except SystemExit as exc:
        return 3 if proc is not None else int(exc.code or 3)
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except Exception:
                proc.kill()

    print(f"[webui-e2e] {'OK' if report['ok'] else 'FAILED'} — report: "
          f"{os.path.join(args.out, 'report.md')}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
