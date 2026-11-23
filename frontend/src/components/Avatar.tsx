import { useState } from "react";
import type { SocialAccount } from "../types";

export default function Avatar({ account, large = false }: { account: SocialAccount; large?: boolean }) {
  const [broken, setBroken] = useState(false);
  const cls = `avatar${large ? " lg" : ""}`;
  if (account.avatar_url && !broken) {
    return (
      <img
        className={cls}
        src={account.avatar_url}
        alt=""
        referrerPolicy="no-referrer"
        onError={() => setBroken(true)}
      />
    );
  }
  const initial = (account.display_name || account.handle).charAt(0).toUpperCase();
  return (
    <span className={`${cls} placeholder`} aria-hidden>
      {initial}
    </span>
  );
}
