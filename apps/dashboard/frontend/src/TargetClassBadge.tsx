import { targetClassBanner, type TargetClass } from "./targetClass";

type TargetClassBadgeProps = {
  targetClass: TargetClass | null;
  /** Larger strip cell copy for the operator status row. */
  variant?: "strip" | "compact";
};

export function TargetClassBadge({ targetClass, variant = "compact" }: TargetClassBadgeProps) {
  const banner = targetClassBanner(targetClass);
  if (variant === "strip") {
    return (
      <div>
        <p className="eyebrow">PX4 CÉL MINŐSÍTÉS</p>
        <strong>{banner.title}</strong>
        <span>{banner.detail}</span>
      </div>
    );
  }

  return (
    <p
      className={`target-class-badge target-class-badge--${banner.targetClass}`}
      data-target-class={banner.targetClass}
      aria-live="polite"
    >
      <span className="target-class-badge__label">{banner.title}</span>
    </p>
  );
}

type TargetClassDangerBannerProps = {
  targetClass: TargetClass | null;
};

export function TargetClassDangerBanner({ targetClass }: TargetClassDangerBannerProps) {
  const banner = targetClassBanner(targetClass);
  if (!banner.showDangerBanner) {
    return null;
  }
  return (
    <section
      className="target-class-danger-banner"
      role="alert"
      aria-label="PX4 cél figyelmeztetés"
    >
      <p className="eyebrow">PX4 CÉL FIGYELMEZTETÉS</p>
      <strong>{banner.title}</strong>
      <p>{banner.detail}</p>
    </section>
  );
}