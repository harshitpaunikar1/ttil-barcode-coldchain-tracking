"""
Cold-chain shipment tracker with barcode scanning, TTIL reading, and checkpoint capture.
Implements offline-first local storage with temperature excursion detection.
"""
import hashlib
import json
import logging
import re
import sqlite3
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

try:
    from pyzbar import pyzbar
    PYZBAR_AVAILABLE = True
except ImportError:
    PYZBAR_AVAILABLE = False


class TTILStatus(str, Enum):
    ACCEPTABLE = "acceptable"
    BORDERLINE = "borderline"
    EXCURSION = "excursion"
    UNKNOWN = "unknown"


class CheckpointType(str, Enum):
    ORIGIN = "origin"
    TRANSIT_HUB = "transit_hub"
    DELIVERY = "delivery"
    RETURN = "return"
    CUSTOM_CLEARANCE = "custom_clearance"
    COLD_STORAGE = "cold_storage"


@dataclass
class BarcodeRead:
    barcode_data: str
    barcode_type: str
    item_id: Optional[str]
    lot_id: Optional[str]
    scanned_at: float = field(default_factory=time.time)


@dataclass
class TTILReading:
    indicator_id: str
    status: TTILStatus
    color_code: str
    estimated_exposure_hours: float
    threshold_hours: float
    captured_at: float = field(default_factory=time.time)

    @property
    def is_compliant(self) -> bool:
        return self.status in (TTILStatus.ACCEPTABLE, TTILStatus.BORDERLINE)


@dataclass
class Checkpoint:
    checkpoint_id: str
    shipment_id: str
    item_id: str
    checkpoint_type: CheckpointType
    location: str
    operator_id: str
    barcode_read: BarcodeRead
    ttil_reading: Optional[TTILReading]
    temperature_c: Optional[float]
    humidity_pct: Optional[float]
    notes: str = ""
    captured_at: float = field(default_factory=time.time)
    synced: bool = False


class BarcodeDecoder:
    """Decodes barcodes from image frames or raw strings."""

    ITEM_ID_PATTERN = re.compile(r"ITEM[:\-]?([A-Z0-9]{6,16})", re.IGNORECASE)
    LOT_ID_PATTERN = re.compile(r"LOT[:\-]?([A-Z0-9]{4,12})", re.IGNORECASE)

    def decode_from_frame(self, frame: np.ndarray) -> List[BarcodeRead]:
        if PYZBAR_AVAILABLE and CV2_AVAILABLE:
            try:
                decoded = pyzbar.decode(frame)
                reads = []
                for d in decoded:
                    data = d.data.decode("utf-8", errors="ignore")
                    reads.append(self._parse(data, d.type))
                return reads
            except Exception as exc:
                logger.error("Barcode decode error: %s", exc)
        return self._stub_decode()

    def decode_from_string(self, raw: str) -> BarcodeRead:
        return self._parse(raw, "CODE128")

    def _parse(self, data: str, barcode_type: str) -> BarcodeRead:
        item_match = self.ITEM_ID_PATTERN.search(data)
        lot_match = self.LOT_ID_PATTERN.search(data)
        return BarcodeRead(
            barcode_data=data,
            barcode_type=str(barcode_type),
            item_id=item_match.group(1) if item_match else None,
            lot_id=lot_match.group(1) if lot_match else None,
        )

    def _stub_decode(self) -> List[BarcodeRead]:
        return [BarcodeRead(
            barcode_data="ITEM:CHEM001234-LOT:AB2024",
            barcode_type="STUB_QR",
            item_id="CHEM001234",
            lot_id="AB2024",
        )]


class TTILReader:
    """
    Interprets Time-Temperature Indicator Label readings.
    Uses color code and threshold to determine excursion status.
    """

    COLOR_STATUS_MAP: Dict[str, TTILStatus] = {
        "GREEN": TTILStatus.ACCEPTABLE,
        "YELLOW": TTILStatus.BORDERLINE,
        "RED": TTILStatus.EXCURSION,
        "DARK_RED": TTILStatus.EXCURSION,
    }

    ACCEPTANCE_THRESHOLDS_HOURS: Dict[str, float] = {
        "pharma_cold": 2.0,
        "pharma_ambient": 8.0,
        "chemical_hazmat": 4.0,
        "food_frozen": 1.0,
        "food_chilled": 3.0,
    }

    def read_from_color(self, color_code: str, indicator_id: str,
                         product_category: str = "pharma_cold") -> TTILReading:
        color_upper = color_code.upper()
        status = self.COLOR_STATUS_MAP.get(color_upper, TTILStatus.UNKNOWN)
        threshold = self.ACCEPTANCE_THRESHOLDS_HOURS.get(product_category, 4.0)
        exposure_map = {"GREEN": 0.0, "YELLOW": threshold * 0.7,
                        "RED": threshold * 1.2, "DARK_RED": threshold * 2.0}
        exposure = exposure_map.get(color_upper, 0.0)
        return TTILReading(
            indicator_id=indicator_id,
            status=status,
            color_code=color_upper,
            estimated_exposure_hours=exposure,
            threshold_hours=threshold,
        )

    def read_from_camera(self, frame: np.ndarray, indicator_id: str,
                          product_category: str = "pharma_cold") -> TTILReading:
        if not CV2_AVAILABLE:
            return self.read_from_color("GREEN", indicator_id, product_category)
        try:
            avg_color = frame.mean(axis=(0, 1))
            b, g, r = avg_color[0], avg_color[1], avg_color[2]
            if r > 150 and g < 80:
                color = "RED"
            elif r > 100 and g > 100:
                color = "YELLOW"
            else:
                color = "GREEN"
            return self.read_from_color(color, indicator_id, product_category)
        except Exception as exc:
            logger.error("TTIL camera read error: %s", exc)
            return self.read_from_color("UNKNOWN", indicator_id, product_category)


class LocalColdChainStore:
    """SQLite-backed offline-first store for checkpoints."""

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS checkpoints (
        checkpoint_id TEXT PRIMARY KEY,
        shipment_id TEXT,
        item_id TEXT,
        lot_id TEXT,
        checkpoint_type TEXT,
        location TEXT,
        operator_id TEXT,
        barcode_data TEXT,
        ttil_status TEXT,
        ttil_color TEXT,
        temperature_c REAL,
        humidity_pct REAL,
        notes TEXT,
        captured_at REAL,
        synced INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS excursions (
        excursion_id TEXT PRIMARY KEY,
        checkpoint_id TEXT,
        shipment_id TEXT,
        item_id TEXT,
        ttil_status TEXT,
        temperature_c REAL,
        detected_at REAL
    );
    CREATE INDEX IF NOT EXISTS idx_checkpoints_shipment ON checkpoints(shipment_id);
    CREATE INDEX IF NOT EXISTS idx_checkpoints_synced ON checkpoints(synced);
    """

    def __init__(self, db_path: str = "coldchain_local.db"):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.executescript(self.SCHEMA)
        self.conn.commit()

    def save_checkpoint(self, cp: Checkpoint) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO checkpoints
               (checkpoint_id, shipment_id, item_id, lot_id, checkpoint_type,
                location, operator_id, barcode_data, ttil_status, ttil_color,
                temperature_c, humidity_pct, notes, captured_at, synced)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (cp.checkpoint_id, cp.shipment_id, cp.item_id or "",
             cp.barcode_read.lot_id or "",
             cp.checkpoint_type.value, cp.location, cp.operator_id,
             cp.barcode_read.barcode_data,
             cp.ttil_reading.status.value if cp.ttil_reading else None,
             cp.ttil_reading.color_code if cp.ttil_reading else None,
             cp.temperature_c, cp.humidity_pct,
             cp.notes, cp.captured_at, int(cp.synced)),
        )
        if cp.ttil_reading and cp.ttil_reading.status == TTILStatus.EXCURSION:
            excursion_id = f"exc_{cp.checkpoint_id}"
            self.conn.execute(
                """INSERT OR IGNORE INTO excursions
                   (excursion_id, checkpoint_id, shipment_id, item_id, ttil_status, temperature_c, detected_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (excursion_id, cp.checkpoint_id, cp.shipment_id, cp.item_id or "",
                 cp.ttil_reading.status.value, cp.temperature_c, time.time()),
            )
        self.conn.commit()

    def unsynced_checkpoints(self) -> List[Dict]:
        import pandas as pd
        return pd.read_sql_query(
            "SELECT * FROM checkpoints WHERE synced=0 ORDER BY captured_at", self.conn
        ).to_dict(orient="records")

    def mark_synced(self, checkpoint_ids: List[str]) -> int:
        if not checkpoint_ids:
            return 0
        placeholders = ",".join("?" * len(checkpoint_ids))
        self.conn.execute(
            f"UPDATE checkpoints SET synced=1 WHERE checkpoint_id IN ({placeholders})",
            checkpoint_ids,
        )
        self.conn.commit()
        return len(checkpoint_ids)

    def shipment_trail(self, shipment_id: str) -> List[Dict]:
        import pandas as pd
        return pd.read_sql_query(
            "SELECT * FROM checkpoints WHERE shipment_id=? ORDER BY captured_at",
            self.conn, params=(shipment_id,),
        ).to_dict(orient="records")

    def excursion_report(self) -> List[Dict]:
        import pandas as pd
        return pd.read_sql_query(
            "SELECT * FROM excursions ORDER BY detected_at DESC", self.conn
        ).to_dict(orient="records")

    def stats(self) -> Dict[str, Any]:
        cur = self.conn.execute(
            "SELECT COUNT(*) AS total, SUM(1-synced) AS unsynced FROM checkpoints"
        )
        row = cur.fetchone()
        exc_count = self.conn.execute("SELECT COUNT(*) FROM excursions").fetchone()[0]
        return {"total_checkpoints": row[0], "unsynced": row[1], "excursions": exc_count}


class ColdChainCaptureSession:
    """
    Manages a single field capture session for scanning and recording a checkpoint.
    """

    def __init__(self, operator_id: str, store: LocalColdChainStore):
        self.operator_id = operator_id
        self.store = store
        self._barcode_decoder = BarcodeDecoder()
        self._ttil_reader = TTILReader()

    def capture(self, shipment_id: str, location: str,
                 checkpoint_type: CheckpointType,
                 barcode_raw: str,
                 ttil_color: str,
                 temperature_c: Optional[float] = None,
                 humidity_pct: Optional[float] = None,
                 notes: str = "",
                 product_category: str = "pharma_cold") -> Checkpoint:
        barcode = self._barcode_decoder.decode_from_string(barcode_raw)
        ttil_indicator_id = hashlib.md5(f"{shipment_id}{time.time()}".encode()).hexdigest()[:8]
        ttil = self._ttil_reader.read_from_color(ttil_color, ttil_indicator_id, product_category)
        checkpoint_id = hashlib.md5(
            f"{shipment_id}{barcode.barcode_data}{time.time()}".encode()
        ).hexdigest()[:16]
        cp = Checkpoint(
            checkpoint_id=checkpoint_id,
            shipment_id=shipment_id,
            item_id=barcode.item_id or "UNKNOWN",
            checkpoint_type=checkpoint_type,
            location=location,
            operator_id=self.operator_id,
            barcode_read=barcode,
            ttil_reading=ttil,
            temperature_c=temperature_c,
            humidity_pct=humidity_pct,
            notes=notes,
        )
        self.store.save_checkpoint(cp)
        if ttil.status == TTILStatus.EXCURSION:
            logger.warning("EXCURSION detected at checkpoint %s (shipment %s)",
                           checkpoint_id, shipment_id)
        return cp


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    store = LocalColdChainStore(":memory:")
    session = ColdChainCaptureSession(operator_id="OP_007", store=store)

    checkpoints_data = [
        ("SHP_20240101", "Mumbai Warehouse", CheckpointType.ORIGIN,
         "ITEM:CHEM001234-LOT:AB2024", "GREEN", 4.2, 65),
        ("SHP_20240101", "Delhi Transit Hub", CheckpointType.TRANSIT_HUB,
         "ITEM:CHEM001234-LOT:AB2024", "YELLOW", 6.1, 70),
        ("SHP_20240101", "Chandigarh Delivery", CheckpointType.DELIVERY,
         "ITEM:CHEM001234-LOT:AB2024", "RED", 9.8, 75),
    ]

    print("Cold Chain Tracking Demo\n")
    for shipment_id, location, cp_type, barcode, color, temp, hum in checkpoints_data:
        cp = session.capture(
            shipment_id=shipment_id,
            location=location,
            checkpoint_type=cp_type,
            barcode_raw=barcode,
            ttil_color=color,
            temperature_c=temp,
            humidity_pct=hum,
        )
        status_str = cp.ttil_reading.status.value if cp.ttil_reading else "N/A"
        compliant = cp.ttil_reading.is_compliant if cp.ttil_reading else True
        print(f"{location}: TTIL={color} ({status_str}) | "
              f"Temp={temp}C | Compliant={compliant}")

    print("\nShipment trail for SHP_20240101:")
    trail = store.shipment_trail("SHP_20240101")
    for t in trail:
        print(f"  {t['checkpoint_type']} at {t['location']} | TTIL={t['ttil_status']}")

    print("\nExcursion report:")
    for exc in store.excursion_report():
        print(f"  Checkpoint {exc['checkpoint_id']}: {exc['ttil_status']} at {exc['detected_at']:.0f}")

    print("\nStore stats:", store.stats())
    print(f"Unsynced checkpoints: {store.stats()['unsynced']}")
