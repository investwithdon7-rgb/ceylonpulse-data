# CeylonPulse data

Pre-computed climate data for [CeylonPulse](https://tekdruid.com/CeylonPulse/), the Sri Lanka live dashboard.
GitHub Actions build these files on a schedule so visitors download a few small JSON files instead of
calling many APIs from their browser.

| File | Updated | What it holds |
|---|---|---|
| `data/enso.json` | daily, 06:00 Sri Lanka | NOAA CPC ONI / Relative ONI, the ENSO diagnostic discussion, and the ECMWF SEAS5 Niño-3.4 projection |
| `data/outlook-3m.json` | daily, 06:00 Sri Lanka | Next 3 months for all 25 districts and 13 reservoir catchments: ECMWF SEAS5 rain and temperature vs normal, and below / near / above-normal chances from the 51 ensemble runs |
| `data/c3s-outlook.json` | monthly, 14th | Copernicus C3S multi-model chances (ECMWF, UK Met Office, Météo-France, DWD, CMCC, NCEP, JMA, ECCC, BoM) |
| `data/status.json` | daily | When the daily files were last refreshed (each file also carries its own `generated` time) |
| `archive/` | every run | Dated copies of each forecast, kept so forecast accuracy can be checked later |

Website access (CORS enabled, cached at the edge):
`https://cdn.jsdelivr.net/gh/investwithdon7-rgb/ceylonpulse-data@main/data/<file>`

## Method

- **Chances:** each forecast run's monthly rain is compared with the 1991–2020 range at that place
  (ERA5, `ref/lk-climate.json`). *Below normal* = drier than 2 in 3 years, *above normal* = wetter than 2 in 3 years;
  in an average year each has a 33% chance. The Copernicus file compares each system with its own 1993–2016 hindcasts
  and averages the systems with equal weight.
- **Catchments** (`ref/lk-points.json`) are approximated by sample points inside each upper catchment. They describe rain
  over the catchment, not reservoir inflow. Corrections from irrigation engineers are welcome.
- Seasonal forecasts show tendencies, not daily weather. Official warnings come from the Department of Meteorology and the DMC.

## Secrets

The Copernicus CDS token is stored only as the encrypted repository secret `CDSAPI_KEY`. It is never committed.

## Sources and licences

NOAA Climate Prediction Center (public domain) · ECMWF SEAS5 and ERA5 via [Open-Meteo](https://open-meteo.com) (CC BY 4.0) ·
Copernicus Climate Change Service, C3S seasonal forecasts (generated using Copernicus Climate Change Service information;
neither the European Commission nor ECMWF is responsible for any use that may be made of it) ·
district boundaries from geoBoundaries / OpenStreetMap (ODbL).
