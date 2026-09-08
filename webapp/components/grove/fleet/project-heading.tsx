import { ProjectLabel } from "@/components/grove/entity";
import { workspaceCountLabel } from "./filter";

/** Only populated groups reach this heading; a project is not an empty-state card. */
export function ProjectHeading({
  name,
  count,
}: {
  name: string;
  count: number;
}): React.ReactNode {
  return (
    <h2 className="flex min-w-0 items-center justify-between gap-3 text-xs font-medium text-content-secondary" data-testid="fleet-project-heading">
      <ProjectLabel name={name} />
      <span className="shrink-0 font-normal tabular-nums text-content-tertiary">
        {workspaceCountLabel(count, count)}
      </span>
    </h2>
  );
}
