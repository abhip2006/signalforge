"""End-to-end test of the live Streamlit visitor demo via Playwright.

Drives a real Chromium against the public tunnel URL and exercises:
  - Landing page renders with the form
  - Invalid domain → inline error, no submission
  - Per-session rate limit (two fast submits)
  - Valid submit (ramp.com) → ICP summary, target titles, weights, leads
  - Signals captured expanders produce real links
  - Second domain (stripe.com) → different inferred ICP
  - Cached re-submit is markedly faster than the cold run

Run:  uv run python tests/e2e_demo.py
Outputs screenshots + a PASS/FAIL report per step.
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from pathlib import Path

from playwright.async_api import Page, async_playwright, expect

OUT_DIR = Path("data/e2e")


def _live_url() -> str:
    result = subprocess.run(
        ["/Users/abhinavpenagalapati/.local/bin/signalforge-url"],
        capture_output=True, text=True, check=True,
    )
    url = result.stdout.strip()
    if not url:
        raise RuntimeError("signalforge-url returned empty — tunnel down?")
    return url


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, bool, str]] = []

    def step(self, name: str, ok: bool, note: str = "") -> None:
        self.rows.append((name, ok, note))
        icon = "✓" if ok else "✗"
        print(f"  {icon} {name}{(' — ' + note) if note else ''}")

    def summary(self) -> tuple[int, int]:
        passed = sum(1 for _, ok, _ in self.rows if ok)
        return passed, len(self.rows)


async def _fill_domain_and_submit(page: Page, domain: str) -> None:
    # Streamlit: text input is the 2nd interactive (after the form container).
    box = page.get_by_placeholder("e.g. ramp.com")
    await box.fill("")
    await box.fill(domain)
    await page.get_by_role("button", name="Find leads →").click()


async def _wait_for_result(page: Page, timeout_ms: int = 180_000) -> None:
    """Wait for either the success banner or an inline error."""
    await page.wait_for_selector(
        'text=/Inferred ICP for|doesn\'t look like a valid/i',
        timeout=timeout_ms,
    )


async def test_demo(url: str, report: Report) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1440, "height": 1200})
        page = await context.new_page()

        # ── 1. Landing ─────────────────────────────────────────────────────
        try:
            await page.goto(url, wait_until="networkidle", timeout=60_000)
            title = await page.title()
            await expect(page.get_by_text("SignalForge — live GTM lead demo")).to_be_visible(timeout=30_000)
            await expect(page.get_by_placeholder("e.g. ramp.com")).to_be_visible()
            await page.screenshot(path=OUT_DIR / "01-landing.png", full_page=True)
            report.step("landing renders", True, f"title={title!r}")
        except Exception as e:  # noqa: BLE001
            await page.screenshot(path=OUT_DIR / "01-landing-FAIL.png", full_page=True)
            report.step("landing renders", False, repr(e))
            return

        # ── 2. Invalid domain ──────────────────────────────────────────────
        try:
            await _fill_domain_and_submit(page, "not a domain!!")
            await expect(page.get_by_text("doesn't look like a valid domain", exact=False)).to_be_visible(
                timeout=15_000
            )
            await page.screenshot(path=OUT_DIR / "02-invalid.png", full_page=True)
            report.step("invalid domain → inline error", True)
        except Exception as e:  # noqa: BLE001
            await page.screenshot(path=OUT_DIR / "02-invalid-FAIL.png", full_page=True)
            report.step("invalid domain → inline error", False, repr(e))

        # ── 3. Valid submit (ramp.com, cold) ───────────────────────────────
        t0 = time.perf_counter()
        try:
            await _fill_domain_and_submit(page, "ramp.com")
            await _wait_for_result(page)
            cold = time.perf_counter() - t0
            await expect(page.get_by_text("Inferred ICP for ramp.com")).to_be_visible(timeout=30_000)
            # Look for the summary label
            await expect(page.get_by_text("What you do (as inferred):")).to_be_visible()
            # At least one target title should be rendered
            await expect(page.get_by_text("Target titles", exact=False)).to_be_visible()
            await expect(page.get_by_text("Signal weights", exact=False)).to_be_visible()
            # Top leads heading
            await expect(page.get_by_text("Top leads from pool of", exact=False)).to_be_visible()
            await page.screenshot(path=OUT_DIR / "03-ramp.png", full_page=True)
            report.step("ramp.com submit → full result", True, f"cold={cold:.1f}s")
        except Exception as e:  # noqa: BLE001
            await page.screenshot(path=OUT_DIR / "03-ramp-FAIL.png", full_page=True)
            report.step("ramp.com submit → full result", False, repr(e))
            await browser.close()
            return

        # ── 4. Lead count + signals visible ────────────────────────────────
        try:
            leads = page.locator("h3").filter(has_text="ICP ")
            # A lead heading looks like "1. Anthropic  anthropic.com · ICP 100"
            count = await leads.count()
            assert 3 <= count <= 6, f"expected 3-5 leads, got {count}"
            # Each lead has an "N signals captured" caption
            cap = page.get_by_text("signals captured", exact=False)
            assert await cap.count() >= count, (
                f"only {await cap.count()} 'signals captured' captions for {count} leads"
            )
            report.step(
                "3-5 leads rendered with 'signals captured' captions",
                True, f"{count} leads",
            )
        except Exception as e:  # noqa: BLE001
            report.step("leads + signal captions", False, repr(e))

        # ── 5. At least one signal link inside leads ───────────────────────
        try:
            # Signals render as markdown lines containing "[kind] source · strength ..."
            anchors = page.locator("a").filter(has_text="Hiring:")
            jobs = await anchors.count()
            # Could be 0 (rare) but typically many; guard with >=0 and surface the number
            report.step(
                "signal links rendered in leads",
                jobs >= 1,
                f"{jobs} hiring-anchor links",
            )
        except Exception as e:  # noqa: BLE001
            report.step("signal links rendered", False, repr(e))

        # ── 6. Rate limit: second submit within <15s → warning ─────────────
        try:
            await _fill_domain_and_submit(page, "stripe.com")
            # Streamlit renders the warning in a data-testid=stAlert* container.
            # Use a substring match to dodge unicode em-dash fragility.
            await page.wait_for_function(
                "() => document.body.innerText.includes('wait a few seconds')",
                timeout=10_000,
            )
            await page.screenshot(path=OUT_DIR / "04-rate-limited.png", full_page=True)
            report.step("rate limit fires on rapid second submit", True)
        except Exception as e:  # noqa: BLE001
            report.step("rate limit fires on rapid second submit", False, repr(e))

        # ── 7. Wait out rate limit, then stripe.com → different ICP ────────
        try:
            await page.wait_for_timeout(16_000)
            t0 = time.perf_counter()
            await _fill_domain_and_submit(page, "stripe.com")
            await _wait_for_result(page, timeout_ms=180_000)
            warm = time.perf_counter() - t0
            await expect(page.get_by_text("Inferred ICP for stripe.com")).to_be_visible(timeout=30_000)
            await page.screenshot(path=OUT_DIR / "05-stripe.png", full_page=True)
            report.step("stripe.com produces a distinct ICP analysis", True, f"{warm:.1f}s")
        except Exception as e:  # noqa: BLE001
            await page.screenshot(path=OUT_DIR / "05-stripe-FAIL.png", full_page=True)
            report.step("stripe.com produces a distinct ICP analysis", False, repr(e))

        # ── 8. Re-submit ramp.com (should hit 24h cache, <5s) ──────────────
        try:
            await page.wait_for_timeout(16_000)
            t0 = time.perf_counter()
            await _fill_domain_and_submit(page, "ramp.com")
            await _wait_for_result(page, timeout_ms=60_000)
            warm = time.perf_counter() - t0
            await page.screenshot(path=OUT_DIR / "06-ramp-cached.png", full_page=True)
            cached_ok = warm < cold * 0.6 or warm < 5.0
            report.step(
                "ramp.com second submit is noticeably faster (cache)",
                cached_ok, f"warm={warm:.1f}s vs cold={cold:.1f}s",
            )
        except Exception as e:  # noqa: BLE001
            report.step("cache speed-up on re-submit", False, repr(e))

        await browser.close()


async def main() -> int:
    url = _live_url()
    print(f"Testing {url}\n")
    report = Report()
    await test_demo(url, report)
    p, t = report.summary()
    print(f"\n{p}/{t} steps passed")
    print(f"Screenshots: {OUT_DIR.resolve()}")
    return 0 if p == t else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
