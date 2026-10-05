package software.zeasy.momir.print

import kotlinx.coroutines.*
import kotlinx.coroutines.test.*
import org.junit.Assert.*
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class H10sTransportTest {
    private class FakeIo : H10sIo {
        val events = mutableListOf<String>()
        val accepted = mutableListOf<Byte>()
        var openResult = 42
        var closeResult = 0
        var drainResult = 0
        var ready = false
        var state = 0
        var silent = false
        var readFailure = false
        var failAfter = Int.MAX_VALUE
        var shutdownFailure = false
        var afterWrite: () -> Unit = {}
        override fun available() = true
        override fun open(): Int { events += "open"; ready = true; return openResult }
        override fun close(fd: Int): Int { events += "close"; return closeResult }
        override fun control(index: Int, enabled: Boolean) {
            events += "control:$index:$enabled"
            if (!enabled && shutdownFailure) error("shutdown error")
        }
        override fun read(fd: Int, bytes: ByteArray): Int {
            if (readFailure) return -5
            if (!ready || silent) return 0
            ready = false
            byteArrayOf(0x1d, 2, 0, 2, 0x53, state.toByte()).copyInto(bytes)
            return 6
        }
        override fun write(fd: Int, bytes: ByteArray, offset: Int, length: Int): Int {
            events += "write"
            if (offset >= failAfter) return -5
            val n = minOf(length, failAfter - offset)
            accepted += bytes.copyOfRange(offset, offset + n).toList()
            afterWrite()
            return n
        }
        override fun drain(fd: Int): Int { events += "drain"; return drainResult }
    }

    @Test fun `capability checks do not acquire hardware`() = runTest {
        val io = FakeIo()
        val printer = H10sTransport(io, backgroundScope, StandardTestDispatcher(testScheduler))
        assertTrue(printer.isAvailable())
        assertEquals("not connected", printer.detail)
        assertTrue(io.events.isEmpty())
    }

    @Test fun `entire concurrent jobs are serialized and reuse an idle session`() = runTest {
        val io = FakeIo()
        val printer = H10sTransport(io, backgroundScope, StandardTestDispatcher(testScheduler))
        val first = async { printer.print(ByteArray(600) { 1 }) }
        val second = async { printer.print(ByteArray(300) { 2 }) }
        assertEquals(PrintResult.Success, first.await())
        assertEquals(PrintResult.Success, second.await())
        assertEquals(List<Byte>(600) { 1 } + List<Byte>(300) { 2 }, io.accepted)
        assertEquals(1, io.events.count { it == "open" })
        advanceTimeBy(5_001); runCurrent()
        assertEquals(listOf("control:0:false", "control:1:false", "control:2:false", "close"), io.events.takeLast(4))
        assertEquals(PrintResult.Success, printer.print(byteArrayOf(3)))
        assertEquals(2, io.events.count { it == "open" })
    }

    @Test fun `failed lock never touches controls or transmits`() = runTest {
        val io = FakeIo().apply { openResult = -11 }
        val printer = H10sTransport(io, backgroundScope, StandardTestDispatcher(testScheduler))
        assertTrue(printer.print(byteArrayOf(1)) is PrintResult.Failure)
        advanceTimeBy(6_000); runCurrent()
        assertEquals(listOf("open"), io.events)
    }

    @Test fun `partial failure waits for cleanup and never replays accepted bytes`() = runTest {
        val io = FakeIo().apply { failAfter = 3 }
        val printer = H10sTransport(io, backgroundScope, StandardTestDispatcher(testScheduler))
        val failure = printer.print(byteArrayOf(1,2,3,4,5)) as PrintResult.Failure
        assertTrue(failure.jobStarted)
        assertTrue(printer.print(byteArrayOf(9)) is PrintResult.Failure)
        assertEquals(listOf<Byte>(1,2,3), io.accepted)
        advanceTimeBy(5_001); runCurrent()
        io.failAfter = Int.MAX_VALUE
        assertEquals(PrintResult.Success, printer.print(byteArrayOf(8)))
        assertEquals(listOf<Byte>(1,2,3,8), io.accepted)
    }

    @Test fun `cancellation after first chunk stops transmission but retains delayed cleanup`() = runTest {
        val io = FakeIo()
        val printer = H10sTransport(io, backgroundScope, StandardTestDispatcher(testScheduler))
        val job = launch(start = CoroutineStart.LAZY) { printer.print(ByteArray(700) { 1 }) }
        io.afterWrite = { job.cancel() }
        job.start(); job.join()
        assertEquals(256, io.accepted.size)
        assertFalse(io.events.contains("drain"))
        assertFalse(io.events.contains("close"))
        advanceTimeBy(5_001); runCurrent()
        assertEquals(1, io.events.count { it == "close" })
    }

    @Test fun `queued cancellation does not disturb the active job`() = runTest {
        val io = FakeIo()
        val printer = H10sTransport(io, backgroundScope, StandardTestDispatcher(testScheduler))
        val first = async { printer.print(ByteArray(700) { 1 }) }
        runCurrent()
        val second = launch { printer.print(byteArrayOf(2)) }
        runCurrent(); second.cancelAndJoin()
        assertEquals(PrintResult.Success, first.await())
        assertEquals(List<Byte>(700) { 1 }, io.accepted)
    }

    @Test fun `stale idle timer cannot close a newer active job`() = runTest {
        val io = FakeIo()
        val printer = H10sTransport(io, backgroundScope, StandardTestDispatcher(testScheduler))
        assertEquals(PrintResult.Success, printer.print(byteArrayOf(1)))
        advanceTimeBy(4_990)
        val second = async { printer.print(ByteArray(1024) { 2 }) }
        runCurrent(); advanceTimeBy(20); runCurrent()
        assertFalse(io.events.contains("close"))
        assertEquals(PrintResult.Success, second.await())
        advanceTimeBy(5_001); runCurrent()
        assertEquals(1, io.events.count { it == "close" })
    }

    @Test fun `missing paper heat and read errors prevent initial transmission`() = runTest {
        for (mode in 0..3) {
            val io = FakeIo().apply {
                silent = mode == 0; state = if (mode == 1) 2 else if (mode == 2) 4 else 0
                readFailure = mode == 3
            }
            val printer = H10sTransport(io, backgroundScope, StandardTestDispatcher(testScheduler))
            val result = printer.print(byteArrayOf(1)) as PrintResult.Failure
            assertFalse(result.jobStarted)
            assertTrue(io.accepted.isEmpty())
            advanceTimeBy(5_001); runCurrent()
            assertTrue(io.events.contains("close"))
        }
    }

    @Test fun `drain timeout reports potentially partial output`() = runTest {
        val io = FakeIo().apply { drainResult = -110 }
        val printer = H10sTransport(io, backgroundScope, StandardTestDispatcher(testScheduler))
        val result = printer.print(byteArrayOf(1)) as PrintResult.Failure
        assertTrue(result.jobStarted)
        assertTrue(result.message.contains("drain"))
    }

    @Test fun `uncertain close is never retried and blocks reopening`() = runTest {
        val io = FakeIo().apply { closeResult = -5 }
        val printer = H10sTransport(io, backgroundScope, StandardTestDispatcher(testScheduler))
        assertEquals(PrintResult.Success, printer.print(byteArrayOf(1)))
        advanceTimeBy(5_001); runCurrent()
        assertTrue(printer.print(byteArrayOf(2)) is PrintResult.Failure)
        advanceTimeBy(6_000); runCurrent()
        assertEquals(1, io.events.count { it == "open" })
        assertEquals(1, io.events.count { it == "close" })
    }

    @Test fun `control shutdown failures still close the descriptor and remain visible`() = runTest {
        val io = FakeIo()
        val printer = H10sTransport(io, backgroundScope, StandardTestDispatcher(testScheduler))
        assertEquals(PrintResult.Success, printer.print(byteArrayOf(1)))
        io.shutdownFailure = true
        advanceTimeBy(5_001); runCurrent()
        assertTrue(io.events.contains("close"))
        assertTrue(printer.detail.contains("shutdown error"))
    }

    @Test fun `cancelled initialization releases without transmitting`() = runTest {
        val io = FakeIo()
        val printer = H10sTransport(io, backgroundScope, StandardTestDispatcher(testScheduler))
        val job = launch { printer.print(byteArrayOf(1)) }
        runCurrent() // During MCU boot delay, after exclusive acquisition.
        assertTrue(io.events.contains("open"))
        job.cancelAndJoin()
        assertTrue(io.accepted.isEmpty())
        advanceTimeBy(5_001); runCurrent()
        assertEquals(1, io.events.count { it == "close" })
    }

    @Test fun `reader error after a chunk stops the remaining payload`() = runTest {
        val io = FakeIo()
        val printer = H10sTransport(io, backgroundScope, StandardTestDispatcher(testScheduler))
        io.afterWrite = { io.readFailure = true }
        val result = printer.print(ByteArray(700) { 1 }) as PrintResult.Failure
        assertTrue(result.jobStarted)
        assertEquals(256, io.accepted.size)
        assertFalse(io.events.contains("drain"))
    }

    @Test fun `failed initialization still cleans up under acquired ownership`() = runTest {
        val io = FakeIo().apply { shutdownFailure = true }
        val printer = H10sTransport(io, backgroundScope, StandardTestDispatcher(testScheduler))
        assertTrue(printer.print(byteArrayOf(1)) is PrintResult.Failure)
        assertTrue(io.accepted.isEmpty())
        advanceTimeBy(5_001); runCurrent()
        assertEquals(1, io.events.count { it == "close" })
    }
}
