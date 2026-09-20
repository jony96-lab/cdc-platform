import asyncio
import logging

import httpx

log = logging.getLogger("portal.connect")


class ConnectError(Exception):
    pass


class ConnectClient:
    """Cliente asincrono de la REST API de Kafka Connect."""

    def __init__(self, base_url: str, timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout)

    async def close(self):
        await self._client.aclose()

    async def root(self) -> dict:
        r = await self._client.get("/")
        r.raise_for_status()
        return r.json()

    async def connector_plugins(self) -> list[dict]:
        r = await self._client.get("/connector-plugins")
        r.raise_for_status()
        return r.json()

    async def list_connectors(self) -> list[str]:
        r = await self._client.get("/connectors")
        r.raise_for_status()
        return r.json()

    async def get_connector(self, name: str) -> dict:
        r = await self._client.get(f"/connectors/{name}")
        r.raise_for_status()
        return r.json()

    async def get_status(self, name: str) -> dict:
        r = await self._client.get(f"/connectors/{name}/status")
        r.raise_for_status()
        return r.json()

    async def get_all_statuses(self) -> dict:
        r = await self._client.get("/connectors", params={"expand": "status"})
        r.raise_for_status()
        out = {}
        for name, payload in r.json().items():
            out[name] = payload.get("status", {})
        return out

    async def get_all_configs(self) -> dict:
        r = await self._client.get("/connectors", params={"expand": "info"})
        r.raise_for_status()
        out = {}
        for name, payload in r.json().items():
            out[name] = payload.get("info", {}).get("config", {})
        return out

    async def create_or_update(self, name: str, config: dict) -> str:
        payload = {"name": name, "config": config}
        r = await self._client.post("/connectors", json=payload)
        if r.status_code == 409:
            r = await self._client.put(f"/connectors/{name}/config", json=config)
            r.raise_for_status()
            return "updated"
        if r.status_code >= 400:
            raise ConnectError(f"HTTP {r.status_code}: {r.text[:500]}")
        return "created"

    async def delete(self, name: str) -> bool:
        r = await self._client.delete(f"/connectors/{name}")
        return r.status_code in (200, 204, 404)

    async def pause(self, name: str) -> None:
        r = await self._client.put(f"/connectors/{name}/pause")
        r.raise_for_status()

    async def resume(self, name: str) -> None:
        r = await self._client.put(f"/connectors/{name}/resume")
        r.raise_for_status()

    async def restart(self, name: str, tasks: bool = True) -> None:
        r = await self._client.post(
            f"/connectors/{name}/restart", params={"includeTasks": str(tasks).lower()}
        )
        r.raise_for_status()

    async def restart_task(self, name: str, task_id: int) -> None:
        r = await self._client.post(f"/connectors/{name}/tasks/{task_id}/restart")
        r.raise_for_status()

    async def topics(self, name: str) -> list[str]:
        r = await self._client.get(f"/connectors/{name}/topics")
        r.raise_for_status()
        return list(r.json().get(name, {}).get("topics", []))

    async def wait_for_status(self, name: str, target: str = "RUNNING",
                              timeout: float = 120.0) -> dict:
        """Espera a que conector y todas sus tasks alcancen `target`."""
        deadline = asyncio.get_event_loop().time() + timeout
        last = {}
        while asyncio.get_event_loop().time() < deadline:
            try:
                last = await self.get_status(name)
                conn_state = last.get("connector", {}).get("state", "")
                tasks = last.get("tasks", [])
                if conn_state == "UNASSIGNED":
                    await asyncio.sleep(2)
                    continue
                if conn_state != target:
                    return last
                if tasks and all(t.get("state") == target for t in tasks):
                    return last
                if any(t.get("state") == "FAILED" for t in tasks):
                    return last
            except httpx.HTTPError:
                pass
            await asyncio.sleep(2)
        return last
