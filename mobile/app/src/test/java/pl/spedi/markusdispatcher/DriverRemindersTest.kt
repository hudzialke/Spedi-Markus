package pl.spedi.markusdispatcher

import org.junit.Assert.*
import org.junit.Test
import java.time.Instant
import org.json.JSONObject

class DriverRemindersTest {
    private fun driver(returnAt: String = "2026-10-10T10:00:00Z") = DriverSchedule.fromMap("a", mapOf(
        "display_name" to "Jan", "week1_monday" to "2026-10-05", "week4_monday" to "2026-10-26",
        "week4_start_at" to "2026-10-25T23:00:00Z", "planned_return_at" to returnAt,
        "rest_start_by" to "2026-10-09T10:00:00Z"))

    @Test fun localEditsSurviveNewSourceWithoutChangingSource() {
        val original = driver()
        val settings = DriverPreferences(mapOf("rest_start_by" to "2026-10-08T08:00:00Z"), setOf(48))
        val updated = driver("2026-10-12T10:00:00Z")
        val phone = settings.applyTo(updated)
        assertEquals(Instant.parse("2026-10-08T08:00:00Z"), phone.restStartBy)
        assertEquals(updated.plannedReturn, phone.plannedReturn)
        assertEquals(Instant.parse("2026-10-09T10:00:00Z"), updated.restStartBy)
        assertEquals(original.restStartBy, phone.sourceRestStartBy)
        assertTrue(phone.localEdited)
        assertFalse(DriverPreferences().applyTo(updated).localEdited)
    }

    @Test fun nullClearsAndWeek4UsesWarsawMonday() {
        val prefs = DriverPreferences(mapOf("planned_return_at" to null, "week1_monday" to "2026-10-05"), emptySet())
        val phone = DriverPreferences.fromJson(JSONObject(prefs.toJson().toString())).applyTo(driver())
        assertNull(phone.plannedReturn)
        assertEquals(Instant.parse("2026-10-25T23:00:00Z"), phone.week4Start)
        assertEquals(prefs, DriverPreferences.fromJson(prefs.toJson()))
        assertTrue(ReminderPolicy.desired("uid", listOf(driver())) { prefs }.isEmpty())
    }

    @Test fun savingUnchangedFieldsDoesNotFreezePcDates() {
        val source = driver()
        val values = mapOf("week1_monday" to source.week1.toString(), "departure_at" to null,
            "planned_return_at" to source.plannedReturn.toString(), "rest_start_by" to "2026-10-08T10:00:00Z")
        assertEquals(mapOf("rest_start_by" to "2026-10-08T10:00:00Z"), changedPhonePlan(source, DriverPreferences(), values))
    }

    @Test fun allLeadOptionsHaveCorrectTimesAndStableIdentity() {
        val reminders = ReminderPolicy.desired("uid", listOf(driver())) { DriverPreferences() }.filter { it.title == "Powrót planowany" }
        assertEquals(setOf(72,48,24), reminders.map { it.lead }.toSet())
        assertEquals(Instant.parse("2026-10-08T10:00:00Z"), reminders.first { it.lead == 48 }.due)
        val renamed = ReminderPolicy.desired("uid", listOf(driver().copy(name = "Nowa nazwa", revision = "new"))) { DriverPreferences() }
        assertTrue(renamed.map { it.id }.containsAll(reminders.map { it.id }))
        val otherUid = ReminderPolicy.desired("other", listOf(driver())) { DriverPreferences() }
        assertTrue(reminders.none { old -> otherUid.any { it.id == old.id } })
    }

    @Test fun deadlineAndChoiceChangesCancelPreviousReminder() {
        val original = ReminderPolicy.desired("uid", listOf(driver())) { DriverPreferences() }
        val changed = ReminderPolicy.desired("uid", listOf(driver())) { DriverPreferences(mapOf("planned_return_at" to "2026-10-12T10:00:00Z"), setOf(24)) }
        val now = Instant.parse("2026-10-01T10:00:00Z")
        val queue = ReminderPolicy.reconcile(original, changed, emptySet(), now)
        assertEquals(changed.toSet(), queue.toSet())
        assertTrue(queue.all { it.lead == 24 })
        assertTrue(queue.filter { it.title == "Powrót planowany" }.none { n -> original.any { it.id == n.id } })
        assertTrue(ReminderPolicy.reconcile(original, emptyList(), emptySet(), now).isEmpty())
    }

    @Test fun historicalDownloadsDoNotNotifyAndRebootCatchupDoesNotRepeat() {
        val desired = ReminderPolicy.desired("uid", listOf(driver())) { DriverPreferences(leads = setOf(48)) }
            .filter { it.title == "Powrót planowany" }
        val event = desired.single()
        val now = event.due.plusSeconds(3600)
        assertTrue(ReminderPolicy.reconcile(emptyList(), desired, emptySet(), now).isEmpty())
        assertEquals(desired, ReminderPolicy.reconcile(desired, desired, emptySet(), now))
        assertEquals(desired, ReminderPolicy.ready(desired, now))
        assertTrue(ReminderPolicy.reconcile(desired, desired, setOf(event.id), now).isEmpty())
        assertTrue(ReminderPolicy.ready(desired, event.due.plusSeconds(7*3600)).isEmpty())
        assertEquals(event, PhoneReminder.fromJson(JSONObject(event.toJson().toString())))
    }

    @Test fun hiddenProfilePersistsAcrossReloadAndNewPcDataWithoutLosingItsPlan() {
        val settings = DriverPreferences(mapOf("rest_start_by" to "2026-10-08T08:00:00Z"), setOf(48), hidden = true)
        val loaded = DriverPreferences.fromJson(JSONObject(settings.toJson().toString()))
        assertEquals(settings, loaded)
        assertTrue(loaded.hidden)
        val updated = driver("2026-10-12T10:00:00Z")
        assertEquals(Instant.parse("2026-10-08T08:00:00Z"), loaded.applyTo(updated).restStartBy)
        assertTrue(ReminderPolicy.desired("uid", listOf(updated)) { loaded }.isEmpty())
        assertFalse(DriverPreferences.fromJson(JSONObject("{\"leads\":[24],\"overrides\":{}}")).hidden)
    }

    @Test fun hideCancelsOnlyThisDriversRemindersAndRestoreKeepsChoicesAndEdits() {
        val jan = driver()
        val other = driver().copy(id = "b", name = "Piotr")
        val settings = DriverPreferences(mapOf("rest_start_by" to "2026-10-08T08:00:00Z"), setOf(48))
        val original = ReminderPolicy.desired("uid", listOf(jan, other)) { if (it == jan.id) settings else DriverPreferences() }
        val hidden = settings.copy(hidden = true)
        val desired = ReminderPolicy.desired("uid", listOf(jan, other)) { if (it == jan.id) hidden else DriverPreferences() }
        val now = Instant.parse("2026-10-01T00:00:00Z")
        val queue = ReminderPolicy.reconcile(original, desired, emptySet(), now)
        assertTrue(queue.none { it.driverId == jan.id })
        assertEquals(original.filter { it.driverId == other.id }.toSet(), queue.toSet())
        val restored = hidden.copy(hidden = false)
        assertEquals(settings, restored)
        val restoredReminders = ReminderPolicy.desired("uid", listOf(jan, other)) { if (it == jan.id) restored else DriverPreferences() }
        assertEquals(original.toSet(), restoredReminders.toSet())
    }
}
