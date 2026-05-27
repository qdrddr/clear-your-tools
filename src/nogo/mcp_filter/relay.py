import asyncio
import json
import logging
import os
import psutil
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)

RELAY_IDENTITY = "sca-aggregator-relay-server-helper"
RELAY_VERSION = "1.1.0"

class RelayServer:
    def __init__(self, debug: bool = False, only_relay: bool = False) -> None:
        self.app = FastAPI()
        # Primary storage: ppid -> event queue for SSE
        self.sessions: dict[str, asyncio.Queue] = {}
        # Mapping: ppid -> {"created": datetime, "updated": datetime, "data": dict, "events": list}
        self.session_timestamps: dict[str, dict] = {}
        self.debug = debug
        self.only_relay = only_relay
        self._setup_routes()
        # Start liveness check
        asyncio.create_task(self._liveness_check_loop())

    async def _liveness_check_loop(self) -> None:
        """Background task to check if PPIDs are still alive every 10 minutes."""
        while True:
            await asyncio.sleep(600)  # 10 minutes
            logger.info("Running PPID liveness check...")
            dead_ppids = []
            for ppid in list(self.sessions.keys()):
                try:
                    if not psutil.pid_exists(int(ppid)):
                        dead_ppids.append(ppid)
                except (ValueError, TypeError):
                    # If ppid is not a valid int, treat as dead or invalid
                    dead_ppids.append(ppid)

            for ppid in dead_ppids:
                logger.info("PPID %s is dead, removing session.", ppid)
                self.sessions.pop(ppid, None)
                self.session_timestamps.pop(ppid, None)

    async def _get_queue_by_ppid(self, ppid: str) -> asyncio.Queue:
        """Resolves a ppid to its event queue."""
        if ppid not in self.sessions:
            # Implicit registration
            now = datetime.now(timezone.utc).isoformat()
            self.sessions[ppid] = asyncio.Queue()
            self.session_timestamps[ppid] = {
                "created": now,
                "updated": now,
                "data": {},
                "events": []
            }
            logger.info("Implicitly registered ppid: %s", ppid)

        return self.sessions[ppid]

    def _setup_routes(self) -> None:
        @self.app.get("/list_sessions")
        async def list_sessions():
            results = []
            for ppid, ts in self.session_timestamps.items():
                data = ts.get("data", {})
                events = ts.get("events", [])
                results.append({
                    "ppid": ppid,
                    "created": ts.get("created"),
                    "updated": ts.get("updated"),
                    "events": events,
                    **data
                })
            return results

        @self.app.get("/health")
        async def health():
            return {"name": RELAY_IDENTITY, "version": RELAY_VERSION}

        @self.app.post("/set/ppid/{ppid}")
        async def register_ppid(ppid: str, request: Request):
            logger.info("RelayServer: Registering/Updating PPID: %s", ppid)
            try:
                data = await request.json()
            except Exception:
                data = {}

            prompt = data.get("prompt")
            # Validate prompt is not empty if provided
            if "prompt" in data and (not prompt or not str(prompt).strip()):
                raise HTTPException(status_code=400, detail="prompt cannot be empty")

            now = datetime.now(timezone.utc).isoformat()
            if ppid not in self.sessions:
                self.sessions[ppid] = asyncio.Queue()
                self.session_timestamps[ppid] = {
                    "created": now,
                    "updated": now,
                    "data": data,
                    "events": []
                }
                logger.info("RelayServer: Registered new PPID queue: %s", ppid)
            else:
                self.session_timestamps[ppid]["updated"] = now
                self.session_timestamps[ppid]["data"].update(data)

            if prompt:
                event = {"type": "search", "timestamp": now, "ack": False}
                self.session_timestamps[ppid].setdefault("events", []).append(event)
                queue = self.sessions[ppid]
                logger.info("Delivering search event for PPID %s: %s", ppid, prompt)
                await queue.put({"type": "search", "prompt": prompt, "timestamp": now, "ack": False})

            return {"status": "ok"}

        @self.app.get("/events/{ppid}")
        async def events(ppid: str, request: Request):
            queue = await self._get_queue_by_ppid(ppid)
            if ppid in self.session_timestamps:
                self.session_timestamps[ppid]["updated"] = datetime.now(timezone.utc).isoformat()

            async def event_generator():
                try:
                    while True:
                        if await request.is_disconnected():
                            break
                        try:
                            # Use timeout to allow checking for disconnection
                            event = await asyncio.wait_for(queue.get(), timeout=1.0)
                            yield f"data: {json.dumps(event)}\n\n"
                        except asyncio.TimeoutError:
                            continue
                except asyncio.CancelledError:
                    pass

            return StreamingResponse(event_generator(), media_type="text/event-stream")

        @self.app.post("/ack/ppid/{ppid}")
        async def acknowledge_event(ppid: str, request: Request):
            """Acknowledge events for a PPID and optionally store the filtered tool list."""
            if ppid not in self.session_timestamps:
                raise HTTPException(status_code=404, detail="PPID not found")

            try:
                data = await request.json()
            except Exception:
                data = {}

            tools = data.get("tools")
            now = datetime.now(timezone.utc).isoformat()
            self.session_timestamps[ppid]["updated"] = now
            if tools is not None:
                # Store tools in the session data so it shows up in list_sessions
                self.session_timestamps[ppid].setdefault("data", {})["tools"] = tools
                logger.info("Stored %d tools for PPID %s during ack", len(tools), ppid)

            events = self.session_timestamps[ppid].get("events", [])
            if not events:
                return {"status": "ok", "message": "no events to ack", "tools_stored": tools is not None}

            # For simplicity, ack the latest unacknowledged event
            for event in reversed(events):
                if not event.get("ack"):
                    event["ack"] = True
                    event["updated"] = now
                    if tools is not None:
                        event["tools"] = tools
                    logger.info("Acknowledged event for PPID %s", ppid)
                    return {"status": "ok", "event": event}

            return {"status": "already acknowledged", "tools_stored": tools is not None}

        @self.app.delete("/del/ppid/{ppid}")
        async def delete_ppid(ppid: str):
            """Delete session and queue for a PPID."""
            logger.info("RelayServer: Deleting PPID: %s", ppid)
            if ppid in self.sessions:
                self.sessions.pop(ppid)
                self.session_timestamps.pop(ppid, None)
                return {"status": "ok", "message": f"PPID {ppid} deleted"}
            else:
                raise HTTPException(status_code=404, detail="PPID not found")

        @self.app.post("/set/ppid/{ppid}/search")
        async def search(ppid: str, request: Request):
            try:
                data = await request.json()
            except Exception:
                raise HTTPException(status_code=400, detail="Invalid JSON")

            prompt = data.get("prompt") or data.get("query")
            if not prompt or not str(prompt).strip():
                raise HTTPException(status_code=400, detail="prompt or query is required and cannot be empty")

            queue = await self._get_queue_by_ppid(ppid)
            now = datetime.now(timezone.utc).isoformat()
            if ppid in self.session_timestamps:
                self.session_timestamps[ppid]["updated"] = now
                self.session_timestamps[ppid]["data"].update(data)

            event = {"type": "search", "timestamp": now, "ack": False}
            self.session_timestamps[ppid].setdefault("events", []).append(event)

            logger.info("Delivering search event for ppid %s: %s", ppid, prompt)
            await queue.put({"type": "search", "prompt": prompt, "timestamp": now, "ack": False})
            return {"status": "delivered"}
