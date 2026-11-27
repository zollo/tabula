// The Tabula mark: a faceted eye whose iris is a speech bubble, on a black-to-red
// rounded square. Same artwork as the favicon and app icons in public/ (public/icons/*),
// drawn by scripts/make_icons.py.
export default function Logo({ size = 28 }: { size?: number }) {
  return <img className="logo" src="/icons/logo-256.png" width={size} height={size} alt="" aria-hidden draggable={false} />;
}
