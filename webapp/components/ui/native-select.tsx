import * as React from "react";
import { cn } from "@/lib/utils";

// A native <select> styled like input.tsx. shadcn's Select is a radix combobox
// (@radix-ui/react-select, not a dependency here); for the create form's repo /
// agent / branch / base pickers a native select is the right, dep-free tool —
// it's a closed list, keyboard-accessible for free, and avoids dragging in a
// portal-heavy combobox the design doesn't otherwise need.
export const NativeSelect = React.forwardRef<
  HTMLSelectElement,
  React.SelectHTMLAttributes<HTMLSelectElement>
>(({ className, children, ...props }, ref) => (
  <select
    ref={ref}
    className={cn(
      "flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-base ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50 md:text-sm",
      className,
    )}
    {...props}
  >
    {children}
  </select>
));
NativeSelect.displayName = "NativeSelect";
