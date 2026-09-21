"use client";

import { ArrowDownIcon, ArrowUpIcon, FileDiffIcon } from "lucide-react";

import { Figure, Glyph, Groups, Section } from "@/components/grove/shell/footer/primitives";
import { abbreviate } from "@/components/grove/usage/format";
import { hasGitActivity, type GitFacts } from "@/lib/grove/adapters/footer";

/**
 * The open workspace's git position — three groups, never one figure.
 *
 * `ahead`/`behind` compares the BASE BRANCH, `+`/`−` is the branch delta since
 * the creation anchor, and `dirty` is what is uncommitted right now. The engine
 * keeps them apart because they answer different questions, so collapsing them
 * into one "changes" number here would undo that on the one surface where a
 * reader is least able to ask. `ahead`/`behind` stay INSIDE one group: two
 * directions of a single comparison, and a seam between them would present
 * them as independent facts.
 */
export function GitSection({ git }: { git: GitFacts | null | undefined }): React.ReactNode {
  if (!hasGitActivity(git)) return null;

  return (
    <Section id="footer-git" className="shrink-0">
      <Groups>
        {git.ahead > 0 || git.behind > 0 ? (
          <span className="inline-flex shrink-0 items-center gap-1.5" data-testid="footer-git-sync">
            {git.ahead > 0 ? (
              <span className="inline-flex items-center gap-0.5 text-success">
                <Glyph Icon={ArrowUpIcon} />
                <span className="tabular-nums">{git.ahead}</span>
              </span>
            ) : null}
            {git.behind > 0 ? (
              <span className="inline-flex items-center gap-0.5 text-warning">
                <Glyph Icon={ArrowDownIcon} />
                <span className="tabular-nums">{git.behind}</span>
              </span>
            ) : null}
          </span>
        ) : null}
        {git.added > 0 || git.removed > 0 ? (
          <span
            className="inline-flex shrink-0 items-center gap-1.5"
            title={`${git.added} added, ${git.removed} removed since this workspace was created`}
            data-testid="footer-git-delta"
          >
            <span className="tabular-nums text-success">+{abbreviate(git.added)}</span>
            <span className="tabular-nums text-destructive">−{abbreviate(git.removed)}</span>
          </span>
        ) : null}
        {git.dirty > 0 ? (
          <Figure Icon={FileDiffIcon} count={git.dirty} word="dirty" tone="text-warning" testId="footer-git-dirty" />
        ) : null}
      </Groups>
    </Section>
  );
}
