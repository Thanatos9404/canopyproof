import L from "leaflet";
import { useEffect, useRef } from "react";
import type { Bounds, Polygon, Status } from "../lib/api";
import { STATUS_META } from "./ui";

export type MapFeature = { id: string; label: string; geometry: Polygon; status: Status };
export type MapOverlay = { url: string; bounds: Bounds; opacity?: number; pixelated?: boolean };

const BASEMAPS = {
  light: {
    url: "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}",
    attribution: 'Tiles &copy; Esri, HERE, Garmin, &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    className: "tiles-basemap",
    maxZoom: 16,
  },
  imagery: {
    url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    attribution: "Imagery &copy; Esri, Maxar, Earthstar Geographics and the GIS User Community",
    className: "",
    maxZoom: 17,
  },
} as const;

const toLatLngs = (g: Polygon) => g.coordinates[0].map(([lon, lat]) => [lat, lon] as [number, number]);

/**
 * Leaflet map restyled to the paper palette (Esri Light Gray Canvas or Esri imagery, attributed), with cell
 * outlines coloured by integrity status and optional Sentinel-2 / change-map image overlays.
 */
export function CellMap({
  features,
  overlays = [],
  selected,
  onSelect,
  basemap = "light",
  height = 360,
  fit = "features",
  label,
}: {
  features: MapFeature[];
  overlays?: MapOverlay[];
  selected?: string | null;
  onSelect?: (id: string) => void;
  basemap?: keyof typeof BASEMAPS;
  height?: number;
  fit?: "features" | "selected";
  label: string;
}) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const tiles = useRef<L.TileLayer | null>(null);
  const layer = useRef<L.LayerGroup | null>(null);

  useEffect(() => {
    if (!el.current || map.current) return;
    map.current = L.map(el.current, { zoomControl: true, attributionControl: true, scrollWheelZoom: false, worldCopyJump: true });
    map.current.attributionControl.setPrefix(false);
    layer.current = L.layerGroup().addTo(map.current);
    map.current.setView([20, 0], 2);
    return () => {
      map.current?.remove();
      map.current = null;
    };
  }, []);

  useEffect(() => {
    if (!map.current) return;
    tiles.current?.remove();
    const b = BASEMAPS[basemap];
    tiles.current = L.tileLayer(b.url, { attribution: b.attribution, className: b.className, maxZoom: b.maxZoom }).addTo(
      map.current,
    );
    tiles.current.bringToBack();
  }, [basemap]);

  useEffect(() => {
    const m = map.current;
    const g = layer.current;
    if (!m || !g) return;
    g.clearLayers();
    for (const o of overlays) {
      const img = L.imageOverlay(o.url, o.bounds, { opacity: o.opacity ?? 1, className: o.pixelated ? "pixelated" : "" });
      g.addLayer(img);
    }
    const all: L.LatLngExpression[] = [];
    for (const f of features) {
      const colour = getComputedStyle(document.documentElement).getPropertyValue(STATUS_META[f.status].colour.slice(4, -1)).trim() || "#757170";
      const latlngs = toLatLngs(f.geometry);
      all.push(...latlngs);
      const isSel = f.id === selected;
      const poly = L.polygon(latlngs, {
        color: isSel ? getComputedStyle(document.documentElement).getPropertyValue("--accent").trim() : colour,
        weight: isSel ? 2.5 : 2,
        fill: overlays.length === 0,
        fillOpacity: 0.12,
        fillColor: colour,
      });
      poly.bindTooltip(f.label, { className: "cp-tip", direction: "top", sticky: true });
      if (onSelect) poly.on("click", () => onSelect(f.id));
      g.addLayer(poly);
      const c = poly.getBounds().getCenter();
      const dot = L.circleMarker(c, { radius: 6, color: "#ffffff", weight: 2, fillColor: colour, fillOpacity: 1 });
      dot.bindTooltip(f.label, { className: "cp-tip", direction: "top" });
      if (onSelect) dot.on("click", () => onSelect(f.id));
      g.addLayer(dot);
    }
    const target = fit === "selected" && selected ? features.find((f) => f.id === selected) : null;
    if (target) {
      m.fitBounds(L.latLngBounds(toLatLngs(target.geometry)).pad(0.35), { animate: false });
    } else if (all.length) {
      m.fitBounds(L.latLngBounds(all).pad(0.4), { animate: false, maxZoom: 5 });
    }
  }, [features, overlays, selected, onSelect, fit]);

  return <div className="cp-map" style={{ height }} role="region" aria-label={label} ref={el} />;
}
