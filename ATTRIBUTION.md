# Attribution / terms

## Robotaxi Tracker data
- Source: [Robotaxi Tracker](https://robotaxitracker.com) — Cybercab https://robotaxitracker.com/cybercab ; Camera watch https://robotaxitracker.com/cameras
- Endpoint used: `GET https://robotaxitracker.com/v1/cybercab-cameras` (`daily` / `hourly` / `variants` arrays). Also `/v1/cybercab-cameras/area-stats?window=7d` and `/v1/camera-sightings/timeline?market=austin&window=7d`.
- Required attribution (https://robotaxitracker.com/llms.txt): credit **Robotaxi Tracker** by name; include clickable link and visible URL https://robotaxitracker.com; link the specific page when possible; preserve original-source credits, dates, estimates, caveats.
- Camera detections are labeled **experimental**; Cybercab detections may include false positives.
- Terms: https://robotaxitracker.com/terms — no public CSV export of the Cybercab daily series was found; JSON via `/v1/cybercab-cameras` is the scrapeable history (~36 days in `daily` as of this fetch).

## City of Austin traffic cameras
- List: https://data.austintexas.gov/resource/b4k4-adkb.json — license **Public Domain**
- Images: https://cctv.austinmobility.io/image/{id}.jpg — published for public/research use (github.com/cityofaustin/atd-cctv-images). Video is not recorded.
