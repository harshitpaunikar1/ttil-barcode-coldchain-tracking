"""
Background sync engine for cold-chain tracking.
Detects connectivity, queues unsynced checkpoints, and pushes to remote API.
"""
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

try:
    from tracker import LocalColdChainStore
    TRACKER_AVAILABLE = True
except ImportError:
    TRACKER_AVAILABLE = False


@dataclass
class SyncConfig:
    remote_base_url: str = "https://api.coldchain.internal"
    sync_interval_s: float = 60.0
    batch_size: int = 50
    timeout_s: float = 10.0
    health_endpoint: str = "/health"
    checkpoints_endpoint: str = "/api/v1/checkpoints/batch"
    excursions_endpoint: str = "/api/v1/excursions"


@dataclass
class SyncResult:
    attempted: int
    succeeded: int
    failed: int
    duration_ms: float
    synced_at: float = field(default_factory=time.time)


class ConnectivityChecker:
    """Checks if the remote API is reachable."""

    def __init__(self, base_url: str, health_path: str = "/health", timeout: float = 5.0):
        self.url = base_url.rstrip("/") + health_path
        self.timeout = timeout

    def is_online(self) -> bool:
        if not REQUESTS_AVAILABLE:
            return False
        try:
            resp = requests.get(self.url, timeout=self.timeout)
            return resp.status_code < 500
        except Exception:
            return False


class RemoteAPIClient:
    """HTTP client with retry logic for pushing cold-chain data."""

    def __init__(self, base_url: str, timeout: float = 10.0, stub_mode: bool = False):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.stub_mode = stub_mode
        self._session = None

    def _get_session(self) -> "requests.Session":
        if self._session is not None:
            return self._session
        session = requests.Session()
        retry = Retry(total=3, backoff_factor=1,
                       status_forcelist=[500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        self._session = session
        return session

    def push_checkpoints(self, checkpoints: List[Dict],
                          endpoint: str = "/api/v1/checkpoints/batch") -> bool:
        if self.stub_mode or not REQUESTS_AVAILABLE:
            logger.info("[stub] Would push %d checkpoints to %s", len(checkpoints), endpoint)
            time.sleep(0.05)
            return True
        try:
            url = self.base_url + endpoint
            resp = self._get_session().post(
                url,
                json={"checkpoints": checkpoints},
                timeout=self.timeout,
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            return True
        except Exception as exc:
            logger.error("Push checkpoints failed: %s", exc)
            return False

    def push_excursion(self, excursion: Dict,
                        endpoint: str = "/api/v1/excursions") -> bool:
        if self.stub_mode or not REQUESTS_AVAILABLE:
            logger.info("[stub] Would push excursion %s", excursion.get("excursion_id"))
            return True
        try:
            url = self.base_url + endpoint
            resp = self._get_session().post(url, json=excursion, timeout=self.timeout)
            resp.raise_for_status()
            return True
        except Exception as exc:
            logger.error("Push excursion failed: %s", exc)
            return False


class ColdChainSyncEngine:
    """
    Background sync engine that polls local store, checks connectivity,
    and pushes unsynced records in batches to the remote API.
    """

    def __init__(self, store: "LocalColdChainStore", config: SyncConfig,
                 stub_mode: bool = False):
        self.store = store
        self.config = config
        self.client = RemoteAPIClient(config.remote_base_url,
                                       timeout=config.timeout_s,
                                       stub_mode=stub_mode)
        self.connectivity = ConnectivityChecker(
            config.remote_base_url, config.health_endpoint
        )
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._sync_history: List[SyncResult] = []

    def sync_once(self) -> Optional[SyncResult]:
        if not self.connectivity.is_online() and not self.client.stub_mode:
            logger.info("Offline; skipping sync.")
            return None

        t0 = time.perf_counter()
        unsynced = self.store.unsynced_checkpoints()
        if not unsynced:
            return SyncResult(0, 0, 0, 0.0)

        batches = [unsynced[i:i + self.config.batch_size]
                   for i in range(0, len(unsynced), self.config.batch_size)]

        succeeded = 0
        failed = 0
        for batch in batches:
            ok = self.client.push_checkpoints(batch, self.config.checkpoints_endpoint)
            if ok:
                ids = [r["checkpoint_id"] for r in batch]
                self.store.mark_synced(ids)
                succeeded += len(batch)
            else:
                failed += len(batch)

        excursions = self.store.excursion_report()
        for exc in excursions:
            self.client.push_excursion(exc, self.config.excursions_endpoint)

        duration_ms = (time.perf_counter() - t0) * 1000
        result = SyncResult(
            attempted=len(unsynced),
            succeeded=succeeded,
            failed=failed,
            duration_ms=round(duration_ms, 1),
        )
        self._sync_history.append(result)
        logger.info("Sync complete: attempted=%d succeeded=%d failed=%d %.0fms",
                    result.attempted, result.succeeded, result.failed, result.duration_ms)
        return result

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._sync_loop, daemon=True, name="cold-chain-sync")
        self._thread.start()
        logger.info("Sync engine started (interval=%.0fs)", self.config.sync_interval_s)

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Sync engine stopped.")

    def _sync_loop(self) -> None:
        while self._running:
            try:
                self.sync_once()
            except Exception as exc:
                logger.error("Sync loop error: %s", exc)
            time.sleep(self.config.sync_interval_s)

    def sync_history(self) -> List[Dict[str, Any]]:
        return [
            {
                "attempted": r.attempted,
                "succeeded": r.succeeded,
                "failed": r.failed,
                "duration_ms": r.duration_ms,
                "synced_at": r.synced_at,
            }
            for r in self._sync_history
        ]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    if not TRACKER_AVAILABLE:
        print("tracker.py not found; cannot run demo.")
    else:
        from tracker import LocalColdChainStore, ColdChainCaptureSession, CheckpointType

        store = LocalColdChainStore(":memory:")
        session = ColdChainCaptureSession("OP_001", store)

        for i in range(5):
            session.capture(
                shipment_id="SHP_DEMO",
                location=f"Hub_{i}",
                checkpoint_type=CheckpointType.TRANSIT_HUB,
                barcode_raw=f"ITEM:CHEM00{i}-LOT:D2024",
                ttil_color="GREEN" if i < 4 else "RED",
                temperature_c=4.0 + i * 0.5,
            )

        print(f"Unsynced before sync: {store.stats()['unsynced']}")

        config = SyncConfig(
            remote_base_url="https://api.coldchain.internal",
            sync_interval_s=30,
        )
        engine = ColdChainSyncEngine(store=store, config=config, stub_mode=True)

        result = engine.sync_once()
        if result:
            print(f"Sync result: attempted={result.attempted} "
                  f"succeeded={result.succeeded} failed={result.failed} "
                  f"duration={result.duration_ms:.1f}ms")

        print(f"Unsynced after sync: {store.stats()['unsynced']}")

        engine.start()
        time.sleep(0.5)
        engine.stop()

        print("Sync history:", engine.sync_history())
