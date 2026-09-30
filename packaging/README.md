# packaging

How Grove is installed and run as a service. CI component: **tooling**.

```
packaging/
├── systemd/          user units for the daemon, webapp and MCP server (see systemd/README.md)
├── docker/           clean-install smoke test in a fresh container
└── otel-collector/   OpenTelemetry collector config (see otel-collector/README.md)
```

Install paths, the locked-versus-unlocked dependency trap and the service
contract are in [CLAUDE.md](CLAUDE.md). The unlocked install is checked daily by
`.github/workflows/nightly.yml`.
