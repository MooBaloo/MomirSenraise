package software.zeasy.momir.print

internal data class H10sPrinterStatus(
    val paperOut: Boolean,
    val overheated: Boolean,
    val raw: Int,
)

/** Parses the status frames emitted by the H10S printer MCU. */
internal class H10sStatusParser {
    private val pending = ArrayList<Byte>()

    fun consume(bytes: ByteArray, length: Int): List<H10sPrinterStatus> {
        repeat(length.coerceIn(0, bytes.size)) { pending += bytes[it] }
        val statuses = ArrayList<H10sPrinterStatus>()
        while (pending.size >= HEADER_SIZE) {
            val header = findHeader()
            if (header < 0) {
                pending.subList(0, pending.size - 1).clear()
                break
            }
            if (header > 0) pending.subList(0, header).clear()
            if (pending.size < HEADER_SIZE) break

            val payloadLength = (unsigned(pending[2]) shl 8) or unsigned(pending[3])
            if (payloadLength !in 1..MAX_PAYLOAD) {
                pending.removeAt(0)
                continue
            }
            val frameLength = HEADER_SIZE + payloadLength
            if (pending.size < frameLength) break

            if (payloadLength >= 2 && unsigned(pending[4]) == STATUS_MESSAGE) {
                val state = unsigned(pending[5])
                statuses += H10sPrinterStatus(
                    paperOut = state and PAPER_OUT_MASK != 0,
                    overheated = state and OVERHEAT_MASK != 0,
                    raw = state,
                )
            }
            pending.subList(0, frameLength).clear()
        }
        return statuses
    }

    private fun findHeader(): Int {
        for (index in 0 until pending.size - 1) {
            if (unsigned(pending[index]) == FRAME_START && unsigned(pending[index + 1]) == FRAME_KIND) {
                return index
            }
        }
        return -1
    }

    private fun unsigned(value: Byte): Int = value.toInt() and 0xff

    private companion object {
        const val HEADER_SIZE = 4
        const val MAX_PAYLOAD = 4096
        const val FRAME_START = 0x1d
        const val FRAME_KIND = 0x02
        const val STATUS_MESSAGE = 0x53
        const val PAPER_OUT_MASK = 0x02
        const val OVERHEAT_MASK = 0x04
    }
}
