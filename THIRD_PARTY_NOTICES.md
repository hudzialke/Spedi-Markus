# Third-party notices

Spedi is authored by **Patryk Prośniak (hudzialke)** with assistance from various
artificial intelligence models. This attribution does not claim authorship of
the third-party components below.

## DDD Tachograph Reader

The modified parser under `spedi/_vendor/tachograph/` is derived from
[Syax89/ddd-tachograph-reader](https://github.com/Syax89/ddd-tachograph-reader).
Copyright (c) 2024 Syax89. Licensed under MIT. Its original license is preserved
at `spedi/_vendor/tachograph/LICENSE`. The bundled copy includes local parsing
fixes and is loaded by Spedi's DDD Analyzer adapter.

The `certs/` directory contains public tachograph verification material,
including ERCA public keys. These are public verification keys, not credentials
for a user's account. See the included certificate README files and
[EU JRC Digital Tachograph](https://dtc.jrc.ec.europa.eu/).

## Country boundaries

`ddd_analyzer/data/eu_countries.geojson` is a subset of Natural Earth's
1:110m country boundaries, with ISO-2 codes and names selected from its
[source GeoJSON](https://github.com/nvkelso/natural-earth-vector/blob/master/geojson/ne_110m_admin_0_countries.geojson).
Made with Natural Earth. Natural Earth data is
[in the public domain](https://www.naturalearthdata.com/about/terms-of-use/).
Boundaries are approximate and are not evidence of a driver's location.

## Dependencies

Python, Qt/PySide6, ReportLab, pypdf, Pillow, cryptography, openpyxl,
platformdirs, httpx, requests and Google/Firebase libraries retain their own
licenses. Android, Kotlin, AndroidX, Compose and Firebase SDKs retain theirs.
MIT applies to Spedi's original code; it does not replace dependency licenses.
Binary distributors must retain applicable dependency notices and comply with
their licenses, including Qt/PySide6 licensing requirements.

The Gradle wrapper is a Gradle component licensed under Apache-2.0:
[Gradle license](https://github.com/gradle/gradle/blob/master/LICENSE).
