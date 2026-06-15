/*
 * Vitest stub for `@/app/fonts`. `next/font/{local,google}` is a build-time
 * transform that only resolves inside the Next compiler — importing the real
 * module in jsdom/ESM throws ("Directory import … next/font/local is not
 * supported"). Components only consume the `.variable` class string, so the
 * stub hands back the same shape (a stable class token) and the render path
 * is otherwise exercised for real. Aliased in `vitest.config.ts`.
 */
const face = (variable: string) => ({ variable, className: variable, style: { fontFamily: variable } });

export const GeistSans = face("font-geist-sans");
export const GeistMono = face("font-geist-mono");
export const JetBrainsMonoNerd = face("font-jetbrains-nerd");
