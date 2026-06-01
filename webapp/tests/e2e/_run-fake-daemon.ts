// Bootstrap entry for Playwright's webServer command. Starts the fake
// daemon on the configured port; exits when the parent process kills it.
import { startFakeDaemon } from "./_fake-daemon";

const port = Number(process.env.FAKE_DAEMON_PORT ?? 8421);
startFakeDaemon(port).then((server) => {
  // Log so Playwright sees ready output if it ever waits on stdout.
  console.log(`fake daemon listening on 127.0.0.1:${port}`);
  // Keep the process alive until SIGTERM/SIGINT.
  process.on("SIGTERM", () => server.close(() => process.exit(0)));
  process.on("SIGINT", () => server.close(() => process.exit(0)));
});
