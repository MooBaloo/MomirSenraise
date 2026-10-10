package software.zeasy.momir.print

import android.util.Log
import java.io.File
import java.io.FileOutputStream

sealed class PrintResult {
    object Success : PrintResult()
    data class Failure(val message: String, val jobStarted: Boolean = false) : PrintResult()
}

/** The H10S printer is process-owned, never tied to an Activity's lifetime. */
class H10sPrinter {
    fun isAvailable(): Boolean = transport.isAvailable()
    suspend fun print(raster: Raster, feedDots: Int): PrintResult = transport.print(
        EscPos.initialise() + EscPos.align(0) +
            EscPos.raster(raster.bytes, raster.height) + EscPos.feedDots(feedDots),
    )
    fun diagnostics(): Map<String, String> {
        val job = transport.jobStatus
        return mapOf(
            "backend" to "H10S direct UART", "state" to transport.detail,
            "lastJob" to (job?.let { "${it.id}: ${it.outcome}" } ?: "none"),
            "lastJobDetail" to (job?.detail ?: ""),
        )
    }

    private companion object {
        val transport = H10sTransport(H10sDeviceIo(), onTerminal = {
            // No card content, raster bytes, device identifiers or exception text.
            Log.i("H10sPrinter", "Print job ${it.id}: ${it.outcome}")
        })
    }
}

/** No vendor binaries, shell commands, permission changes, or arbitrary device paths. */
internal object NativeSerial {
    val available = runCatching { System.loadLibrary("momir_serial") }.isSuccess
    external fun open(): Int
    external fun close(fd: Int): Int
    external fun read(fd: Int, bytes: ByteArray, offset: Int, length: Int, timeoutMs: Int): Int
    external fun write(fd: Int, bytes: ByteArray, offset: Int, length: Int, timeoutMs: Int): Int
    external fun drain(fd: Int, timeoutMs: Int): Int
}

internal class H10sDeviceIo : H10sIo {
    override fun available() = NativeSerial.available &&
        (listOf("/dev/ttyS1") + controls).all { File(it).canRead() && File(it).canWrite() }
    override fun open() = NativeSerial.open()
    override fun close(fd: Int) = NativeSerial.close(fd)
    override fun read(fd: Int, bytes: ByteArray) = NativeSerial.read(fd, bytes, 0, bytes.size, 250)
    override fun write(fd: Int, bytes: ByteArray, offset: Int, length: Int) =
        NativeSerial.write(fd, bytes, offset, length, 2_000)
    override fun drain(fd: Int) = NativeSerial.drain(fd, 2_000)
    override fun control(index: Int, enabled: Boolean) {
        FileOutputStream(controls[index]).use { it.write(if (enabled) '1'.code else '0'.code) }
        Thread.sleep(50)
    }

    private companion object {
        // Order is MCU download, power, enable; all writes require the UART lock.
        val controls = listOf("printer_mcu_dl", "printer_pwr", "printer_en").map {
            "/sys/bus/i2c/drivers/psc5415a/5-006a/$it"
        }
    }
}
