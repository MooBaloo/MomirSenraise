package software.zeasy.momir.print

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class H10sStatusParserTest {
    @Test
    fun parsesReadyPaperAndHeatStates() {
        val parser = H10sStatusParser()
        val statuses = parser.consume(
            byteArrayOf(
                0x1d, 0x02, 0x00, 0x02, 0x53, 0x00,
                0x1d, 0x02, 0x00, 0x02, 0x53, 0x02,
                0x1d, 0x02, 0x00, 0x02, 0x53, 0x04,
            ),
            18,
        )

        assertEquals(3, statuses.size)
        assertFalse(statuses[0].paperOut)
        assertFalse(statuses[0].overheated)
        assertTrue(statuses[1].paperOut)
        assertTrue(statuses[2].overheated)
    }

    @Test
    fun retainsSplitFrameAndSkipsNoise() {
        val parser = H10sStatusParser()
        assertTrue(parser.consume(byteArrayOf(0x7f, 0x1d, 0x02), 3).isEmpty())

        val statuses = parser.consume(byteArrayOf(0x00, 0x02, 0x53, 0x06), 4)

        assertEquals(1, statuses.size)
        assertTrue(statuses.single().paperOut)
        assertTrue(statuses.single().overheated)
    }

    @Test fun rejectsOversizedFrameAndResynchronizes() {
        val parser = H10sStatusParser()
        val bytes = byteArrayOf(0x1d, 2, 0x7f, 0x7f, 0, 0x1d, 2, 0, 2, 0x53, 2)
        val statuses = bytes.flatMap { parser.consume(byteArrayOf(it), 1) }
        assertEquals(1, statuses.size)
        assertTrue(statuses.single().paperOut)
    }
}
