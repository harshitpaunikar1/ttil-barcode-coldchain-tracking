# TTIL Barcode Cold-Chain Tracking

> **Domain:** Logistics / Cold Chain

## Overview

Temperature-sensitive chemicals were moving without consistent, end-to-end visibility into exposure. Paper logs and sporadic checks created gaps, allowing excursions to go unnoticed. Field teams lacked a simple way to capture and verify status at handoff points. Supervisors could not audit shipments across lanes and vendors quickly. As volumes scaled, manual data entry caused delays and errors, while dedicated scanners raised cost and complexity. Without a trustworthy temperature trail, the business risked rejected loads, write-offs, compliance issues, and reputational damage. A low-cost, mobile-first solution was needed to standardise capture at each checkpoint, tie readings to items unambiguously, and produce an auditable record without adding friction.

## Approach

- Mapped shipment flows and defined scan checkpoints, acceptance thresholds, exception paths aligned to existing SOPs and QA requirements
- Selected suitable Time Temperature Indicator Labels (TTILs) and encoded item/lot IDs; standardised label placement and scan prompts to reduce misses
- Built Android app with in-app camera barcode scanning (no external hardware), stamping each scan with time, location context, user, shipment reference
- Designed offline-first data model (local store with background sync) and export/API options for reporting, audits, customer updates
- Ran controlled pilots to simulate excursions, validated data integrity and usability, refined workflows, updated SOPs and training materials
- Rolled out in phases, instrumented usage metrics, established cost model demonstrating savings versus dedicated scanners

## Skills & Technologies

- Android Development
- Barcode Scanning & Decoding
- Time-Temperature Indicator Integration
- Offline-First Architecture
- SQLite Data Modeling
- API Design & Integration
- Field UX Design
- QA & UAT Documentation
