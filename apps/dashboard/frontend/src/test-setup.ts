import "@testing-library/jest-dom/vitest";
import { vi } from "vitest";

vi.mock("leaflet", () => {
  class FakeLayer {
    addTo() {
      return this;
    }
    remove() {
      return this;
    }
    setLatLng() {
      return this;
    }
    setIcon() {
      return this;
    }
    setLatLngs() {
      return this;
    }
  }

  const map = {
    on: vi.fn(),
    off: vi.fn(),
    setView: vi.fn(),
    getZoom: () => 17,
    remove: vi.fn(),
  };

  return {
    default: {
      map: vi.fn(() => map),
      tileLayer: vi.fn(() => ({ addTo: vi.fn() })),
      marker: vi.fn(() => new FakeLayer()),
      polyline: vi.fn(() => new FakeLayer()),
      divIcon: vi.fn((options: { html: string }) => options),
      latLng: (lat: number, lng: number) => ({ lat, lng }),
    },
  };
});