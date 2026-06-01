# Authentication & pairing

The [web dashboard](use-webapp.md) talks to a real HTTP daemon, so the
daemon requires authentication. Grove borrows the model your headphones use:
a new device asks to connect, both sides show the same short code, and you
approve it on the host. There is deliberately no network-exposed,
unauthenticated surface — the daemon binds loopback and every device earns
its access by pairing.

## The handshake

Pairing is a three-step, out-of-band handshake. The first time a browser
opens the dashboard it is redirected to a pairing screen.

**1. Name the device.** The browser suggests a label from its user agent
("Pixel 8", "MacBook", and so on); edit it to something you'll recognise and
request pairing. The daemon records a pending request and returns a code.

<figure class="grove-shot" markdown>
  <span class="grove-shot__frame">
    ![Web dashboard pairing screen with a device-name field](img/screenshots/webapp-pair-device.png)
  </span>
  <p class="grove-shot__body">On the device: name it and request pairing.</p>
</figure>

**2. Read the code.** The browser shows an eight-character code
(`XXXX-XXXX`) and starts polling. The code is your out-of-band proof — you
compare it against what the host shows before approving.

<figure class="grove-shot" markdown>
  <span class="grove-shot__frame">
    ![Web dashboard showing the pairing code to confirm on the host](img/screenshots/webapp-pair-code.png)
  </span>
  <p class="grove-shot__body">On the device: the code to confirm on the host. It expires after five minutes.</p>
</figure>

**3. Approve on the host.** Confirm the matching code on the machine running
the daemon. The moment you approve, the browser's next poll collects its
session and redirects into the dashboard.

<figure class="grove-shot" markdown>
  <span class="grove-shot__frame">
    ![Grove TUI pairing modal prompting to approve a new device](img/screenshots/tui-pair-approve.png)
  </span>
  <p class="grove-shot__body">On the host: the TUI pops this modal automatically. Approve with <code>a</code>, deny with <code>d</code>.</p>
</figure>

## Approving on the host

There are two ways to approve, and both run on the host — approval is never
reachable over HTTP, so a remote caller can never approve itself.

While the TUI is running it watches for pending requests and pops the modal
above automatically; press `a` to approve or `d` to deny. Headless hosts use
the CLI instead:

```bash
grove auth pending                 # list requests; each line shows the code
grove auth approve <challenge-id>  # approve the matching one
grove auth deny <challenge-id>     # reject it
```

Only ever approve when the code on the host matches the code on the device.
That comparison is what stops someone who merely reached the pairing screen
from getting in — the code lives for five minutes, then the request expires
and the device has to start over.

## Managing sessions

A session persists for thirty days and renews itself on every use, so a
device you use daily pairs exactly once. List and revoke sessions from the
host:

```bash
grove auth sessions                # active sessions with labels and expiry
grove auth revoke <session-id>     # lock a device out until it pairs again
```

The full command reference, including arguments, lives on the
[CLI page](use-cli.md#grove-auth).

## The security model

Grove's access control rests on a few deliberate invariants:

- **Loopback by default.** `grove daemon serve` binds `127.0.0.1`. There is
  no blessed `--host 0.0.0.0`; to reach the daemon from elsewhere you forward
  a port over SSH or stand up a real tunnel (see
  [reaching the dashboard from outside](use-webapp.md#reaching-it-from-outside-the-network)).
  Widening the bind is the wrong lever.
- **Only two unauthenticated endpoints.** The liveness probe (`/healthz`)
  and the pairing handshake are open so a device can bootstrap; every other
  endpoint needs a session token presented as `Authorization: Bearer …`.
- **Approval can't cross the wire.** The daemon exposes *deny* over HTTP but
  never *approve* — a pairing request can only be granted from the host's TUI
  or CLI.
- **Secrets stay where they belong.** Session tokens are stored only as
  SHA-256 hashes (in `${user_config_dir}/grove/auth.json`, mode `0600`); the
  plaintext token is handed to the device once and never written to disk. In
  the browser the token never appears at all — the web app holds it
  server-side and gives the browser an `HttpOnly` cookie.

## See also

- [Web dashboard](use-webapp.md): what the paired session unlocks.
- [CLI](use-cli.md#grove-auth): the `grove auth` command group.
