package pl.spedi.markusdispatcher

import org.junit.Assert.*
import org.junit.Test
import java.time.Instant

class DriverScheduleTest {
    @Test fun week1MustBeMondayAndEmptyClears() {
        assertEquals("2026-10-05", parseWeek1("05.10.2026"))
        assertNull(parseWeek1(""))
        assertThrows(IllegalArgumentException::class.java) { parseWeek1("06.10.2026") }
    }
    @Test fun dstGapRejectedAndBothFoldsExplicit() {
        assertThrows(IllegalArgumentException::class.java) { parsePlanTime("29.03.2026 02:30") }
        assertThrows(IllegalArgumentException::class.java) { parsePlanTime("25.10.2026 02:30") }
        assertEquals("2026-10-25T00:30:00Z", parsePlanTime("25.10.2026 02:30", 0))
        assertEquals("2026-10-25T01:30:00Z", parsePlanTime("25.10.2026 02:30", 1))
    }
    @Test fun winterSummerConversionsAndInvalidCalendarDay() {
        assertEquals("2026-01-15T11:00:00Z", parsePlanTime("15.01.2026 12:00"))
        assertEquals("2026-07-15T10:00:00Z", parsePlanTime("15.07.2026 12:00"))
        assertThrows(java.time.format.DateTimeParseException::class.java) { parsePlanTime("31.02.2026 12:00") }
    }
    @Test fun returnBeforeDepartureRejected() {
        assertThrows(IllegalArgumentException::class.java) {
            buildDriverPlan("05.10.2026", "06.10.2026 10:00", "05.10.2026 10:00", null)
        }
    }
    @Test fun unchangedFoldInstantPreservedAndBothFieldsHaveIndependentChoices() {
        val earlier = Instant.parse("2026-10-25T00:30:00Z")
        assertEquals(earlier.toString(), buildDriverPlan("19.10.2026", "25.10.2026 02:30", "", null,
            originalDeparture = earlier)["departure_at"])
        val plan = buildDriverPlan("19.10.2026", "25.10.2026 02:30", "25.10.2026 02:40", 0, 1)
        assertEquals("2026-10-25T00:30:00Z", plan["departure_at"])
        assertEquals("2026-10-25T01:40:00Z", plan["planned_return_at"])
    }
    @Test fun parserKeepsMissingDatesAndSeparateReturnRestEvents() {
        val missing = DriverSchedule.fromMap("missing", mapOf("display_name" to "Jan"))
        assertNull(missing.week1)
        assertTrue(missing.events().isEmpty())
        assertEquals("Start wyjazdu nieuzupełniony", missing.tripLabel(Instant.now()))
        val active = DriverSchedule.fromMap("active", mapOf(
            "display_name" to "Jan", "planned_return_at" to "2026-10-08T10:00:00Z",
            "rest_start_by" to "2026-10-07T10:00:00Z", "departure_at" to "2026-10-01T10:00:00Z"))
        assertEquals(2, active.events().size)
        assertEquals("W trasie 6 dni 2 h", active.tripLabel(Instant.parse("2026-10-07T12:00:00Z")))
        assertEquals("active-rest", active.events().minBy { it.at }.key)
    }
}
