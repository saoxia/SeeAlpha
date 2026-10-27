type SeeAlphaLogoProps = {
  className?: string;
  inverse?: boolean;
};

export function SeeAlphaLogo({ className = "", inverse = false }: SeeAlphaLogoProps) {
  return (
    <span
      className={`seealpha-logo${inverse ? " inverse" : ""}${className ? ` ${className}` : ""}`}
      role="img"
      aria-label="SeeAlpha"
    >
      <span>See</span>
      <b>Alpha</b>
    </span>
  );
}
