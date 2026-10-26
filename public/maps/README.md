# Globe basemap

`land.geojson` is Natural Earth's public-domain 1:110m land dataset.

Source: https://github.com/nvkelso/natural-earth-vector/blob/master/geojson/ne_110m_land.geojson

Terms: https://www.naturalearthdata.com/about/terms-of-use/

The basemap is served locally so globe rotation and event selection do not depend on a third-party tile service.

The globe uses charcoal land, light gray ocean, and colored event overlays.
It does not load satellite imagery. Event overlays use complete grid cells at
0.25 degrees or coarser, without smoothing or clipping cells to invented outlines.
The synthetic demonstration is explicitly labeled and contains seven exclusive
combinations, with fractions calculated from synthetic depth masks. It is not
observed data. Actual analysis frames bypass demo generation entirely. See
`docs/FRONTEND_GRID_CONTRACT.md` for the integration and aggregation notes.

`land-detail.geojson` comes from Natural Earth's public-domain 1:10m land dataset,
simplified with topology preservation at 0.004 degrees. It replaces the 1:110m
outline when zoomed in and masks event fills over land.

Source: https://github.com/nvkelso/natural-earth-vector/blob/master/geojson/ne_10m_land.geojson

Rebuild detailed land and the synthetic fixture with
`python scripts/build-map-demo.py /path/to/ne_10m_land.geojson` (requires Shapely).

`relief/` contains local grayscale Web Mercator tiles generated from the elevation
texture at https://cdn.jsdelivr.net/npm/three-globe/example/img/earth-topology.png,
masked with the Natural Earth land polygons. It retains only topographic relief;
ocean is light gray and land is charcoal. No natural-color or satellite imagery
is used. Tiles cover zoom levels 0–3 and fade when inspecting individual events.
