# Authentication & pairing

## Pair a device with Grove

The [web dashboard](use-webapp.md) talks to a real HTTP daemon, so it needs to know who is calling.
Grove borrows the model your headphones already use: a new device asks to connect, both sides show
the same short code, and you approve it on the host. The daemon binds loopback, and pairing is how a
device earns access.

## The handshake

Pairing is a three-step handshake, out of band, and a browser's first visit lands on a pairing screen.

- **Step one, on the device.** Edit the suggested label, such as "Pixel 8", then request pairing. The daemon returns a code.
- **Step two, on the device.** The browser shows an eight-character code, `XXXX-XXXX`, and polls. Compare it against the host before approving.
- **Step three, on the host.** Confirm the matching code. Approval redirects the browser into the dashboard.

<div class="swiper ms-shots">
  <div class="swiper-wrapper">
    <div class="swiper-slide">
      <figure>
        <img loading="lazy" src="../img/screenshots/webapp-pair-device.png" alt="Web dashboard pairing screen with a device-name field">
        <figcaption>Step 1, on the device. Name it, then request pairing.</figcaption>
      </figure>
    </div>
    <div class="swiper-slide">
      <figure>
        <img loading="lazy" src="../img/screenshots/webapp-pair-code.png" alt="Web dashboard showing the pairing code to confirm on the host">
        <figcaption>Step 2, on the device. The code to confirm on the host, expiring after five minutes.</figcaption>
      </figure>
    </div>
    <div class="swiper-slide">
      <figure>
        <img loading="lazy" src="../img/screenshots/tui-pair-approve.svg" alt="Grove TUI pairing modal prompting to approve a new device">
        <figcaption>Step 3, on the host. The TUI pops this modal on its own: approve with <code>a</code>, deny with <code>d</code>.</figcaption>
      </figure>
    </div>
  </div>
  <div class="swiper-pagination"></div>
  <div class="swiper-button-prev"></div>
  <div class="swiper-button-next"></div>
</div>

## Approving on the host

Both ways to approve run on the host, since approval never travels over HTTP: the TUI's own modal, or
the CLI on a headless host.

```bash
grove auth pending                 # list requests; each line shows the code
grove auth approve <challenge-id>  # approve the matching one
grove auth deny <challenge-id>     # reject it
```

Approve only when the codes match. That single check stops someone who merely reached the pairing
screen. The code expires after five minutes, and the device starts over.

## Managing sessions

A session lasts thirty days, renewing on every use, so a daily device pairs once.

```bash
grove auth sessions                # active sessions with labels and expiry
grove auth revoke <session-id>     # lock a device out until it pairs again
```

The full reference lives on the [CLI page](use-cli.md#grove-auth).

## The security model

Grove's access control rests on keeping the daemon private and letting people in by hand.

- **Loopback by default.** `grove daemon serve` binds `127.0.0.1`, no blessed `--host 0.0.0.0`. Reach it from elsewhere by forwarding a port over SSH. See [reaching the dashboard from outside](use-webapp.md#reaching-it-from-outside-the-network).
- **Only two open endpoints.** `/healthz` and the pairing handshake answer without a session. Every other endpoint needs a session token as `Authorization: Bearer`.
- **Approval cannot cross the wire.** The daemon exposes deny over HTTP, never approve, so only the host's TUI or CLI can grant a request.
- **Secrets stay where they belong.** Session tokens live only as SHA-256 hashes, in `${user_config_dir}/grove/auth.json`, mode `0600`. The plaintext token reaches the device once, never touching disk. In the browser it never appears: the server holds it, giving an `HttpOnly` cookie.

## See also

- [Web dashboard](use-webapp.md): what the paired session unlocks.
- [CLI](use-cli.md#grove-auth): the `grove auth` command group.
