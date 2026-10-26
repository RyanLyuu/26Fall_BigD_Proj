import { useEffect, useRef, useState } from 'react';
import maplibregl from 'maplibre-gl';
import type { FeatureCollection, LineString } from 'geojson';
import { AimOutlined, GlobalOutlined, MinusOutlined, PlusOutlined, SelectOutlined } from '@ant-design/icons';
import { useAnalysisStore } from '../store/useAnalysisStore';
import eventData from '../mock/events.json';
import type { OceanEvent } from '../types';
import { eventLayers } from '../lib/eventLayers';
import { eventBoundary, gridGeoJSON } from '../lib/oceanGrid';

const events = eventData as OceanEvent[];
const homeCenter: [number, number] = [95, 20];
type InteractionMode = 'rotate' | 'select';

function graticule(): FeatureCollection<LineString> {
  const features: FeatureCollection<LineString>['features'] = [];
  for (let lng = -180; lng < 180; lng += 30) {
    features.push({ type: 'Feature', properties: {}, geometry: {
      type: 'LineString', coordinates: Array.from({ length: 85 }, (_, i) => [lng, -84 + i * 2]),
    } });
  }
  for (let lat = -60; lat <= 60; lat += 30) {
    features.push({ type: 'Feature', properties: {}, geometry: {
      type: 'LineString', coordinates: Array.from({ length: 181 }, (_, i) => [-180 + i * 2, lat]),
    } });
  }
  return { type: 'FeatureCollection', features };
}

// Keep the entire globe in view across desktop and narrow layouts.
function globeZoom(container: HTMLDivElement) {
  return Math.max(0, Math.log2(Math.min(container.clientWidth, container.clientHeight) / 140));
}

function syncEvents(map: maplibregl.Map) {
  if (!map.getLayer('event-cells')) return;
  const { activeLayer, currentEvent, gridFrame } = useAnalysisStore.getState();
  const filter: maplibregl.FilterSpecification = activeLayer === '全部事件'
    ? ['has', 'combination'] : ['==', ['get', 'combination'], activeLayer];
  map.setFilter('event-cells', filter);
  const visibleFrame = activeLayer === '全部事件' ? gridFrame : {
    ...gridFrame, cells: gridFrame.cells.filter(cell => cell.combination === activeLayer),
  };
  (map.getSource('selection') as maplibregl.GeoJSONSource).setData(eventBoundary(visibleFrame, currentEvent.eventId));
}

export default function OceanMap() {
  const ref = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const [mode, setMode] = useState<InteractionMode>('rotate');
  const modeRef = useRef<InteractionMode>('rotate');
  const [mapError, setMapError] = useState(false);
  const [focusHint, setFocusHint] = useState(false);
  const s = useAnalysisStore();

  useEffect(() => {
    const container = ref.current;
    if (!container) return;
    let map: maplibregl.Map;
    try {
      map = new maplibregl.Map({
        container,
        style: {
          version: 8,
          projection: { type: 'globe' },
          sky: {
            'sky-color': '#ffffff', 'horizon-color': '#b9d7f4',
            'sky-horizon-blend': 0.08, 'atmosphere-blend': 0.75,
          },
          sources: {
            land: { type: 'geojson', data: `${import.meta.env.BASE_URL}maps/land.geojson`, attribution: 'Natural Earth' },
            'land-detail': { type: 'geojson', data: `${import.meta.env.BASE_URL}maps/land-detail.geojson`, attribution: 'Natural Earth', tolerance: 0.1, maxzoom: 9 },
            relief: {
              type: 'raster', tileSize: 256, minzoom: 0, maxzoom: 3,
              tiles: [`${new URL(`${import.meta.env.BASE_URL}maps/relief/`, window.location.href)}{z}/{y}/{x}.jpg`],
              attribution: 'Earth topography',
            },
            grid: { type: 'geojson', data: graticule() },
            cells: { type: 'geojson', data: gridGeoJSON(useAnalysisStore.getState().gridFrame) },
            selection: { type: 'geojson', data: { type: 'FeatureCollection', features: [] } },
          },
          layers: [
            { id: 'ocean', type: 'background', paint: { 'background-color': ['interpolate', ['linear'], ['zoom'], 2.8, '#d7dadd', 3.5, '#ffffff'] } },
            { id: 'land', type: 'fill', source: 'land', maxzoom: 3, paint: { 'fill-color': ['interpolate', ['linear'], ['zoom'], 3, '#454a4f', 5, '#bec2c6'] } },
            { id: 'terrain-relief', type: 'raster', source: 'relief', paint: {
              'raster-opacity': ['interpolate', ['linear'], ['zoom'], 2.8, 1, 3.4, 0],
              'raster-fade-duration': 0,
            } },
            { id: 'graticule', type: 'line', source: 'grid', paint: {
              'line-color': '#fff', 'line-width': 0.5, 'line-opacity': 0.04,
            } },
            { id: 'event-cells', type: 'fill', source: 'cells', paint: {
              'fill-color': ['get', 'color'], 'fill-opacity': 1, 'fill-antialias': false,
            } },
            { id: 'event-outline', type: 'line', source: 'selection', paint: {
              'line-color': '#313747',
              'line-width': ['interpolate', ['linear'], ['zoom'], 2, 0, 4, 0.7, 7, 1.2],
              'line-opacity': 0.5,
            } },
            // The detailed land mask sits above the measured cells: coastal
            // pixels stay square but never paint over islands or the mainland.
            { id: 'detail-land', type: 'fill', source: 'land-detail', minzoom: 3, paint: {
              'fill-color': ['interpolate', ['linear'], ['zoom'], 3, '#62666a', 3.5, '#d1d2d2'],
            } },
            { id: 'detail-coastline', type: 'line', source: 'land-detail', minzoom: 3, paint: {
              'line-color': '#494d50', 'line-width': ['interpolate', ['linear'], ['zoom'], 3, 0.25, 6, 0.7],
              'line-opacity': 0.8,
            } },
          ],
        },
        center: homeCenter,
        zoom: globeZoom(container),
        minZoom: 0,
        maxZoom: 7,
        maxPitch: 0,
        dragRotate: false,
        touchPitch: false,
        pitchWithRotate: false,
        renderWorldCopies: false,
        attributionControl: { compact: true },
      });
    } catch {
      setMapError(true);
      return;
    }
    mapRef.current = map;
    map.touchZoomRotate.disableRotation();
    if (modeRef.current === 'select') {
      map.dragPan.disable();
      map.keyboard.disable();
      map.getCanvas().style.cursor = 'crosshair';
    }
    setFocusHint(false);
    map.getCanvas().setAttribute('aria-label', modeRef.current === 'rotate'
      ? '海洋事件地球仪，可拖动旋转，使用加减按钮缩放'
      : '海洋事件地球仪，点击事件查看详情，使用加减按钮缩放');
    map.on('load', () => syncEvents(map));
    map.on('click', event => {
      if (modeRef.current !== 'select') return;
      const { x, y } = event.point;
      const precise = map.queryRenderedFeatures(event.point, { layers: ['event-cells'] });
      const features = precise.length ? precise : map.queryRenderedFeatures([[x - 9, y - 9], [x + 9, y + 9]], {
        layers: ['event-cells'],
      });
      const candidates = events.filter(item => features.some(feature =>
        String(feature.properties.eventKeys).includes(`|${item.eventId}|`)));
      if (!candidates.length) return;
      // Nearby events overlap at globe scale; first reveal their actual footprints.
      if (map.getZoom() < 3.5) {
        map.flyTo({ center: [features[0].properties.longitude, features[0].properties.latitude], zoom: 3.7, duration: 900 });
        setFocusHint(true);
        return;
      }
      const selected = candidates.sort((a, b) => {
        const pa = map.project(a.coordinates);
        const pb = map.project(b.coordinates);
        return Math.hypot(pa.x - x, pa.y - y) - Math.hypot(pb.x - x, pb.y - y);
      })[0];
      useAnalysisStore.getState().set({ currentEvent: selected, eventType: selected.type });
      setFocusHint(false);
    });
    const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 12, className: 'grid-popup' });
    map.on('mousemove', event => {
      if (modeRef.current !== 'select') { popup.remove(); return; }
      const hits = map.queryRenderedFeatures(event.point, { layers: ['event-cells'] });
      map.getCanvas().style.cursor = hits.length ? 'pointer' : 'crosshair';
      if (!hits.length) { popup.remove(); return; }
      const cell = hits[0].properties;
      const text = document.createElement('div');
      const title = document.createElement('strong');
      title.textContent = String(cell.label);
      const coordinates = document.createElement('span');
      const longitude = Number(cell.longitude);
      const latitude = Number(cell.latitude);
      coordinates.textContent = `${Math.abs(longitude).toFixed(3)}°${longitude < 0 ? 'W' : 'E'} · ${Math.abs(latitude).toFixed(3)}°${latitude < 0 ? 'S' : 'N'}`;
      const fraction = document.createElement('span');
      fraction.textContent = cell.fraction === null || cell.fraction === undefined
        ? '受影响水层占比待接入' : `${useAnalysisStore.getState().gridFrame.source === 'demo' ? '演示占比' : '受影响水层'} ${(Number(cell.fraction) * 100).toFixed(0)}%`;
      text.append(title, coordinates, fraction);
      popup.setLngLat(event.lngLat).setDOMContent(text).addTo(map);
    });
    const removePopup = () => popup.remove();
    map.getCanvas().addEventListener('mouseleave', removePopup);
    map.on('movestart', removePopup);
    let overviewZoom = globeZoom(container);
    const observer = new ResizeObserver(() => {
      const isOverview = Math.abs(map.getZoom() - overviewZoom) < 0.05;
      map.resize();
      overviewZoom = globeZoom(container);
      if (isOverview) map.setZoom(overviewZoom);
    });
    observer.observe(container);
    return () => {
      observer.disconnect();
      map.getCanvas().removeEventListener('mouseleave', removePopup);
      popup.remove();
      mapRef.current = null;
      map.remove();
    };
  }, []);

  useEffect(() => {
    if (mapRef.current) syncEvents(mapRef.current);
  }, [s.currentEvent, s.activeLayer]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const source = map.getSource('cells') as maplibregl.GeoJSONSource | undefined;
    source?.setData(gridGeoJSON(s.gridFrame));
    if (source) syncEvents(map);
  }, [s.gridFrame]);

  function toggleMode() {
    const next = mode === 'rotate' ? 'select' : 'rotate';
    modeRef.current = next;
    setMode(next);
    setFocusHint(false);
    const map = mapRef.current;
    if (!map) return;
    map.stop();
    if (next === 'rotate') {
      map.dragPan.enable();
      map.keyboard.enable();
      map.getCanvas().style.cursor = 'grab';
    } else {
      map.dragPan.disable();
      map.keyboard.disable();
      map.getCanvas().style.cursor = 'crosshair';
    }
    map.getCanvas().setAttribute('aria-label', next === 'rotate'
      ? '海洋事件地球仪，可拖动旋转，使用加减按钮缩放'
      : '海洋事件地球仪，点击事件查看详情，使用加减按钮缩放');
  }

  function resetView() {
    if (!ref.current) return;
    setFocusHint(false);
    mapRef.current?.flyTo({ center: homeCenter, zoom: globeZoom(ref.current), bearing: 0, pitch: 0, duration: 900 });
  }

  return <section className={`map-wrap panel mode-${mode}`} aria-label="事件空间分布">
    <div className="map-toolbar">
      <h2>复合极端事件分布</h2>
      <button className="mode-switch" onClick={toggleMode} aria-pressed={mode === 'select'}
        title={mode === 'rotate' ? '切换为事件点击模式' : '切换为地球旋转模式'}>
        {mode === 'rotate' ? <GlobalOutlined/> : <SelectOutlined/>}
        <span>{mode === 'rotate' ? '旋转模式' : '事件模式'}</span>
        <span className="switch-track" aria-hidden="true"><span/></span>
      </button>
    </div>
    <div ref={ref} className="map-canvas"/>
    {mapError && <div className="map-error" role="alert">地球仪暂时无法显示，请使用支持 WebGL 的浏览器。</div>}
    <div className="map-instructions" aria-live="polite">
      {mode === 'rotate' ? '拖动旋转地球 · 滚轮缩放' : focusHint ? '已展开事件格点 · 点击查看详情' : '点击格点查看详情 · 滚轮缩放'}
    </div>
    <div className="zoom">
      <button title="回到地球全景" aria-label="回到地球全景" onClick={resetView}><AimOutlined/></button>
      <button title="放大" aria-label="放大" onClick={() => mapRef.current?.zoomIn()}><PlusOutlined/></button>
      <button title="缩小" aria-label="缩小" onClick={() => mapRef.current?.zoomOut()}><MinusOutlined/></button>
    </div>
    <div className="map-legend" aria-label="事件图层筛选">
      <div className="legend-heading">
        <span>{s.gridFrame.depthRange[0]}–{s.gridFrame.depthRange[1]} m 受影响水层占比</span>
        <span className="legend-note">{s.gridFrame.source === 'demo' ? '合成演示 · 非观测数据' : s.gridFrame.time}</span>
        <button className={s.activeLayer === '全部事件' ? 'active' : ''}
          aria-pressed={s.activeLayer === '全部事件'} onClick={() => s.set({ activeLayer: '全部事件' })}>全部</button>
      </div>
      <div className="legend-categories">
        {eventLayers.map(layer => <button key={layer.key}
          className={s.activeLayer === layer.key ? 'active' : ''} aria-pressed={s.activeLayer === layer.key}
          aria-label={layer.label} onClick={() => s.set({ activeLayer: layer.key })}>
          <span className="legend-ramp" aria-hidden="true">
            <span className="legend-swatches" style={{ background: `linear-gradient(to right, ${layer.colors.join(',')})` }}/>
            <span className="legend-ticks"><span>25</span><span>75%</span></span>
          </span>
          <span className="legend-label">{layer.label}</span>
        </button>)}
      </div>
    </div>
    <div className="map-status">
      <span>当前日期 <b>{s.gridFrame.time ?? s.date}</b></span>
      <span>当前深度 <b>{s.depth} m</b></span>
      <span>空间格点 <b>{s.gridFrame.resolutionDegrees}° × {s.gridFrame.resolutionDegrees}°</b></span>
    </div>
  </section>;
}
