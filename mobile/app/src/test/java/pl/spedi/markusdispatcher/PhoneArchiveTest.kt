package pl.spedi.markusdispatcher

import androidx.datastore.preferences.core.PreferenceDataStoreFactory
import androidx.datastore.preferences.core.PreferencesSerializer
import androidx.datastore.core.okio.OkioStorage
import okio.FileSystem
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.first
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import okio.Path.Companion.toPath

class PhoneArchiveTest {
    @get:Rule val temp = TemporaryFolder()
    // Okio path storage supports atomic replacement on the Windows JVM test host.
    private fun store(file: File, scope: CoroutineScope) = PhoneArchive(PreferenceDataStoreFactory.create(
        storage = OkioStorage(FileSystem.SYSTEM, PreferencesSerializer) { file.absolutePath.toPath() }, scope = scope))

    @Test fun persistenceSurvivesStoreRestartAndSeparatesAccounts() = runBlocking {
        val file = File(temp.root, "archive.preferences_pb")
        val scope1 = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        val first = store(file, scope1)
        first.hide("account-a", JobDto(id = "same", status = "done", refNumbers = "REF"))
        first.hide("account-b", JobDto(id = "same", status = "loading"))
        scope1.coroutineContext[Job]!!.cancelAndJoin()
        val scope2 = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        try {
            val reopened = store(file, scope2)
            assertEquals("done", reopened.observe("account-a").first()["same"]!!.status)
            assertEquals("loading", reopened.observe("account-b").first()["same"]!!.status)
            reopened.restore("account-a", setOf("same"))
            assertTrue(reopened.observe("account-a").first().isEmpty())
            assertEquals(1, reopened.observe("account-b").first().size)
        } finally { scope2.coroutineContext[Job]!!.cancelAndJoin() }
    }

    @Test fun reconcileRestoresTerminalButPreservesManualActiveAndMissingDocuments() = runBlocking {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        try {
            val archive = store(File(temp.root, "rules.preferences_pb"), scope)
            archive.hide("u", JobDto(id = "terminal", status = "cancelled"))
            archive.hide("u", JobDto(id = "manual", status = "loading"))
            archive.hide("u", JobDto(id = "missing", status = "done"))
            archive.reconcile("u", listOf(JobDto(id = "terminal", status = "assigned"), JobDto(id = "manual", status = "assigned")))
            assertEquals(setOf("manual", "missing"), archive.observe("u").first().keys)
            archive.restore("u", archive.observe("u").first().keys)
            assertTrue(archive.observe("u").first().isEmpty())
        } finally { scope.coroutineContext[Job]!!.cancelAndJoin() }
    }

    @Test fun rehideReplacesStatusAndDuplicateHideDoesNotDuplicateRecord() = runBlocking {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        try {
            val archive = store(File(temp.root, "rehide.preferences_pb"), scope)
            archive.hide("u", JobDto(id = "j", status = "done"))
            archive.hide("u", JobDto(id = "j", status = "loading"))
            archive.reconcile("u", listOf(JobDto(id = "j", status = "assigned")))
            assertEquals("loading", archive.observe("u").first().getValue("j").status)
            assertEquals(1, archive.observe("u").first().size)
        } finally { scope.coroutineContext[Job]!!.cancelAndJoin() }
    }
}
