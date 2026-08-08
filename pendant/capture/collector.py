"""CDP-based recorder: four concurrent channels into one time-correlated stream.

- Network (Tier 1): CDP Network domain, XHR/Fetch traffic, templatized
  URLs, body hashes computed over registry-redacted bodies.
- Structure (Tier 2): identity vector snapshotted by the content
  script at every user-initiated event.
- Events (Tier 3): clicks, inputs, keys, navigation, focus, clipboard,
  dialogs, downloads; monotonic + wall-clock timestamps on every record.
- Pixels (Tier 4): masked screenshot per user event, content-addressed.
- Narration (Tier 5): operator notes anchored at entry time
  (docs/DECISIONS.md D-012).

Redaction happens here, before any write to disk (invariant 3):
password values never arrive (blocked in the content script); registry
matches are replaced before buffering; the blank-last-N-seconds hotkey
(Alt+Shift+X) scrubs buffered values and deletes buffered screenshots.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from playwright.async_api import BrowserContext, CDPSession, Page
from pydantic import ValidationError

from pendant.capture.redaction import REDACTED, RedactionRegistry
from pendant.capture.schema import Event, NetworkInfo, Payload, RunTrace, save_run_trace
from pendant.capture.templatize import templatize_url
from pendant.ir.models import TargetVector

INJECT_JS = (Path(__file__).parent / "inject.js").read_text(encoding="utf-8")

_CAPTURED_RESOURCE_TYPES = {"XHR", "Fetch"}


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


class Collector:
    """Records one demonstration run from a Playwright page."""

    def __init__(
        self,
        run_id: str,
        out_dir: Path,
        registry: RedactionRegistry | None = None,
        *,
        screenshots: bool = True,
        blank_window_s: float = 10.0,
    ) -> None:
        self.run_id = run_id
        self.out_dir = out_dir
        self.registry = registry or RedactionRegistry()
        self.screenshots = screenshots
        self.blank_window_s = blank_window_s
        self.events: list[Event] = []
        self._seq = 0
        self._t0 = time.monotonic()
        self._page: Page | None = None
        self._cdp: CDPSession | None = None
        self._pending_requests: dict[str, dict[str, Any]] = {}
        self._awaiting_body: dict[str, dict[str, Any]] = {}
        self._network_tasks: set[asyncio.Task[None]] = set()
        self.blob_dir = out_dir / "blobs"

    # -- lifecycle ---------------------------------------------------------

    async def attach(self, context: BrowserContext, page: Page) -> None:
        self._page = page
        await context.expose_binding("__pendant_report", self._on_report)
        await context.add_init_script(INJECT_JS)
        cdp = await context.new_cdp_session(page)
        self._cdp = cdp
        cdp.on("Network.requestWillBeSent", self._on_request_will_be_sent)
        cdp.on("Network.responseReceived", self._on_response_received)
        cdp.on("Network.loadingFinished", self._on_loading_finished)
        cdp.on("Page.frameNavigated", self._on_frame_navigated)
        cdp.on("Page.javascriptDialogOpening", self._on_dialog_opening)
        await cdp.send("Network.enable")
        await cdp.send("Page.enable")
        page.on("download", self._on_download)

    async def finalize(self, settle_s: float = 0.5) -> RunTrace:
        """Flush pending network work and write the trace to disk."""
        await asyncio.sleep(settle_s)
        if self._network_tasks:
            await asyncio.gather(*self._network_tasks, return_exceptions=True)
        trace = RunTrace(run_id=self.run_id, events=self.events)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        save_run_trace(trace, self.out_dir / f"{self.run_id}.ndjson")
        return trace

    # -- event plumbing ----------------------------------------------------

    def _now_ms(self) -> float:
        return (time.monotonic() - self._t0) * 1000.0

    def _emit(
        self,
        kind: str,
        tier: int,
        *,
        target: TargetVector | None = None,
        payload: Payload | None = None,
        network: NetworkInfo | None = None,
        screenshot_ref: str | None = None,
        redactions: list[str] | None = None,
    ) -> Event:
        event = Event(
            event_id=f"{self.run_id}-{self._seq:04d}",
            run_id=self.run_id,
            seq=self._seq,
            t_mono_ms=self._now_ms(),
            t_wall=datetime.now(UTC).isoformat(),
            tier=tier,  # type: ignore[arg-type]
            kind=kind,  # type: ignore[arg-type]
            target=target,
            payload=payload,
            network=network,
            screenshot_ref=screenshot_ref,
            redactions=redactions or [],
        )
        self.events.append(event)
        self._seq += 1
        return event

    def _parse_target(self, raw: dict[str, Any] | None) -> TargetVector | None:
        if raw is None:
            return None
        bbox = raw.get("bbox")
        try:
            return TargetVector(
                role=raw.get("role"),
                name=raw.get("name"),
                testid=raw.get("testid"),
                attrs={str(k): str(v) for k, v in (raw.get("attrs") or {}).items()},
                css=raw.get("css"),
                xpath=raw.get("xpath"),
                frame_url=raw.get("frame_url"),
                bbox=tuple(bbox) if bbox else None,
            )
        except ValidationError:
            return None

    # -- content-script channel (Tiers 2+3) --------------------------------

    def _on_report(self, source: Any, message: str) -> None:
        data = json.loads(message)
        kind = data.get("kind")
        if kind == "click":
            event = self._emit("click", 3, target=self._parse_target(data.get("target")))
            self._maybe_screenshot(event)
        elif kind == "input":
            self._handle_input(data)
        elif kind == "key":
            self._emit("key", 3, payload=Payload(keys=[str(k) for k in data.get("keys", [])]))
        elif kind == "focus":
            self._emit("focus", 3, target=self._parse_target(data.get("target")))
        elif kind == "clipboard":
            self._emit(
                "clipboard", 3, payload=Payload(value_class=str(data.get("op", "copy")))
            )
        elif kind == "blank_request":
            self.blank_last(self.blank_window_s)

    def _handle_input(self, data: dict[str, Any]) -> None:
        target = self._parse_target(data.get("target"))
        redactions: list[str] = []
        value: str | None
        value_class = str(data.get("input_type") or "text")
        if data.get("secret"):
            value = None
            value_class = "secret"
            redactions.append("password-type")
        else:
            identity = [
                target.name if target else None,
                target.testid if target else None,
                (target.attrs.get("id") if target else None),
                (target.attrs.get("name") if target else None),
                data.get("autocomplete"),
            ]
            rule = self.registry.match_field(*identity)
            if rule:
                value = REDACTED
                redactions.append(rule)
            else:
                value = data.get("value")
        payload = Payload(value_redacted=value, value_class=value_class)
        # Coalesce successive input events on the same element: typing
        # is one logical input, and the final value is the evidence.
        if (
            self.events
            and self.events[-1].kind == "input"
            and self.events[-1].target is not None
            and target is not None
            and self.events[-1].target.css == target.css
            and self.events[-1].target.frame_url == target.frame_url
        ):
            previous = self.events[-1]
            self.events[-1] = previous.model_copy(
                update={
                    "payload": payload,
                    "redactions": sorted(set(previous.redactions) | set(redactions)),
                    "t_mono_ms": self._now_ms(),
                    "t_wall": datetime.now(UTC).isoformat(),
                }
            )
            return
        event = self._emit("input", 3, target=target, payload=payload, redactions=redactions)
        self._maybe_screenshot(event)

    # -- navigation channel --------------------------------------------------

    def _on_frame_navigated(self, params: dict[str, Any]) -> None:
        frame = params.get("frame", {})
        if frame.get("parentId"):
            return  # subframe navigations are context, not operator steps
        url = frame.get("url", "")
        if not url or url == "about:blank":
            return
        redacted_url, redactions = self.registry.redact_url(url)
        event = self._emit(
            "navigate",
            3,
            payload=Payload(value_redacted=redacted_url, value_class="url"),
            redactions=redactions,
        )
        self._maybe_screenshot(event)

    def _on_dialog_opening(self, params: dict[str, Any]) -> None:
        self._emit(
            "dialog",
            3,
            payload=Payload(
                value_redacted=str(params.get("message", "")),
                value_class=str(params.get("type", "alert")),
            ),
        )

    def _on_download(self, download: Any) -> None:
        self._emit(
            "download", 3, payload=Payload(value_redacted=download.suggested_filename)
        )

    # -- network channel (Tier 1) -------------------------------------------

    def _on_request_will_be_sent(self, params: dict[str, Any]) -> None:
        if params.get("type") not in _CAPTURED_RESOURCE_TYPES:
            return
        request = params.get("request", {})
        self._pending_requests[params["requestId"]] = {
            "method": request.get("method", "GET"),
            "url": request.get("url", ""),
            "post_data": request.get("postData"),
            "content_type": {k.lower(): v for k, v in request.get("headers", {}).items()}.get(
                "content-type"
            ),
        }

    def _on_response_received(self, params: dict[str, Any]) -> None:
        # Emit at responseReceived, not loadingFinished: a response whose
        # body the page never reads (fetch().ok, then navigate away) may
        # never produce loadingFinished, and losing the Tier 1 record of
        # a POST that provably happened would be a capture drop.
        pending = self._pending_requests.pop(params["requestId"], None)
        if pending is None:
            return
        response = params.get("response", {})
        redactions: list[str] = []
        req_sha = None
        if pending.get("post_data"):
            redacted_body, body_redactions = self.registry.redact_body(
                pending["post_data"], pending.get("content_type")
            )
            redactions.extend(body_redactions)
            req_sha = _sha256(redacted_body.encode("utf-8"))
        url, url_redactions = self.registry.redact_url(pending["url"])
        redactions.extend(url_redactions)
        template, url_params = templatize_url(url)
        event = self._emit(
            "network",
            1,
            network=NetworkInfo(
                method=pending["method"],
                url_template=template,
                url_params=url_params,
                status=response.get("status"),
                req_sha=req_sha,
                resp_sha=None,
            ),
            redactions=sorted(set(redactions)),
        )
        self._awaiting_body[params["requestId"]] = {
            "event_id": event.event_id,
            "resp_content_type": response.get("mimeType"),
        }

    def _on_loading_finished(self, params: dict[str, Any]) -> None:
        info = self._awaiting_body.pop(params["requestId"], None)
        if info is None:
            return
        task = asyncio.ensure_future(
            self._attach_response_hash(params["requestId"], info, info["event_id"])
        )
        self._network_tasks.add(task)
        task.add_done_callback(self._network_tasks.discard)

    async def _attach_response_hash(
        self, request_id: str, info: dict[str, Any], event_id: str
    ) -> None:
        if self._cdp is None:
            return
        try:
            body_result = await self._cdp.send(
                "Network.getResponseBody", {"requestId": request_id}
            )
        except Exception:
            return  # body already evicted; hash unavailable
        raw = body_result.get("body", "")
        redacted_resp, resp_redactions = self.registry.redact_body(
            raw, info.get("resp_content_type")
        )
        resp_sha = _sha256(redacted_resp.encode("utf-8"))
        for i, buffered in enumerate(self.events):
            if buffered.event_id == event_id and buffered.network is not None:
                self.events[i] = buffered.model_copy(
                    update={
                        "network": buffered.network.model_copy(update={"resp_sha": resp_sha}),
                        "redactions": sorted(
                            {*buffered.redactions, *(f"resp-{r}" for r in resp_redactions)}
                        ),
                    }
                )
                break

    # -- pixels channel (Tier 4) ----------------------------------------------

    def _maybe_screenshot(self, event: Event) -> None:
        if not self.screenshots or self._page is None:
            return
        task = asyncio.ensure_future(self._screenshot(event))
        self._network_tasks.add(task)
        task.add_done_callback(self._network_tasks.discard)

    async def _screenshot(self, event: Event) -> None:
        if self._page is None:
            return
        try:
            mask = [self._page.locator(", ".join(self.registry.mask_selectors()))]
            data = await self._page.screenshot(
                mask=mask, animations="disabled", timeout=3000
            )
        except Exception:
            return  # mid-navigation; evidence screenshot is best-effort
        digest = _sha256(data)
        self.blob_dir.mkdir(parents=True, exist_ok=True)
        blob_path = self.blob_dir / f"{digest.removeprefix('sha256:')}.png"
        if not blob_path.exists():
            blob_path.write_bytes(data)
        for i, buffered in enumerate(self.events):
            if buffered.event_id == event.event_id:
                self.events[i] = buffered.model_copy(update={"screenshot_ref": digest})
                break

    # -- narration channel (Tier 5) ---------------------------------------------

    def narrate(self, text: str) -> None:
        self._emit(
            "narration", 5, payload=Payload(value_redacted=text, value_class="speech")
        )

    # -- operator hotkey ---------------------------------------------------------

    def blank_last(self, seconds: float) -> int:
        """Blank values and screenshots captured in the last N seconds."""
        cutoff = self._now_ms() - seconds * 1000.0
        blanked = 0
        for i, event in enumerate(self.events):
            if event.t_mono_ms < cutoff:
                continue
            update: dict[str, Any] = {
                "redactions": sorted({*event.redactions, "blank-hotkey"})
            }
            if event.payload is not None and event.payload.value_redacted is not None:
                update["payload"] = event.payload.model_copy(update={"value_redacted": None})
            if event.screenshot_ref:
                blob = self.blob_dir / f"{event.screenshot_ref.removeprefix('sha256:')}.png"
                still_referenced = any(
                    other.screenshot_ref == event.screenshot_ref
                    and other.event_id != event.event_id
                    for other in self.events
                )
                if blob.exists() and not still_referenced:
                    blob.unlink()
                update["screenshot_ref"] = None
            self.events[i] = event.model_copy(update=update)
            blanked += 1
        return blanked
