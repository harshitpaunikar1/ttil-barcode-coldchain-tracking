# TTIL Barcode Cold-Chain Tracking Diagrams

Generated on 2026-04-26T04:29:37Z from README narrative plus project blueprint requirements.

## Cold chain checkpoint flow

```mermaid
flowchart TD
    N1["Step 1\nMapped shipment flows and defined scan checkpoints, acceptance thresholds, excepti"]
    N2["Step 2\nSelected suitable Time Temperature Indicator Labels (TTILs) and encoded item/lot I"]
    N1 --> N2
    N3["Step 3\nBuilt Android app with in-app camera barcode scanning (no external hardware), stam"]
    N2 --> N3
    N4["Step 4\nDesigned offline-first data model (local store with background sync) and export/AP"]
    N3 --> N4
    N5["Step 5\nRan controlled pilots to simulate excursions, validated data integrity and usabili"]
    N4 --> N5
```

## TTIL label scan → data capture

```mermaid
flowchart LR
    N1["Inputs\nImages or camera frames entering the inference workflow"]
    N2["Decision Layer\nTTIL label scan → data capture"]
    N1 --> N2
    N3["User Surface\nAPI-facing integration surface described in the README"]
    N2 --> N3
    N4["Business Outcome\nOperating cost per workflow"]
    N3 --> N4
```

## Evidence Gap Map

```mermaid
flowchart LR
    N1["Present\nREADME, diagrams.md, local SVG assets"]
    N2["Missing\nSource code, screenshots, raw datasets"]
    N1 --> N2
    N3["Next Task\nReplace inferred notes with checked-in artifacts"]
    N2 --> N3
```
