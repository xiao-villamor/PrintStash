import * as React from "react";

import { inputClasses } from "../lib/input-classes";
import { cn } from "../lib/utils";

export interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {}

const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ className, type, ...props }, ref) => {
    return <input type={type} className={cn(inputClasses, className)} ref={ref} {...props} />;
  },
);
Input.displayName = "Input";

export { Input };
