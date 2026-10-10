package software.zeasy.momir.print

import java.io.ByteArrayOutputStream

/** Raster ESC/POS commands shared with the upstream renderer; sent over H10S UART. */
object EscPos {

    /** 203 dpi is 7.992 dots/mm; 8 is close enough and keeps the arithmetic honest. */
    const val DOTS_PER_MM = 8.0f

    /** H10S: 58 mm paper, 384 printable dots = 48 mm. */
    const val PRINT_WIDTH_DOTS = 384
    const val BYTES_PER_ROW = PRINT_WIDTH_DOTS / 8

    /**
     * Some firmware revisions dislike a single enormous raster command, and a
     * short band also lets the printer's motor keep up with the head. 128 rows
     * is 16 mm - small enough to be safe, large enough that the per-command
     * overhead disappears.
     */
    private const val BAND_ROWS = 128

    fun mmToDots(mm: Float): Int = Math.round(mm * DOTS_PER_MM)

    /** ESC @ - reset alignment, spacing and any leftover state from a prior job. */
    fun initialise(): ByteArray = byteArrayOf(0x1B, 0x40)

    /** ESC a n - 0 left, 1 centre, 2 right. */
    fun align(mode: Int): ByteArray = byteArrayOf(0x1B, 0x61, mode.toByte())

    /**
     * ESC J n - feed n dots. Capped at 255 per command, so long feeds are split.
     * This is what puts the finished slip past the tear bar.
     */
    fun feedDots(dots: Int): ByteArray {
        if (dots <= 0) return ByteArray(0)
        val out = ByteArrayOutputStream()
        var remaining = dots
        while (remaining > 0) {
            val step = minOf(remaining, 255)
            out.write(byteArrayOf(0x1B, 0x4A, step.toByte()))
            remaining -= step
        }
        return out.toByteArray()
    }

    /**
     * GS v 0 - raster bit image, split into bands.
     *
     * [raster] is packed 1 bpp, MSB first, [BYTES_PER_ROW] bytes per row, 1 = burn.
     * Bands print flush against each other, so the seam is invisible.
     */
    fun raster(raster: ByteArray, height: Int): ByteArray {
        val out = ByteArrayOutputStream(raster.size + 64)
        var row = 0
        while (row < height) {
            val rows = minOf(BAND_ROWS, height - row)
            out.write(
                byteArrayOf(
                    0x1D, 0x76, 0x30, 0x00,
                    (BYTES_PER_ROW and 0xFF).toByte(), ((BYTES_PER_ROW shr 8) and 0xFF).toByte(),
                    (rows and 0xFF).toByte(), ((rows shr 8) and 0xFF).toByte(),
                )
            )
            out.write(raster, row * BYTES_PER_ROW, rows * BYTES_PER_ROW)
            row += rows
        }
        return out.toByteArray()
    }

    private fun ByteArrayOutputStream.write(bytes: ByteArray) = write(bytes, 0, bytes.size)
}
