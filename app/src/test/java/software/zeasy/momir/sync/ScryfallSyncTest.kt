package software.zeasy.momir.sync

import android.database.sqlite.SQLiteDatabase
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import software.zeasy.momir.data.ArtPack
import software.zeasy.momir.data.CardRepository
import java.io.File
import java.io.IOException

@RunWith(RobolectricTestRunner::class)
@Config(manifest = Config.NONE, sdk = [28])
class ScryfallSyncTest {
    private lateinit var repository: CardRepository
    private lateinit var art: ArtPack
    private var cancelled = false
    private var artworkRequests = 0
    private var bulkRequests = 0
    private val progress = object : ScryfallSync.Progress {
        override fun onStage(stage: String) = Unit
        override fun onProgress(done: Int, total: Int) = Unit
        override fun isCancelled() = cancelled
    }
    private val card = """{"oracle_id":"fixture-id","name":"Fixture","layout":"normal","type_line":"Creature","legalities":{"vintage":"legal"},"image_uris":{"art_crop":"https://example.invalid/art"},"cmc":2}"""

    @Before fun setup() {
        repository = CardRepository(RuntimeEnvironment.getApplication())
        repository.databaseFile.parentFile!!.mkdirs()
        repository.databaseFile.delete()
        SQLiteDatabase.openOrCreateDatabase(repository.databaseFile, null).use { db ->
            db.execSQL("CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT)")
            db.execSQL("""CREATE TABLE cards(oracle_id TEXT PRIMARY KEY, name TEXT, mana_cost TEXT,
                mv INTEGER, type_line TEXT, oracle_text TEXT, power TEXT, toughness TEXT,
                color_identity TEXT, type_mask INTEGER, loyalty TEXT, scryfall_uri TEXT,
                art_uri TEXT, art_off INTEGER, art_len INTEGER, art_h INTEGER)""")
        }
        assertTrue(repository.open())
        art = ArtPack(File(repository.databaseFile.parentFile, "fixture.pack").also { it.delete() })
    }
    @After fun teardown() { art.close(); repository.close() }

    private fun sync(body: String, jsonl: Boolean = false, artwork: Boolean = false,
                     onBulk: () -> Unit = {}): ScryfallSync.Outcome {
        val field = if (jsonl) "jsonl_download_uri" else "download_uri"
        val index = """{"data":[{"type":"oracle_cards","updated_at":"fixture-v1","$field":"https://example.invalid/bulk"}]}"""
        return ScryfallSync(repository, art) { url, _ ->
            when {
                url.endsWith("bulk-data") -> index.byteInputStream()
                url.endsWith("/bulk") -> { bulkRequests++; onBulk(); body.byteInputStream() }
                else -> { artworkRequests++; throw SyncHttp.HttpFailure(429, 60) }
            }
        }.run(progress, artwork)
    }

    @Test fun compactArrayImportsAndCheckpoints() {
        val result = sync("[$card]")
        assertNull(result.error)
        assertEquals(1, result.newCards)
        assertEquals(1, repository.cardCount())
        assertEquals("fixture-v1", repository.meta(ScryfallSync.META_BULK_TIMESTAMP))
    }

    @Test fun multilineArrayAndJsonlBothImport() {
        assertNull(sync("[\n${card.replace(",", ",\n")}\n]").error)
        repository.setMeta(ScryfallSync.META_BULK_TIMESTAMP, "old")
        val result = sync("$card\n", jsonl = true)
        assertNull(result.error)
        assertEquals(1, result.refreshedCards)
    }

    @Test fun malformedTruncatedAndEmptyExportsRollBackAndDoNotCheckpoint() {
        for (body in listOf("[$card, BROKEN]", "[$card", "[]", "[$card] garbage")) {
            assertNotNull(sync(body).error)
            assertEquals(0, repository.cardCount())
            assertNull(repository.meta(ScryfallSync.META_BULK_TIMESTAMP))
            assertNull(repository.meta(ScryfallSync.META_LAST_SYNC))
        }
        assertNotNull(sync("$card\n{broken", jsonl = true).error)
        assertEquals(0, repository.cardCount())
    }

    @Test fun cancellationDoesNotCommitOrReportSuccess() {
        val result = sync("[$card]") { cancelled = true }
        assertTrue(result.error!!.contains("cancelled"))
        assertEquals(0, repository.cardCount())
        assertNull(repository.meta(ScryfallSync.META_BULK_TIMESTAMP))
    }

    @Test fun artworkRateLimitPreservesCommittedCardsAndReportsStage() {
        val result = sync("[$card]", artwork = true)
        assertTrue(result.error!!.contains("Fetching artwork: HTTP 429"))
        assertEquals(1, result.newCards)
        assertEquals(1, repository.cardCount())
        assertEquals("fixture-v1", repository.meta(ScryfallSync.META_BULK_TIMESTAMP))
        assertNull(repository.meta(ScryfallSync.META_LAST_SYNC))
        assertEquals(1, repository.cardsMissingArt(10).size)
        assertEquals(1, result.failedArtwork)
        assertNotNull(sync("unused", artwork = true).error)
        assertEquals(1, bulkRequests)
        assertEquals(2, artworkRequests)
    }

    @Test fun rateLimitStopsBeforeNextArtwork() {
        val second = card.replace("fixture-id", "fixture-id-2")
        val result = sync("[$card,$second]", artwork = true)
        assertTrue(result.error!!.contains("429"))
        assertEquals(2, repository.cardCount())
        assertEquals(1, artworkRequests)
    }

    @Test fun cancellationAfterARecordRollsBack() {
        var checks = 0
        val cancelling = object : ScryfallSync.Progress {
            override fun onStage(stage: String) = Unit
            override fun onProgress(done: Int, total: Int) = Unit
            override fun isCancelled() = ++checks >= 3
        }
        val index = """{"data":[{"type":"oracle_cards","updated_at":"new","download_uri":"https://example.invalid/bulk"}]}"""
        val result = ScryfallSync(repository, art) { url, _ ->
            (if (url.endsWith("bulk-data")) index else "[$card,$card]").byteInputStream()
        }.run(cancelling, false)
        assertTrue(result.error!!.contains("cancelled"))
        assertEquals(0, result.newCards)
        assertEquals(0, repository.cardCount())
        assertNull(repository.meta(ScryfallSync.META_BULK_TIMESTAMP))
    }

    @Test fun writeFailureRollsBackAndKeepsOldCheckpoint() {
        repository.setMeta(ScryfallSync.META_BULK_TIMESTAMP, "old")
        SQLiteDatabase.openDatabase(repository.databaseFile.path, null, SQLiteDatabase.OPEN_READWRITE).use {
            it.execSQL("CREATE TRIGGER fixture_reject BEFORE INSERT ON cards BEGIN SELECT RAISE(ABORT, 'fixture write failure'); END")
        }
        val result = sync("[$card]")
        assertTrue(result.error!!.contains("Downloading card data"))
        assertEquals(0, repository.cardCount())
        assertEquals("old", repository.meta(ScryfallSync.META_BULK_TIMESTAMP))
        assertNull(repository.meta(ScryfallSync.META_LAST_SYNC))
    }

    @Test fun gzipImportAndTruncatedGzipRollback() {
        val bytes = java.io.ByteArrayOutputStream().apply {
            java.util.zip.GZIPOutputStream(this).use { it.write("[$card]".toByteArray()) }
        }.toByteArray()
        val index = """{"data":[{"type":"oracle_cards","updated_at":"gzip-v1","download_uri":"https://example.invalid/bulk.gz"}]}"""
        fun run(payload: ByteArray) = ScryfallSync(repository, art) { url, _ ->
            if (url.endsWith("bulk-data")) index.byteInputStream() else payload.inputStream()
        }.run(progress, false)
        assertNotNull(run(bytes.copyOf(bytes.size - 8)).error)
        assertEquals(0, repository.cardCount())
        assertNull(repository.meta(ScryfallSync.META_BULK_TIMESTAMP))
        assertNull(run(bytes).error)
        assertEquals(1, repository.cardCount())
    }

    @Test fun individualArtworkFailureIsVisibleAndRetryable() {
        sync("[$card]")
        val index = """{"data":[{"type":"oracle_cards","updated_at":"fixture-v1","download_uri":"https://example.invalid/bulk"}]}"""
        val result = ScryfallSync(repository, art) { url, _ ->
            if (url.endsWith("bulk-data")) index.byteInputStream()
            else throw SyncHttp.HttpFailure(404, null)
        }.run(progress)
        assertEquals(1, result.failedArtwork)
        assertTrue(result.error!!.contains("1 artworks failed"))
        assertEquals(1, repository.cardsMissingArt(10).size)
    }

    @Test fun networkFailureDoesNotBecomeDatabaseDiagnosis() {
        val result = ScryfallSync(repository, art) { _, _ -> throw IOException("fixture timeout") }.run(progress)
        assertEquals("Checking Scryfall: fixture timeout", result.error)
        assertNull(repository.meta(ScryfallSync.META_LAST_SYNC))
    }
}
