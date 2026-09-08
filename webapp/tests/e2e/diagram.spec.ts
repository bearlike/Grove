import { expect, test } from "@playwright/test";

import { FIXTURE_WORKSPACES } from "./_fixtures";
import { dp } from "./density";

/**
 * What only a browser can prove about the Diagram tab.
 *
 * The protocol and the persistence races are pinned as pure functions in
 * `tests/unit` — re-asserting them through a browser buys flakiness. What is
 * genuinely browser-only is the CONDITION (does the tab exist at all, and only
 * where a descriptor does) and the frame's own attributes: a third-party
 * `src` that must never carry the document, and the read-only mount.
 *
 * The fake daemon serves a descriptor for `diagram-workspace` and nothing else.
 */
const DIAGRAM_WORKSPACE = "diagram-workspace";
const PLAIN_WORKSPACE = FIXTURE_WORKSPACES[0].id;

test.describe("diagram tab", () => {
  test("fits initial paint and major layout changes without resetting on minor shifts", async ({ page }) => {
    let fits = 0;
    await page.exposeFunction("recordDiagramFit", () => { fits += 1; });
    await page.route("https://embed.diagrams.net/**", (route) => route.fulfill({
      contentType: "text/html",
      body: `<html><body><script>
        let xml='';
        addEventListener('message', e => {
          const m=JSON.parse(e.data);
          if(m.action==='configure') parent.postMessage(JSON.stringify({event:'init'}),'*');
          if(m.action==='load'){xml=m.xml;parent.postMessage(JSON.stringify({event:'load'}),'*');}
          if(m.action==='export' && m.format==='xml') parent.postMessage(JSON.stringify({event:'export',xml,message:m}),'*');
          if(m.action==='fit') window.recordDiagramFit();
        });
        parent.postMessage(JSON.stringify({event:'configure'}),'*');
      </script></body></html>`,
    }));
    await page.addInitScript(() => {
      localStorage.setItem("grove.sidebar.collapsed", "true");
      localStorage.setItem("grove-workspace-view:diagram-workspace", "work");
      localStorage.setItem("grove-workspace-work-tab:diagram-workspace", "diagram");
    });
    await page.setViewportSize({ width: 1400, height: 900 });
    await page.goto(`/w/${DIAGRAM_WORKSPACE}`);
    await page.getByTestId("work-panel-tab-diagram").click();
    await expect.poll(() => fits).toBeGreaterThan(0);
    await page.waitForTimeout(600);
    const initial = fits;
    await page.setViewportSize({ width: 1390, height: 900 });
    await page.waitForTimeout(600);
    expect(fits).toBe(initial);
    await page.setViewportSize({ width: 1100, height: 750 });
    await expect.poll(() => fits).toBeGreaterThan(initial);
    await page.getByTestId("work-panel-tab-info").click();
    await page.waitForTimeout(400);
    const hidden = fits;
    await page.setViewportSize({ width: 1400, height: 900 });
    await page.waitForTimeout(600);
    expect(fits).toBe(hidden);
    await page.getByTestId("work-panel-tab-diagram").click();
    await expect.poll(() => fits).toBeGreaterThan(hidden);
    const beforeSplit = fits;
    await page.getByTestId("pane-split").click();
    await expect.poll(() => fits).toBeGreaterThan(beforeSplit);
    const beforeWork = fits;
    await page.getByTestId("pane-work").click();
    await expect.poll(() => fits).toBeGreaterThan(beforeWork);
    await page.unrouteAll({ behavior: "wait" });
  });
  test("conflicted browser draft can replace the backend only after confirmation", async ({ page }) => {
    const endpoint = `/api/grove/workspaces/${DIAGRAM_WORKSPACE}/diagram`;
    let forceConflict = true;
    const writes: { expected_revision: string; xml: string }[] = [];
    await page.route("**/api/grove/workspaces/diagram-workspace/diagram?*", async (route) => {
      if (route.request().method() === "PUT") {
        writes.push(route.request().postDataJSON());
        if (forceConflict) {
          await route.fulfill({ status: 409, json: { detail: { error: "diagram_conflict", message: "backend changed" } } });
          return;
        }
      }
      await route.continue();
    });
    await page.route("https://embed.diagrams.net/**", (route) => route.fulfill({
      contentType: "text/html",
      body: `<html><body><button id="edit">Edit client</button><script>
        let xml = '';
        addEventListener('message', event => {
          const message = JSON.parse(event.data);
          if (message.action === 'configure') parent.postMessage(JSON.stringify({event:'init'}), '*');
          if (message.action === 'load') { xml = message.xml; parent.postMessage(JSON.stringify({event:'load'}), '*'); }
          if (message.action === 'export' && message.format === 'xml') parent.postMessage(JSON.stringify({event:'export', xml, message}), '*');
          if (message.action === 'export' && message.format === 'png') parent.postMessage(JSON.stringify({event:'export', data:'data:image/png;base64,iVBORw0KGgo=', message}), '*');
        });
        document.getElementById('edit').onclick=()=>{xml=xml.replace('Page 1','Client choice');parent.postMessage(JSON.stringify({event:'autosave',xml}), '*');};
        parent.postMessage(JSON.stringify({event:'configure'}), '*');
      </script></body></html>`,
    }));
    await page.goto(`/w/${DIAGRAM_WORKSPACE}`);
    await page.getByTestId("work-panel-tab-diagram").click();
    await page.frameLocator('[data-testid="diagram-frame"]').getByRole("button", { name: "Edit client" }).click();
    await expect(page.getByTestId("diagram-save-state")).toHaveText("Conflict");
    const current = await (await page.request.get(endpoint)).json();
    const changed = await page.request.put(endpoint, { data: {
      session_id: current.diagram.session_id, expected_revision: current.revision,
      xml: current.xml.replace('Page 1', 'Agent choice'),
    } });
    expect(changed.ok()).toBeTruthy();
    const backend = await changed.json();
    await page.getByTestId("diagram-overwrite").click();
    await expect(page.getByRole("dialog")).toContainText("Backend and agent changes");
    await page.getByRole("button", { name: "Cancel", exact: true }).click();
    expect((await (await page.request.get(endpoint)).json()).revision).toBe(backend.revision);
    forceConflict = false;
    await page.getByTestId("diagram-overwrite").click();
    await page.getByTestId("diagram-overwrite-confirm").click();
    await expect(page.getByTestId("diagram-save-state")).toHaveText("Saved");
    const saved = await (await page.request.get(endpoint)).json();
    expect(saved.xml).toContain("Client choice");
    expect(saved.xml).not.toContain("Agent choice");
    expect(writes.at(-1)?.expected_revision).toBe(backend.revision);
    await page.unrouteAll({ behavior: "wait" });
  });
  test("compact toolbar expands Diagrams into the available work area", async ({
    page,
  }) => {
    await page.route("https://embed.diagrams.net/**", (route) =>
      route.fulfill({
        contentType: "text/html",
        body: `<html><body><script>
        let xml = '';
        addEventListener('message', event => {
          const message = JSON.parse(event.data);
          if (message.action === 'configure') parent.postMessage(JSON.stringify({event:'init'}), '*');
          if (message.action === 'load') { xml = message.xml; parent.postMessage(JSON.stringify({event:'load'}), '*'); }
          if (message.action === 'export' && message.format === 'png') parent.postMessage(JSON.stringify({event:'export', data:'data:image/png;base64,iVBORw0KGgo=', message}), '*');
          if (message.action === 'export' && message.format !== 'png') parent.postMessage(JSON.stringify({event:'export', xml, message}), '*');
        });
        parent.postMessage(JSON.stringify({event:'configure'}), '*');
      </script></body></html>`,
      }),
    );
    await page.addInitScript(() => {
      localStorage.setItem("grove.sidebar.collapsed", "false");
      localStorage.setItem("grove-workspace-view:diagram-workspace", "split");
      localStorage.setItem(
        "grove-workspace-work-tab:diagram-workspace",
        "diagram",
      );
    });
    await page.goto(`/w/${DIAGRAM_WORKSPACE}`);
    await page.getByTestId("work-panel-tab-diagram").click();
    const toolbar = page.getByTestId("diagram-toolbar");
    await expect(toolbar).toBeVisible();
    expect((await toolbar.boundingBox())!.height).toBeCloseTo(32, 1);
    expect((await page.getByTestId("diagram-stop").boundingBox())!.height).toBeCloseTo(
      dp(24),
      1,
    );
    const before = (await page.getByTestId("diagram-frame").boundingBox())!;
    await page.getByTestId("diagram-expand").click();
    await expect(page.getByTestId("pane-work")).toHaveAttribute(
      "data-state",
      "active",
    );
    await expect(page.getByTestId("work-panel-tab-diagram")).toHaveAttribute(
      "data-state",
      "active",
    );
    await expect(page.getByTestId("app-sidebar")).toHaveAttribute(
      "data-collapsed",
      "true",
    );
    await expect
      .poll(
        async () =>
          (await page.getByTestId("diagram-frame").boundingBox())!.width,
      )
      .toBeGreaterThan(before.width);
    const panel = (await page.getByTestId("work-panel").boundingBox())!;
    const editor = (await page.getByTestId("diagram-frame").boundingBox())!;
    expect(
      Math.abs(editor.y + editor.height - panel.y - panel.height),
    ).toBeLessThanOrEqual(2);
    expect(editor.width).toBeGreaterThan(panel.width - 2);
    await page.setViewportSize({ width: 420, height: 850 });
    await expect(toolbar).toBeVisible();
    expect((await toolbar.boundingBox())!.height).toBeCloseTo(32, 1);
    await expect(page.getByTestId("diagram-expand")).toBeInViewport();
    await page.screenshot({ path: "test-results/diagram-compact-mobile.png" });
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.screenshot({
      path: "test-results/diagram-expanded-desktop.png",
    });
  });
  test("exports the acknowledged first page and uploads its revision-bound PNG", async ({
    page,
  }) => {
    const uploads: unknown[] = [];
    await page.route(
      "**/api/grove/workspaces/diagram-workspace/diagram/preview",
      async (route) => {
        if (route.request().method() === "POST")
          uploads.push(route.request().postDataJSON());
        await route.continue();
      },
    );
    await page.route("https://embed.diagrams.net/**", (route) =>
      route.fulfill({
        contentType: "text/html",
        body: `<html><body><script>
        let xml = '';
        addEventListener('message', event => {
          const message = JSON.parse(event.data);
          if (message.action === 'configure') parent.postMessage(JSON.stringify({event:'init'}), '*');
          if (message.action === 'load') { xml = message.xml; parent.postMessage(JSON.stringify({event:'load'}), '*'); }
          if (message.action === 'export' && message.format === 'png') parent.postMessage(JSON.stringify({event:'export', data:'data:image/png;base64,iVBORw0KGgo=', message}), '*');
          if (message.action === 'export' && message.format !== 'png') parent.postMessage(JSON.stringify({event:'export', xml, message}), '*');
        });
        parent.postMessage(JSON.stringify({event:'configure'}), '*');
      </script></body></html>`,
      }),
    );
    await page.addInitScript(() => {
      localStorage.setItem("grove-workspace-view:diagram-workspace", "work");
      localStorage.setItem(
        "grove-workspace-work-tab:diagram-workspace",
        "diagram",
      );
    });
    await page.goto(`/w/${DIAGRAM_WORKSPACE}`);
    await page.getByTestId("work-panel-tab-diagram").click();
    await expect.poll(() => uploads.length).toBe(1);
    expect(uploads[0]).toMatchObject({
      session_id: "a".repeat(32),
      content_base64: "iVBORw0KGgo=",
    });
    const preview = await page.request.get(
      `/api/grove/workspaces/${DIAGRAM_WORKSPACE}/diagram/preview`,
    );
    expect(preview.ok()).toBeTruthy();
    expect(await preview.json()).toMatchObject({
      page_index: 0,
      mime_type: "image/png",
      content_base64: "iVBORw0KGgo=",
    });
  });

  for (const mode of ["active", "read_only"] as const) {
    test(`${mode} diagram never takes space below another selected tab`, async ({ page }) => {
      await page.route("https://embed.diagrams.net/**", (route) => route.fulfill({
        contentType: "text/html", body: "<html><body>Diagram fixture</body></html>",
      }));
      const peek = await (await page.request.get(`/api/grove/workspaces/${DIAGRAM_WORKSPACE}/peek`)).json();
      const document = await (await page.request.get(`/api/grove/workspaces/${DIAGRAM_WORKSPACE}/diagram`)).json();
      peek.state.diagram.mode = mode;
      document.diagram.mode = mode;
      await page.route("**/api/grove/workspaces/diagram-workspace/peek", (route) => route.fulfill({ json: peek }));
      await page.route("**/api/grove/workspaces/diagram-workspace/diagram?*", (route) => route.fulfill({ json: document }));
      await page.addInitScript(() => {
        localStorage.setItem("grove-workspace-view:diagram-workspace", "work");
        localStorage.setItem("grove-workspace-work-tab:diagram-workspace", "diagram");
      });
      await page.goto(`/w/${DIAGRAM_WORKSPACE}`);
      await page.getByTestId("work-panel-tab-diagram").click();
      const frame = page.getByTestId("diagram-frame");
      await expect(frame).toBeVisible();
      await frame.evaluate((element) => element.setAttribute("data-draft-witness", "retained"));
      for (const tab of ["info", "controls", "terminal"] as const) {
        await page.getByTestId(`work-panel-tab-${tab}`).click();
        await expect(page.getByTestId(`work-panel-tab-${tab}`)).toHaveAttribute("data-state", "active");
        await expect(page.getByTestId("diagram-tab")).not.toBeVisible();
        const content = page.getByTestId("work-panel").getByRole("tabpanel");
        await expect(content).toHaveCount(1);
        const panelBounds = (await page.getByTestId("work-panel").boundingBox())!;
        const contentBounds = (await content.boundingBox())!;
        expect(Math.abs(contentBounds.y + contentBounds.height - panelBounds.y - panelBounds.height)).toBeLessThanOrEqual(2);
        if (mode === "active") {
          await expect(frame).toHaveCount(1);
          expect(await frame.boundingBox()).toBeNull();
        } else {
          await expect(frame).toHaveCount(0);
        }
      }
      await page.getByTestId("work-panel-tab-diagram").click();
      await expect(frame).toBeVisible();
      if (mode === "active") await expect(frame).toHaveAttribute("data-draft-witness", "retained");
      await page.unrouteAll({ behavior: "wait" });
    });
  }

  test("does not exist on a workspace with no descriptor", async ({ page }) => {
    await page.goto(`/w/${PLAIN_WORKSPACE}`);
    await expect(page.getByTestId("work-panel")).toBeVisible();
    await expect(page.getByTestId("work-panel-tab-diagram")).toHaveCount(0);
  });

  test("appears and opens the editor when the workspace carries one", async ({
    page,
  }) => {
    await page.goto(`/w/${DIAGRAM_WORKSPACE}`);
    const trigger = page.getByTestId("work-panel-tab-diagram");
    await expect(trigger).toBeVisible();
    await trigger.click();

    const frame = page.getByTestId("diagram-frame");
    await expect(frame).toBeVisible();

    // The document crosses as a postMessage payload, never in the URL — which
    // is readable in the DOM, in a referrer and in the editor host's logs.
    const src = await frame.getAttribute("src");
    expect(src).toContain("proto=json");
    expect(src).not.toContain("mxfile");
    expect(src).not.toContain("flow.drawio");

    // Whose JavaScript is rendering this, said on the surface.
    await expect(page.getByTestId("diagram-editor-host")).toBeVisible();
  });
});
