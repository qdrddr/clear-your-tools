import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)

RELAY_IDENTITY = "sca-aggregator-relay-server-helper"
RELAY_VERSION = "1.0.0"

class RelayServer:
    def __init__(self, debug: bool = False, only_relay: bool = False) -> None:
        self.app = FastAPI()
        # Primary storage: ppid -> event queue
        self.sessions: dict[str, asyncio.Queue] = {}
        # Mapping: ppid -> {"created": datetime, "updated": datetime, "data": dict}
        self.session_timestamps: dict[str, dict] = {}
        self.debug = debug
        self.only_relay = only_relay
        self.fallback_session_id = os.environ.get("PPID")
        self._setup_routes()

    async def _get_queue_by_ppid(self, ppid: str) -> asyncio.Queue:
        """Resolves a ppid to its event queue."""
        if ppid not in self.sessions:
            if self.only_relay and self.debug:
                # Reuse fallback if ppid matches or we have a registered fallback
                if self.fallback_session_id and self.fallback_session_id in self.sessions:
                     logger.info("Reusing fallback session queue for %s", ppid)
                     return self.sessions[self.fallback_session_id]
                logger.warning("Unregistered ppid %s in debug+relay mode", ppid)
                raise HTTPException(status_code=404, detail=f"PPID {ppid} is not registered.")

            # Implicit registration
            now = datetime.now(timezone.utc).isoformat()
            self.sessions[ppid] = asyncio.Queue()
            self.session_timestamps[ppid] = {"created": now, "updated": now, "data": {}}
            logger.info("Implicitly registered ppid: %s", ppid)

        return self.sessions[ppid]

    def _setup_routes(self) -> None:
        @self.app.get("/list_sessions")
        async def list_sessions():
            results = []
            for ppid, ts in self.session_timestamps.items():
                data = ts.get("data", {})
                results.append({
                    "ppid": ppid,
                    "created": ts.get("created"),
                    "updated": ts.get("updated"),
                    **data
                })
            return results

        @self.app.get("/health")
        async def health():
            return {"name": RELAY_IDENTITY, "version": RELAY_VERSION}

        @self.app.post("/set/ppid/{ppid}")
        async def register_ppid(ppid: str, request: Request):
            logger.info("RelayServer: Registering PPID: %s", ppid)
            try:
                data = await request.json()
            except Exception:
                data = {}

            # Treat PPID as primary key.
            now = datetime.now(timezone.utc).isoformat()
            if ppid not in self.sessions:
                self.sessions[ppid] = asyncio.Queue()
                self.session_timestamps[ppid] = {
                    "created": now,
                    "updated": now,
                    "data": data
                }
                logger.info("RelayServer: Registered new PPID queue: %s", ppid)
            else:
                if ppid in self.session_timestamps:
                    self.session_timestamps[ppid]["updated"] = now
                    # Update metadata
                    self.session_timestamps[ppid]["data"].update(data)
                else:
                    self.session_timestamps[ppid] = {
                        "created": now,
                        "updated": now,
                        "data": data
                    }

            prompt = data.get("prompt")
            if prompt:
                queue = self.sessions[ppid]
                logger.info("Delivering search event for PPID %s: %s", ppid, prompt)
                await queue.put({"type": "search", "prompt": prompt})

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
                finally:
                    pass

            return StreamingResponse(event_generator(), media_type="text/event-stream")

        @self.app.post("/set/ppid/{ppid}/search")
        async def search(ppid: str, request: Request):
            try:
                data = await request.json()
            except Exception:
                raise HTTPException(status_code=400, detail="Invalid JSON")

            prompt = data.get("prompt") or data.get("query")
            if not prompt:
                raise HTTPException(status_code=400, detail="prompt or query is required")

            queue = await self._get_queue_by_ppid(ppid)
            if ppid in self.session_timestamps:
                self.session_timestamps[ppid]["updated"] = datetime.now(timezone.utc).isoformat()
                self.session_timestamps[ppid]["data"].update(data)

            logger.info("Delivering search event for ppid %s: %s", ppid, prompt)
            await queue.put({"type": "search", "prompt": prompt})
            return {"status": "delivered"}
