// Bootstrap entry for Playwright's webServer command. Starts the fake daemon
// on the configured port and stays alive until the parent kills it.
import { startFakeDaemon } from "./_fake-daemon";

const port = Number(process.env.FAKE_DAEMON_PORT ?? 8422);

void startFakeDaemon(port).then((server) => {
  console.log(`fake daemon listening on 127.0.0.1:${port}`);
  process.on("SIGTERM", () => server.close(() => process.exit(0)));
  process.on("SIGINT", () => server.close(() => process.exit(0)));
});
