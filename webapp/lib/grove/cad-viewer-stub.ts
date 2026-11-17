/**
 * Stands in for `@flyfish-dev/cad-viewer` (AGPL-3.0-only); see the alias in
 * `next.config.ts`. Opening a DWG/DWF artifact fails with this message instead
 * of loading a runtime Grove's licence cannot ship.
 */
const unavailable = (): never => {
  throw new Error("CAD preview (DWG/DWF) is not included in this Grove build.");
};

export class CadViewer {
  constructor() {
    unavailable();
  }
}

export const inferEntityKind = unavailable;
