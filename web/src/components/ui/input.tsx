import * as React from "react";

import { cn } from "@/lib/utils";

function Input({ className, type, ...props }: React.ComponentProps<"input">) {
  return (
    <input
      type={type}
      data-slot="input"
      className={cn(
        "border-white/[0.1] file:text-white/70 placeholder:text-white/25 selection:bg-white/20 selection:text-white flex h-11 w-full min-w-0 rounded-2xl border bg-white/[0.06] px-4 py-2 text-sm text-white/85 shadow-sm transition-[color,box-shadow] outline-none file:inline-flex file:h-7 file:border-0 file:bg-transparent file:text-sm file:font-medium disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50 focus-visible:border-white/20 focus-visible:ring-[3px] focus-visible:ring-white/[0.08]",
        className,
      )}
      {...props}
    />
  );
}

export { Input };
