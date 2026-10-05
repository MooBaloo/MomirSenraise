package software.zeasy.momir.print

import kotlinx.coroutines.*
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/** Blocking I/O is bounded; production calls run on Dispatchers.IO. */
internal interface H10sIo {
    fun available(): Boolean
    fun open(): Int
    fun close(fd: Int): Int
    fun read(fd: Int, bytes: ByteArray): Int
    fun write(fd: Int, bytes: ByteArray, offset: Int, length: Int): Int
    fun drain(fd: Int): Int
    fun control(index: Int, enabled: Boolean)
}

/** Serializes entire slips and cleanup. A failed/cancelled slip is never replayed. */
internal class H10sTransport(
    private val io: H10sIo,
    private val scope: CoroutineScope = CoroutineScope(SupervisorJob() + Dispatchers.IO),
    private val dispatcher: CoroutineDispatcher = Dispatchers.IO,
    private val idleMs: Long = 5_000,
) {
    private val gate = Mutex()
    private var fd = -1
    private var reader: Job? = null
    private var idle: Job? = null
    private var generation = 0L
    private var closeUncertain = false
    private var needsRecovery = false
    @Volatile private var status: H10sPrinterStatus? = null
    @Volatile private var readFailed = false
    @Volatile var detail = "not connected"
        private set

    fun isAvailable(): Boolean = io.available() // Never opens or powers hardware.

    suspend fun print(payload: ByteArray): PrintResult = withContext(dispatcher) {
        gate.withLock {
            if (payload.isEmpty()) return@withLock PrintResult.Failure("Empty print job")
            if (closeUncertain) return@withLock PrintResult.Failure("UART close uncertain; restart required")
            if (needsRecovery) return@withLock PrintResult.Failure("Printer recovering; wait five seconds before a new job")
            if (!isAvailable()) return@withLock PrintResult.Failure("H10S printer hardware unavailable")
            generation++
            idle?.cancel()
            idle = null
            var sent = 0
            var success = false
            try {
                currentCoroutineContext().ensureActive()
                if (fd < 0) acquire()
                currentCoroutineContext().ensureActive()
                checkStatus()
                while (sent < payload.size) {
                    currentCoroutineContext().ensureActive()
                    checkStatus()
                    val length = minOf(256, payload.size - sent)
                    val written = io.write(fd, payload, sent, length)
                    check(written in 1..length) { "UART write failed ($written)" }
                    sent += written
                    delay(10) // Pace the MCU and observe cancellation between writes.
                }
                checkStatus()
                val drained = io.drain(fd)
                check(drained == 0) { "UART drain failed ($drained)" }
                delay(100)
                checkStatus()
                success = true
                detail = "ready"
                PrintResult.Success
            } catch (cancelled: CancellationException) {
                detail = "Print cancelled; output may be partial"
                throw cancelled
            } catch (error: Exception) {
                detail = error.message ?: "Printer operation failed"
                PrintResult.Failure(detail, jobStarted = sent > 0)
            } finally {
                if (fd >= 0) {
                    needsRecovery = !success
                    scheduleRelease()
                }
            }
        }
    }

    private suspend fun acquire() {
        val opened = io.open()
        check(opened >= 0) { "UART acquisition failed ($opened); printer may be in use" }
        fd = opened // Only after exclusive flock; never touch controls on open failure.
        status = null
        readFailed = false
        detail = "initialising"
        repeat(3) { io.control(it, false) }
        io.control(2, true)
        io.control(0, false)
        delay(200)
        io.control(1, true)
        reader = scope.launch {
            val parser = H10sStatusParser()
            val bytes = ByteArray(256)
            try {
                while (isActive) {
                    val count = io.read(opened, bytes)
                    check(count in 0..bytes.size) { "UART read failed ($count)" }
                    if (count > 0) parser.consume(bytes, count).lastOrNull()?.let { status = it }
                    else delay(1)
                }
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (_: Exception) {
                readFailed = true
            }
        }
        // Require a fresh MCU response before sending a slip; no stale ready state.
        repeat(20) {
            if (status == null && !readFailed) delay(25)
        }
        checkStatus()
    }

    private fun checkStatus() {
        check(!readFailed) { "Printer UART read failed" }
        val observed = checkNotNull(status) { "Printer did not report status" }
        check(!observed.paperOut) { "Printer is out of paper" }
        check(!observed.overheated) { "Printer is too hot; let it cool" }
    }

    /** Called under gate; external scope survives the printing Activity's cancellation. */
    private fun scheduleRelease() {
        val expected = ++generation
        idle = scope.launch {
            delay(idleMs)
            gate.withLock {
                if (generation == expected) {
                    idle = null
                    withContext(NonCancellable) { release() }
                }
            }
        }
    }

    private suspend fun release() {
        reader?.cancelAndJoin() // No reader can use a recycled descriptor after close.
        reader = null
        if (fd < 0) return
        val errors = mutableListOf<String>()
        repeat(3) { index ->
            runCatching { io.control(index, false) }.onFailure {
                errors += "Printer shutdown failed: ${it.message}"
            }
        }
        val closing = fd
        fd = -1 // close may consume its descriptor even on failure: never retry it.
        try {
            val result = io.close(closing)
            if (result != 0) {
                closeUncertain = true
                errors += "UART close uncertain ($result); restart required"
            }
        } catch (_: Exception) {
            closeUncertain = true
            errors += "UART close uncertain; restart required"
        }
        status = null
        needsRecovery = false
        detail = if (errors.isEmpty()) "not connected" else errors.joinToString("; ")
    }
}
