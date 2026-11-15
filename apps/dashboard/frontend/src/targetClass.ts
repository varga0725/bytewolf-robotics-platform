export type TargetClass = "sitl_verified" | "unknown" | "real_suspected";

export type TargetClassBanner = {
  targetClass: TargetClass;
  title: string;
  detail: string;
  showDangerBanner: boolean;
};

export function targetClassBanner(targetClass: TargetClass | null | undefined): TargetClassBanner {
  if (targetClass === "sitl_verified") {
    return {
      targetClass: "sitl_verified",
      title: "SITL (ellenőrzött)",
      detail: "A telemetria több független szimulátor-jelzőt mutat.",
      showDangerBanner: false,
    };
  }
  if (targetClass === "real_suspected") {
    return {
      targetClass: "real_suspected",
      title: "VALÓDI (gyanított)",
      detail: "A rendszer hardver-szerű PX4-jelzőket lát. Nincs igazolt szimulátor.",
      showDangerBanner: true,
    };
  }
  return {
    targetClass: "unknown",
    title: "ISMERETLEN CÉL",
    detail: "Nincs igazolt szimulátor-állapot. A kötés nem minősíthető biztonságos SITL-nek.",
    showDangerBanner: true,
  };
}

export function parseTargetClass(value: unknown): TargetClass | null {
  if (value === "sitl_verified" || value === "unknown" || value === "real_suspected") {
    return value;
  }
  return null;
}