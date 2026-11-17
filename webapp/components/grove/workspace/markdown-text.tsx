"use client";

/**
 * A PORT of `components/assistant-ui/markdown-text.tsx`, kept diffable against it.
 *
 * GROVE DELTA 1 — a ```` ```mermaid ```` fence renders as a diagram. The vendored
 * renderer hard-codes `components={defaultComponents}` and passes no
 * `componentsByLanguage`, which is the only seam upstream's own Mermaid recipe
 * plugs into, so this file exists to pass it. The diagram is `./mermaid-diagram`,
 * the port of the vendored element that draws with mermaid itself, loaded lazily
 * so a transcript with no diagram never downloads the engine.
 *
 * GROVE DELTA 2 — `CodeHeader` takes `children`, the actions placed before Copy.
 * A Mermaid block keeps the ordinary header (language, Copy) and adds one toggle
 * between the diagram and its source, so it still reads as a code block.
 *
 * GROVE DELTA 3 — fences, inline code and table heads sit on the surface ladder,
 * not on `--muted`. Upstream's `bg-muted/30` body and `bg-muted/50` header are
 * tuned for a white page; on Grove's 0.95 L light page `--muted` (0.967) is
 * LIGHTER than the datum, so header, body and page composited within 0.01 L of
 * each other and the fence had no planes at all. The header is the cards'
 * `surface-header` band and the body is a `sunken` well (design-system §4.3:
 * code blocks recess), which reads in both themes and on a raised card too.
 *
 * Everything else is upstream's.
 */

import "@assistant-ui/react-markdown/styles/dot.css";

import { useAuiState } from "@assistant-ui/react";
import {
  type CodeHeaderProps,
  MarkdownTextPrimitive,
  type SyntaxHighlighterProps,
  unstable_memoizeMarkdownComponents as memoizeMarkdownComponents,
  useIsMarkdownCodeBlock,
} from "@assistant-ui/react-markdown";
import remarkGfm from "remark-gfm";
import { type FC, type ReactNode, Suspense, lazy, memo, useState } from "react";
import { CheckIcon, CodeIcon, CopyIcon, WorkflowIcon } from "lucide-react";

import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import { cn } from "@/lib/utils";

const MarkdownTextImpl = () => {
  return (
    <MarkdownTextPrimitive
      remarkPlugins={[remarkGfm]}
      className="aui-md"
      components={defaultComponents}
      componentsByLanguage={componentsByLanguage}
      defer
    />
  );
};

export const MarkdownText = memo(MarkdownTextImpl);

const MermaidDiagram = lazy(() =>
  import("./mermaid-diagram").then((module) => ({
    default: module.MermaidDiagram,
  })),
);

/** Which face a Mermaid block shows; the source is always one click away. */
export type MermaidView = "diagram" | "code";

/**
 * A Mermaid fence as a code block whose body is the rendered diagram.
 *
 * Header and body share the view state, so this one component draws both and the
 * fence's own `CodeHeader` slot is emptied (`MermaidCodeHeader`).
 *
 * @param props.components - The fence's own `Pre`/`Code`, so the source view is
 *   byte-for-byte the block an ordinary fence would have drawn
 * @returns The header, then the diagram; the source while the renderer chunk
 *   loads or while the message is still streaming a half-written diagram
 */
const MermaidBlock: FC<SyntaxHighlighterProps> = ({
  code,
  language,
  components: { Pre, Code },
}) => {
  const [view, setView] = useState<MermaidView>("diagram");
  const isStreaming = useAuiState((s) => s.part.status.type === "running");
  const source = (
    <Pre>
      <Code>{code}</Code>
    </Pre>
  );

  return (
    <>
      <CodeHeader language={language} code={code}>
        <TooltipIconButton
          tooltip={view === "diagram" ? "Show code" : "Show diagram"}
          aria-pressed={view === "code"}
          onClick={() => setView(view === "diagram" ? "code" : "diagram")}
        >
          {view === "diagram" ? <CodeIcon /> : <WorkflowIcon />}
        </TooltipIconButton>
      </CodeHeader>
      {view === "code" ? (
        source
      ) : (
        <Suspense fallback={source}>
          <MermaidDiagram
            code={code}
            streaming={isStreaming}
            className="border-surface-edge bg-surface-sunken rounded-t-none rounded-b-xl border border-t-0"
          />
        </Suspense>
      )}
    </>
  );
};

/** Empties the fence's header slot; `MermaidBlock` draws its own. */
const MermaidCodeHeader: FC<CodeHeaderProps> = () => null;

/** Module-level so `CodeOverride`'s memo sees one stable reference. */
const componentsByLanguage = {
  mermaid: { CodeHeader: MermaidCodeHeader, SyntaxHighlighter: MermaidBlock },
};

const CodeHeader: FC<CodeHeaderProps & { children?: ReactNode }> = ({
  language,
  code,
  children,
}) => {
  const { isCopied, copyToClipboard } = useCopyToClipboard();
  const onCopy = () => {
    if (!code || isCopied) return;
    copyToClipboard(code);
  };

  return (
    <div className="aui-code-header-root border-surface-edge surface-header mt-3 flex items-center justify-between rounded-t-xl border border-b-0 px-3.5 py-1.5 text-xs">
      <span className="aui-code-header-language text-muted-foreground font-medium lowercase">
        {language}
      </span>
      <div className="flex items-center">
        {children}
        <TooltipIconButton tooltip="Copy" onClick={onCopy}>
          {!isCopied && (
            <CopyIcon className="animate-in zoom-in-75 fade-in duration-150" />
          )}
          {isCopied && (
            <CheckIcon className="animate-in zoom-in-50 fade-in duration-200 ease-out" />
          )}
        </TooltipIconButton>
      </div>
    </div>
  );
};

const useCopyToClipboard = ({
  copiedDuration = 3000,
}: {
  copiedDuration?: number;
} = {}) => {
  const [isCopied, setIsCopied] = useState<boolean>(false);

  const copyToClipboard = (value: string) => {
    if (!value || typeof navigator === "undefined" || !navigator.clipboard) {
      return;
    }

    navigator.clipboard.writeText(value).then(
      () => {
        setIsCopied(true);
        setTimeout(() => setIsCopied(false), copiedDuration);
      },
      () => {},
    );
  };

  return { isCopied, copyToClipboard };
};

const defaultComponents = memoizeMarkdownComponents({
  h1: ({ className, ...props }) => (
    <h1
      className={cn(
        "aui-md-h1 mt-5 mb-2 scroll-m-20 text-xl font-semibold first:mt-0 last:mb-0",
        className,
      )}
      {...props}
    />
  ),
  h2: ({ className, ...props }) => (
    <h2
      className={cn(
        "aui-md-h2 mt-5 mb-2 scroll-m-20 text-lg font-semibold first:mt-0 last:mb-0",
        className,
      )}
      {...props}
    />
  ),
  h3: ({ className, ...props }) => (
    <h3
      className={cn(
        "aui-md-h3 mt-4 mb-1.5 scroll-m-20 text-base font-semibold first:mt-0 last:mb-0",
        className,
      )}
      {...props}
    />
  ),
  h4: ({ className, ...props }) => (
    <h4
      className={cn(
        "aui-md-h4 mt-3.5 mb-1 scroll-m-20 text-base font-medium first:mt-0 last:mb-0",
        className,
      )}
      {...props}
    />
  ),
  h5: ({ className, ...props }) => (
    <h5
      className={cn(
        "aui-md-h5 mt-3 mb-1 text-sm font-semibold first:mt-0 last:mb-0",
        className,
      )}
      {...props}
    />
  ),
  h6: ({ className, ...props }) => (
    <h6
      className={cn(
        "aui-md-h6 mt-3 mb-1 text-sm font-medium first:mt-0 last:mb-0",
        className,
      )}
      {...props}
    />
  ),
  p: ({ className, ...props }) => (
    <p
      className={cn(
        "aui-md-p my-3 leading-relaxed first:mt-0 last:mb-0",
        className,
      )}
      {...props}
    />
  ),
  a: ({ className, ...props }) => (
    <a
      className={cn(
        "aui-md-a text-primary hover:text-primary/80 underline underline-offset-2",
        className,
      )}
      {...props}
    />
  ),
  blockquote: ({ className, ...props }) => (
    <blockquote
      className={cn(
        "aui-md-blockquote border-muted-foreground/30 text-muted-foreground my-3 border-s-2 ps-4",
        className,
      )}
      {...props}
    />
  ),
  ul: ({ className, ...props }) => (
    <ul
      className={cn(
        "aui-md-ul marker:text-muted-foreground my-3 ms-5 list-disc [&>li]:mt-1",
        className,
      )}
      {...props}
    />
  ),
  ol: ({ className, ...props }) => (
    <ol
      className={cn(
        "aui-md-ol marker:text-muted-foreground my-3 ms-5 list-decimal [&>li]:mt-1",
        className,
      )}
      {...props}
    />
  ),
  hr: ({ className, ...props }) => (
    <hr
      className={cn("aui-md-hr border-muted-foreground/20 my-3", className)}
      {...props}
    />
  ),
  table: ({ className, ...props }) => (
    <table
      className={cn(
        "aui-md-table my-3 w-full border-separate border-spacing-0 overflow-y-auto",
        className,
      )}
      {...props}
    />
  ),
  th: ({ className, ...props }) => (
    <th
      className={cn(
        "aui-md-th bg-surface-sunken px-3 py-1.5 text-start font-medium first:rounded-ss-lg last:rounded-se-lg [[align=center]]:text-center [[align=right]]:text-right",
        className,
      )}
      {...props}
    />
  ),
  td: ({ className, ...props }) => (
    <td
      className={cn(
        "aui-md-td border-muted-foreground/20 border-s border-b px-3 py-1.5 text-start last:border-e [[align=center]]:text-center [[align=right]]:text-right",
        className,
      )}
      {...props}
    />
  ),
  tr: ({ className, ...props }) => (
    <tr
      className={cn(
        "aui-md-tr m-0 border-b p-0 first:border-t [&:last-child>td:first-child]:rounded-es-lg [&:last-child>td:last-child]:rounded-ee-lg",
        className,
      )}
      {...props}
    />
  ),
  li: ({ className, ...props }) => (
    <li className={cn("aui-md-li leading-relaxed", className)} {...props} />
  ),
  strong: ({ className, ...props }) => (
    <strong
      className={cn("aui-md-strong font-semibold", className)}
      {...props}
    />
  ),
  sup: ({ className, ...props }) => (
    <sup
      className={cn("aui-md-sup [&>a]:text-xs [&>a]:no-underline", className)}
      {...props}
    />
  ),
  pre: ({ className, ...props }) => (
    <pre
      className={cn(
        "aui-md-pre border-surface-edge bg-surface-sunken overflow-x-auto rounded-t-none rounded-b-xl border border-t-0 p-3.5 text-[13px] leading-relaxed",
        className,
      )}
      {...props}
    />
  ),
  code: function Code({ className, ...props }) {
    const isCodeBlock = useIsMarkdownCodeBlock();
    return (
      <code
        className={cn(
          !isCodeBlock &&
            "aui-md-inline-code bg-surface-sunken rounded-md px-1.5 py-0.5 font-mono text-[0.85em]",
          className,
        )}
        {...props}
      />
    );
  },
  CodeHeader,
});
