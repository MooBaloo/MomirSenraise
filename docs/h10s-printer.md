# H10S printing

This backend targets the Senraise H10S printer MCU directly. It packages no
vendor SDK and contains no Sunmi service binding. The upstream renderer and
ESC/POS encoding remain intact. Physical acceptance of this port is pending.

## Acquisition and jobs

`H10sPrinter` shares one process-wide `H10sTransport`. Startup, diagnostics,
and capability checks do not open the UART or change power controls. A print
acquires `/dev/ttyS1` with a nonblocking exclusive `flock`, configures 460800
baud, 8N1 and RTS/CTS, then sequences the MCU download, enable and power nodes
under `/sys/bus/i2c/drivers/psc5415a/5-006a/`. A failed acquisition never touches
those controls. The application does not change permissions, invoke a shell,
provision Device Owner, or stop competing services.

The lock is advisory: another process that ignores it may still interfere.
Hardware validation must establish exclusive printer use; this port does not
implement OEM service management or a cross-application printer queue.

A fresh MCU status frame is required before the first payload. Paper-out,
overheat, absent status, and reader errors block transmission. Whole slips are
serialized, with writes of at most 256 bytes and 10 ms pacing. Native polling
and drain are bounded; drain checks the kernel output queue for up to two
seconds instead of calling an unbounded `tcdrain`. A final status check follows.
That queue becoming empty is not proof of physical paper completion.

## Failure and release

A failed or cancelled job is never retried or replayed. Cancellation is observed
between bounded I/O operations; bytes already accepted may still print. A
reported failure after accepted bytes warns that a partial slip may exist.
After any acquired job ends, cleanup waits five seconds. New jobs cancel the
old timer; after failure, new attempts are blocked until cleanup completes.

Cleanup holds the same job mutex, joins the bounded status reader, disables the
controls while still holding the UART lock, then closes the descriptor. Every
control shutdown is attempted even if another fails. Errors remain visible in
diagnostics until a subsequent operation. An uncertain close blocks reacquisition
until process restart; its numeric descriptor is never closed twice.

Five seconds is an empirical starting policy, not evidence that the MCU has
finished printing. Long/dense slips and flow-control stalls require physical
validation. This is not a background print service: Android process death is
outside the graceful cleanup contract, and no interrupted job is persisted.

## Validation

JVM tests use fake I/O and virtual time for serialization, cancellation, partial
writes, status errors, idle release, lock failures and uncertain close. The host
native test checks JNI bounds and actual pipe I/O plus a simulated drain timeout.
Neither substitutes for the [physical checklist](device-setup.md).
