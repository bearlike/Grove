import * as React from "react";
import { cn } from "@/lib/utils";

// Plain styled <label> — shadcn's Label wraps @radix-ui/react-label, which we
// don't depend on (and don't need: native <label htmlFor> gives the same
// click-to-focus + a11y association). Keep the shadcn type/peer-disabled
// styling so form labels read identically to the rest of the kit.
export const Label = React.forwardRef<
  HTMLLabelElement,
  React.LabelHTMLAttributes<HTMLLabelElement>
>(({ className, ...props }, ref) => (
  <label
    ref={ref}
    className={cn(
      "text-sm font-medium leading-none text-foreground peer-disabled:cursor-not-allowed peer-disabled:opacity-70",
      className,
    )}
    {...props}
  />
));
Label.displayName = "Label";
