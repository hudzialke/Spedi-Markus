package pl.spedi.markusdispatcher

import org.junit.Assert.*
import org.junit.Test

class JobRulesTest {
    @Test fun terminalJobsReturnOnlyWhenExplicitlyActive() {
        for (status in ArchiveRules.terminal) for (active in ArchiveRules.active)
            assertTrue(ArchiveRules.shouldRestore(HiddenJob("j", status), active))
    }
    @Test fun manuallyHiddenActiveJobsStayHidden() {
        for (status in ArchiveRules.active) for (next in ArchiveRules.active + ArchiveRules.terminal)
            assertFalse(ArchiveRules.shouldRestore(HiddenJob("j", status), next))
    }
    @Test fun unknownOrMissingStatusesNeverRestore() {
        for (next in listOf("", "unknown", "done", "cancelled"))
            assertFalse(ArchiveRules.shouldRestore(HiddenJob("j", "done"), next))
    }
    @Test fun hiddenJobsExcludedEvenWithoutActiveFilter() {
        val jobs = listOf(JobDto(id = "a"), JobDto(id = "b", status = "done"))
        assertEquals(listOf("b"), ArchiveRules.visible(jobs, mapOf("a" to HiddenJob("a", "draft")), false).map { it.id })
        assertTrue(ArchiveRules.visible(jobs, mapOf("a" to HiddenJob("a", "draft")), true).isEmpty())
    }
    @Test fun restoredJobsRespectFilter() {
        val jobs = listOf(JobDto(id = "a"), JobDto(id = "b", status = "done"))
        assertEquals(1, ArchiveRules.visible(jobs, emptyMap(), true).size)
        assertEquals(2, ArchiveRules.visible(jobs, emptyMap(), false).size)
    }
    @Test fun emptyFieldsOmittedAndLongValuesPreserved() {
        val notes = "Zażółć\n" + "Adres ".repeat(300)
        val sections = JobPresentation.sections(JobDto(status = "", notes = notes))
        assertEquals(1, sections.size)
        assertEquals(notes, sections.single().fields.single().second)
        assertTrue(JobPresentation.copyAll(JobDto(notes = notes)).contains(notes))
    }
    @Test fun allDtoFieldsAreExposed() {
        val job = JobDto("id-value", "ref", "custom-status", "driver-id", "driver-name", "client",
            "load-address", "load-time", "load-gps", "unload-address", "unload-time", "unload-gps",
            "pallets", "notes", "maps-url", "phone", "email", "message", "updated")
        val values = JobPresentation.sections(job).flatMap { it.fields }.map { it.second }
        assertEquals(19, values.size)
        job.javaClass.declaredFields.filter { it.type == String::class.java }.forEach {
            it.isAccessible = true
            assertTrue("Missing ${it.name}", values.contains(it.get(job)))
        }
    }
    @Test fun mapsAndContactsAreIncludedInCopyAll() {
        val text = JobPresentation.copyAll(JobDto(mapsUrl = "https://maps.example/a?b=c", contactPhone = "+48 123", contactEmail = "a@example.com"))
        assertTrue(text.contains("https://maps.example/a?b=c")); assertTrue(text.contains("+48 123")); assertTrue(text.contains("a@example.com"))
    }
    @Test fun archiveRoundTripPreservesIdsStatusReasonAndUnicode() {
        val record = HiddenJob("a:/\"ą", "done", "manual", "Ref żółć")
        assertEquals(mapOf(record.jobId to record), ArchiveCodec.decode(ArchiveCodec.encode(mapOf(record.jobId to record))))
    }
    @Test fun malformedArchiveFailsInsteadOfSilentlyUnhiding() {
        assertThrows(Exception::class.java) { ArchiveCodec.decode("broken") }
    }
    @Test fun gpsUsesDotsRegardlessOfDeviceLocale() {
        val old = java.util.Locale.getDefault()
        try { java.util.Locale.setDefault(java.util.Locale.forLanguageTag("pl-PL"))
            assertEquals("52.100000, 16.200000", formatGps(52.1, 16.2))
        } finally { java.util.Locale.setDefault(old) }
    }
    @Test fun invalidGpsDoesNotCreateFakeLocation() {
        assertEquals("", formatGps(null, 12)); assertEquals("", formatGps("abc", 12)); assertEquals("", formatGps(Double.NaN, 12))
    }
}
