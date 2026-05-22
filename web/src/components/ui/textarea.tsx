import * as React from "react";

import { cn } from "@/lib/utils";

function Textarea({ className, ...props }: React.ComponentProps<"textarea">) {
  return (
    <textarea
      data-slot="textarea"
      className={cn(
        "border-white/[0.1] placeholder:text-white/25 focus-visible:border-white/20 focus-visible:ring-white/[0.08] flex min-h-32 w-full rounded-[24px] border bg-white/[0.06] px-4 py-3 text-sm text-white/85 shadow-sm outline-none focus-visible:ring-[3px] disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      {...props}
    />
  );
}

export { Textarea };
