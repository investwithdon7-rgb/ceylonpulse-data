# CeylonPulse data

Pre-computed climate data for [CeylonPulse](https://tekdruid.com/CeylonPulse/), the Sri Lanka live dashboard.
GitHub Actions build these files on a schedule so visitors download a few small JSON files instead of
calling many APIs from their browser.

| File | Updated | What it holds |
|---|---|---|
| `data/enso.json` | daily, 06:00 Sri Lanka | NOAA CPC ONI / Relative ONI, the ENSO diagnostic discussion, and the ECMWF SEAS5 Niño-3.4 projection |
| `data/outlook-3m.json` | daily, 06:00 Sri Lanka | Next 3 months for all 25 districts and 13 reservoir catchments: ECMWF SEAS5 rain and temperature vs normal, and below / near / above-normal chances from the 51 ensemble runs |
| `data/c3s-outlook.json` | monthly, 14th | Copernicus C3S multi-model chances (ECMWF, UK Met Office, Météo-France, DWD, CMCC, NCEP, JMA, ECCC, BoM) |
| `data/fuel.json` | hourly check | Ceylon Petroleum Corporation retail prices with effective dates |
| `data/reservoirs.json` | hourly check | Irrigation Department daily water level and storage of 74 major reservoirs, island total |
| `data/rivers.json` | hourly | Latest reading at each Irrigation Department river gauge with alert / minor / major flood levels |
| `data/fires.json` | hourly | NASA FIRMS VIIRS fire detections over Sri Lanka, last 3 days |
| `data/epidemiology.json` | daily check | Epidemiology Unit Weekly Epidemiological Report, Table 1: weekly and year-to-date dengue, leptospirosis and other notifiable diseases by RDHS. National figures are the sum of districts (the printed national row is occasionally wrong) |
| `data/prices.json` | hourly check | Central Bank Daily Price Report: wholesale and retail prices of ~40 foods (vegetables, rice, fish, fruit, staples) at Pettah, Dambulla, Narahenpita, Marandagahamula, Peliyagoda and Negombo, the day's biggest moves with CBSL's reasons, and up to a year of Colombo retail history for a 20-food basket. PDFs are read in memory and never stored; every report is kept as compact values in one file per year, `archive/prices/YYYY.json` (back to September 2025) |
| `data/fuel-history.json` | hourly check | Ceylon Petroleum Corporation price revisions since 2015 (petrol 92/95, auto and super diesel, kerosene) |
| `data/ccpi.json` | daily check | Colombo Consumer Price Index from January 2016, monthly: index (2021 = 100; the 2013-base series is linked at December 2022) and year-on-year inflation, Department of Census and Statistics |
| `data/news.json` | hourly | Latest 60 headlines merged from Ada Derana, Daily Mirror, The Island, Newswire, ColomboPage, OnLanka and Tamil Guardian RSS feeds (title, link, time, source) |
| `data/economy.json` | daily check | Central Bank of Sri Lanka statistical tables: monthly policy rate (OPR/SDFR/SLFR), T-bill yields, AWPR, AWDR; official reserves; monthly workers' remittances and tourism earnings; monthly exports and imports; quarterly real GDP growth; quarterly unemployment (DCS Labour Force Survey) |
| `data/tourism.json` | hourly check | SLTDA weekly report: tourist arrivals by month (this year, last year, 2018) and top twenty source markets this year; US State Department travel advisory level for Sri Lanka |
| `data/metro-routes.json` | daily check | Lanka Metro Transit route lines and stops (Makumbura–Colombo, Makumbura–Kadawatha) for the SmartMetro map |
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

The Copernicus CDS token (`CDSAPI_KEY`) and NASA FIRMS key (`FIRMS_KEY`) are stored only as encrypted repository secrets. They are never committed.

The hourly feeds rewrite a file only when its data changes, so `generated` is the time the data last changed.

## Sources and licences

NOAA Climate Prediction Center (public domain) · Irrigation Department of Sri Lanka · Epidemiology Unit, Ministry of Health · Ceylon Petroleum Corporation · Central Bank of Sri Lanka · Department of Census and Statistics · Sri Lanka Tourism Development Authority · U.S. Department of State · Lanka Metro Transit · NASA FIRMS · ECMWF SEAS5 and ERA5 via [Open-Meteo](https://open-meteo.com) (CC BY 4.0) ·
Copernicus Climate Change Service, C3S seasonal forecasts (generated using Copernicus Climate Change Service information;
neither the European Commission nor ECMWF is responsible for any use that may be made of it) ·
district boundaries from geoBoundaries / OpenStreetMap (ODbL).
